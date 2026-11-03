"""遗物查询：遗物内容、杜卡德、出入库状态与 warframe.market 行情。

数据源：
  * warframestat `/pc/relics`：遗物、奖励、稀有度、概率、vaulted；
  * warframe.market 物品索引：奖励物品的杜卡德与 slug；
  * warframe.market `/v1/items/{slug}/statistics`：48h / 90d 加权均价。

warframestat 不可用时读 `data/relics_zh.json` 缓存。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger("qqwarframe.relics")

# 中文名、缩写、英文名都接受
TIER_ALIASES = {
    "后纪": "Axi", "后": "Axi", "axi": "Axi",
    "前纪": "Meso", "前": "Meso", "meso": "Meso",
    "中纪": "Neo", "中": "Neo", "neo": "Neo",
    "古纪": "Lith", "古": "Lith", "lith": "Lith",
}
TIER_LABELS = {"Lith": "古纪", "Meso": "前纪", "Neo": "中纪", "Axi": "后纪"}
TIER_ORDER = ("Lith", "Meso", "Neo", "Axi")
_CODE_RE = re.compile(r"[A-Za-z]?\d+[A-Za-z]?", re.I)
_CLEAN_RE = re.compile(r"\s+")

RARITY_LABELS = {"common": "铜", "uncommon": "银", "rare": "金"}


@dataclass
class RelicReward:
    name: str = ""
    slug: str = ""
    rarity: str = ""
    chance: float = 0.0
    ducats: int = 0
    price48: float = 0.0
    price90: float = 0.0
    volume48: int = 0
    volume90: int = 0
    market_name: str = ""

    @property
    def rarity_label(self) -> str:
        return RARITY_LABELS.get(self.rarity.lower(), self.rarity or "—")


@dataclass
class Relic:
    name: str = ""
    tier: str = ""
    code: str = ""
    vaulted: bool = False
    rewards: List[RelicReward] = field(default_factory=list)

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier or "遗物")

    @property
    def title(self) -> str:
        return "%s %s" % (self.tier_label, self.code or self.name)

    @property
    def vault_label(self) -> str:
        return "已入库" if self.vaulted else "出库中"


def _clean(text: Any) -> str:
    return _CLEAN_RE.sub(" ", str(text or "")).strip()


def _code_from(text: str) -> str:
    matches = _CODE_RE.findall(str(text or ""))
    return matches[-1].upper() if matches else ""


def parse_tier_code(text: str) -> Tuple[str, str]:
    """`后纪s18` / `Axi S18 Relic` / `S18 后` → (Axi, S18)。"""
    raw = _clean(text)
    lowered = raw.lower()
    tier = ""
    for alias, value in TIER_ALIASES.items():
        if alias in lowered or alias in raw:
            tier = value
            break
    # 去掉遗物/Relic 和 tier，剩下编号
    rest = re.sub(r"(?i)relic|遗物|圣遗物", " ", raw)
    for alias in TIER_ALIASES:
        rest = rest.replace(alias, " ")
    rest = re.sub(r"(?i)axi|lith|meso|neo", " ", rest)
    code = _code_from(rest)
    if not code:
        code = _code_from(raw)
    return tier, code


def _reward_from_raw(raw: Dict[str, Any]) -> RelicReward:
    item = raw.get("item") if isinstance(raw.get("item"), dict) else raw
    market = (item.get("warframeMarket") or item.get("warframe_market")
              or item.get("market") or {})
    if not isinstance(market, dict):
        market = {}
    slug = str(market.get("url_name") or market.get("urlName")
               or item.get("url_name") or "")
    try:
        chance = float(str(raw.get("chance") or 0).rstrip("%"))
    except (TypeError, ValueError):
        chance = 0.0
    try:
        ducats = int(item.get("ducats") or raw.get("ducats") or 0)
    except (TypeError, ValueError):
        ducats = 0
    return RelicReward(
        name=_clean(item.get("name") or item.get("itemName")),
        slug=slug,
        rarity=_clean(raw.get("rarity")),
        chance=chance,
        ducats=ducats,
    )


def parse_relic(raw: Dict[str, Any]) -> Optional[Relic]:
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name") or raw.get("relicName"))
    tier, code = parse_tier_code(name)
    if not tier and not code:
        return None
    rewards = []
    for row in raw.get("rewards") or []:
        if isinstance(row, dict):
            rewards.append(_reward_from_raw(row))
    return Relic(
        name=name, tier=tier, code=code,
        vaulted=bool(raw.get("vaulted")),
        rewards=rewards,
    )


class RelicClient:
    def __init__(self, worldstate, market, cache_file: str = "data/relics_zh.json",
                 logger: Optional[logging.Logger] = None):
        self.worldstate = worldstate
        self.market = market
        self.cache_file = str(cache_file or "data/relics_zh.json")
        self.log = logger or LOGGER
        self._cache: List[Relic] = []
        self._cached_at = 0.0
        self._load_cache()

    # ------------------------------------------------------------------ 数据
    def _load_cache(self) -> None:
        try:
            with open(self.cache_file, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        rows = data.get("relics") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return
        relics = []
        for row in rows:
            if isinstance(row, dict):
                relic = parse_relic(row)
                if relic is not None:
                    relics.append(relic)
        self._cache = relics
        self._cached_at = float(data.get("fetched_at") or 0) if isinstance(data, dict) else 0.0

    def _save_cache(self, raw: Any) -> None:
        directory = os.path.dirname(os.path.abspath(self.cache_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp = self.cache_file + ".tmp"
        payload = {"fetched_at": time.time(), "relics": raw}
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temp, self.cache_file)

    def relics(self) -> List[Relic]:
        raw = None
        if self.worldstate is not None:
            try:
                raw = self.worldstate.fetch("relics")
            except Exception as exc:  # noqa: BLE001
                self.log.warning("读取遗物接口失败：%s", exc)
        if isinstance(raw, dict):
            raw = raw.get("relics") or raw.get("data")
        if isinstance(raw, list) and raw:
            parsed = [parse_relic(row) for row in raw]
            parsed = [item for item in parsed if item is not None]
            if parsed:
                self._cache = parsed
                try:
                    self._save_cache(raw)
                except OSError as exc:
                    self.log.warning("遗物缓存写入失败：%s", exc)
                return list(parsed)
        return list(self._cache)

    def search(self, text: str) -> List[Relic]:
        query = _clean(text)
        if not query:
            return []
        tier, code = parse_tier_code(query)
        results = []
        for relic in self.relics():
            if tier and relic.tier != tier:
                continue
            if code and relic.code.upper() != code.upper():
                continue
            if tier or code:
                results.append(relic)
                continue
            # 最后退回名称模糊匹配（中文/英文）
            if (query.lower() in relic.name.lower()
                    or query.lower() in relic.title.lower()):
                results.append(relic)
        results.sort(key=lambda item: (TIER_ORDER.index(item.tier)
                                        if item.tier in TIER_ORDER else 99,
                                        item.code))
        return results

    # ------------------------------------------------------------------ 行情
    def _item_for(self, reward: RelicReward):
        index = getattr(self.market, "index", None)
        if index is not None:
            item = index.by_slug.get(reward.slug)
            if item is not None:
                return item
        if reward.slug:
            try:
                return self.market.lookup(reward.slug, limit=1)[0].item
            except Exception:  # noqa: BLE001
                return None
        return None

    def enrich(self, relic: Relic, platform: Optional[str] = None) -> Relic:
        rows: List[RelicReward] = []
        for reward in relic.rewards:
            item = self._item_for(reward)
            if item is not None:
                reward.ducats = int(getattr(item, "ducats", 0) or 0)
                reward.market_name = getattr(item, "name", "") or reward.name
                reward.slug = reward.slug or getattr(item, "slug", "")
            if reward.slug:
                try:
                    stats = self.market.statistics(reward.slug, platform=platform)
                    reward.price48 = float(getattr(stats.h48, "avg", 0) or 0)
                    reward.price90 = float(getattr(stats.d90, "avg", 0) or 0)
                    reward.volume48 = int(getattr(stats.h48, "volume", 0) or 0)
                    reward.volume90 = int(getattr(stats.d90, "volume", 0) or 0)
                except Exception as exc:  # noqa: BLE001
                    self.log.info("遗物奖励行情失败（%s）：%s", reward.slug, exc)
            rows.append(reward)
        relic.rewards = rows
        return relic
