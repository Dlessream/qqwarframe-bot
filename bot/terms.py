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
    "Zariman": "扎里曼", "Veil": "虚空之纱", "Höllvania": "霍瓦尼亚",
}

RELICS = {
    "Strata Relay": "斯特拉塔中继站", "Orcus Relay": "奥库斯中继站",
    "Larunda Relay": "拉伦达中继站", "Vesper Relay": "维斯珀中继站",
    "Saturn Relay": "土星中继站", "Pluto Relay": "冥王星中继站",
}

FACTIONS = {
    "Grineer": "克隆尼", "Corpus": "科普斯", "Infested": "感染者",
    "Infestation": "感染者", "The Infested": "感染者",
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
    "Alchemy": "元素转换", "Mirror Defense": "镜像防御", "Ascension": "升天",
    "Free Roam": "开放地形", "Bounty": "赏金", "Conclave": "武形秘仪",
    "Sanctuary Onslaught": "圣殿突袭", "Elite Sanctuary Onslaught": "精英圣殿突袭",
    "Hive": "蜂巢", "Infested Salvage": "感染者回收", "Pursuit": "追击",
    "Rush": "突袭", "Assault": "强袭", "Orphix": "奥菲克斯",
    "Skirmish": "遭遇战", "Volatile": "挥发",
    "Void Sabotage": "虚空破坏", "Void Exterminate": "虚空歼灭",
    "Void Survival": "虚空生存", "Void Defense": "虚空防御",
    "Void Interception": "虚空拦截", "Void Mobile Defense": "虚空移动防御",
    "Railjack": "航道星舰", "Empyrean": "九重天",
    "Exterminate": "歼灭", "Assassinate": "刺杀",
    "Artifact": "中断", "Dual Defense": "镜像防御",
    "Legacyte Harvest": "传承种捕获", "Hell-Scrub": "地狱净化",
    "Stage Defense": "舞台防御", "Tank Assassination": "坦克刺杀",
    "Survivor": "生存",
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
    "Bold Venture": "大胆冒险", "Alluring Arcocanids": "诱人弧蛛",
    "Fortified Foes": "强化敌人", "Postmortal Surges": "死后涌动",
    "Entanglement": "纠缠", "Commanding Culverins": "指挥重炮",
    # 深层 / 时光科研的偏差与风险（官方 weekly 数据）
    "Hostile Security": "敌对安保", "Sticky Fingers": "妙手空空",
    "Tank Strong Armor": "重装强化", "Disruptive Sounds": "干扰音波",
    "Techrot Conjunction": "科魔者汇合", "Void Aberration": "虚空畸变",
    "Quicksand": "流沙", "Draining Residuals": "流失残留物",
    "Explosive Crawlers": "爆炸爬行者", "Anti Material Weapons": "反器材武器",
    "Magnetic Hounds": "磁性猎犬", "Accelerated Enemies": "加速敌人",
    "Murmur Incursion": "低语者入侵", "Voidburst": "虚空爆发",
    "Faction Swarm Scaldra": "炽蛇群袭", "Heavy Warfare": "重装战争",
    "Anti Guard": "反护卫", "Decaying Flesh": "腐朽血肉",
    "Time Dilation": "时间膨胀", "Starvation": "饥饿",
    "Over Sensitive": "过度敏感", "Void Energy Overload": "虚空能量超载",
    "Combo Count Chance": "连击计数几率", "Ability Lockout": "技能封锁",
}

RESOURCES = {
    "Endo": "内融核心", "Kuva": "赤毒", "Credits": "星币",
    "Platinum": "白金", "Oxium": "氧金属", "Cryotic": "低温尘埃",
    "Argon Crystal": "氩结晶", "Orokin Cell": "奥罗金电池",
    "Neurodes": "神经节点", "Neural Sensors": "神经传感器",
    "Gallium": "镓", "Morphics": "非晶态合金", "Polymer Bundle": "聚合纤维",
    "Circuits": "电路", "Alloy Plate": "合金板", "Ferrite": "铁氧体",
    "Rubedo": "红化结晶", "Plastids": "生物质", "Nano Spores": "纳米孢子",
    "Cache": "缓存", "Necramech": "死灵机甲",
}

RELIC_TIERS = {
    "Lith": "古纪", "Meso": "前纪", "Neo": "中纪",
    "Axi": "后纪", "Requiem": "安魂", "Omnia": "全能",
}

STORM_REGIONS = {
    "Earth": "地球比邻星域", "Venus": "金星比邻星域",
    "Saturn": "土星比邻星域", "Neptune": "海王星比邻星域",
    "Pluto": "冥王星比邻星域", "Veil": "面纱比邻星域",
}

