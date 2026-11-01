#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qqwarframe-bot SQLite 查询 CLI。

数据库由 `python db/build.py` 生成，默认位于 data/warframe.db。
这里只读查询，不会修改数据库；也方便排查导入结果。

示例：
    python db/query.py stats
    python db/query.py validate
    python db/query.py search 女枪
    python db/query.py item mesa_prime_set
    python db/query.py alias 电男
    python db/query.py bounty 希图斯
    python db/query.py riven 翁
    python db/query.py rank riven --limit 20
    python db/query.py rotation
    python db/query.py sources
    python db/query.py stats --json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "warframe.db")


class QueryError(Exception):
    pass


def connect(path: str = DEFAULT_DB) -> sqlite3.Connection:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise QueryError(
            "数据库不存在：%s\n先运行：python db/build.py" % path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _one(conn: sqlite3.Connection, sql: str,
         params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    row = conn.execute(sql, params).fetchone()
    return _dict(row) if row else None


def _all(conn: sqlite3.Connection, sql: str,
         params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    return [_dict(row) for row in conn.execute(sql, params).fetchall()]


def _scalar(conn: sqlite3.Connection, sql: str,
            params: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _print_table(rows: Sequence[Dict[str, Any]],
                 columns: Optional[Sequence[str]] = None) -> None:
    if not rows:
        print("（无结果）")
        return
    headers = list(columns or rows[0].keys())
    values = [[("" if row.get(col) is None else str(row.get(col)))
               for col in headers] for row in rows]
    widths = [len(header) for header in headers]
    for row in values:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print("  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in values:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    meta = {row["key"]: row["value"]
            for row in conn.execute("SELECT key,value FROM meta")}
    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts = {table: int(_scalar(conn, "SELECT COUNT(*) FROM %s" % table) or 0)
              for table in tables}
    sources = _all(conn, "SELECT name,rows,imported_at,note FROM sources"
                         " ORDER BY name")
    key_tables = ("items", "item_tags", "item_aliases", "zh_names",
                  "baro_names", "sol_nodes", "bounty_rewards", "rotation_items",
                  "riven_weapons", "riven_market", "price_quotes")
    return {
        "db": getattr(conn, "database", ""),
        "meta": meta,
        "tables": counts,
        "sources": sources,
        "key": {name: counts.get(name, 0) for name in key_tables},
        "total_rows": sum(counts.values()),
    }


def validate(conn: sqlite3.Connection) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    integrity = _scalar(conn, "PRAGMA integrity_check")
    if integrity != "ok":
        errors.append("SQLite integrity_check: %s" % integrity)
    for row in conn.execute("PRAGMA foreign_key_check"):
        errors.append("外键错误：%s" % tuple(row))
    checks = (
        ("item_tags 指向不存在的物品",
         "SELECT COUNT(*) FROM item_tags t LEFT JOIN items i ON i.slug=t.slug"
         " WHERE i.slug IS NULL"),
        ("item_aliases 指向不存在的物品",
         "SELECT COUNT(*) FROM item_aliases a LEFT JOIN items i ON i.slug=a.slug"
         " WHERE i.slug IS NULL"),
        ("price_quotes 指向不存在的物品",
         "SELECT COUNT(*) FROM price_quotes q LEFT JOIN items i ON i.slug=q.slug"
         " WHERE i.slug IS NULL"),
    )
    for label, sql in checks:
        count = int(_scalar(conn, sql) or 0)
        if count:
            errors.append("%s：%d 行" % (label, count))
    required = ("items", "item_tags", "frame_alias_zh", "sol_nodes")
    for table in required:
        count = int(_scalar(conn, "SELECT COUNT(*) FROM %s" % table) or 0)
        if count <= 0:
            errors.append("关键表为空：%s" % table)
    if int(_scalar(conn, "SELECT COUNT(*) FROM riven_weapons") or 0) == 0:
        warnings.append(
            "riven_weapons 为空：离线构建不会联网，"
            "在服务器执行 python db/build.py --online 补齐")
    if int(_scalar(conn, "SELECT COUNT(*) FROM price_quotes") or 0) <= 10:
        warnings.append(
            "price_quotes 很少：通常说明本地 board_cache.json 为空，"
            "需把服务器扫描后的缓存拉回再构建")
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "integrity": integrity}


def search_items(conn: sqlite3.Connection, text: str,
                 limit: int = 10) -> List[Dict[str, Any]]:
    query = str(text or "").strip()
    if not query:
        raise QueryError("搜索词不能为空")
    like = "%" + query + "%"
    rows = _all(conn, """
        SELECT DISTINCT i.slug, i.name_zh, i.name_en, i.ducats,
               i.mastery_rank, i.tradable
          FROM items i
          LEFT JOIN item_aliases a ON a.slug = i.slug
         WHERE i.slug LIKE ? OR i.name_zh LIKE ? OR i.name_en LIKE ?
            OR a.alias LIKE ?
         ORDER BY CASE
                    WHEN i.slug = ? OR i.name_zh = ? OR i.name_en = ? THEN 0
                    WHEN i.slug LIKE ? OR i.name_zh LIKE ? THEN 1
                    ELSE 2
                  END,
                  i.name_zh
         LIMIT ?""", (like, like, like, like, query, query, query,
                      query + "%", query + "%", int(limit)))
    return rows


def item_detail(conn: sqlite3.Connection, text: str) -> Dict[str, Any]:
    query = str(text or "").strip()
    if not query:
        raise QueryError("物品名/slug 不能为空")
    row = _one(conn, """
        SELECT * FROM v_item_detail
         WHERE slug = ? OR name_zh = ? OR name_en = ?
         ORDER BY CASE WHEN slug = ? THEN 0 ELSE 1 END
         LIMIT 1""", (query, query, query, query))
    if row:
        slug = row["slug"]
        row["price_quotes"] = _all(
            conn, "SELECT kind, price, buy_price, orders, updated"
                  " FROM price_quotes WHERE slug=? ORDER BY kind", (slug,))
        return row
    hits = search_items(conn, query, limit=5)
    if hits:
        return item_detail(conn, hits[0]["slug"])
    raise QueryError("没有找到物品：%s" % query)


def resolve_alias(conn: sqlite3.Connection, text: str) -> Dict[str, Any]:
    query = str(text or "").strip()
    if not query:
        raise QueryError("别名不能为空")
    like = "%" + query + "%"
    result: Dict[str, Any] = {"query": query, "hits": []}
    for row in conn.execute(
            "SELECT alias, slug, kind, source FROM item_aliases"
            " WHERE alias=? OR alias LIKE ? ORDER BY alias LIMIT 20",
            (query, like)):
        result["hits"].append({"type": "item_alias", **_dict(row)})
    for row in conn.execute(
            "SELECT nickname, name_en, priority, note FROM frame_nicknames"
            " WHERE nickname=? OR nickname LIKE ? ORDER BY priority DESC LIMIT 20",
            (query, like)):
        result["hits"].append({"type": "frame_nickname", **_dict(row)})
    for row in conn.execute(
            "SELECT name_en, alias_zh FROM frame_alias_zh"
            " WHERE name_en=? OR name_en LIKE ? OR alias_zh LIKE ? LIMIT 20",
            (query, like, like)):
        result["hits"].append({"type": "frame_alias", **_dict(row)})
    for row in conn.execute(
            "SELECT source, category, name_en, name_zh FROM zh_names"
            " WHERE name_en=? OR name_zh=? OR name_en LIKE ? OR name_zh LIKE ?"
            " LIMIT 20", (query, query, like, like)):
        result["hits"].append({"type": "zh_name", **_dict(row)})
    return result


def bounties(conn: sqlite3.Connection, location: str = "") -> Dict[str, Any]:
    key = str(location or "").strip()
    if key:
        rows = _all(conn, "SELECT * FROM bounty_locations"
                          " WHERE location_key=? OR label_zh=? OR label_zh LIKE ?",
                    (key, key, "%" + key + "%"))
    else:
        rows = _all(conn, "SELECT * FROM bounty_locations ORDER BY location_key")
    for row in rows:
        row["missions"] = [r["mission_zh"] for r in conn.execute(
            "SELECT mission_zh FROM bounty_location_missions"
            " WHERE location_key=? ORDER BY position", (row["location_key"],))]
        row["tiers"] = _all(
            conn, "SELECT tier, level_range, reward_text FROM bounty_tiers"
                  " WHERE location_key=? ORDER BY position", (row["location_key"],))
        row["rewards"] = _all(
            conn, "SELECT tier, position, reward FROM bounty_rewards"
                  " WHERE syndicate=? ORDER BY tier, position", (row["location_key"],))
    return {"locations": rows}


def riven_weapon(conn: sqlite3.Connection, text: str) -> Dict[str, Any]:
    query = str(text or "").strip()
    if not query:
        raise QueryError("武器名不能为空")
    like = "%" + query + "%"
    rows = _all(conn, """
        SELECT * FROM riven_weapons
         WHERE slug=? OR name_zh=? OR name_en=?
            OR slug LIKE ? OR name_zh LIKE ? OR name_en LIKE ?
         ORDER BY CASE WHEN slug=? OR name_zh=? OR name_en=? THEN 0 ELSE 1 END
         LIMIT 20""", (query, query, query, like, like, like,
                      query, query, query))
    if not rows:
        count = int(_scalar(conn, "SELECT COUNT(*) FROM riven_weapons") or 0)
        if count == 0:
            raise QueryError(
                "riven_weapons 为空：需在服务器运行 "
                "python db/build.py --online")
        raise QueryError("没有找到紫卡武器：%s" % query)
    for row in rows:
        row["market"] = _one(
            conn, "SELECT value, samples, metric_version, updated"
                  " FROM riven_market WHERE slug=?", (row["slug"],))
        row["attributes"] = _all(
            conn, "SELECT word, url_name, kind FROM riven_attribute_aliases"
                  " WHERE url_name IN (SELECT url_name FROM riven_attributes)"
                  " ORDER BY word LIMIT 100")
    return {"weapons": rows}


def rank(conn: sqlite3.Connection, kind: str,
         limit: int = 20) -> List[Dict[str, Any]]:
    kind = str(kind or "").lower()
    limit = max(1, min(int(limit), 200))
    if kind == "junk":
        return _all(conn, """
            SELECT slug, name_zh, ducats, label, price, orders
              FROM v_junk WHERE price IS NOT NULL AND price > 0
             ORDER BY price ASC LIMIT ?""", (limit,))
    if kind == "frames":
        return _all(conn, """
            SELECT i.slug, i.name_zh, i.name_en, q.price, q.orders
              FROM items i JOIN price_quotes q ON q.slug=i.slug
             WHERE q.kind='frame' AND q.price > 0
             ORDER BY q.price DESC LIMIT ?""", (limit,))
    if kind == "riven":
        return _all(conn, """
            SELECT slug, name_zh, name_en, group_name, disposition,
                   value, samples, metric_version, updated
              FROM v_riven_rank LIMIT ?""", (limit,))
    raise QueryError("rank 类型只能是 junk / frames / riven")


def rotation(conn: sqlite3.Connection,
             vendor: str = "") -> Dict[str, Any]:
    query = str(vendor or "").strip()
    if query:
        vendors = _all(conn, "SELECT * FROM rotation_vendors"
                            " WHERE vendor=? OR vendor LIKE ?",
                       (query, "%" + query + "%"))
    else:
        vendors = _all(conn, "SELECT * FROM rotation_vendors ORDER BY vendor")
    for row in vendors:
        row["items"] = _all(
            conn, "SELECT rotation, position, item, element, percent"
                  " FROM rotation_items WHERE vendor=? ORDER BY rotation, position",
            (row["vendor"],))
    return {"vendors": vendors}


def sources(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    return _all(conn, "SELECT name,path,kind,rows,fetched_at,imported_at,note"
                      " FROM sources ORDER BY name")


def _print_stats(result: Dict[str, Any]) -> None:
    print("数据库：%s" % result["db"])
    print("schema：%s  构建：%s" % (
        result["meta"].get("schema_version", "?"),
        result["meta"].get("built_at", "?")))
    print("总行数：%d" % result["total_rows"])
    print("\n关键表：")
    for name, count in result["key"].items():
        print("  %-22s %8d" % (name, count))
    print("\n数据源：%d 个" % len(result["sources"]))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="查询 qqwarframe-bot SQLite 数据库")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats")
    sub.add_parser("validate")
    sub.add_parser("sources")

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("item")
    p.add_argument("query")

    p = sub.add_parser("alias")
    p.add_argument("query")

    p = sub.add_parser("bounty")
    p.add_argument("location", nargs="?", default="")

    p = sub.add_parser("riven")
    p.add_argument("query")

    p = sub.add_parser("rank")
    p.add_argument("kind", choices=("junk", "frames", "riven"))
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("rotation")
    p.add_argument("vendor", nargs="?", default="")

    args = parser.parse_args(argv)
    try:
        conn = connect(args.db)
    except QueryError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        if args.command == "stats":
            result = stats(conn)
        elif args.command == "validate":
            result = validate(conn)
        elif args.command == "sources":
            result = {"sources": sources(conn)}
        elif args.command == "search":
            result = {"query": args.query,
                      "items": search_items(conn, args.query, args.limit)}
        elif args.command == "item":
            result = item_detail(conn, args.query)
        elif args.command == "alias":
            result = resolve_alias(conn, args.query)
        elif args.command == "bounty":
            result = bounties(conn, args.location)
        elif args.command == "riven":
            result = riven_weapon(conn, args.query)
        elif args.command == "rank":
            result = {"kind": args.kind,
                      "rows": rank(conn, args.kind, args.limit)}
        elif args.command == "rotation":
            result = rotation(conn, args.vendor)
        else:
            raise QueryError("未知命令")
    except (QueryError, sqlite3.Error) as exc:
        print("查询失败：%s" % exc, file=sys.stderr)
        return 1
    finally:
        conn.close()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not (isinstance(result, dict) and result.get("ok") is False) else 1

    if args.command == "stats":
        _print_stats(result)
    elif args.command == "validate":
        if result["ok"]:
            print("校验通过：integrity=%s" % result["integrity"])
        else:
            print("校验失败：")
            for error in result["errors"]:
                print("  - " + error)
        for warning in result["warnings"]:
            print("WARN " + warning)
    elif args.command == "sources":
        _print_table(result["sources"], ["name", "kind", "rows", "note"])
    elif args.command == "search":
        _print_table(result["items"],
                     ["slug", "name_zh", "name_en", "ducats", "mastery_rank"])
    elif args.command == "item":
        row = result
        print("%s  %s" % (row.get("name_zh"), row.get("name_en")))
        print("slug=%s ducats=%s MR=%s tradable=%s" % (
            row.get("slug"), row.get("ducats"), row.get("mastery_rank"),
            row.get("tradable")))
        if row.get("tags"):
            print("tags:", row["tags"])
        if row.get("aliases"):
            print("aliases:", row["aliases"])
        if row.get("price_quotes"):
            print("price_quotes:", row["price_quotes"])
    elif args.command == "alias":
        if not result["hits"]:
            print("（无匹配）")
        else:
            _print_table(result["hits"])
    elif args.command == "bounty":
        for location in result["locations"]:
            print("%s (%s) %s" % (location.get("label_zh"),
                                  location.get("location_key"),
                                  location.get("npc") or ""))
            print("  missions:", "、".join(location["missions"]))
            for tier in location["tiers"]:
                print("  %s %s %s" % (tier.get("tier"), tier.get("level_range") or "",
                                      tier.get("reward_text") or ""))
            print("  rewards:", len(location["rewards"]))
    elif args.command == "riven":
        for weapon in result["weapons"]:
            print("%s  %s / %s  %s  disp=%s" % (
                weapon.get("slug"), weapon.get("name_zh"), weapon.get("name_en"),
                weapon.get("group_name"), weapon.get("disposition")))
            print("  market:", weapon.get("market"))
    elif args.command == "rank":
        _print_table(result["rows"])
    elif args.command == "rotation":
        for vendor in result["vendors"]:
            print("%s  %s" % (vendor.get("vendor"), vendor.get("observed") or ""))
            for item in vendor["items"]:
                print("  %s #%s %s %s %s" % (
                    item.get("rotation"), item.get("position"), item.get("item"),
                    item.get("element") or "", item.get("percent") or ""))
    return 0 if not (isinstance(result, dict) and result.get("ok") is False) else 1


if __name__ == "__main__":
    sys.exit(main())
