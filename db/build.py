#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把项目里散落的 JSON 数据全量灌进 SQLite（db/schema.sql）。

用法：
    python db/build.py                       # 只导本地 JSON
    python db/build.py --online              # 额外联网拉紫卡武器 / 词条表
    python db/build.py --db data/warframe.db # 指定库文件

脚本是幂等的：同一个源重复导入会先清掉旧行再写，可以反复跑。
联网部分（紫卡武器表 / 词条表）失败只会留空并提示，不影响其余数据。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SCHEMA = os.path.join(ROOT, "db", "schema.sql")
DEFAULT_DB = os.path.join(ROOT, "data", "warframe.db")

SCHEMA_VERSION = "2"


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------

def _path(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def _read_json(rel: str) -> Optional[Any]:
    """读仓库里的 JSON，缺文件或坏文件返回 None（不中断整个导入）。"""
    try:
        with open(_path(*rel.split("/")), "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> int:
    return 1 if value else 0


class Loader:
    """包一层 sqlite3，顺手记 sources 与统计。"""

    def __init__(self, conn: sqlite3.Connection, verbose: bool = True):
        self.conn = conn
        self.verbose = verbose
        self.stats: List[Tuple[str, int]] = []

    def source(self, name: str, path: str, kind: str, rows: int,
               fetched_at: Optional[float] = None, note: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sources"
            " (name, path, kind, rows, fetched_at, imported_at, note)"
            " VALUES (?,?,?,?,?,?,?)",
            (name, path, kind, rows, fetched_at, time.time(), note))
        self.stats.append((name, rows))
        if self.verbose:
            print("  %-18s %7d 行  <- %s" % (name, rows, path))

    def clear(self, table: str, where: str = "", args: Sequence[Any] = ()) -> None:
        sql = "DELETE FROM %s" % table
        if where:
            sql += " WHERE " + where
        self.conn.execute(sql, tuple(args))

    def many(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        rows = list(rows)
        if not rows:
            return 0
        self.conn.executemany(sql, rows)
        return len(rows)


# --------------------------------------------------------------------------
# 1. 物品
# --------------------------------------------------------------------------

def import_items(load: Loader) -> None:
    data = _read_json("data/items_cache.json")
    if not isinstance(data, dict):
        return
    items = [row for row in (data.get("items") or []) if isinstance(row, dict)]
    load.clear("item_tags")
    load.clear("items")

    rows: List[Tuple[Any, ...]] = []
    tags: List[Tuple[str, str]] = []
    for item in items:
        slug = str(item.get("slug") or "")
        if not slug:
            continue
        rows.append((
            slug,
            str(item.get("name") or "") or None,
            str(item.get("name_en") or "") or None,
            str(item.get("id") or "") or None,
            _as_int(item.get("ducats")),
            _as_int(item.get("mr")),
            _as_int(item.get("tax")),
            str(item.get("thumb") or "") or None,
            str(item.get("desc") or "") or None,
            str(item.get("wiki") or "") or None,
            _bool(item.get("tradable")),
            _bool(item.get("set_root")),
            _as_int(item.get("max_rank")),
        ))
        for tag in item.get("tags") or []:
            tag = str(tag or "").strip()
            if tag:
                tags.append((slug, tag))

    written = load.many(
        "INSERT OR REPLACE INTO items (slug, name_zh, name_en, item_id, ducats,"
        " mastery_rank, trading_tax, thumb, description, wiki_url, tradable,"
        " set_root, max_rank) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    load.many("INSERT OR REPLACE INTO item_tags (slug, tag) VALUES (?,?)", tags)
    load.source("items_cache", "data/items_cache.json", "json", written,
                _as_float(data.get("fetched_at")) or None,
                "warframe.market 物品表（%s / %s）" % (data.get("language", ""),
                                                      data.get("platform", "")))


def import_item_relations(load: Loader) -> None:
    """由 `*_set` 与同前缀部件 slug 派生套装 → 部件关系。"""
    load.clear("item_relations")
    slugs = [str(row[0]) for row in load.conn.execute(
        "SELECT slug FROM items ORDER BY slug")]
    parents = [slug for slug in slugs if slug.endswith("_set")]
    rows: List[Tuple[str, str, str, int]] = []
    for parent in parents:
        base = parent[:-len("_set")]
        prefix = base + "_"
        children = sorted(
            slug for slug in slugs
            if slug.startswith(prefix) and slug != parent
            and not slug.endswith("_set"))
        for position, child in enumerate(children):
            rows.append((parent, child, "component", position))
    written = load.many(
        "INSERT OR REPLACE INTO item_relations"
        " (parent_slug, child_slug, relation, position) VALUES (?,?,?,?)",
        rows)
    load.source("item_relations", "data/items_cache.json", "derived", written,
                None, "由套装 slug 与同前缀部件自动派生")


# --------------------------------------------------------------------------
# 2. 中英译名
# --------------------------------------------------------------------------

# data/names_zh.json 的键 → 统一 category
_NAMES_ZH_CATEGORY = {"warframes": "warframe", "weapons": "weapon"}


def import_zh_names(load: Loader) -> None:
    for name, rel, category_map in (
        ("names_zh", "data/names_zh.json", _NAMES_ZH_CATEGORY),
        ("wiki_zh", "data/wiki_zh.json", None),
    ):
        data = _read_json(rel)
        if not isinstance(data, dict):
            continue
        load.clear("zh_names", "source = ?", (name,))
        rows: List[Tuple[str, str, str, str]] = []
        for key, table in data.items():
            if str(key).startswith("_") or not isinstance(table, dict):
                continue
            category = (category_map or {}).get(str(key), str(key))
            for name_en, name_zh in table.items():
                name_en = str(name_en or "").strip()
                name_zh = str(name_zh or "").strip()
                if name_en and name_zh:
                    rows.append((name, category, name_en, name_zh))
        written = load.many(
            "INSERT OR REPLACE INTO zh_names (source, category, name_en, name_zh)"
            " VALUES (?,?,?,?)", rows)
        note = str(data.get("_来源") or data.get("_说明") or "")[:200]
        load.source(name, rel, "json", written, None, note)


def import_baro_names(load: Loader) -> None:
    data = _read_json("data/baro_names_zh.json")
    if not isinstance(data, dict):
        return
    table = data.get("names") or {}
    load.clear("baro_names")
    rows = [(str(key).strip(), str(value).strip())
            for key, value in table.items() if str(key).strip() and str(value).strip()]
    written = load.many(
        "INSERT OR REPLACE INTO baro_names (unique_name, name_zh) VALUES (?,?)", rows)
    load.source("baro_names_zh", "data/baro_names_zh.json", "json", written,
                _as_float(data.get("fetched_at")) or None,
                "虚空商人物品唯一名（/Lotus/...）→ 中文名")


def import_frame_alias(load: Loader) -> None:
    data = _read_json("data/warframe_alias.json")
    if not isinstance(data, dict):
        return
    table = data.get("aliases") or {}
    load.clear("frame_alias_zh")
    rows = [(str(key).strip(), str(value).strip())
            for key, value in table.items() if str(key).strip() and str(value).strip()]
    written = load.many(
        "INSERT OR REPLACE INTO frame_alias_zh (name_en, alias_zh) VALUES (?,?)", rows)
    load.source("warframe_alias", "data/warframe_alias.json", "json", written, None,
                "战甲英文名 → 国服玩家黑话")


def import_frame_nicknames(load: Loader) -> None:
    data = _read_json("data/frame_nicknames.json")
    if not isinstance(data, dict):
        return
    priority = {str(item) for item in (data.get("_优先列表") or [])}
    load.clear("frame_nicknames")
    rows = []
    for key, value in data.items():
        if str(key).startswith("_") or not isinstance(value, str):
            continue
        rows.append((str(key).strip(), value.strip(),
                     1 if str(key).strip() in priority else 0,
                     "优先按战甲解释" if str(key).strip() in priority else ""))
    written = load.many(
        "INSERT OR REPLACE INTO frame_nicknames (nickname, name_en, priority, note)"
        " VALUES (?,?,?,?)", rows)
    load.source("frame_nicknames", "data/frame_nicknames.json", "json", written, None,
                "战甲俗名（市场上没有可交易物品的那些）")


# --------------------------------------------------------------------------
# 3. 别名
# --------------------------------------------------------------------------

def import_item_aliases(load: Loader) -> None:
    for kind, rel in (("general", "data/aliases.json"),
                      ("frame", "data/frame_aliases.json"),
                      ("component", "data/component_aliases.json")):
        data = _read_json(rel)
        if not isinstance(data, dict):
            continue
        load.clear("item_aliases", "kind = ?", (kind,))
        rows = []
        for alias, slug in data.items():
            if str(alias).startswith("_") or not isinstance(slug, str):
                continue
            alias = str(alias).strip()
            slug = slug.strip()
            if alias and slug:
                rows.append((alias, slug, kind, os.path.basename(rel)))
        written = load.many(
            "INSERT OR REPLACE INTO item_aliases (alias, slug, kind, source)"
            " VALUES (?,?,?,?)", rows)
        load.source(os.path.basename(rel)[:-5], rel, "json", written, None,
                    "%s 类别名 → 物品 slug" % kind)


# --------------------------------------------------------------------------
# 4. 星图
# --------------------------------------------------------------------------

def import_sol_nodes(load: Loader) -> None:
    data = _read_json("data/sol_nodes.json")
    if not isinstance(data, dict):
        return
    load.clear("sol_nodes")
    rows = []
    for node_id, node in data.items():
        if not isinstance(node, dict):
            continue
        rows.append((str(node_id).strip(),
                     str(node.get("name") or "") or None,
                     str(node.get("enemy") or "") or None,
                     str(node.get("type") or "") or None))
    written = load.many(
        "INSERT OR REPLACE INTO sol_nodes (node_id, name, enemy, mission_type)"
        " VALUES (?,?,?,?)", rows)
    load.source("sol_nodes", "data/sol_nodes.json", "json", written, None,
                "星图节点（官方 worldState 的 SolNode 表）")


# --------------------------------------------------------------------------
# 5. 赏金
# --------------------------------------------------------------------------

def _syndicate_zh() -> Dict[str, str]:
    """集团中文名：优先用 bot.present 里的常量，读不到就用兜底表。"""
    try:
        from bot.present import BOUNTY_LOCATIONS  # type: ignore
        if isinstance(BOUNTY_LOCATIONS, dict) and BOUNTY_LOCATIONS:
            return dict(BOUNTY_LOCATIONS)
    except Exception:
        pass
    return {"Ostrons": "希图斯", "Solaris United": "福尔图娜",
            "Entrati": "火卫二", "Cavia": "解剖圣所",
            "The Hex": "霍瓦尼亚", "The Holdfasts": "扎里曼"}


def import_bounties(load: Loader) -> None:
    labels = _syndicate_zh()

    # 5.1 固定赏金（扎里曼 / 解剖圣所 / 霍瓦尼亚）
    static = _read_json("data/bounty_static.json")
    if isinstance(static, dict):
        locations = static.get("locations") or {}
        load.clear("bounty_tier_missions")
        load.clear("bounty_tiers")
        load.clear("bounty_location_missions")
        load.clear("bounty_locations")
        refresh = _as_float(static.get("refresh_hours")) or None
        loc_rows, pool_rows, tier_rows, tier_mission_rows = [], [], [], []
        for key, node in locations.items():
            if not isinstance(node, dict):
                continue
            key = str(key)
            loc_rows.append((key, str(node.get("label") or labels.get(key, "")) or None,
                             str(node.get("npc") or "") or None,
                             str(static.get("source") or "") or None, refresh))
            for position, mission in enumerate(node.get("missions") or []):
                pool_rows.append((key, str(mission), position))
            for position, tier in enumerate(node.get("tiers") or []):
                if not isinstance(tier, dict):
                    continue
                tier_key = str(tier.get("tier"))
                tier_rows.append((key, tier_key,
                                  str(tier.get("level") or "") or None,
                                  str(tier.get("reward") or "") or None, position))
                for order, mission in enumerate(tier.get("missions") or []):
                    tier_mission_rows.append((key, tier_key, str(mission), order))
        written = load.many(
            "INSERT OR REPLACE INTO bounty_locations (location_key, label_zh, npc,"
            " source, refresh_hours) VALUES (?,?,?,?,?)", loc_rows)
        load.many("INSERT OR REPLACE INTO bounty_location_missions"
                  " (location_key, mission_zh, position) VALUES (?,?,?)", pool_rows)
        load.many("INSERT OR REPLACE INTO bounty_tiers"
                  " (location_key, tier, level_range, reward_text, position)"
                  " VALUES (?,?,?,?,?)", tier_rows)
        load.many("INSERT OR REPLACE INTO bounty_tier_missions"
                  " (location_key, tier, mission_zh, position) VALUES (?,?,?,?)",
                  tier_mission_rows)
        load.source("bounty_static", "data/bounty_static.json", "json", written, None,
                    "三处固定赏金的档位/等级/任务池")

    # 5.2 赏金目标
    objectives = _read_json("data/bounty_objectives.json")
    if isinstance(objectives, dict):
        jobs = objectives.get("jobs") or {}
        load.clear("bounty_objectives")
        rows = []
        for job_type, node in jobs.items():
            if not isinstance(node, dict):
                continue
            rows.append((str(job_type).strip(),
                         str(node.get("en") or "") or None,
                         str(node.get("zh") or "") or None))
        written = load.many(
            "INSERT OR REPLACE INTO bounty_objectives (job_type, name_en, name_zh)"
            " VALUES (?,?,?)", rows)
        load.source("bounty_objectives", "data/bounty_objectives.json", "json",
                    written, None, "官方 jobType → 赏金目标名")

    # 5.3 奖励池（顺便把官方三处据点补进 locations，那里没有 static 配置）
    rewards = _read_json("data/bounty_rewards.json")
    if isinstance(rewards, dict):
        load.clear("bounty_rewards")
        rows = []
        for syndicate, table in rewards.items():
            if str(syndicate).startswith("_") or not isinstance(table, dict):
                continue
            syndicate = str(syndicate)
            for tier, entries in table.items():
                for position, reward in enumerate(entries or []):
                    reward = str(reward or "").strip()
                    if reward:
                        rows.append((syndicate, str(tier), position, reward))
            load.conn.execute(
                "INSERT OR IGNORE INTO bounty_locations (location_key, label_zh,"
                " npc, source, refresh_hours) VALUES (?,?,'','',NULL)",
                (syndicate, labels.get(syndicate) or None))
        written = load.many(
            "INSERT OR REPLACE INTO bounty_rewards (syndicate, tier, position, reward)"
            " VALUES (?,?,?,?)", rows)
        load.source("bounty_rewards", "data/bounty_rewards.json", "json", written,
                    None, "据点 × 档位 的赏金奖励池")


# --------------------------------------------------------------------------
# 6. 紫卡
# --------------------------------------------------------------------------

def import_riven_dictionary(load: Loader) -> None:
    """词条别名 / 拍卖变体写法：这些在 bot/riven.py 里，是纯本地的静态知识。"""
    try:
        from bot.riven import (ATTRIBUTE_GROUPS, AUCTION_ATTRIBUTE_NAMES,
                               NEGATIVE_WORDS, POSITIVE_ONLY_WORDS)
    except Exception as exc:                      # pragma: no cover
        print("  跳过词条字典：导入 bot.riven 失败（%s）" % exc)
        return

    load.clear("riven_attribute_aliases")
    rows: List[Tuple[str, str, str]] = []
    for words, url_names in ATTRIBUTE_GROUPS:
        for word in words:
            for url_name in url_names:
                rows.append((str(word).strip(), str(url_name).strip(), "group"))
    for word in NEGATIVE_WORDS:
        rows.append((str(word).strip(), "*", "negative"))
    for word in POSITIVE_ONLY_WORDS:
        rows.append((str(word).strip(), "*", "positive"))
    written = load.many(
        "INSERT OR REPLACE INTO riven_attribute_aliases (word, url_name, kind)"
        " VALUES (?,?,?)", rows)
    load.source("riven_attribute_aliases", "bot/riven.py", "code", written, None,
                "玩家叫法 → 词条 url_name（ATTRIBUTE_GROUPS）")

    load.clear("riven_attribute_variants")
    variant_rows = [(str(key).strip(), str(value).strip())
                    for key, value in AUCTION_ATTRIBUTE_NAMES.items()
                    if str(key).strip() and str(value).strip()]
    written = load.many(
        "INSERT OR REPLACE INTO riven_attribute_variants (variant, name_zh)"
        " VALUES (?,?)", variant_rows)
    load.source("riven_attribute_variants", "bot/riven.py", "code", written, None,
                "拍卖接口里和官方表不一致的写法兜底")


_RIVEN_WEAPONS_API = "https://api.warframe.market/v2/riven/weapons"
_RIVEN_ATTRIBUTES_API = "https://api.warframe.market/v2/riven/attributes"


def _get_json(url: str, timeout: float = 20.0) -> Optional[Any]:
    import urllib.request
    request = urllib.request.Request(
        url, headers={"User-Agent": "qqwarframe-bot/db-build",
                      "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def import_riven_online(load: Loader, timeout: float = 20.0) -> None:
    """联网补紫卡武器表 / 词条表。失败只提示，不写坏数据。"""
    # 武器表
    try:
        data = _get_json(_RIVEN_WEAPONS_API, timeout)
        rows = (data or {}).get("data") or []
        payload: List[Tuple[Any, ...]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or "")
            if not slug:
                continue
            i18n = row.get("i18n") or {}
            english = i18n.get("en") or {}
            zh = i18n.get("zh-hans") or {}
            thumb = str(zh.get("thumb") or english.get("thumb") or "")
            payload.append((
                slug,
                str(zh.get("name") or "") or None,
                str(english.get("name") or "") or None,
                str(row.get("rivenType") or "") or None,
                str(row.get("group") or "") or None,
                _as_float(row.get("disposition")) or None,
                _as_int(row.get("reqMasteryRank")) or None,
                thumb or None,
                ("https://warframe.market/static/assets/" + thumb.lstrip("/"))
                if thumb else None,
                json.dumps(row, ensure_ascii=False),
            ))
        load.clear("riven_weapons")
        written = load.many(
            "INSERT OR REPLACE INTO riven_weapons (slug, name_zh, name_en,"
            " riven_type, group_name, disposition, mastery_rank, thumb, icon_url,"
            " raw) VALUES (?,?,?,?,?,?,?,?,?,?)", payload)
        load.source("riven_weapons", _RIVEN_WEAPONS_API, "api", written,
                    time.time(), "紫卡武器表（倾向 / 类型 / MR）")
    except Exception as exc:
        print("  紫卡武器表拉取失败（跳过）：%s" % exc)

    # 词条表
    try:
        data = _get_json(_RIVEN_ATTRIBUTES_API, timeout)
        rows = (data or {}).get("data") or []
        payload = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            url_name = str(row.get("slug") or row.get("url_name") or "")
            if not url_name:
                continue
            i18n = row.get("i18n") or {}
            payload.append((
                url_name,
                str((i18n.get("zh-hans") or {}).get("name") or "") or None,
                str((i18n.get("en") or {}).get("name") or "") or None,
                json.dumps(row, ensure_ascii=False),
            ))
        load.clear("riven_attributes")
        written = load.many(
            "INSERT OR REPLACE INTO riven_attributes (url_name, name_zh, name_en, raw)"
            " VALUES (?,?,?,?)", payload)
        load.source("riven_attributes", _RIVEN_ATTRIBUTES_API, "api", written,
                    time.time(), "紫卡词条官方名称")
    except Exception as exc:
        print("  紫卡词条表拉取失败（跳过）：%s" % exc)


# --------------------------------------------------------------------------
# 7. 行情快照
# --------------------------------------------------------------------------

def import_board_cache(load: Loader) -> None:
    data = _read_json("data/board_cache.json")
    if not isinstance(data, dict):
        return

    # 垃圾 / 战甲：[最低售价, 最高收购价, 挂单数, 更新时间]
    load.clear("price_quotes")
    rows: List[Tuple[Any, ...]] = []
    for kind, key in (("junk", "junk"), ("frame", "frames")):
        table = data.get(key) or {}
        for slug, entry in table.items():
            if not isinstance(entry, (list, tuple)) or not entry:
                continue
            rows.append((str(slug), kind,
                         _as_int(entry[0]) if len(entry) > 0 else 0,
                         _as_int(entry[1]) if len(entry) > 1 else 0,
                         _as_int(entry[2]) if len(entry) > 2 else 0,
                         _as_float(entry[3]) if len(entry) > 3 else None))
    written = load.many(
        "INSERT OR REPLACE INTO price_quotes (slug, kind, price, buy_price, orders,"
        " updated) VALUES (?,?,?,?,?,?)", rows)
    load.source("price_quotes", "data/board_cache.json", "json", written,
                _as_float(data.get("saved")) or None,
                "后台扫描的垃圾 / 战甲地板价（v%s）" % data.get("version", "?"))

    # 紫卡：[抓取时间, 候选, 标志位, 均价, 样本数]
    riven = data.get("riven") or {}
    load.clear("riven_listings")
    load.clear("riven_market")
    market_rows: List[Tuple[Any, ...]] = []
    listing_rows: List[Tuple[Any, ...]] = []
    for slug, entry in riven.items():
        if not isinstance(entry, (list, tuple)) or not entry:
            continue
        slug = str(slug)
        updated = _as_float(entry[0]) or None
        flags = _as_int(entry[2]) if len(entry) > 2 else 0
        value = _as_float(entry[3]) if len(entry) > 3 else 0.0
        samples = _as_int(entry[4]) if len(entry) > 4 else 0
        market_rows.append((slug, value, samples, flags >> 2,
                            1 if flags & 1 else 0, updated))
        for position, item in enumerate(entry[1] if len(entry) > 1 else []):
            if not isinstance(item, (list, tuple)) or not item:
                continue
            listing_rows.append((
                slug, position,
                _as_int(item[0]) if len(item) > 0 else 0,
                _as_int(item[1]) if len(item) > 1 else 0,
                str(item[2] or "") if len(item) > 2 else "",
                json.dumps(list(item[3] or []), ensure_ascii=False)
                if len(item) > 3 else "[]",
                _as_int(item[5]) if len(item) > 5 else 0,
                updated,
            ))
    written = load.many(
        "INSERT OR REPLACE INTO riven_market (slug, value, samples, metric_version,"
        " legacy, updated) VALUES (?,?,?,?,?,?)", market_rows)
    listings = load.many(
        "INSERT OR REPLACE INTO riven_listings (slug, position, price, rank, seller,"
        " attributes, flags, updated) VALUES (?,?,?,?,?,?,?,?)", listing_rows)
    load.source("riven_market", "data/board_cache.json", "json", written,
                _as_float(data.get("saved")) or None,
                "紫卡行情价（含 %d 条候选挂单）" % listings)


def import_rotation(load: Loader) -> None:
    data = _read_json("data/rotation_cache.json")
    if not isinstance(data, dict):
        return
    payload = data.get("data") or {}
    fetched = data.get("fetched") or {}
    load.clear("rotation_items")
    load.clear("rotation_vendors")
    vendor_rows: List[Tuple[Any, ...]] = []
    item_rows: List[Tuple[Any, ...]] = []
    for vendor, node in payload.items():
        vendor = str(vendor)
        observed = ""
        if isinstance(node, dict):
            observed = str(node.get("observed") or "")
        vendor_rows.append((vendor, observed or None,
                            _as_float(fetched.get(vendor)) or None,
                            json.dumps(node, ensure_ascii=False)))
        # 三种形态：{items:[...]}（字符串列表）/ [[名,元素,百分比]] / {A/B/C:[[...]]}
        if isinstance(node, dict) and isinstance(node.get("items"), list):
            for position, item in enumerate(node["items"]):
                item_rows.append((vendor, "-", position, str(item), None, None))
        elif isinstance(node, dict):
            for rotation, entries in node.items():
                if rotation == "items" or not isinstance(entries, list):
                    continue
                for position, entry in enumerate(entries):
                    if isinstance(entry, (list, tuple)):
                        item_rows.append((
                            vendor, str(rotation), position,
                            str(entry[0]) if len(entry) > 0 else "",
                            str(entry[1]) if len(entry) > 1 else None,
                            _as_float(entry[2]) if len(entry) > 2 else None))
        elif isinstance(node, list):
            for position, entry in enumerate(node):
                if isinstance(entry, (list, tuple)):
                    item_rows.append((
                        vendor, "-", position,
                        str(entry[0]) if len(entry) > 0 else "",
                        str(entry[1]) if len(entry) > 1 else None,
                        _as_float(entry[2]) if len(entry) > 2 else None))
    written = load.many(
        "INSERT OR REPLACE INTO rotation_vendors (vendor, observed, updated, payload)"
        " VALUES (?,?,?,?)", vendor_rows)
    items = load.many(
        "INSERT OR REPLACE INTO rotation_items (vendor, rotation, position, item,"
        " element, percent) VALUES (?,?,?,?,?,?)", item_rows)
    load.source("rotation_cache", "data/rotation_cache.json", "json", written,
                _as_float(data.get("saved")) or None,
                "轮换商人库存（含 %d 个商品条目）" % items)


# --------------------------------------------------------------------------
# 8. 术语 / 工具字典
# --------------------------------------------------------------------------

def import_conquest(load: Loader) -> None:
    data = _read_json("bot/conquest_zh.json")
    if not isinstance(data, dict):
        return
    load.clear("conquest_terms")
    rows = []
    for dict_key, table in data.items():
        if not isinstance(table, dict):
            continue
        for term_en, term_zh in table.items():
            rows.append((str(dict_key), str(term_en).strip(), str(term_zh).strip()))
    written = load.many(
        "INSERT OR REPLACE INTO conquest_terms (dict_key, term_en, term_zh)"
        " VALUES (?,?,?)", rows)
    load.source("conquest_zh", "bot/conquest_zh.json", "json", written, None,
                "回廊 / 科研术语（来源 %s）" % data.get("source", ""))


def import_t2s(load: Loader) -> None:
    data = _read_json("bot/t2s.json")
    if not isinstance(data, dict):
        return
    load.clear("t2s")
    rows = [(str(key), str(value)) for key, value in data.items()
            if str(key) and str(value)]
    written = load.many(
        "INSERT OR REPLACE INTO t2s (traditional, simplified) VALUES (?,?)", rows)
    load.source("t2s", "bot/t2s.json", "json", written, None, "繁体 → 简体")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def build(db_path: str = DEFAULT_DB, online: bool = False,
          timeout: float = 20.0, verbose: bool = True) -> sqlite3.Connection:
    with open(SCHEMA, "r", encoding="utf-8") as handle:
        schema = handle.read()

    directory = os.path.dirname(os.path.abspath(db_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(schema)

    load = Loader(conn, verbose=verbose)
    if verbose:
        print("建库 %s" % db_path)
    with conn:
        import_items(load)
        import_item_relations(load)
        import_zh_names(load)
        import_baro_names(load)
        import_frame_alias(load)
        import_frame_nicknames(load)
        import_item_aliases(load)
        import_sol_nodes(load)
        import_bounties(load)
        import_riven_dictionary(load)
        import_board_cache(load)
        import_rotation(load)
        import_conquest(load)
        import_t2s(load)
        if online:
            import_riven_online(load, timeout=timeout)
        conn.execute("INSERT OR REPLACE INTO meta (key, value, updated)"
                     " VALUES ('schema_version', ?, ?)",
                     (SCHEMA_VERSION, time.time()))
        conn.execute("INSERT OR REPLACE INTO meta (key, value, updated)"
                     " VALUES ('built_at', ?, ?)",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), time.time()))
    return conn


def summary(conn: sqlite3.Connection, verbose: bool = True) -> Dict[str, int]:
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts: Dict[str, int] = {}
    for table in tables:
        counts[table] = int(conn.execute(
            "SELECT COUNT(*) FROM %s" % table).fetchone()[0])
    if verbose:
        print("\n表行数：")
        for table in tables:
            print("  %-24s %7d" % (table, counts[table]))
        total = sum(counts.values())
        print("  %-24s %7d" % ("合计", total))
    return counts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="构建 qqwarframe-bot 全量数据库")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite 文件路径")
    parser.add_argument("--online", action="store_true",
                        help="联网补紫卡武器 / 词条表（需要能连 warframe.market）")
    parser.add_argument("--timeout", type=float, default=20.0, help="联网超时秒数")
    parser.add_argument("--quiet", action="store_true", help="少输出")
    args = parser.parse_args(argv)

    conn = build(args.db, online=args.online, timeout=args.timeout,
                 verbose=not args.quiet)
    try:
        summary(conn, verbose=not args.quiet)
        size = os.path.getsize(args.db)
        if not args.quiet:
            print("\n完成：%s（%.1f MB）" % (args.db, size / 1048576.0))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
