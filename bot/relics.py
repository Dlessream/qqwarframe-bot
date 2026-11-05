"""遗物查询：本地遗物库 + warframe.market 实时行情。

遗物内容、奖励、精炼掉率、掉落地点与出入库状态全部读取
`data/relics_local.json`，运行时不请求任何遗物数据接口；
warframe.market 只负责奖励物品的杜卡德/中文名与 48h、90d 行情。
"""

from __future__ import annotations

import json
import logging
import os
import re
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

# 遗物精炼：完整 / 优良 / 无瑕 / 光辉。
# 每个稀有度的单条概率来自 Wiki 的 Void Relic 掉率表。
REFINEMENTS = {
    "Intact": {
        "label": "完整", "chances": {"common": 25.33, "uncommon": 11.0, "rare": 2.0},
    },
    "Exceptional": {
        "label": "优良", "chances": {"common": 23.33, "uncommon": 13.0, "rare": 4.0},
    },
    "Flawless": {
        "label": "无瑕", "chances": {"common": 20.0, "uncommon": 17.0, "rare": 6.0},
    },
    "Radiant": {
        "label": "光辉", "chances": {"common": 16.67, "uncommon": 20.0, "rare": 10.0},
    },
}
REFINEMENT_ALIASES = {
    "完整": "Intact", "完好": "Intact", "intact": "Intact",
    "优良": "Exceptional", "exceptional": "Exceptional",
    "无瑕": "Flawless", "flawless": "Flawless",
    "光辉": "Radiant", "radiant": "Radiant",
}
WIKI_URL = "https://wiki.warframe.com/w/Void_Relic"


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
    refinement: str = "Intact"
    locations: List[str] = field(default_factory=list)
    vault_date: str = ""
    wiki_url: str = WIKI_URL
    data_updated: str = ""

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.tier, self.tier or "遗物")

    @property
    def refinement_label(self) -> str:
        return REFINEMENTS.get(self.refinement, {}).get("label", self.refinement)

    @property
    def title(self) -> str:
        return "%s %s" % (self.tier_label, self.code or self.name)

    @property
    def vault_label(self) -> str:
        return "已入库" if self.vaulted else "未入库"


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


def _rarity_from_chance(chance: float, fallback: str = "") -> str:
    """WFCD 的 rarity 字段不可靠，以 Intact 基础掉率为准。"""
    if chance >= 20:
        return "Common"
    if chance >= 6:
        return "Uncommon"
    if chance > 0:
        return "Rare"
    return fallback


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
    rarity = _clean(raw.get("rarity"))
    return RelicReward(
        name=_clean(item.get("name") or item.get("itemName")),
        slug=slug,
        rarity=_rarity_from_chance(chance, rarity),
        chance=chance,
        ducats=ducats,
    )


def _locations_from_raw(raw: Dict[str, Any]) -> List[str]:
    rows = raw.get("locations") or raw.get("location") or []
    if isinstance(rows, (str, dict)):
        rows = [rows]
    result = []
    for row in rows:
        if isinstance(row, dict):
            text = (row.get("name") or row.get("node")
                    or row.get("location") or "")
        else:
            text = row
        text = _clean(text)
        if text and text not in result:
            result.append(text)
    return result


def parse_relic(raw: Dict[str, Any]) -> Optional[Relic]:
    if not isinstance(raw, dict):
        return None
    name = _clean(raw.get("name") or raw.get("relicName"))
    tier, code = parse_tier_code(name)
    tier_raw = _clean(raw.get("tier") or raw.get("relicTier") or "")
    if tier_raw:
        tier = TIER_ALIASES.get(tier_raw.lower(), tier)
    code = (_clean(raw.get("relicName") or raw.get("code") or raw.get("relic_code"))
            or code)
    if not name and tier and code:
        name = "%s %s Relic" % (tier, code)
    if not tier and not code:
        return None
    rewards = []
    rows = (raw.get("rewards") or raw.get("drops") or raw.get("items")
            or raw.get("relicIntact") or [])
    if isinstance(rows, dict):
        rows = rows.get("rewards") or rows.get("drops") or []
    for row in rows:
        if isinstance(row, dict):
            rewards.append(_reward_from_raw(row))
    vaulted = raw.get("vaulted")
    if vaulted is None:
        state = _clean(raw.get("state") or "").lower()
        vaulted = state in ("vaulted", "已入库")
    return Relic(
        name=name, tier=tier, code=code,
        vaulted=bool(vaulted),
        rewards=rewards,
        locations=_locations_from_raw(raw),
        vault_date=_clean(raw.get("vaultDate") or raw.get("vault_date")),
    )


