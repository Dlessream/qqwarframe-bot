"""奸商库存的名称解析。

`api.warframe.com/cdn/worldState.php` 的 `VoidTraders[].Manifest` 只给
`ItemType`（uniqueName）与杜卡德/星币价，**没有名称**；能给名称的
`api.warframestat.us` 对数据中心 IP 一律 Cloudflare 403，所以走兜底时
奸商卡片只剩表头。

这里改用 DE 的官方公开导出（`content.warframe.com/PublicExport/`）补名称：

  1. 读 `index_zh.txt.lzma` 拿到各导出文件的当前版本号；
  2. 依次下 `Manifest/ExportXxx_zh.json!版本`；
  3. 拼成 uniqueName → 中文名，缓存到 `data/baro_names_zh.json`。

导出的键是 `/Lotus/Weapons/...`，而商店里是 `/Lotus/StoreItems/Weapons/...`，
两个方向都存一份，查表时不用再拼。缓存过期只在后台刷新，不阻塞查询。
"""

from __future__ import annotations

import json
import logging
import lzma
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.baronames")

INDEX_URL = "https://content.warframe.com/PublicExport/index_zh.txt.lzma"
MANIFEST_BASE = "https://content.warframe.com/PublicExport/Manifest/"

# 奸商卖的东西基本落在这些导出里；缺一两个不影响主要物品
WANTED: tuple = (
    "ExportWeapons_zh", "ExportCustoms_zh", "ExportGear_zh",
    "ExportResources_zh", "ExportUpgrades_zh", "ExportRecipes_zh",
    "ExportKeys_zh", "ExportWarframes_zh", "ExportSentinels_zh",
    "ExportRelicArcane_zh",
)

STORE_PREFIX = "/Lotus/StoreItems/"
_NAME_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|\d+")

# 官方导出里没有的（加成、宝箱、头像、场景模型等），按末段补齐。
# 这些名字长期不变，写死比留英文强；导出将来收录了也不冲突（先查表）。
EXTRA: Dict[str, str] = {
    "CreditBooster3DayStoreItem": "3 天星币加成",
    "CreditBooster7DayStoreItem": "7 天星币加成",
    "AffinityBooster3DayStoreItem": "3 天经验加成",
    "ResourceBooster3DayStoreItem": "3 天资源数量加成",
    "BaroTreasureBox": "奸商宝箱",
    "MummyQuestKeyBlueprint": "任务钥匙蓝图",
    "EmpyreanRegionADiorama": "九重天场景模型",
    "ImageBaroKiteerEvil": "终劫·基·提尔头像",
    "Halloween2019GrendelTreat": "万圣节 Grendel 糖果头像",
    "LisetGyroscopeSkinPrimeTrader": "登舰艇陀螺仪 Prime 装饰",
}

_names: Dict[str, str] = {}
_fetched_at = 0.0
_lock = threading.Lock()
_fetching = False
_config: Optional[Dict[str, Any]] = None
_http: Optional[HttpClient] = None
_logger = LOGGER


# --------------------------------------------------------------------- 配置
def configure(config: Optional[Dict[str, Any]],
              http: Optional[HttpClient] = None,
              logger: Optional[logging.Logger] = None,
              background: bool = True) -> None:
    """装载本地缓存；缓存缺失/过期时在后台刷新（不阻塞启动）。"""
    global _config, _http, _logger
    _config = config or {}
    _http = http
    if logger is not None:
        _logger = logger
    load_cache()
    if background and stale():
        start_refresh()


def _cache_file() -> str:
    section = (_config or {}).get("baro_names") or {}
    return str(section.get("cache_file") or "data/baro_names_zh.json")


def _ttl() -> float:
    section = (_config or {}).get("baro_names") or {}
    return float(section.get("ttl_seconds") or 7 * 24 * 3600)


def client() -> HttpClient:
    global _http
    if _http is None:
        world = (_config or {}).get("worldstate") or {}
        _http = HttpClient(
            proxy=str(world.get("proxy") or ""),
            timeout=float(world.get("timeout_seconds") or 30),
            retries=1, logger=_logger)
    return _http


