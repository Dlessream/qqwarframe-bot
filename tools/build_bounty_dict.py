#!/usr/bin/env python3
"""生成 data/bounty_objectives.json：官方 jobType → 赏金目标名。

来源：WFCD warframe-worldstate-data 的 data/zh/languages.json
（键就是 /Lotus/Types/Gameplay/.../Jobs/xxx 的资源路径，值是目标名）。
该文件里这些键只给了英文，中文按游戏内文案补齐（见 ZN）。

用法：python tools/build_bounty_dict.py [languages.json]
不给参数时只用本脚本里内置的表（离线也能跑）。
"""

from __future__ import annotations

import json
import os
import sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "bounty_objectives.json")

# 官方英文目标名（与 languages.json 里的值一致）
EN = {
    "eidolon/jobs/assassinatebountyass": "Assassinate the Commander",
    "eidolon/jobs/assassinatebountycap": "Capture the New Grineer Commander",
    "eidolon/jobs/attritionbountycap": "Capture Their Leader",
    "eidolon/jobs/attritionbountyext": "Cull the Enemy",
    "eidolon/jobs/attritionbountylib": "Weaken the Grineer Foothold",
    "eidolon/jobs/attritionbountysab": "Sabotage the Enemy Supply Lines",
    "eidolon/jobs/capturebountycapone": "Capture the Grineer Commander",
    "eidolon/jobs/capturebountycaptwo": "Spy Catcher",
    "eidolon/jobs/events/ghoulalertbountyass": "Eliminate A Ghoul Alpha",
    "eidolon/jobs/events/ghoulalertbountyext": "Wipe out Ghoul Burial Grounds",
    "eidolon/jobs/events/ghoulalertbountyhunt": "Purge a Massive Ghoul Burial Site",
    "eidolon/jobs/events/ghoulalertbountyres": "Rescue the Ghoul Defector",
    "eidolon/jobs/narmer/assassinatebountyass": "Rise and Fall (Narmer)",
    "eidolon/jobs/narmer/attritionbountyext": "For the Unum (Narmer)",
    "eidolon/jobs/narmer/attritionbountylib": "Bring Them Home (Narmer)",
    "eidolon/jobs/reclamationbountycache": "Find the Hidden Artifact",
    "eidolon/jobs/reclamationbountycap": "Capture the Grineer Agent",
    "eidolon/jobs/reclamationbountytheft": "Reclaim the Stolen Artifact",
    "eidolon/jobs/rescuebountyresc": "Search and Rescue",
    "eidolon/jobs/sabotagebountysab": "Sabotage Bounty",
    "infestedmicroplanet/jobs/deimosareadefensebounty": "Reclaim What's Ours",
    "infestedmicroplanet/jobs/deimosassassinatebounty": "Cleanse the Land",
    "infestedmicroplanet/jobs/deimoscrpsurvivorbounty": "For Science!",
    "infestedmicroplanet/jobs/deimosendlessexcavatebounty": "Strip Mining (Endless)",
    "infestedmicroplanet/jobs/deimosendlesspurifybounty": "Anomaly Retrieval (Endless)",
    "infestedmicroplanet/jobs/deimosexcavatebounty": "Core Samples",
    "infestedmicroplanet/jobs/deimosgrnsurvivorbounty": "Brute Force",
    "infestedmicroplanet/jobs/deimoskeypiecesbounty": "Salvage",
    "infestedmicroplanet/jobs/deimospurifybounty": "Anomaly Retrieval",
    "venus/jobs/narmer/narmervenusculljobassassinate": "Master's Voice (Narmer)",
    "venus/jobs/narmer/narmervenusculljobexterminate": "Crush the Cult (Narmer)",
    "venus/jobs/narmer/narmervenustheftjobexcavation": "Digging for Narmer (Narmer)",
    "venus/jobs/venusartifactjobambush": "Courier Ambush",
    "venus/jobs/venusartifactjobexcavation": "Archaeology",
    "venus/jobs/venusartifactjobrecovery": "Blood Relics",
    "venus/jobs/venuschaosjobassassinate": "Scorched Earth",
    "venus/jobs/venuschaosjobexcavation": "Bury Them",
    "venus/jobs/venusculljobassassinate": "Network Collapse",
    "venus/jobs/venusculljobexterminate": "Hunter-Killer",
    "venus/jobs/venusculljobresource": "Distract and Divert",
    "venus/jobs/venushelpingjobcaches": "Seems Legit",
    "venus/jobs/venushelpingjobresource": "Dirt Unit",
    "venus/jobs/venushelpingjobspy": "Dog Boards",
    "venus/jobs/venusinteljobrecovery": "Proof of Life",
    "venus/jobs/venusinteljobresource": "Operational Intelligence",
    "venus/jobs/venusinteljobspy": "Financial Liberation",
    "venus/jobs/venuspreservationjobdefense": "Picket Duty",
    "venus/jobs/venuspreservationjobrecovery": "Protect the Innocent",
    "venus/jobs/venuspreservationjobresource": "Trash Their Traps",
    "venus/jobs/venusspyjobspy": "Agent Down",
    "venus/jobs/venustheftjobambush": "Software Subterfuge",
    "venus/jobs/venustheftjobexcavation": "Resource Theft",
    "venus/jobs/venustheftjobresource": "Tax the Taxmen",
    "venus/jobs/venuswetworkjobassassinate": "Served Cold",
    "venus/jobs/venuswetworkjobspy": "Falling Star",
}

