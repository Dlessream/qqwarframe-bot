"""世界状态里的常见英文名词本地化（warframestat 不返回中文）。"""

from __future__ import annotations

import re

PLANETS = {
    "Earth": "地球", "Venus": "金星", "Mercury": "水星", "Mars": "火星",
    "Jupiter": "木星", "Saturn": "土星", "Uranus": "天王星", "Neptune": "海王星",
    "Pluto": "冥王星", "Sedna": "赛德娜", "Eris": "阋神星", "Ceres": "谷神星",
    "Europa": "欧罗巴", "Phobos": "火卫一", "Deimos": "火卫二", "Lua": "月球",
    "Void": "虚空", "Kuva Fortress": "赤毒要塞", "Earth Proxima": "地球比邻星",
    "Venus Proxima": "金星比邻星", "Neptune Proxima": "海王星比邻星",
}

RELICS = {
    "Strata Relay": "Strata 中继站", "Orcus Relay": "Orcus 中继站",
    "Larunda Relay": "Larunda 中继站", "Vesper Relay": "Vesper 中继站",
    "Saturn Relay": "土星中继站", "Pluto Relay": "冥王星中继站",
}

FACTIONS = {
    "Grineer": "Grineer", "Corpus": "Corpus", "Infested": "Infested",
    "Corrupted": "Corrupted", "Sentient": "Sentient", "Tenno": "Tenno",
}

MISSIONS = {
    "Extermination": "歼灭", "Survival": "生存", "Defense": "防御",
    "Mobile Defense": "移动防御", "Sabotage": "破坏", "Spy": "间谍",
    "Capture": "捕获", "Rescue": "救援", "Hijack": "劫持",
    "Interception": "拦截", "Assassination": "刺杀", "Excavation": "挖掘",
    "Defection": "叛逃", "Disruption": "中断", "Void Flood": "虚空洪流",
    "Void Cascade": "虚空瀑流", "Void Armageddon": "虚空末日",
    "Alchemy": "炼金", "Mirror Defense": "镜像防御", "Ascension": "升天",
    "Free Roam": "开放地形", "Bounty": "赏金", "Conclave": "武形秘仪",
    "Sanctuary Onslaught": "圣殿突袭", "Elite Sanctuary Onslaught": "精英圣殿突袭",
}

BOSSES = {
    "Lech Kril": "莱希·克里尔", "Sargas Ruk": "萨加斯·鲁克",
    "Vay Hek": "维·赫克", "Tyl Regor": "泰尔·雷格",
    "Kela De Thaym": "凯拉·德·塞姆", "Alad V": "阿拉德五世",
    "Ambulas": "安布拉", "Phorid": "恐惧", "Lephantis": "勒凡提斯",
    "Jordas Golem": "约尔达斯魔像", "Raptor": "猛禽", "Hyena": "鬣狗",
}


def localize_mission(text: str) -> str:
    return MISSIONS.get(text, text)


def localize_faction(text: str) -> str:
    return FACTIONS.get(text, text)


def localize_boss(text: str) -> str:
    return BOSSES.get(text, text)


def localize_location(text: str) -> str:
    """Strata Relay (Earth) → Strata 中继站（地球）。"""
    if not text:
        return ""
    for english, chinese in RELICS.items():
        if english.lower() in text.lower():
            text = re.sub(re.escape(english), chinese, text, flags=re.I)
            break
    text = text.replace("(", "（").replace(")", "）")

    def replace_planet(match):
        name = match.group(1)
        return "（%s）" % PLANETS.get(name, name)

    text = re.sub(r"（([A-Za-z ]+)）", replace_planet, text)
    return re.sub(r"\s+（", "（", text).strip()


def localize_text(text: str) -> str:
    """把句子里出现的任务类型/星球/中继站替换成中文。"""
    if not text:
        return ""
    result = text
    for english, chinese in sorted(MISSIONS.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(PLANETS.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(RELICS.items(), key=lambda item: -len(item[0])):
        result = re.sub(re.escape(english), chinese, result, flags=re.I)
    return result


def localize_node(text: str) -> str:
    """Vallis (Mars) → Vallis（火星）。"""
    if not text:
        return ""
    text = text.replace("(", "（").replace(")", "）")

    def replace_planet(match):
        name = match.group(1)
        return "（%s）" % PLANETS.get(name, name)

    text = re.sub(r"（([A-Za-z ]+)）", replace_planet, text)
    return re.sub(r"\s+（", "（", text).strip()
