"""中文 → 英文词条映射：让中文关键词也能查英文 Wiki。

英文维基（wiki.warframe.com）只认英文词条，直接拿中文去搜基本查不到。
这里用两层数据把中文关键词翻成英文词条名：

1. 内置术语表：把 terms.py 里的星球/派系/任务/集团/BOSS/资源等地名、
   阵营、玩法名词倒置成“中文 → 英文”，覆盖“赤毒要塞”“执刑官”这类词条；
2. warframe.market 物品索引：中文名 / 别名 → 英文名，覆盖战甲、武器、
   MOD、赋能、宠物等绝大多数可交易名词（含“电男”这类俗称）。

拿到英文名后还会去掉“Set / Blueprint / 部件”等后缀再试一次，因为英文维基
通常只有「Volt Prime」这样的主词条，没有「Volt Prime Set」。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import terms

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 关键词里出现这些，说明玩家要的是 Prime 版词条。
_PRIME_RE = re.compile(r"prime|圣装|(?<![a-z])p(?![a-z])", re.I)

# 英文维基一般没有“套装/蓝图/单个部件”的独立词条，逐级去掉后缀再试。
_STRIP_SUFFIXES = (
    " Set", " Blueprint",
    " Neuroptics", " Chassis", " Systems",
    " Barrel", " Receiver", " Stock",
    " Blade", " Handle", " Gauntlet",
    " Upper Limb", " Lower Limb",
    " Hilt", " Ornament", " String",
    " Carapace", " Cerebrum", " Harness", " Wings",
)

_TERM_TABLES = (
    terms.PLANETS,
    terms.FACTIONS,
    terms.SYNDICATES,
    terms.BOSSES,
    terms.MISSIONS,
    terms.RESOURCES,
    terms.RELIC_TIERS,
    terms.STORM_NODES,
    terms.STORM_REGIONS,
)

# 术语表里没有、但英文维基必定有的概念（伤害类型、系统玩法、地名）。
# 这些词用物品索引去猜反而会命中不相干的 MOD，所以优先写死。
EXTRA_TERMS: Dict[str, str] = {
    # 伤害类型 / 状态效果
    "冲击": "Impact", "穿刺": "Puncture", "切割": "Slash", "冰冻": "Cold",
    "电击": "Electricity", "火焰": "Heat", "毒素": "Toxin", "爆炸": "Blast",
    "腐蚀": "Corrosive", "毒气": "Gas", "磁力": "Magnetic", "辐射": "Radiation",
    "病毒": "Viral", "真实伤害": "True Damage",
    # 玩法系统
    "钢铁之路": "Steel Path", "钢铁精华": "Steel Essence",
    "仲裁": "Arbitration", "电波": "Nightwave", "午夜电波": "Nightwave",
    "紫卡": "Riven Mod", "裂罅": "Riven Mod", "灵化": "Incarnon",
    "赋能": "Arcane", "遗物": "Relic", "虚空裂缝": "Void Fissure",
    "裂缝": "Void Fissure", "入侵": "Invasion", "警报": "Alert",
    "噩梦任务": "Nightmare Mode", "集团": "Syndicate", "段位": "Mastery Rank",
    "道场": "Dojo", "圣殿": "Sanctuary Onslaught", "执刑官": "Archon",
    "执刑官猎杀": "Archon Hunt", "赤毒巫妖": "Kuva Lich",
    "帕尔沃斯姐妹": "Sisters of Parvos", "九重天": "Empyrean",
    "航道星舰": "Railjack", "扎里曼": "Zariman", "杜维里": "Duviri",
    "夜灵平原": "Plains of Eidolon", "奥布山谷": "Orb Vallis",
    "魔胎之城": "Cambion Drift", "希图斯": "Cetus", "福尔图娜": "Fortuna",
    "内克塔": "Necralisk", "霍瓦尼亚": "Höllvania",
    # 装备大类
    "战甲": "Warframe", "武器": "Weapon", "同伴": "Companion",
    "守护": "Sentinel", "库狛": "Kubrow", "库娃": "Kavat",
    "恐鸟": "Moa", "机甲": "Necramech",
}

_term_cache: Optional[Dict[str, str]] = None


def has_cjk(text: str) -> bool:
    """是否含有汉字（纯英文/数字不走映射）。"""
    return bool(CJK_RE.search(str(text or "")))


def _key(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _add_term(mapping: Dict[str, str], chinese: str, english: str) -> None:
    for raw in (chinese, str(chinese).replace(" ", ""),
                str(chinese).replace("·", "")):
        key = _key(raw)
        if key and key not in mapping:
            mapping[key] = english


def term_table() -> Dict[str, str]:
    """内置术语表（中文 → 英文），首次调用时构建。"""
    global _term_cache
    if _term_cache is not None:
        return _term_cache
    mapping: Dict[str, str] = {}
    # EXTRA_TERMS 是“中文 → 英文”，terms.py 里的是“英文 → 中文”。
    for chinese, english in EXTRA_TERMS.items():
        _add_term(mapping, chinese, english)
    for table in _TERM_TABLES:
        for english, chinese in table.items():
            _add_term(mapping, chinese, english)
    _term_cache = mapping
    return mapping


def term_to_english(text: str) -> str:
    """术语表精确匹配；匹配不到返回空串。"""
    return term_table().get(_key(text), "")


def _variants(name: str) -> List[str]:
    """「Volt Prime Set」→「Volt Prime」这类逐级去后缀的候选词条名。"""
    value = str(name or "").strip()
    result: List[str] = []
    seen = set()
    while value and value not in seen:
        seen.add(value)
        result.append(value)
        trimmed = value
        for suffix in _STRIP_SUFFIXES:
            if trimmed.endswith(suffix) and len(trimmed) > len(suffix):
                trimmed = trimmed[: -len(suffix)].rstrip()
                break
        if trimmed == value:
            break
        value = trimmed
    return result


def _item_english(item: Any) -> str:
    return str(getattr(item, "name_en", "") or getattr(item, "name", "") or "").strip()


_TITLE_NOISE_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def title_compatible(requested: str, actual: str) -> bool:
    """维基返回的词条是否还对得上我们想要的词条。

    搜索兜底会把「Volt Set」匹配到「Volt Animation Set」这种完全不相干的页面，
    所以映射出来的候选词条必须做一次名称校验，对不上就换下一个候选。
    """
    left = _TITLE_NOISE_RE.sub("", str(requested or "").lower())
    right = _TITLE_NOISE_RE.sub("", str(actual or "").lower())
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _deprime(name: str) -> str:
    """"Volt Prime Set" → "Volt Set"：玩家没说 P 时优先查普通版词条。"""
    value = re.sub(r"\s*\bPrime\b", "", str(name or "")).strip()
    return re.sub(r"\s{2,}", " ", value)


def english_titles(query: str, item_lookup: Optional[Any] = None,
                   limit: int = 3) -> List[str]:
    """中文关键词 → 英文维基候选词条名（按优先级去重）。

    item_lookup 传入 market.lookup 一类的函数，返回带 .item 的搜索结果。

    关键词里没有 P / Prime / 圣装 时，先试普通版词条（玩家说“高斯”“电男”
    一般想看战甲本身），再退到 Prime 版；说了 P 才直接查 Prime。
    """
    plain = str(query or "").strip()
    if not plain:
        return []
    want_prime = bool(_PRIME_RE.search(plain))
    names: List[str] = []

    def add(name: str) -> None:
        name = str(name or "").strip()
        if name and name not in names:
            names.append(name)

    add(term_to_english(plain))
    if item_lookup is not None:
        # “电男p”这类缩写去掉尾字母 p 再查一次，物品索引对纯 p 后缀命中很差。
        stripped = re.sub(r"\s*[pP]$", "", plain).strip()
        queries = [plain]
        if stripped and stripped != plain and has_cjk(stripped):
            queries = [stripped, plain] if want_prime else [plain, stripped]
        for query_text in queries:
            try:
                hits = item_lookup(query_text, limit=limit)
            except Exception:  # 物品索引不可用时只靠术语表
                hits = []
            for hit in hits or []:
                add(_item_english(getattr(hit, "item", hit)))

    titles: List[str] = []

    def push(values: List[str]) -> None:
        for value in values:
            if value and value not in titles:
                titles.append(value)

    for name in names:
        if not want_prime:
            base = _deprime(name)
            if base and base != name:
                push(_variants(base))
        push(_variants(name))
    return titles