# --------------------------------------------------------------------- 缓存
def load_cache() -> int:
    global _names, _fetched_at
    path = _cache_file()
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        _logger.warning("奸商名称缓存读取失败：%s", exc)
        return 0
    names = data.get("names") if isinstance(data, dict) else None
    if not isinstance(names, dict):
        return 0
    with _lock:
        _names = {str(k): str(v) for k, v in names.items() if v}
        _fetched_at = float(data.get("fetched_at") or 0)
    return len(_names)


def save_cache() -> None:
    path = _cache_file()
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            return
    tmp = path + ".tmp"
    try:
        with _lock:
            payload = {"fetched_at": _fetched_at, "count": len(_names),
                       "names": dict(_names)}
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as exc:
        _logger.warning("奸商名称缓存写入失败：%s", exc)


def stale() -> bool:
    if not _names:
        return True
    return time.time() - _fetched_at > _ttl()


# --------------------------------------------------------------------- 解析
def parse_index(raw: bytes) -> Dict[str, str]:
    """`index_zh.txt.lzma` → {文件名(不含 .json): 文件名!版本}。"""
    text = lzma.decompress(raw).decode("utf-8", "replace")
    mapping: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "!" not in line:
            continue
        name, version = line.split("!", 1)
        if name.endswith(".json"):
            mapping[name[:-5]] = name + "!" + version
    return mapping


def parse_export(raw: bytes) -> Dict[str, str]:
    """一个 ExportXxx_zh.json → uniqueName → 中文名（含 StoreItems 形态）。"""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise HttpError("导出文件不是 JSON：%s" % exc) from exc
    if not isinstance(data, dict):
        return {}
    found: Dict[str, str] = {}
    for rows in data.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            unique = row.get("uniqueName")
            name = row.get("name")
            if not unique or not name:
                continue
            unique, name = str(unique), str(name)
            found[unique] = name
            if unique.startswith("/Lotus/"):
                found[unique.replace("/Lotus/", STORE_PREFIX, 1)] = name
    return found


def readable(unique_name: str) -> str:
    """查不到中文时，把末段驼峰拆成可读英文，总比空着强。"""
    text = str(unique_name or "").rstrip("/")
    tail = text.rsplit("/", 1)[-1]
    if not tail:
        return "未知物品"
    words = _NAME_RE.findall(tail)
    if not words:
        return tail
    for suffix in ("StoreItem", "Blueprint", "Item"):
        if len(words) > 1 and words[-1] == suffix:
            words = words[:-1]
    return " ".join(words)


def lookup(unique_name: str) -> Optional[str]:
    if not unique_name:
        return None
    with _lock:
        hit = _names.get(unique_name)
    if hit:
        return hit
    tail = str(unique_name).rstrip("/").rsplit("/", 1)[-1]
    return EXTRA.get(tail)


def display(unique_name: str) -> str:
    return lookup(unique_name) or readable(unique_name)


# --------------------------------------------------------------------- 刷新
def start_refresh() -> None:
    global _fetching
    with _lock:
        if _fetching:
            return
        _fetching = True
    thread = threading.Thread(target=_refresh_worker, name="baro-names",
                              daemon=True)
    thread.start()


def _refresh_worker() -> None:
    global _fetching
    try:
        refresh()
    except Exception as exc:  # noqa: BLE001 - 名称只是锦上添花，失败不冒泡
        _logger.warning("奸商名称刷新失败：%s", exc)
    finally:
        with _lock:
            _fetching = False


def refresh(force: bool = False) -> int:
    """联网重建名称表，返回条数。失败抛 HttpError。"""
    global _fetched_at
    if not force and not stale():
        return len(_names)
    http = client()
    index = parse_index(http.request(INDEX_URL))
    names: Dict[str, str] = {}
    for stem in WANTED:
        filename = index.get(stem)
        if not filename:
            continue
        try:
            names.update(parse_export(http.request(MANIFEST_BASE + filename)))
        except (HttpError, ValueError) as exc:
            _logger.warning("导出 %s 拉取失败：%s", stem, exc)
    if not names:
        raise HttpError("官方导出里没有解析到任何名称")
    with _lock:
        _names.clear()
        _names.update(names)
        _fetched_at = time.time()
    save_cache()
    _logger.info("奸商名称表已更新：%d 条", len(names))
    return len(names)