class RelicClient:
    """本地遗物库；内容不再依赖 warframestat / WFCD 接口。

    市场价仍由 warframe.market 实时查询；本地文件只保存遗物内容、
    奖励、掉率、掉落地点与出入库状态。
    """

    def __init__(self, worldstate, market,
                 local_file: str = "data/relics_local.json",
                 cache_file: Optional[str] = None,
                 logger: Optional[logging.Logger] = None):
        self.worldstate = worldstate
        self.market = market
        # cache_file 仅保留旧配置兼容；新配置使用 local_file。
        self.local_file = str(local_file or cache_file or "data/relics_local.json")
        self.log = logger or LOGGER
        self._cache: List[Relic] = []
        self._updated = ""
        self._mtime = 0.0
        self.last_error = ""
        self._load_local()

    # ------------------------------------------------------------------ 数据
    def _load_local(self) -> None:
        try:
            with open(self.local_file, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            self.last_error = "本地遗物库读取失败：%s" % exc
            self.log.warning(self.last_error)
            return
        rows = data.get("relics") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            self.last_error = "本地遗物库格式异常"
            return
        relics = []
        for row in rows:
            if isinstance(row, dict):
                relic = parse_relic(row)
                if relic is not None:
                    relics.append(relic)
        self._cache = relics
        self._updated = str(data.get("updated") or "") if isinstance(data, dict) else ""
        try:
            self._mtime = os.path.getmtime(self.local_file)
        except OSError:
            self._mtime = 0.0
        self.last_error = "" if relics else "本地遗物库为空"

    def available(self) -> bool:
        return bool(self._cache)

    def relics(self) -> List[Relic]:
        # 修改 data/relics_local.json 后无需重启，下一次查询自动重载。
        try:
            mtime = os.path.getmtime(self.local_file)
        except OSError:
            mtime = 0.0
        if not self._cache or mtime != self._mtime:
            self._load_local()
        return list(self._cache)

    def search(self, text: str) -> List[Relic]:
        query, _ = self.extract_refinement(text)
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
    @staticmethod
    def extract_refinement(text: str) -> Tuple[str, str]:
        """`后纪s18 光辉` → (`后纪s18`, `Radiant`)；没写默认 Intact。"""
        words = [word for word in str(text or "").split() if word]
        refinement = "Intact"
        if words:
            key = REFINEMENT_ALIASES.get(words[-1].lower())
            if key:
                refinement = key
                words.pop()
        return " ".join(words), refinement

    def _item_for(self, reward: RelicReward):
        index = getattr(self.market, "index", None)
        if index is not None and reward.slug:
            item = index.by_slug.get(reward.slug)
            if item is not None:
                return item
        if reward.slug:
            try:
                return self.market.lookup(reward.slug, limit=1)[0].item
            except Exception:  # noqa: BLE001
                pass
        if reward.name:
            try:
                return self.market.lookup(reward.name, limit=1)[0].item
            except Exception:  # noqa: BLE001
                return None
        return None

    def enrich(self, relic: Relic, platform: Optional[str] = None,
               refinement: str = "Intact") -> Relic:
        relic.refinement = (refinement if refinement in REFINEMENTS
                            else "Intact")
        relic.data_updated = self._updated
        chances = REFINEMENTS[relic.refinement]["chances"]
        for reward in relic.rewards:
            rarity = (reward.rarity or "").lower()
            if rarity in chances:
                reward.chance = chances[rarity]
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
