"""生成 data/node_names_zh.json：官方中文客户端的星图节点译名。

数据源是 Digital Extremes 的公开导出（与游戏内简体中文客户端同源）：

* ``https://content.warframe.com/PublicExport/index_en.txt.lzma``
* ``https://content.warframe.com/PublicExport/index_zh.txt.lzma``

索引里给的是 ``ExportRegions_<lang>.json!00_<hash>``，中英文两份按
``uniqueName`` 对齐后，就能拿到「英文节点名 → 游戏内中文名」的官方映射。

!!! 重要 !!!
官方中文客户端里**大部分星图节点仍然显示英文原名**（Tessera、Venera、
Aphrodite…），只有一部分被真正汉化（Apollodorus → 阿波罗多罗斯撞击坑）。
因此这里只收录「中文名与英文名不同」的条目：卡片显示什么，就和玩家在
游戏里看到的完全一致，不自行发明译名。

航道星舰节点不在 ExportRegions 里（官方导出没有比邻星域），那部分维护在
``bot/terms.py`` 的 ``STORM_NODES``。

用法::

    python tools/build_node_names.py            # 重新生成并写盘
    python tools/build_node_names.py --check    # 只比对，不写盘（CI 用）
"""

from __future__ import annotations

import argparse
import json
import lzma
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "node_names_zh.json")

BASE = "https://content.warframe.com/PublicExport/"
INDEX_URL = BASE + "index_%s.txt.lzma"
MANIFEST_URL = BASE + "Manifest/%s"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "qqwarframe-bot"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _manifest_name(language: str) -> str:
    """从索引里取出 ExportRegions_<lang>.json 的带哈希文件名。"""
    raw = lzma.decompress(_fetch(INDEX_URL % language)).decode("utf-8")
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("ExportRegions_%s.json!" % language):
            return line
    raise SystemExit("索引里没有 ExportRegions_%s.json" % language)


def _regions(language: str) -> dict:
    name = _manifest_name(language)
    payload = json.loads(_fetch(MANIFEST_URL % name).decode("utf-8"))
    return {row["uniqueName"]: row for row in payload.get("ExportRegions", [])}


def build() -> dict:
    english = _regions("en")
    chinese = _regions("zh")
    mapping = {}
    for unique, row in english.items():
        name_en = str(row.get("name") or "").strip()
        name_zh = str((chinese.get(unique) or {}).get("name") or "").strip()
        if not name_en or not name_zh or name_en == name_zh:
            continue
        mapping[name_en] = name_zh
    return dict(sorted(mapping.items(), key=lambda item: item[0].lower()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只比对现有文件，不写盘")
    args = parser.parse_args(argv)

    mapping = build()
    payload = {
        "_comment": "官方简体中文客户端星图节点译名（仅收录已汉化的节点）",
        "_source": "content.warframe.com PublicExport ExportRegions_{en,zh}.json",
        "_count": len(mapping),
        "nodes": mapping,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        if not os.path.isfile(OUT_PATH):
            print("缺少 %s" % OUT_PATH)
            return 1
        with open(OUT_PATH, encoding="utf-8") as handle:
            current = json.load(handle).get("nodes") or {}
        if current == mapping:
            print("一致：%d 条" % len(mapping))
            return 0
        added = sorted(set(mapping) - set(current))
        removed = sorted(set(current) - set(mapping))
        print("不一致：新增 %s / 移除 %s" % (added[:10], removed[:10]))
        return 1

    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print("写入 %s：%d 条" % (OUT_PATH, len(mapping)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
