"""紫卡（Riven）查询。

warframe.market 的 v2 只公开紫卡武器（倾向、类型），拍卖数据走 v1：
    GET /v1/auctions/search?type=riven&weapon_url_name={slug}
若该接口不可用（官方已多次调整），就只展示武器倾向信息并明确说明。
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import wiki_zh
from .cache import TTLCache
from .market import Item, MarketClient, MarketError
from .net import HttpError

LOGGER = logging.getLogger("qqwarframe.riven")

# 常见紫卡词条的中文名
ATTRIBUTE_NAMES = {
    "critical_chance": "暴击几率",
    "critical_damage": "暴击伤害",
    "damage": "伤害",
    "multishot": "多重射击",
    "status_chance": "触发几率",
    "status_duration": "触发时间",
    "fire_rate": "射速",
    "reload_speed": "装填速度",
    "magazine_capacity": "弹匣容量",
    "zoom": "变焦",
    "recoil": "后坐力",
    "punch_through": "穿透",
    "projectile_speed": "飞行速度",
    "ammo_maximum": "弹药上限",
    "toxin_damage": "毒素伤害",
    "heat_damage": "火焰伤害",
    "cold_damage": "冰冻伤害",
    "electricity_damage": "电击伤害",
    "slash_damage": "切割伤害",
    "impact_damage": "冲击伤害",
    "puncture_damage": "穿刺伤害",
    "damage_vs_grineer": "对克隆尼伤害",
    "damage_vs_corpus": "对科普斯伤害",
    "damage_vs_infested": "对异变体伤害",
    "damage_vs_corrupted": "对堕落者伤害",
    "critical_chance_on_slide": "滑铲暴击几率",
    "critical_damage_on_slide": "滑铲暴击伤害",
    "finisher_damage": "处决伤害",
    "channeling_damage": "引导伤害",
    "channeling_efficiency": "引导效率",
    "combo_duration": "连击持续时间",
    "initial_combo": "初始连击",
    "range": "攻击范围",
    "attack_speed": "攻击速度",
    "heavy_attack_efficiency": "重击效率",
    "heavy_attack_speed": "重击速度",
    "additional_combo_count": "额外连击数",
    "chance_to_gain_combo_count": "获得连击几率",
}

# 拍卖接口（/v1/auctions/search）里的 url_name 与官方 attributes 表的 slug
# 不完全一致（实测 2026-11）：近战攻速是 `fire_rate_/_attack_speed`、
# 基础伤害是 `base_damage_/_melee_damage`、电击是 `electric_damage`……
# 这些写法补进来，卡面和筛选才不会露出生 slug。
AUCTION_ATTRIBUTE_NAMES = {
    "base_damage_/_melee_damage": "伤害",
    "fire_rate_/_attack_speed": "攻击速度",
    "electric_damage": "电击伤害",
    "melee_damage_on_heavy_attack": "重击伤害",
    "critical_chance_on_slide_attack": "滑行攻击暴击几率",
    "chance_to_gain_extra_combo_count": "额外连击数几率",
    "chance_to_gain_combo_count": "获得连击几率",
    "channeling_damage": "初始连击",
    "channeling_efficiency": "重击效率",
    "damage_to_techrot": "对科腐者伤害",
    "damage_to_orokin": "对奥罗金伤害",
    "status_damage": "异常状态伤害",
    "viral": "病毒伤害",
    "blast": "爆炸伤害",
    "range": "攻击范围",
    "recoil": "后坐力",
    "zoom": "变焦",
}

# 玩家叫法 → 该词条在拍卖 / 官方表里的所有写法（同一组内任写法即算命中）
ATTRIBUTE_GROUPS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("暴击伤害", "爆伤", "暴伤", "critical damage", "crit damage"),
     ("critical_damage",)),
    (("暴击几率", "暴击概率", "暴击率", "暴击", "爆率", "暴率",
      "critical chance", "crit chance"),
     ("critical_chance",)),
    (("多重射击", "多重", "multishot"), ("multishot",)),
    (("触发几率", "触发概率", "触发", "状态几率", "异常几率", "status chance"),
     ("status_chance",)),
    (("触发时间", "状态持续时间", "status duration"), ("status_duration",)),
    (("攻击速度", "攻速", "射速", "挥舞速度", "attack speed", "fire rate"),
     ("fire_rate_/_attack_speed", "attack_speed", "fire_rate",
      "heavy_attack_speed")),
    (("伤害", "基础伤害", "近战伤害", "damage", "base damage", "melee damage"),
     ("base_damage_/_melee_damage", "damage", "melee_damage")),
    (("重击伤害", "重击", "heavy attack damage"), ("melee_damage_on_heavy_attack",)),
    (("攻击范围", "范围", "range"), ("range", "melee_range")),
    (("装填速度", "装填", "reload speed", "reload"), ("reload_speed",)),
    (("弹匣容量", "弹匣", "magazine capacity", "magazine"), ("magazine_capacity",)),
    (("弹药最大值", "弹药上限", "弹药", "ammo maximum", "ammo"), ("ammo_maximum",)),
    (("后坐力", "recoil"), ("recoil",)),
    (("变焦", "zoom"), ("zoom",)),
    (("穿透", "punch through"), ("punch_through",)),
    (("投射物速度", "飞行速度", "弹速", "projectile speed"),
     ("projectile_speed", "flight_speed")),
    (("连击持续时间", "连击时间", "连击", "combo duration"), ("combo_duration",)),
    (("初始连击", "initial combo"), ("channeling_damage", "initial_combo")),
    (("额外连击数几率", "额外连击", "extra combo"),
     ("chance_to_gain_extra_combo_count",)),
    (("获得连击几率", "连击几率", "combo chance"), ("chance_to_gain_combo_count",)),
    (("重击效率", "heavy attack efficiency"),
     ("channeling_efficiency", "heavy_attack_efficiency")),
    (("处决伤害", "处决", "finisher"), ("finisher_damage",)),
    (("滑行攻击暴击几率", "滑铲暴击", "滑行暴击", "slide crit"),
     ("critical_chance_on_slide_attack", "critical_chance_on_slide")),
    (("毒素伤害", "毒素", "毒", "toxin"), ("toxin_damage",)),
    (("电击伤害", "电击", "电", "electric", "electricity"),
     ("electric_damage", "electricity_damage")),
    (("火焰伤害", "火焰", "火", "heat"), ("heat_damage",)),
    (("冰冻伤害", "冰冻", "冰", "cold", "ice"), ("cold_damage",)),
    (("切割伤害", "切割", "slash"), ("slash_damage",)),
    (("冲击伤害", "冲击", "impact"), ("impact_damage",)),
    (("穿刺伤害", "穿刺", "puncture"), ("puncture_damage",)),
    (("病毒伤害", "病毒", "viral"), ("viral", "viral_damage")),
    (("爆炸伤害", "爆炸", "blast"), ("blast", "blast_damage")),
    (("对克隆尼伤害", "对g系", "克隆尼", "grineer"), ("damage_vs_grineer",)),
    (("对科普斯伤害", "对c系", "科普斯", "corpus"), ("damage_vs_corpus",)),
    (("对异变体伤害", "对i系", "异变体", "infested"), ("damage_vs_infested",)),
    (("对堕落者伤害", "对o系", "堕落者", "corrupted"), ("damage_vs_corrupted",)),
    (("对奥罗金伤害", "奥罗金", "orokin"), ("damage_to_orokin",)),
    (("对科腐者伤害", "科腐者", "techrot"), ("damage_to_techrot",)),
    (("异常状态伤害", "状态伤害", "status damage"), ("status_damage",)),
)

# 「负任意」这类控制词
NEGATIVE_WORDS = ("负任意", "负面任意", "任意负面", "负词条", "带负面", "有负面")
POSITIVE_ONLY_WORDS = ("无负", "不要负面", "纯正面", "正面向")

_SPLIT_RE = re.compile(r"[\s_/\-]+")


def normalize_attribute(text: str) -> str:
    """词条归一：小写 + 去掉空格/下划线/斜杠/连字符。"""
    return _SPLIT_RE.sub("", str(text or "").lower())


_ATTRIBUTE_GROUPS_INDEX: Optional[Dict[str, frozenset]] = None


def _attribute_group_index() -> Dict[str, frozenset]:
    global _ATTRIBUTE_GROUPS_INDEX
    if _ATTRIBUTE_GROUPS_INDEX is not None:
        return _ATTRIBUTE_GROUPS_INDEX
    index: Dict[str, frozenset] = {}
    for aliases, slugs in ATTRIBUTE_GROUPS:
        group = frozenset(normalize_attribute(slug) for slug in slugs)
        for word in list(aliases) + list(slugs):
            index.setdefault(normalize_attribute(word), group)
    _ATTRIBUTE_GROUPS_INDEX = index
    return index


def resolve_attribute(word: str) -> Optional[frozenset]:
    """单个词条词 → 可接受的 url_name 集合；认不出来返回 None。"""
    key = normalize_attribute(word)
    if not key:
        return None
    return _attribute_group_index().get(key)


def attribute_label(url_name: str, names: Optional[Dict[str, str]] = None) -> str:
    """url_name → 中文显示名（官方表 → 拍卖补充表 → 英文兜底）。"""
    url_name = str(url_name or "")
    table = names or {}
    for candidate in (url_name, url_name.replace("/", ""), *url_name.split("/")):
        key = str(candidate).strip("_")
        if key and key in table:
            return table[key]
    for candidate in (url_name, *url_name.split("/")):
        key = str(candidate).strip("_")
        if key and key in AUCTION_ATTRIBUTE_NAMES:
            return AUCTION_ATTRIBUTE_NAMES[key]
    return url_name.replace("/", " ").replace("_", " ").strip().title()


@dataclass
class RivenAttribute:
    """紫卡上的一条词条（带正负与数值）。"""

    url_name: str = ""
    label: str = ""
    value: float = 0.0
    positive: bool = True

    @property
    def text(self) -> str:
        # 对派系伤害（歧视）在卡面上是倍率：x1.4 对 Corpus 的伤害
        if _is_multiplier(self.url_name):
            return "x%.2f" % abs(self.value)
        return "%s%.1f%%" % ("+" if self.positive else "-", abs(self.value))


def _is_multiplier(url_name: str) -> bool:
    """对派系 / 对奥罗金这类词条是倍率，不是百分比。"""
    raw = str(url_name or "").lower()
    return "damage_to_" in raw or "damage_vs_" in raw or "faction" in raw


@dataclass
class RivenFilter:
    """词条筛选条件：values 里任写法命中即算这条满足。"""

    word: str = ""
    values: frozenset = frozenset()
    negative: bool = False

    def matches(self, attributes: Sequence[RivenAttribute]) -> bool:
        for attr in attributes:
            # 列出的词条默认按正面算（负词条要显式说「负任意」，不点名具体负面）
            if attr.positive != self.negative and \
                    normalize_attribute(attr.url_name) in self.values:
                return True
        return False


@dataclass
class RivenQuery:
    """`wr 翁 攻击速度 范围 负任意` 解析结果。"""

    weapon: str = ""
    filters: List[RivenFilter] = field(default_factory=list)
    need_negative: bool = False
    forbid_negative: bool = False
    unknown: List[str] = field(default_factory=list)

    @property
    def has_filters(self) -> bool:
        return bool(self.filters or self.need_negative or self.forbid_negative)

    def match(self, attributes: Sequence[RivenAttribute]) -> bool:
        for item in self.filters:
            if not item.matches(attributes):
                return False
        has_negative = any(not attr.positive for attr in attributes)
        if self.need_negative and not has_negative:
            return False
        if self.forbid_negative and has_negative:
            return False
        return True


def parse_riven_filters(words: Sequence[str]) -> RivenQuery:
    """解析词条部分（不含武器名）。"""
    query = RivenQuery()
    for word in words:
        key = normalize_attribute(word)
        if not key:
            continue
        if key in {normalize_attribute(item) for item in NEGATIVE_WORDS}:
            query.need_negative = True
            continue
        if key in {normalize_attribute(item) for item in POSITIVE_ONLY_WORDS}:
            query.forbid_negative = True
            continue
        group = resolve_attribute(word)
        if group:
            query.filters.append(RivenFilter(word=word, values=group))
        else:
            query.unknown.append(word)
    return query


def parse_riven_query(text: str) -> RivenQuery:
    """解析 `武器 [词条…] [负任意]`，第一个词是武器名。"""
    words = [word for word in str(text or "").split() if word]
    if not words:
        return RivenQuery()
    query = parse_riven_filters(words[1:])
    query.weapon = words[0]
    return query


_STRIP_SUFFIXES = (
    "_prime_set", "_prime_blueprint", "_prime", "_set", "_wraith", "_vandal",
    "_prisma", "_kuva", "_tenet", "_dex", "_mk1", "_umbra", "_prime_receiver",
    "_prime_barrel", "_prime_stock", "_prime_blade", "_prime_handle",
    "_prime_blueprint", "_blueprint",
)

# 紫卡武器分组（玩家口径）：官方 group / rivenType -> 组 key。
# 「自制武器」Kitgun 除近战外都按副武器算，Zaw 按近战算。
GROUP_LABELS = {
    "primary": "主武器",
    "secondary": "副武器",
    "melee": "近战",
    "archgun": "空战",
    "sentinel": "同伴",
}
GROUP_KEYS = ("primary", "secondary", "melee", "archgun", "sentinel")

GROUP_ALIASES = {
    "主武器": "primary", "主武": "primary", "主": "primary",
    "primary": "primary", "步枪": "primary", "rifle": "primary",
    "霰弹枪": "primary", "霰弹": "primary", "shotgun": "primary",
    "副武器": "secondary", "副武": "secondary", "副": "secondary",
    "secondary": "secondary", "手枪": "secondary", "pistol": "secondary",
    "自制": "secondary", "组合枪": "secondary", "kitgun": "secondary",
    "近战": "melee", "melee": "melee", "刀": "melee",
    "组合近战": "melee", "zaw": "melee",
    "空战": "archgun", "空战武器": "archgun", "archgun": "archgun",
    "archwing": "archgun", "空战枪": "archgun",
    "同伴": "sentinel", "同伴武器": "sentinel", "哨兵": "sentinel",
    "哨兵武器": "sentinel", "守护": "sentinel", "sentinel": "sentinel",
}

# 类别（决定紫卡基础数值）→ 中文名
CATEGORY_LABELS = {
    "rifle": "步枪",
    "shotgun": "霰弹枪",
    "pistol": "手枪",
    "melee": "近战",
    "archgun": "空战",
}

# 组 key -> 类别显示顺序（主武器含步枪与霰弹，两套数值不一样）
GROUP_CATEGORY_ORDER = {
    "primary": ("rifle", "shotgun"),
    "secondary": ("pistol",),
    "melee": ("melee",),
    "archgun": ("archgun",),
    "sentinel": ("rifle",),
}


def weapon_group(weapon: "RivenWeapon") -> str:
    """把紫卡武器归到玩家口径的分组：主武器/副武器/近战/空战/同伴。"""
    group = str(getattr(weapon, "group", "") or "").lower()
    riven_type = str(getattr(weapon, "riven_type", "") or "").lower()
    if group == "archgun" or riven_type == "archgun":
        return "archgun"          # 空战（含空战近战）
    if group == "kitgun" or riven_type == "kitgun":
        return "secondary"        # 自制枪按副武器算
    if group == "zaw" or riven_type == "zaw":
        return "melee"            # 自制近战按近战算
    if group in GROUP_LABELS:
        return group
    if riven_type in ("rifle", "shotgun"):
        return "primary"
    if riven_type == "pistol":
        return "secondary"
    if riven_type == "melee":
        return "melee"
    return ""


def weapon_category(weapon: "RivenWeapon") -> str:
    """紫卡数值用的是哪一套基础值：rifle / shotgun / pistol / melee / archgun。"""
    riven_type = str(getattr(weapon, "riven_type", "") or "").lower()
    group = str(getattr(weapon, "group", "") or "").lower()
    if riven_type == "archgun" or group == "archgun":
        return "archgun"
    if riven_type in ("rifle", "shotgun", "pistol", "melee"):
        return riven_type
    if riven_type == "zaw" or group == "zaw":
        return "melee"
    if riven_type == "kitgun" or group == "kitgun":
        return "pistol"
    if group == "melee":
        return "melee"
    if group == "secondary":
        return "pistol"
    return "rifle"


# 「步枪」「霰弹」这类只指主武器里的某个类别，范围表也分开算
CATEGORY_ALIASES = {
    "步枪": "rifle", "rifle": "rifle",
    "霰弹": "shotgun", "霰弹枪": "shotgun", "shotgun": "shotgun",
}


def parse_group_detail(text: str) -> Tuple[Optional[str], Optional[str]]:
    """解析成（组 key, 类别过滤）。例如「步枪」-> (primary, rifle)。"""
    key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(text or "").lower())
    if not key:
        return None, None
    for alias, category in CATEGORY_ALIASES.items():
        if key == alias:
            group = "primary"
            return group, category
    return parse_group(text), None


def parse_group(text: str) -> Optional[str]:
    """把「主武器」「pistol」「空战」这类输入解析成组 key。"""
    key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(text or "").lower())
    if not key:
        return None
    if key in GROUP_ALIASES:
        return GROUP_ALIASES[key]
    for alias, target in GROUP_ALIASES.items():
        normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", alias.lower())
        if normalized and (normalized in key or key in normalized):
            return target
    return None


@dataclass
class RivenWeapon:
    slug: str
    name: str
    name_en: str
    riven_type: str
    group: str
    disposition: float
    mastery_rank: int
    disposition_raw: float = 0.0
    thumb: str = ""

    @property
    def disposition_label(self) -> str:
        if self.disposition >= 1.3:
            return "高"
        if self.disposition >= 1.0:
            return "中"
        return "低"

    @property
    def icon_url(self) -> str:
        """武器图标（warframe.market 静态资源，128×128）。"""
        if not self.thumb:
            return ""
        return "https://warframe.market/static/assets/" + self.thumb.lstrip("/")

    @property
    def type_label(self) -> str:
        """类别中文名（rifle → 步枪）；认不出的原样返回。"""
        raw = str(self.riven_type or self.group or "")
        return CATEGORY_LABELS.get(raw) or raw or "—"


@dataclass
class RivenAuction:
    price: int
    seller: str
    rank: int
    re_rolls: int
    attributes: List[str]
    detail: List[RivenAttribute] = field(default_factory=list)
    name: str = ""                 # 紫卡名（laci-acricron）
    polarity: str = ""             # naramon / madurai / vazarin …
    mastery_level: int = 0
    weapon_slug: str = ""
    weapon_name: str = ""
    weapon_icon: str = ""
    status: str = ""               # ingame / online / offline
    buyout: bool = True
    platform: str = "pc"


class RivenClient:
    def __init__(self, market: MarketClient, logger: Optional[logging.Logger] = None,
                 cache_config: Optional[Dict[str, Any]] = None,
                 disposition_override: Optional[float] = None):
        self.market = market
        self.log = logger or LOGGER
        cache_cfg = cache_config or {}
        self.cache = TTLCache(cache_cfg, prefix="riven")
        self.auction_cache_seconds = int(cache_cfg.get("riven_seconds", 300))
        try:
            self.disposition_override = (float(disposition_override)
                                         if disposition_override else None)
        except (TypeError, ValueError):
            self.disposition_override = None
        if self.disposition_override is not None:
            self.log.info("紫卡倾向已全局覆盖为 %.2f", self.disposition_override)
        self._lock = threading.Lock()
        self._weapons: Optional[Dict[str, RivenWeapon]] = None
        self._weapons_at = 0.0
        self._attributes: Optional[Dict[str, str]] = None
        self._attributes_at = 0.0

    # ------------------------------------------------------------------ 武器表
    def weapons(self, ttl: int = 43200) -> Dict[str, RivenWeapon]:
        with self._lock:
            if self._weapons is not None and time.time() - self._weapons_at < ttl:
                return self._weapons
            try:
                data = self.market._get("/v2/riven/weapons")
            except (HttpError, MarketError) as exc:
                self.log.warning("拉取紫卡武器表失败：%s", exc)
                return self._weapons or {}
            rows = data.get("data") if isinstance(data, dict) else None
            result: Dict[str, RivenWeapon] = {}
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                i18n = row.get("i18n") or {}
                english = i18n.get("en") or {}
                localized = i18n.get(self.market.language) or {}
                slug = str(row.get("slug") or "")
                if not slug:
                    continue
                raw_disposition = float(row.get("disposition") or 0)
                english_name = str(english.get("name") or slug)
                result[slug] = RivenWeapon(
                    slug=slug,
                    name=(wiki_zh.lookup(english_name)
                          or str(localized.get("name") or english_name)),
                    name_en=english_name,
                    riven_type=str(row.get("rivenType") or ""),
                    group=str(row.get("group") or ""),
                    disposition=(self.disposition_override
                                 if self.disposition_override is not None
                                 else raw_disposition),
                    mastery_rank=int(row.get("reqMasteryRank") or 0),
                    disposition_raw=raw_disposition,
                    thumb=str(localized.get("thumb") or english.get("thumb") or ""),
                )
            self._weapons = result
            self._weapons_at = time.time()
            return result

    def refresh(self) -> int:
        """强制刷新紫卡武器表（后台自动更新用）。"""
        return len(self.weapons(ttl=0))

    def group_weapons(self, key: str) -> Dict[str, List[RivenWeapon]]:
        """按玩家口径的分组取武器：类别（rifle/shotgun/…）-> 武器列表。"""
        buckets: Dict[str, List[RivenWeapon]] = {}
        for weapon in self.weapons().values():
            if weapon_group(weapon) != key:
                continue
            buckets.setdefault(weapon_category(weapon), []).append(weapon)
        for rows in buckets.values():
            rows.sort(key=lambda item: item.name)
        return buckets

    def group_counts(self) -> Dict[str, int]:
        """各组武器数量（主武器/副武器/近战/空战/同伴）。"""
        counts = {key: 0 for key in GROUP_KEYS}
        for weapon in self.weapons().values():
            key = weapon_group(weapon)
            if key in counts:
                counts[key] += 1
        return counts

    def attributes(self, ttl: int = 43200) -> Dict[str, str]:
        """紫卡词条 slug -> 中文名（官方 /v2/riven/attributes）。

        接口拿不到时退回内置 ATTRIBUTE_NAMES，界面上不会出现生 slug。
        """
        with self._lock:
            if self._attributes is not None and time.time() - self._attributes_at < ttl:
                return self._attributes
        names: Dict[str, str] = dict(ATTRIBUTE_NAMES)
        try:
            data = self.market._get("/v2/riven/attributes")
        except (HttpError, MarketError) as exc:
            self.log.info("拉取紫卡词条表失败，使用内置字典：%s", exc)
            with self._lock:
                self._attributes = names
                self._attributes_at = time.time()
            return names
        rows = data.get("data") if isinstance(data, dict) else None
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or "")
            if not slug:
                continue
            i18n = row.get("i18n") or {}
            localized = i18n.get(self.market.language) or i18n.get("zh-hans") or {}
            english = i18n.get("en") or {}
            label = str(localized.get("name") or "").strip()
            if label:
                names[slug] = label
            elif slug not in names:
                names[slug] = str(english.get("name") or slug.replace("_", " "))
        with self._lock:
            self._attributes = names
            self._attributes_at = time.time()
        return names

    def cache_age(self) -> float:
        return max(0.0, time.time() - self._weapons_at) if self._weapons_at else -1.0

    def weapon_count(self) -> int:
        return len(self._weapons) if self._weapons is not None else 0

    def resolve(self, text: str) -> Optional[RivenWeapon]:
        """按 slug / 中文名 / 英文名解析武器，供紫卡截图自动识别。"""
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(text or "").lower())
        if not key:
            return None
        weapons = self.weapons()
        if not weapons:
            return None
        for weapon in weapons.values():
            names = (weapon.slug, weapon.name, weapon.name_en)
            if any(re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", name.lower()) == key
                   for name in names):
                return weapon
        candidates = []
        for weapon in weapons.values():
            for name in (weapon.name, weapon.name_en):
                normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", name.lower())
                if len(normalized) >= 2 and normalized in key:
                    candidates.append((len(normalized), weapon))
        if candidates:
            candidates.sort(key=lambda pair: pair[0], reverse=True)
            return candidates[0][1]
        return None

    def find(self, item: Item) -> Optional[RivenWeapon]:
        weapons = self.weapons()
        if not weapons:
            return None
        if item.slug in weapons:
            return weapons[item.slug]

        candidates = [item.slug]
        for suffix in _STRIP_SUFFIXES:
            if item.slug.endswith(suffix):
                candidates.append(item.slug[:-len(suffix)])
        for candidate in candidates:
            if candidate in weapons:
                return weapons[candidate]

        # 名称匹配：去掉 Prime / Wraith 等修饰
        base = re.sub(r"\b(prime|wraith|vandal|prisma|kuva|tenet|dex|mk1)\b", " ",
                      item.name_en.lower())
        base = re.sub(r"[^a-z0-9]+", " ", base).strip()
        if not base:
            return None
        for weapon in weapons.values():
            name = re.sub(r"[^a-z0-9]+", " ", weapon.name_en.lower()).strip()
            if name == base or name.startswith(base + " ") or base.startswith(name + " "):
                return weapon
        return self.resolve(item.name) or self.resolve(item.name_en)

    # ------------------------------------------------------------------ 拍卖
    def auctions(self, slug: str, limit: int = 5,
                 query: Optional[RivenQuery] = None,
                 names: Optional[Dict[str, str]] = None
                 ) -> Tuple[List[RivenAuction], str]:
        """在线紫卡拍卖，按价格升序；query 非空时按词条筛选。"""
        filters = tuple(sorted((item.word, item.negative)
                               for item in (query.filters if query else ())))
        key = ("auctions", slug, int(limit), filters,
               bool(query and query.need_negative),
               bool(query and query.forbid_negative))
        return self.cache.get_or_set(
            key, lambda: self._auctions_uncached(slug, limit, query, names),
            self.auction_cache_seconds)

    def _auctions_uncached(self, slug: str, limit: int = 5,
                           query: Optional[RivenQuery] = None,
                           names: Optional[Dict[str, str]] = None
                           ) -> Tuple[List[RivenAuction], str]:
        """官方拍卖搜索：/v1/auctions/search（2026-10 实测可用）。"""
        quoted = urllib.parse.quote(slug)
        paths = (
            "/v1/auctions/search?type=riven&weapon_url_name=%s"
            "&buyout_policy=direct&sort_by=price_asc" % quoted,
            "/v1/auctions/search?type=riven&weapon_url_name=%s"
            "&sort_by=price_asc" % quoted,
        )
        data = None
        last_error = None
        for path in paths:
            try:
                data = self.market.http.get_json(
                    self.market.base_url + path, headers=self.market._headers())
                if data:
                    break
            except HttpError as exc:
                last_error = exc
                continue
            except Exception as exc:  # pragma: no cover - 防御
                last_error = exc
                continue

        if not data:
            self.log.info("紫卡拍卖接口不可用（%s）", last_error)
            return [], "紫卡拍卖接口暂不可用（官方接口调整中），仅显示武器倾向信息。"

        payload = data.get("payload") if isinstance(data, dict) else None
        if payload is None and isinstance(data, dict):
            payload = data.get("data")
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("auctions") or []
        else:
            rows = []
        table = names or self.attributes()
        parsed: List[RivenAuction] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("closed"):
                continue
            price = row.get("buyout_price") or row.get("starting_price") or 0
            try:
                price = int(price)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            item = row.get("item") if isinstance(row.get("item"), dict) else row
            detail: List[RivenAttribute] = []
            for attr in item.get("attributes") or []:
                if not isinstance(attr, dict):
                    continue
                url_name = str(attr.get("url_name") or "")
                try:
                    value = float(attr.get("value") or 0)
                except (TypeError, ValueError):
                    value = 0.0
                if not url_name or value == 0:
                    continue
                detail.append(RivenAttribute(
                    url_name=url_name,
                    label=attribute_label(url_name, table),
                    value=value,
                    positive=bool(attr.get("positive", True)),
                ))
            detail.sort(key=lambda attr: (not attr.positive, attr.label))
            owner = row.get("owner") or {}
            parsed.append(RivenAuction(
                price=price,
                seller=str(owner.get("ingame_name") or owner.get("ingameName") or "?"),
                rank=int(item.get("mod_rank") or row.get("rank") or 0),
                re_rolls=int(item.get("re_rolls") or row.get("re_rolls") or 0),
                attributes=["%s %s" % (attr.label, attr.text) for attr in detail],
                detail=detail,
                name=str(item.get("name") or ""),
                polarity=str(item.get("polarity") or ""),
                mastery_level=int(item.get("mastery_level") or 0),
                weapon_slug=str(item.get("weapon_url_name") or slug),
                status=str(owner.get("status") or ""),
                buyout=bool(row.get("buyout_price")),
                platform=str(row.get("platform") or "pc"),
            ))
        parsed.sort(key=lambda auction: auction.price)

        note = ""
        if query is not None and query.has_filters:
            matched = [auction for auction in parsed if query.match(auction.detail)]
            if matched:
                parsed = matched
            elif parsed:
                # 词条太严没有命中：退回最便宜的几张，并说明原因
                note = ("没有符合这些词条的紫卡，下面是该武器最便宜的 %d 张（未按词条筛）。"
                        % len(parsed[:limit]))
        return parsed[:limit], note


def format_auction(auction: RivenAuction) -> str:
    attrs = " / ".join(auction.attributes[:3]) or "未洗"
    return "%dp · %s · %s" % (auction.price, attrs, auction.seller)