# 中文按游戏内文案（与 terms.BOUNTY_PHRASES 保持一致的写法）
ZH = {
    "eidolon/jobs/assassinatebountyass": "刺杀指挥官",
    "eidolon/jobs/assassinatebountycap": "捕获新克隆尼指挥官",
    "eidolon/jobs/attritionbountycap": "捕获敌方领袖",
    "eidolon/jobs/attritionbountyext": "宰杀敌人",
    "eidolon/jobs/attritionbountylib": "削弱克隆尼的据点",
    "eidolon/jobs/attritionbountysab": "破坏克隆尼的补给线",
    "eidolon/jobs/capturebountycapone": "捕获克隆尼指挥官",
    "eidolon/jobs/capturebountycaptwo": "间谍捕手",
    "eidolon/jobs/events/ghoulalertbountyass": "消灭食尸鬼首领",
    "eidolon/jobs/events/ghoulalertbountyext": "清剿食尸鬼墓地",
    "eidolon/jobs/events/ghoulalertbountyhunt": "净化大型食尸鬼葬地",
    "eidolon/jobs/events/ghoulalertbountyres": "营救食尸鬼叛逃者",
    "eidolon/jobs/narmer/assassinatebountyass": "兴衰（合一众）",
    "eidolon/jobs/narmer/attritionbountyext": "为了乌努母神（合一众）",
    "eidolon/jobs/narmer/attritionbountylib": "带他们回家（合一众）",
    "eidolon/jobs/reclamationbountycache": "寻找隐藏神器",
    "eidolon/jobs/reclamationbountycap": "捕获克隆尼特工",
    "eidolon/jobs/reclamationbountytheft": "取回被盗的器物",
    "eidolon/jobs/rescuebountyresc": "搜索与救援",
    "eidolon/jobs/sabotagebountysab": "破坏原型机",
    "infestedmicroplanet/jobs/deimosareadefensebounty": "夺回属于我们的东西",
    "infestedmicroplanet/jobs/deimosassassinatebounty": "净化土地",
    "infestedmicroplanet/jobs/deimoscrpsurvivorbounty": "为了科学！",
    "infestedmicroplanet/jobs/deimosendlessexcavatebounty": "露天开采（无尽）",
    "infestedmicroplanet/jobs/deimosendlesspurifybounty": "异常回收（无尽）",
    "infestedmicroplanet/jobs/deimosexcavatebounty": "核心样本",
    "infestedmicroplanet/jobs/deimosgrnsurvivorbounty": "暴力破解",
    "infestedmicroplanet/jobs/deimoskeypiecesbounty": "回收",
    "infestedmicroplanet/jobs/deimospurifybounty": "异常回收",
    "venus/jobs/narmer/narmervenusculljobassassinate": "主人的声音（合一众）",
    "venus/jobs/narmer/narmervenusculljobexterminate": "粉碎教团（合一众）",
    "venus/jobs/narmer/narmervenustheftjobexcavation": "为合一众挖掘（合一众）",
    "venus/jobs/venusartifactjobambush": "信使伏击",
    "venus/jobs/venusartifactjobexcavation": "考古",
    "venus/jobs/venusartifactjobrecovery": "血之遗物",
    "venus/jobs/venuschaosjobassassinate": "焦土",
    "venus/jobs/venuschaosjobexcavation": "埋葬他们",
    "venus/jobs/venusculljobassassinate": "网络崩溃",
    "venus/jobs/venusculljobexterminate": "猎杀者",
    "venus/jobs/venusculljobresource": "声东击西",
    "venus/jobs/venushelpingjobcaches": "看起来很靠谱",
    "venus/jobs/venushelpingjobresource": "脏活部门",
    "venus/jobs/venushelpingjobspy": "滑板情报",
    "venus/jobs/venusinteljobrecovery": "生存证明",
    "venus/jobs/venusinteljobresource": "作战情报",
    "venus/jobs/venusinteljobspy": "财务解放",
    "venus/jobs/venuspreservationjobdefense": "哨戒任务",
    "venus/jobs/venuspreservationjobrecovery": "保护无辜者",
    "venus/jobs/venuspreservationjobresource": "破坏他们的陷阱",
    "venus/jobs/venusspyjobspy": "特工倒下",
    "venus/jobs/venustheftjobambush": "软件诡计",
    "venus/jobs/venustheftjobexcavation": "窃取资源",
    "venus/jobs/venustheftjobresource": "向税务官征税",
    "venus/jobs/venuswetworkjobassassinate": "冷盘复仇",
    "venus/jobs/venuswetworkjobspy": "陨落之星",
}


def _key(path: str) -> str:
    """/Lotus/Types/Gameplay/Eidolon/Jobs/Xxx → eidolon/jobs/xxx"""
    text = str(path or "").strip().lower()
    marker = "/types/gameplay/"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.lstrip("/")


def merge_language_file(path: str) -> int:
    """把 languages.json 里更全的英文名并进来（找不到就算了）。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return 0
    added = 0
    for key, value in raw.items():
        if "/jobs/" not in key.lower() or not isinstance(value, dict):
            continue
        name = str(value.get("value") or "").strip()
        short = _key(key)
        if name and short and short not in EN:
            EN[short] = name
            added += 1
    return added


def main(argv) -> int:
    if len(argv) > 1:
        count = merge_language_file(argv[1])
        print("从语言文件补充 %d 条英文目标名" % count)
    jobs = {}
    for short, name in sorted(EN.items()):
        jobs[short] = {"en": name, "zh": ZH.get(short, name)}
    missing = [key for key, value in jobs.items() if value["zh"] == value["en"]]
    payload = {
        "note": "官方 jobType 路径（小写，去掉 /Lotus/Types/Gameplay/）"
                " → 赏金目标名；英文取自 WFCD languages.json，中文按游戏内文案。",
        "jobs": jobs,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print("写入 %s：%d 条，缺中文 %d 条" % (OUT, len(jobs), len(missing)))
    if missing:
        print("缺中文：" + "、".join(missing[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
