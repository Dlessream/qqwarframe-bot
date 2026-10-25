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
    "Strata Relay": "斯特拉塔中继站", "Orcus Relay": "奥库斯中继站",
    "Larunda Relay": "拉伦达中继站", "Vesper Relay": "维斯珀中继站",
    "Saturn Relay": "土星中继站", "Pluto Relay": "冥王星中继站",
}

FACTIONS = {
    "Grineer": "克隆尼", "Corpus": "科普斯", "Infested": "感染者",
    "Corrupted": "堕落者", "Sentient": "灵煞", "Tenno": "天诺",
    "Orokin": "奥罗金", "Narmer": "纳尔迈", "Murmur": "低语者",
    "The Murmur": "低语者", "Man in the Wall": "墙中低语",
    "Scaldra": "炽蛇军", "Techrot": "科魔者", "Crossfire": "交叉火力",
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
    "Hive": "蜂巢", "Infested Salvage": "感染者回收", "Pursuit": "追击",
    "Rush": "突袭", "Assault": "强袭", "Orphix": "奥菲克斯",
    "Skirmish": "遭遇战", "Volatile": "挥发",
    "Void Sabotage": "虚空破坏", "Void Exterminate": "虚空歼灭",
    "Void Survival": "虚空生存", "Void Defense": "虚空防御",
    "Void Interception": "虚空拦截", "Void Mobile Defense": "虚空移动防御",
    "Railjack": "航道星舰", "Empyrean": "九重天",
}

SYNDICATES = {
    "Ostrons": "奥斯唐人", "Solaris United": "索拉里斯联盟",
    "Entrati": "英择谛", "Cavia": "卡维亚", "The Hex": "六人组",
    "The Holdfasts": "坚守者", "Necraloid": "死灵锁",
    "Quills": "夜羽", "Vox Solaris": "索拉里斯之声",
    "Vent Kids": "通风小子", "Kahl's Garrison": "卡尔驻军",
}

RESEARCH_TYPES = {
    "C T_ L A B": "深层科研",
    "C T_ H E X": "时光科研",
}

RESEARCH_TERMS = {
    "Sealed Armor": "密封装甲", "Necramech Influx": "死灵机甲涌入",
    "Unified Purpose": "统一目标", "Pile-On": "堆积",
    "Miasmite Mash": "瘴气碾压", "Reinforcements": "增援",
    "No Pets": "禁止宠物", "Dull Blades": "钝刃",
    "Dropped Guard": "卸防", "Secondary Wounds": "副武器创伤",
    "Range Debuff": "范围削弱", "Anemic Shields": "贫血护盾",
}

RESOURCES = {
    "Endo": "内融核心", "Kuva": "赤毒", "Credits": "星币",
    "Platinum": "白金", "Oxium": "氧金属", "Cryotic": "低温尘埃",
    "Argon Crystal": "氩结晶", "Orokin Cell": "奥罗金电池",
    "Neurodes": "神经节点", "Neural Sensors": "神经传感器",
    "Gallium": "镓", "Morphics": "非晶态合金", "Polymer Bundle": "聚合纤维",
    "Circuits": "电路", "Alloy Plate": "合金板", "Ferrite": "铁氧体",
    "Rubedo": "红化结晶", "Plastids": "生物质", "Nano Spores": "纳米孢子",
}

BOUNTY_PHRASES = {
    "Capture the Grineer Commander": "捕获克隆尼指挥官",
    "Sabotage the Enemy Supply Lines": "破坏敌人补给线",
    "Cull the Enemy": "清剿敌人",
    "Spy Catcher": "间谍捕捉",
    "Find the Hidden Artifact": "寻找隐藏神器",
    "Sabotage Bounty": "破坏赏金",
    "Weaken the Grineer Foothold": "削弱克隆尼据点",
    "Reclaim the Stolen Artifact": "夺回被盗神器",
    "Cleanse the Land": "净化土地",
}

BOUNTY_WORDS = {
    "Capture": "捕获", "Sabotage": "破坏", "Cull": "清剿",
    "Spy": "间谍", "Find": "寻找", "Hidden": "隐藏",
    "Artifact": "神器", "Enemy": "敌人", "Supply Lines": "补给线",
    "Commander": "指挥官", "Foothold": "据点", "Stolen": "被盗",
    "Reclaim": "夺回", "Cleanse": "净化", "Land": "土地",
    "Bounty": "赏金", "Defend": "防守", "Rescue": "救援",
    "Destroy": "摧毁", "Recover": "回收", "Hunt": "狩猎",
    "Assassinate": "刺杀", "Escort": "护送", "Investigate": "调查",
    "Eliminate": "消灭", "Retrieve": "取回", "the": "",
}

BOSSES = {
    "Lech Kril": "莱希·克里尔", "Sargas Ruk": "萨加斯·鲁克",
    "Vay Hek": "维·赫克", "Tyl Regor": "泰尔·雷格",
    "Kela De Thaym": "凯拉·德·塞姆", "Alad V": "阿拉德五世",
    "Ambulas": "安布拉", "Phorid": "恐惧", "Lephantis": "勒凡提斯",
    "Jordas Golem": "约尔达斯魔像", "Raptor": "猛禽", "Hyena": "鬣狗",
    "Archon Amar": "执刑官·阿玛尔", "Archon Boreal": "执刑官·玻瑞尔",
    "Archon Nira": "执刑官·尼拉",
}


def localize_mission(text: str) -> str:
    return MISSIONS.get(text, text)


def localize_faction(text: str) -> str:
    return FACTIONS.get(text, text)


def localize_boss(text: str) -> str:
    return BOSSES.get(text, text)


def localize_syndicate(text: str) -> str:
    return SYNDICATES.get(text, text)


def localize_research(text: str) -> str:
    return RESEARCH_TYPES.get(text, text)


def localize_bounty(text: str) -> str:
    if not text:
        return ""
    if text in BOUNTY_PHRASES:
        return BOUNTY_PHRASES[text]
    result = text
    for english, chinese in sorted(BOUNTY_WORDS.items(),
                                   key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    return re.sub(r"\s+", " ", result).strip()


def localize_duration(text: str) -> str:
    """1h 35m 0s → 1 小时 35 分。"""
    if not text:
        return ""
    match = re.match(r"\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\s*$",
                     str(text), re.I)
    if not match or not any(match.groups()):
        return str(text)
    days, hours, minutes, seconds = (int(value or 0) for value in match.groups())
    parts = []
    if days:
        parts.append("%d 天" % days)
    if hours:
        parts.append("%d 小时" % hours)
    if minutes:
        parts.append("%d 分" % minutes)
    if seconds and not days and not hours:
        parts.append("%d 秒" % seconds)
    return " ".join(parts) or str(text)


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
    for english, chinese in sorted(FACTIONS.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(SYNDICATES.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(RESEARCH_TERMS.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(RESOURCES.items(), key=lambda item: -len(item[0])):
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