STORM_NODES = {
    "Bendar Cluster": "本达尔星团",
    "Beacon Shield Ring": "卫标星环",
    "Enkidu Ice Drifts": "初裔冰渍区",
    "Vand Cluster": "水域星团",
    "Profit Margin": "利益外缘",
    "H-2 Cloud": "H-2 星云",
    "Nu-gua Mines": "女娲之矿",
    "Ogal Cluster": "奥加尔星团",
    "Mordo Cluster": "魔多星团",
    "Orvin-Haarc": "欧文－哈克",
    "Seven Sirens": "七魅之息",
    "R-9 Cloud": "R-9 星云",
}

STORM_MISSIONS = {
    "Skirmish": "前哨战", "Volatile": "爆发",
    "Spy": "间谍", "Survival": "生存",
    "Extermination": "歼灭", "Defense": "防御",
    "Excavation": "挖掘", "Mobile Defense": "移动防御",
}

STORM_ENEMIES = {"Grineer": "G系", "Corpus": "C系"}


def localize_storm_node(text: str) -> str:
    raw = str(text or "").strip()
    match = re.match(r"^(.*?)\s*\(([^()]+)\)$", raw)
    if not match:
        return STORM_NODES.get(raw, localize_node(raw))
    name, region = match.group(1).strip(), match.group(2).strip()
    localized_name = STORM_NODES.get(name, name)
    localized_region = STORM_REGIONS.get(region, localize_location(region))
    return "%s - %s" % (localized_region, localized_name)


def localize_storm_mission(text: str) -> str:
    return STORM_MISSIONS.get(str(text or ""), localize_mission(str(text or "")))


def localize_storm_enemy(text: str) -> str:
    return STORM_ENEMIES.get(str(text or ""), localize_faction(str(text or "")))

NEWS_PHRASES = {
    "Join the official Warframe Discord server": "加入 Warframe 官方 Discord 服务器",
    "Check out the official Warframe Wiki": "查看 Warframe 官方 Wiki",
    "Visit the official Warframe Forums!": "访问 Warframe 官方论坛！",
}

EVENT_PHRASES = {
    "Hallowed Nightmares Tactical Alert": "万圣节梦魇战术警报",
    "Nights of Naberus": "纳贝鲁斯之夜",
    "Road To Tau Event Name": "通往 Tau 之路",
}

REWARD_TERMS = {
    "Pressure Point": "压迫点", "Energy Inversion": "能量反转",
    "Vigilante Armaments": "警戒者军备", "Vazarin Lens": "瓦扎林棱镜",
    "Vigilante Vigor": "警戒者活力", "Zenurik Lens": "泽努里棱镜",
    "Breath Of The Eidolon": "夜灵之息", "Boreal's Anguish": "玻瑞尔之痛",
    "Isoplast": "等渗聚合物", "Thermal Sludge": "热淤泥",
    "Unairu Lens": "乌奈鲁棱镜", "Ayatan Amber Star": "阿耶檀琥珀星",
    "Residual Boils": "残留沸泡",
    "Necramech Redirection": "死灵机甲偏转",
    "Necramech Blitz": "死灵机甲闪击",
    "Necramech Fury": "死灵机甲狂怒",
    "1,500 Credits Cache": "1,500 星币缓存",
    "Gara Chassis Blueprint": "Gara 机体蓝图",
    "Gara Systems Blueprint": "Gara 系统蓝图",
    "Gara Neuroptics Blueprint": "Gara 头部神经光元蓝图",
    "Revenant Chassis Blueprint": "Revenant 机体蓝图",
    "Revenant Systems Blueprint": "Revenant 系统蓝图",
    "Revenant Neuroptics Blueprint": "Revenant 头部神经光元蓝图",
    "Furax Wraith Blueprint": "弗拉克斯·亡魂蓝图",
    "Caliban Neuroptics Blueprint": "Caliban 头部神经光元蓝图",
    "Aya": "阿耶",
    "Cetus Wisp": "希图斯幽魂",
    "Breath Of The Eidolon": "夜灵之息",
    "5X Breath Of The Eidolon": "5 个夜灵之息",
    "Rifle Riven Mod": "步枪裂罅模组",
    "Pistol Riven Mod": "手枪裂罅模组",
    "Melee Riven Mod": "近战裂罅模组",
    "Shotgun Riven Mod": "霰弹枪裂罅模组",
    "Riven Mod": "裂罅模组",
    "Riven Sliver": "裂罅碎银",
    "Steel Essence": "钢铁精华",
    "Road To Tau Event Resource": "「通往 Tau 之路」活动资源",
    "2X Cetus Wisp": "2 个希图斯幽魂",
}


