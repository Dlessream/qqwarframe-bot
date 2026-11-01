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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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

    @property
    def disposition_label(self) -> str:
        if self.disposition >= 1.3:
            return "高"
        if self.disposition >= 1.0:
            return "中"
        return "低"


@dataclass
class RivenAuction:
    price: int
    seller: str
    rank: int
    re_rolls: int
    attributes: List[str]


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
    def auctions(self, slug: str, limit: int = 5) -> Tuple[List[RivenAuction], str]:
        key = ("auctions", slug, int(limit))
        return self.cache.get_or_set(
            key, lambda: self._auctions_uncached(slug, limit),
            self.auction_cache_seconds)

    def _auctions_uncached(self, slug: str,
                           limit: int = 5) -> Tuple[List[RivenAuction], str]:
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
        result: List[RivenAuction] = []
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
            attributes = []
            for attr in item.get("attributes") or []:
                if not isinstance(attr, dict):
                    continue
                url_name = str(attr.get("url_name") or "")
                label = ATTRIBUTE_NAMES.get(url_name, url_name.replace("_", " "))
                try:
                    value = float(attr.get("value") or 0)
                except (TypeError, ValueError):
                    value = 0.0
                positive = bool(attr.get("positive", True))
                if value == 0:
                    continue
                sign = "+" if positive else "-"
                attributes.append("%s %s%.1f%%" % (label, sign, abs(value)))
            owner = row.get("owner") or {}
            result.append(RivenAuction(
                price=price,
                seller=str(owner.get("ingame_name") or owner.get("ingameName") or "?"),
                rank=int(item.get("mod_rank") or row.get("rank") or 0),
                re_rolls=int(item.get("re_rolls") or row.get("re_rolls") or 0),
                attributes=attributes,
            ))
        result.sort(key=lambda auction: auction.price)
        return result[:limit], ""


def format_auction(auction: RivenAuction) -> str:
    attrs = " / ".join(auction.attributes[:3]) or "未洗"
    return "%dp · %s · %s" % (auction.price, attrs, auction.seller)
