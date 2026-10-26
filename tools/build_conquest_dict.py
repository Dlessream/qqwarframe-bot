#!/usr/bin/env python3
"""生成 bot/conquest_zh.json：科研（Archimedea）官方中文名与说明。

数据来自 oracle.browse.wf 的官方语言字典（与游戏内文案同源）：
    https://oracle.browse.wf/dicts/zh.json
    https://oracle.browse.wf/dicts/en.json

字典里的键是资源路径，例如
    /Lotus/Language/Conquest/MissionVariant_LabConquest_HostileSecurity   → 侵蚀感官
    /Lotus/Language/Conquest/Condition_VoidAberration                     → 吸血界影
    /Lotus/Language/Conquest/PersonalMod_AntiGuard                        → 超宏衰减
对应说明的键在末尾加 _Desc。

用法：
    python tools/build_conquest_dict.py                   # 联网下载并重建
    python tools/build_conquest_dict.py zh.json en.json   # 用本地已下载的字典
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "bot", "conquest_zh.json")
SOURCES = {
    "zh": "https://oracle.browse.wf/dicts/zh.json",
    "en": "https://oracle.browse.wf/dicts/en.json",
}
BASE = "/Lotus/Language/Conquest/"

# 任务类型（官方 worldState 的 Missions[].type，字典里没有，手工维护）
MISSION_TYPES = {
    "DualDefense": "镜像防御",
    "MirrorDefense": "镜像防御",
    "Artifact": "中断",
    "Disruption": "中断",
    "Exterminate": "歼灭",
    "Survival": "生存",
    "Defense": "防御",
    "StageDefense": "舞台防御",
    "Assassination": "刺杀",
    "TankAssassination": "坦克刺杀",
    "MobileDefense": "移动防御",
    "Sabotage": "破坏",
    "Spy": "间谍",
    "Rescue": "救援",
    "Capture": "捕获",
    "Excavation": "挖掘",
    "Interception": "拦截",
    "Hijack": "劫持",
    "Defection": "变节",
    "Alchemy": "元素转换",
    "HellScrub": "地狱净化",
    "LegacyteHarvest": "传承种捕获",
    "VoidFlood": "虚空洪流",
}

LABELS = {"LabConquest": "深层科研", "HexConquest": "时光科研"}

# 官方文案里少数条目用 |val| 占位（数值随版本变化），这里按当前版本补上
VAL_TEXT = {
    "TimeDilation": "50",
    "ShieldDelay": "500",
}

MARKUP_RE = re.compile(r"<[^>]+>")


def download(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "qqwarframe-bot"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def clean(text: str) -> str:
    """去掉 <DT_POISON_COLOR> 这类富文本标记，压平多余空格。"""
    value = MARKUP_RE.sub("", str(text or ""))
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def load(path: str) -> dict:
    raw = io.open(path, encoding="utf-8").read()
    # 允许文件尾部混入其它内容（例如手工拼接时的换行）
    return json.JSONDecoder().raw_decode(raw.lstrip())[0]


def build(zh: dict, en: dict) -> dict:
    result = {
        "source": SOURCES["zh"],
        "labels": dict(LABELS),
        "variants": {kind: {} for kind in LABELS},
        "risks": {},
        "personal": {},
        "missionTypes": dict(MISSION_TYPES),
    }
    for key, value in zh.items():
        if not key.startswith(BASE):
            continue
        tail = key[len(BASE):]
        if "_Desc" in tail:
            continue
        name = clean(value)
        if not name:
            continue
        entry = {"name": name, "desc": clean(zh.get(key + "_Desc", ""))}
        for prefix, value in VAL_TEXT.items():
            if tail.endswith(prefix):
                entry["desc"] = entry["desc"].replace("|val|", value)
                break
        if tail.startswith("MissionVariant_"):
            rest = tail[len("MissionVariant_"):]
            for kind in LABELS:
                if rest.startswith(kind + "_"):
                    result["variants"][kind][rest[len(kind) + 1:]] = entry
                    break
        elif tail.startswith("Condition_"):
            result["risks"][tail[len("Condition_"):]] = entry
        elif tail.startswith("PersonalMod_"):
            result["personal"][tail[len("PersonalMod_"):]] = entry

    index = {}
    for kind, bucket in result["variants"].items():
        for key, entry in bucket.items():
            _index_english(index, en, "MissionVariant_%s_%s" % (kind, key), entry)
    for key, entry in result["risks"].items():
        _index_english(index, en, "Condition_" + key, entry)
    for key, entry in result["personal"].items():
        _index_english(index, en, "PersonalMod_" + key, entry)
    result["englishIndex"] = index
    return result


def _index_english(index: dict, en: dict, tail: str, entry: dict) -> None:
    name_en = clean(en.get(BASE + tail, "")) if en else ""
    if name_en:
        index[name_en.lower()] = entry["name"]


def main(argv) -> int:
    if len(argv) >= 3:
        zh = load(argv[1])
        en = load(argv[2])
    else:
        zh = download(SOURCES["zh"])
        en = download(SOURCES["en"])
    data = build(zh, en)
    with io.open(TARGET, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    print("写入 %s" % TARGET)
    print("偏差 深层 %d / 时光 %d · 风险 %d · 个人修正 %d"
          % (len(data["variants"]["LabConquest"]),
             len(data["variants"]["HexConquest"]),
             len(data["risks"]), len(data["personal"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