def localize_reward(text: str) -> str:
    if not text:
        return ""
    text = localize_text(text)
    for english, chinese in sorted(REWARD_TERMS.items(),
                                   key=lambda item: -len(item[0])):
        text = re.sub(re.escape(english), chinese, text, flags=re.I)
    return text


def localize_phrase(text: str) -> str:
    if not text:
        return ""
    text = localize_text(text)
    for english, chinese in sorted(
            list(NEWS_PHRASES.items()) + list(EVENT_PHRASES.items()),
            key=lambda item: -len(item[0])):
        text = re.sub(re.escape(english), chinese, text, flags=re.I)
    return text

BOUNTY_PHRASES = {
    "Capture the Grineer Commander": "捕获克隆尼指挥官",
    "Sabotage the Enemy Supply Lines": "破坏克隆尼的补给线",
    "Cull the Enemy": "宰杀敌人",
    "Spy Catcher": "间谍捕手",
    "Find the Hidden Artifact": "寻找隐藏神器",
    "Sabotage Bounty": "破坏原型机",
    "Weaken the Grineer Foothold": "削弱克隆尼的据点",
    "Reclaim the Stolen Artifact": "取回被盗的器物",
    "Cleanse the Land": "净化土地",
    "Rise and Fall": "兴衰",
    "Operational Intelligence": "作战情报",
    "Picket Duty": "哨戒任务",
    "Agent Down": "特工倒下",
    "Trash Their Traps": "破坏陷阱",
    "Financial Liberation": "财务解放",
    "Tax Taxmen": "税务官",
    "Tax the Taxmen": "向税务官征税",
    "Crush Cult": "粉碎教团",
    "Crush the Cult": "粉碎教团",
    "For Unum": "为了乌努母神",
    "For the Unum": "为了乌努母神",
    "For the Unum (Narmer)": "为了乌努母神（合一众）",
    "Brute Force": "暴力破解",
    "Strip Mining (Endless)": "露天开采（无尽）",
    "Isolation Vault Chamber": "隔离宝库",
    "Salvage": "回收",
    "Core Samples": "核心样本",
    "Anomaly Retrieval": "异常回收",
    "Isolation Vault Chambers": "隔离宝库",
}

BOUNTY_WORDS = {
    "Capture": "捕获", "Sabotage": "破坏", "Cull": "清剿",
    "Spy": "间谍", "Find": "寻找", "Hidden": "隐藏",
    "Artifact": "神器", "Enemy": "敌人", "Supply Lines": "补给线",
    "Commander": "指挥官", "Foothold": "据点", "Stolen": "被盗",
    "Reclaim": "夺回", "Cleanse": "净化", "Land": "土地",
    "Rise and Fall": "兴衰", "Crush Cult": "粉碎教团",
    "Isolation Vault Chamber": "隔离宝库", "Tax Taxmen": "税务官",
    "For Unum": "为了乌努姆", "For the Unum": "为了乌努姆",
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
    """任务类型 → 中文（大小写不敏感，官方偶尔给 ASSAULT 这种全大写）。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw in MISSIONS:
        return MISSIONS[raw]
    lowered = raw.lower()
    for english, chinese in MISSIONS.items():
        if english.lower() == lowered:
            return chinese
    # 驼峰/下划线写法兜底：ArtifactMission → Artifact
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw.replace("_", " "))
    for english, chinese in MISSIONS.items():
        if english.lower() == spaced.lower():
            return chinese
    return raw


def localize_faction(text: str) -> str:
    return FACTIONS.get(text, text)


def localize_boss(text: str) -> str:
    return BOSSES.get(text, text)


def localize_syndicate(text: str) -> str:
    return SYNDICATES.get(text, text)


def localize_research(text: str) -> str:
    return RESEARCH_TYPES.get(text, text)


def localize_relic_tier(text: str) -> str:
    return RELIC_TIERS.get(str(text or ""), str(text or ""))


def localize_bounty(text: str) -> str:
    if not text:
        return ""
    if text in BOUNTY_PHRASES:
        return BOUNTY_PHRASES[text]
    result = localize_text(text)
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
    # 科研词条要在派系/仙族之前替换，否则 "Techrot Conjunction" 会先被拆成
    # 「科魔者 Conjunction」，整词就再也匹配不上。
    for english, chinese in sorted(RESEARCH_TERMS.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(FACTIONS.items(), key=lambda item: -len(item[0])):
        result = re.sub(r"\b%s\b" % re.escape(english), chinese, result, flags=re.I)
    for english, chinese in sorted(SYNDICATES.items(), key=lambda item: -len(item[0])):
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
