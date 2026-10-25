"""warframe.market 客户端：物品索引、中文模糊搜索、订单与行情。

接口说明（2026-10 实测）：
  GET /v2/items                         全部可交易物品（简表）
  GET /v2/item/{slug}                   物品详情（杜卡德、MR、交易税、描述）
  GET /v2/orders/item/{slug}/top        在线买/卖前 5 单（按价格排序）
  GET /v1/items/{slug}/statistics       48 小时 / 90 天成交统计

请求头 `Language: zh-hans` 会让 i18n 中额外返回简体中文名。
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from .aliases import DEFAULT_ALIASES
from .cache import TTLCache
from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.market")

SITE_ITEM_URL = "https://warframe.market/zh-hans/items/%s"

COMPONENT_LABELS = {
    "neuroptics": "头部", "chassis": "机体", "systems": "系统",
    "blueprint": "蓝图", "barrel": "枪管", "receiver": "枪机",
    "stock": "枪托", "blade": "刀刃", "handle": "握柄",
    "hilt": "剑柄", "gauntlet": "护手", "link": "连接器",
    "upper_limb": "上肢", "lower_limb": "下肢", "head": "头部",
    "carapace": "甲壳", "cerebrum": "脑", "wings": "翼",
    "harness": "挽具", "pouch": "袋", "claw": "爪",
    "motor": "马达", "disc": "圆盘", "stars": "星标",
    "ornament": "饰物", "string": "弓弦", "grip": "握把",
    "scope": "瞄准镜", "magazine": "弹匣", "trigger": "扳机",
    "accessory": "配件", "buckle": "扣具", "ring": "环",
    "core": "核心", "shell": "外壳", "pod": "舱",
}


def component_label(slug: str) -> str:
    for suffix, label in COMPONENT_LABELS.items():
        if str(slug).endswith("_" + suffix):
            return label
    return ""

# 分类 -> 判定函数
CATEGORY_LABELS = {
    "warframe": "战甲",
    "weapon": "武器",
    "mod": "MOD",
    "arcane": "赋能",
    "relic": "遗物",
    "set": "套装",
    "prime": "Prime",
}

WEAPON_TAGS = {"weapon", "primary", "secondary", "melee", "archgun", "archmelee",
               "arch-gun", "arch-melee", "shotgun", "rifle", "pistol", "bow",
               "thrown_melee", "dual_swords"}


def _clean(text: Any) -> str:
    return str(text or "").strip()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class Item:
    slug: str
    name: str = ""
    name_en: str = ""
    item_id: str = ""
    tags: Tuple[str, ...] = ()
    ducats: int = 0
    mastery_rank: int = 0
    trading_tax: int = 0
    thumb: str = ""
    description: str = ""
    wiki_link: str = ""
    tradable: bool = True
    set_root: bool = False
    max_rank: int = 0
    set_parts: Tuple[str, ...] = ()

    # ------------------------------------------------------------------ 派生
    @property
    def tag_set(self) -> set:
        return set(self.tags)

    @property
    def kind(self) -> str:
        tags = self.tag_set
        if "mod" in tags:
            if "riven_mod" in tags or "veiled_riven" in tags:
                return "riven"
            return "mod"
        if "arcane_enhancement" in tags:
            return "arcane"
        if "relic" in tags or "relics" in tags:
            return "relic"
        if "warframe" in tags and "component" not in tags:
            return "warframe"
        if ("weapon" in tags or tags & WEAPON_TAGS) and "component" not in tags:
            return "weapon"
        if "set" in tags:
            return "set"
        if "component" in tags:
            return "component"
        if "blueprint" in tags:
            return "blueprint"
        if "gem" in tags:
            return "gem"
        if "scene" in tags:
            return "scene"
        if "key" in tags:
            return "key"
        return "misc"

    @property
    def kind_label(self) -> str:
        labels = {
            "warframe": "战甲",
            "weapon": "武器",
            "mod": "MOD",
            "riven": "紫卡",
            "arcane": "赋能",
            "relic": "遗物",
            "set": "套装",
            "component": "部件",
            "blueprint": "蓝图",
            "gem": "宝石",
            "scene": "场景",
            "key": "钥匙",
            "misc": "其他",
        }
        label = labels.get(self.kind, "其他")
        tags = self.tag_set
        if self.kind == "weapon":
            if "primary" in tags or "rifle" in tags or "shotgun" in tags:
                label += "·主武器"
            elif "secondary" in tags or "pistol" in tags:
                label += "·副武器"
            elif "melee" in tags:
                label += "·近战"
            elif "archgun" in tags or "archwing" in tags:
                label += "·空战"
        if "prime" in tags and "Prime" not in label:
            label += "·Prime"
        if self.set_root or "set" in tags:
            if "套装" not in label:
                label += "·套装"
        return label

    @property
    def url(self) -> str:
        return SITE_ITEM_URL % self.slug

    @property
    def thumb_url(self) -> str:
        if not self.thumb:
            return ""
        if self.thumb.startswith("http"):
            return self.thumb
        return "https://warframe.market/static/assets/" + self.thumb.lstrip("/")

    def matches_category(self, category: Optional[str]) -> bool:
        if not category:
            return True
        tags = self.tag_set
        if category == "warframe":
            return "warframe" in tags and "mod" not in tags and "component" not in tags
        if category == "weapon":
            return ("weapon" in tags or bool(tags & WEAPON_TAGS)) \
                and "mod" not in tags and "component" not in tags
        if category == "mod":
            return "mod" in tags
        if category == "arcane":
            return "arcane_enhancement" in tags
        if category == "relic":
            return "relic" in tags or "relics" in tags
        if category == "set":
            return "set" in tags
        if category == "prime":
            return "prime" in tags
        return True

    # ------------------------------------------------------------------ 序列化
    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "name_en": self.name_en,
            "id": self.item_id,
            "tags": list(self.tags),
            "ducats": self.ducats,
            "mr": self.mastery_rank,
            "tax": self.trading_tax,
            "thumb": self.thumb,
            "desc": self.description,
            "wiki": self.wiki_link,
            "tradable": self.tradable,
            "set_root": self.set_root,
            "max_rank": self.max_rank,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Item":
        return cls(
            slug=_clean(raw.get("slug")),
            name=_clean(raw.get("name")),
            name_en=_clean(raw.get("name_en")),
            item_id=_clean(raw.get("id")),
            tags=tuple(str(t) for t in (raw.get("tags") or [])),
            ducats=_as_int(raw.get("ducats")),
            mastery_rank=_as_int(raw.get("mr")),
            trading_tax=_as_int(raw.get("tax")),
            thumb=_clean(raw.get("thumb")),
            description=_clean(raw.get("desc")),
            wiki_link=_clean(raw.get("wiki")),
            tradable=bool(raw.get("tradable", True)),
            set_root=bool(raw.get("set_root")),
            max_rank=_as_int(raw.get("max_rank")),
            set_parts=tuple(str(value) for value in (raw.get("set_parts") or [])),
        )


@dataclass
class Order:
    side: str  # sell / buy
    platinum: int
    quantity: int
    user: str
    status: str
    reputation: int = 0
    rank: Optional[int] = None
    charges: Optional[int] = None

    @property
    def status_label(self) -> str:
        return {"ingame": "游戏中", "online": "在线", "offline": "离线"}.get(
            (self.status or "").lower(), self.status or "未知")

    @classmethod
    def from_api(cls, raw: Dict[str, Any], side: str = "") -> "Order":
        user = raw.get("user") or {}
        rank = raw.get("rank")
        charges = raw.get("charges")
        return cls(
            side=_clean(raw.get("type") or raw.get("order_type") or side),
            platinum=_as_int(raw.get("platinum")),
            quantity=_as_int(raw.get("quantity"), 1),
            user=_clean(user.get("ingameName") or user.get("ingame_name") or "?"),
            status=_clean(user.get("status")),
            reputation=_as_int(user.get("reputation")),
            rank=int(rank) if rank is not None else None,
            charges=int(charges) if charges is not None else None,
        )


@dataclass
class StatSummary:
    volume: int = 0
    avg: float = 0.0
    median: float = 0.0
    min_price: float = 0.0
    max_price: float = 0.0

    @property
    def empty(self) -> bool:
        return self.volume <= 0


@dataclass
class PricePoint:
    date: str
    avg: float = 0.0
    volume: int = 0
    min_price: float = 0.0
    max_price: float = 0.0
    median: float = 0.0


@dataclass
class MarketStats:
    h48: Optional[StatSummary] = None
    d90: Optional[StatSummary] = None
    updated_at: float = 0.0
    history: List[PricePoint] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return (self.h48 is None or self.h48.empty) and (self.d90 is None or self.d90.empty)


@dataclass
class SearchHit:
    score: float
    item: Item


@dataclass
class PartQuote:
    slug: str
    name: str
    sell: Optional[int] = None
    buy: Optional[int] = None


def _normalize(text: str) -> str:
    import re
    import unicodedata

    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    value = re.sub(r"[\s_\-·・,，.。:：;；'\"“”‘’()（）\[\]【】]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


class ItemIndex:
    """物品索引 + 中文/英文/别名模糊搜索。"""

    def __init__(self, items: Sequence[Item], aliases: Optional[Dict[str, str]] = None):
        self.items: List[Item] = list(items)
        self.by_slug: Dict[str, Item] = {item.slug: item for item in self.items}
        self.by_id: Dict[str, Item] = {item.item_id: item for item in self.items if item.item_id}
        self.aliases: Dict[str, str] = {}
        for key, slug in (aliases or {}).items():
            normalized = _normalize(key).replace(" ", "")
            if normalized and slug in self.by_slug:
                self.aliases[normalized] = slug
        # 战甲别名 -> 总图蓝图；再自动展开 头/头部/机体/系统，避免逐个人工维护。
        part_suffixes = {
            "头": "neuroptics", "头部": "neuroptics",
            "头部神经光元": "neuroptics",
            "机体": "chassis", "系统": "systems",
            "总图": "", "蓝图": "",
        }
        for alias_key, slug in list(self.aliases.items()):
            if not slug.endswith("_blueprint"):
                continue
            # 已经是部件蓝图时不要再展开“头/机体/系统”，避免 电男头p 之类。
            if any(slug.endswith(suffix) for suffix in (
                    "_neuroptics_blueprint", "_chassis_blueprint",
                    "_systems_blueprint", "_barrel_blueprint",
                    "_receiver_blueprint", "_stock_blueprint",
                    "_blade_blueprint", "_handle_blueprint",
                    "_gauntlet_blueprint", "_upper_limb_blueprint",
                    "_lower_limb_blueprint", "_hilt_blueprint",
                    "_ornament_blueprint", "_string_blueprint")):
                continue
            stem = slug[:-len("_blueprint")]
            for suffix, part in part_suffixes.items():
                component = (stem + "_blueprint" if not part
                             else "%s_%s_blueprint" % (stem, part))
                if component not in self.by_slug:
                    continue
                generated = alias_key + suffix
                if generated not in self.aliases:
                    self.aliases[generated] = component
            prime_alias = alias_key + "p"
            if not alias_key.endswith("p") and prime_alias not in self.aliases:
                self.aliases[prime_alias] = slug
        # 武器/守护/Archwing 等套装 -> 按部件类型展开 枪管/枪机/枪托/蓝图 等。
        for alias_key, slug in list(self.aliases.items()):
            if not slug.endswith("_set"):
                continue
            base = slug[:-len("_set")]
            for suffix, label in COMPONENT_LABELS.items():
                part_slug = "%s_%s" % (base, suffix)
                if part_slug not in self.by_slug:
                    continue
                generated = alias_key + label
                if generated not in self.aliases:
                    self.aliases[generated] = part_slug
        self._searchable: List[Tuple[Item, str, str, str]] = [
            (item,
             _normalize(item.name),
             _normalize(item.name_en),
             item.slug.replace("_", " "))
            for item in self.items
        ]

    def __len__(self) -> int:
        return len(self.items)

    def get(self, slug: str) -> Optional[Item]:
        return self.by_slug.get(slug)

    def search(self, query: str, category: Optional[str] = None,
               limit: int = 5) -> List[SearchHit]:
        normalized = _normalize(query)
        if not normalized:
            return []
        compact = normalized.replace(" ", "")
        alias_slug = self.aliases.get(compact)

        # “绝路p”“mesa p”这类简写自动展开成 prime
        variants = [normalized]
        if compact.endswith("p") and len(compact) > 1:
            variants.append(normalized[:-1].rstrip() + " prime")

        results: List[SearchHit] = []
        for item, name_zh, name_en, slug_text in self._searchable:
            if not item.matches_category(category):
                continue
            score = 0.0
            for variant in variants:
                score = max(score, self._score(
                    item, variant, variant.replace(" ", ""),
                    name_zh, name_en, slug_text, alias_slug))
            if score > 0:
                results.append(SearchHit(score, item))

        results.sort(key=lambda hit: (-hit.score, len(hit.item.name), hit.item.slug))
        return results[:limit]

    def _score(self, item: Item, query: str, compact: str, name_zh: str,
               name_en: str, slug_text: str, alias_slug: Optional[str]) -> float:
        if alias_slug and item.slug == alias_slug:
            return 2000.0

        if not name_zh:
            name_zh = name_en
        compact_zh = name_zh.replace(" ", "")
        compact_en = name_en.replace(" ", "")

        if query in (name_zh, name_en) or compact in (compact_zh, compact_en):
            score = 1000.0
        elif compact == slug_text.replace(" ", ""):
            score = 980.0
        elif name_zh.startswith(query) or name_en.startswith(query):
            gap = abs(len(name_zh) - len(query))
            score = 900.0 - min(gap, 120)
        elif query in name_zh or query in name_en:
            gap = abs(len(name_zh) - len(query))
            score = 760.0 - min(gap, 120)
        else:
            tokens = query.split()
            haystack = " ".join((name_zh, name_en, slug_text))
            if tokens and all(token in haystack for token in tokens):
                score = 620.0 - min(len(name_zh), 200) * 0.05
            else:
                ratio = max(
                    SequenceMatcher(None, query, name_zh).ratio(),
                    SequenceMatcher(None, query, name_en).ratio(),
                )
                if ratio < 0.72:
                    return 0.0
                score = ratio * 480.0

        # 细节偏好：套装优先、蓝图/部件在未点名时降权
        if item.set_root or "set" in item.tag_set:
            score += 6.0
        if "blueprint" in item.tag_set and "blueprint" not in query and "蓝图" not in query:
            score -= 12.0
        if "component" in item.tag_set and not any(
                word in query for word in ("部件", "part", "component")):
            score -= 6.0
        return score


class MarketError(Exception):
    """市场数据不可用。"""


class MarketClient:
    def __init__(self, config: Dict[str, Any], http: Optional[HttpClient] = None,
                 logger: Optional[logging.Logger] = None,
                 cache_config: Optional[Dict[str, Any]] = None):
        self.cfg = config
        self.log = logger or LOGGER
        cache_cfg = cache_config or {}
        self.cache = TTLCache(cache_cfg, prefix="market")
        self.item_cache_seconds = int(cache_cfg.get("item_seconds", 3600))
        self.order_cache_seconds = int(cache_cfg.get("orders_seconds", 60))
        self.stats_cache_seconds = int(cache_cfg.get("stats_seconds", 900))
        self.base_url = str(config["base_url"]).rstrip("/")
        self.language = str(config.get("language", "zh-hans"))
        self.platform = str(config.get("platform", "pc"))
        self.crossplay = bool(config.get("crossplay", True))
        self.cache_file = str(config.get("cache_file", "data/items_cache.json"))
        self.cache_ttl = int(config.get("cache_ttl_seconds", 43200))
        self.alias_file = str(config.get("alias_file", "data/aliases.json"))
        self.max_rps = float(config.get("max_requests_per_second", 3) or 0)

        self.http = http or HttpClient(
            user_agent=config.get("user_agent", "QQWarframeBot/1.0"),
            timeout=config.get("timeout_seconds", 15),
            proxy=config.get("proxy", ""),
            retries=config.get("retries", 2),
            insecure_tls=bool(config.get("insecure_tls", False)),
            logger=self.log,
        )

        self._lock = threading.RLock()
        self._index: Optional[ItemIndex] = None
        self._fetched_at = 0.0
        self._retry_after = 0.0
        self._rate_lock = threading.Lock()
        self._recent_requests: Deque[float] = collections.deque()
        self._part_cache: Dict[Tuple[str, str], Tuple[float, List["PartQuote"]]] = {}

    # ------------------------------------------------------------------ 基础请求
    def _headers(self, platform: Optional[str] = None) -> Dict[str, str]:
        return {
            "Language": self.language,
            "Platform": platform or self.platform,
            "Crossplay": "true" if self.crossplay else "false",
        }

    def _get(self, path: str, platform: Optional[str] = None) -> Dict[str, Any]:
        self._throttle()
        url = self.base_url + "/" + path.lstrip("/")
        return self.http.get_json(url, headers=self._headers(platform))

    def _throttle(self) -> None:
        """warframe.market 限制 3 请求/秒，超出会返回 429。"""
        if self.max_rps <= 0:
            return
        with self._rate_lock:
            now = time.time()
            while self._recent_requests and now - self._recent_requests[0] > 1.0:
                self._recent_requests.popleft()
            if len(self._recent_requests) >= self.max_rps:
                wait = 1.0 - (now - self._recent_requests[0]) + 0.01
                if wait > 0:
                    time.sleep(wait)
                now = time.time()
                while self._recent_requests and now - self._recent_requests[0] > 1.0:
                    self._recent_requests.popleft()
            self._recent_requests.append(now)

    # ------------------------------------------------------------------ 索引
    @property
    def index(self) -> ItemIndex:
        return self.load_items()

    def refresh(self) -> int:
        """强制刷新物品索引（后台自动更新用）。"""
        return len(self.load_items(force=True).items)

    def cache_age(self) -> float:
        return max(0.0, time.time() - self._fetched_at) if self._fetched_at else -1.0

    def item_count(self) -> int:
        return len(self._index.items) if self._index is not None else 0

    def load_items(self, force: bool = False) -> ItemIndex:
        with self._lock:
            now = time.time()
            if not force and self._index is not None \
                    and now - self._fetched_at < self.cache_ttl:
                return self._index
            if not force and now < self._retry_after:
                if self._index is not None:
                    return self._index
                raise MarketError("物品索引刚刚加载失败，请稍后再试")

            stale_items: Optional[List[Item]] = None
            cached = self._read_cache() if self._index is None else None
            if not force and cached:
                if now - cached["fetched_at"] < self.cache_ttl:
                    self._index = self._build_index(cached["items"])
                    self._fetched_at = cached["fetched_at"]
                    self._warn_language_coverage()
                    return self._index
                stale_items = cached["items"]

            try:
                items = self._fetch_items()
            except Exception as exc:
                self.log.warning("拉取物品列表失败：%s", exc)
                fallback = stale_items or (
                    [item for item in (self._index.items if self._index else [])])
                if fallback:
                    self.log.warning("使用本地缓存兜底（%d 条）", len(fallback))
                    if self._index is None:
                        self._index = self._build_index(fallback)
                    self._retry_after = now + 300
                    return self._index
                self._retry_after = now + 60
                raise MarketError(self._friendly_error(exc))

            self._index = self._build_index(items)
            self._fetched_at = time.time()
            self._retry_after = 0.0
            self._write_cache(items, self._fetched_at)
            self._warn_language_coverage()
            return self._index

    def _build_index(self, items: Iterable[Item]) -> ItemIndex:
        return ItemIndex(list(items), self._load_aliases())

    def _warn_language_coverage(self) -> None:
        if not self._index or self.language not in ("zh-hans", "zh-hant"):
            return
        localized = sum(1 for item in self._index.items
                        if item.name and item.name != item.name_en)
        if localized == 0:
            self.log.warning(
                "接口没有返回中文名（Language: %s）。中文搜索将退化为英文匹配，"
                "请检查网络或代理。", self.language)
        else:
            self.log.info("物品索引就绪：%d 条，其中 %d 条为中文名",
                          len(self._index), localized)

    def _load_aliases(self) -> Dict[str, str]:
        aliases = dict(DEFAULT_ALIASES)
        if not self.alias_file or not os.path.isfile(self.alias_file):
            return aliases
        try:
            with open(self.alias_file, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                aliases.update({str(k): str(v) for k, v in raw.items()
                                if not str(k).startswith("_")})
        except (OSError, ValueError) as exc:
            self.log.warning("读取别名文件失败（%s）：%s", self.alias_file, exc)
        return aliases

    def reload_aliases(self) -> int:
        """只重载别名，不重新请求物品索引；返回别名数量。"""
        with self._lock:
            if self._index is not None:
                self._index = ItemIndex(self._index.items, self._load_aliases())
            return len(self._load_aliases())

    def _read_cache(self) -> Optional[Dict[str, Any]]:
        if not self.cache_file or not os.path.isfile(self.cache_file):
            return None
        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if raw.get("language") != self.language:
                return None
            items = [Item.from_dict(row) for row in raw.get("items") or []]
            if not items:
                return None
            return {"items": items, "fetched_at": float(raw.get("fetched_at") or 0)}
        except (OSError, ValueError, TypeError) as exc:
            self.log.warning("读取物品缓存失败：%s", exc)
            return None

    def _write_cache(self, items: Sequence[Item], fetched_at: float) -> None:
        if not self.cache_file:
            return
        directory = os.path.dirname(os.path.abspath(self.cache_file))
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {
            "fetched_at": fetched_at,
            "language": self.language,
            "platform": self.platform,
            "count": len(items),
            "items": [item.to_dict() for item in items],
        }
        temp_path = self.cache_file + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_path, self.cache_file)
        except OSError as exc:
            self.log.warning("写入物品缓存失败：%s", exc)
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def _fetch_items(self) -> List[Item]:
        self.log.info("正在从 warframe.market 拉取物品列表…")
        data = self._get("/v2/items")
        rows = data.get("data") if isinstance(data, dict) else data
        if rows is None and isinstance(data, dict):
            rows = (data.get("payload") or {}).get("items")
        if not isinstance(rows, list):
            raise MarketError("接口返回格式异常：缺少物品数组")
        items = [item for item in (self._parse_short(row) for row in rows) if item]
        if not items:
            raise MarketError("接口返回了 0 条物品")
        return items

    def _parse_short(self, raw: Dict[str, Any]) -> Optional[Item]:
        if not isinstance(raw, dict):
            return None
        slug = _clean(raw.get("slug") or raw.get("url_name"))
        if not slug:
            return None

        i18n = raw.get("i18n") or {}
        localized = i18n.get(self.language) or i18n.get("zh-hans") or i18n.get("zh-hant") or {}
        english = i18n.get("en") or {}

        name_en = _clean(english.get("name") or raw.get("item_name"))
        name = _clean(localized.get("name") or name_en or slug.replace("_", " "))
        thumb = _clean(localized.get("thumb") or english.get("thumb")
                       or raw.get("thumb") or "")
        tags = tuple(str(tag).lower() for tag in (raw.get("tags") or []))

        return Item(
            slug=slug,
            name=name,
            name_en=name_en or name,
            item_id=_clean(raw.get("id")),
            tags=tags,
            ducats=_as_int(raw.get("ducats")),
            mastery_rank=_as_int(raw.get("reqMasteryRank", raw.get("mr"))),
            trading_tax=_as_int(raw.get("tradingTax", raw.get("trading_tax"))),
            thumb=thumb,
            tradable=bool(raw.get("tradable", True)),
            set_root=bool(raw.get("setRoot", False)),
            max_rank=_as_int(raw.get("maxRank", raw.get("max_rank"))),
            set_parts=tuple(str(value) for value in (raw.get("setParts") or [])),
        )

    # ------------------------------------------------------------------ 详情
    def item_detail(self, slug: str) -> Optional[Item]:
        key = ("item", slug)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        item = self._item_detail_uncached(slug)
        if item is not None:
            self.cache.set(key, item, self.item_cache_seconds)
        return item

    def _item_detail_uncached(self, slug: str) -> Optional[Item]:
        try:
            data = self._get("/v2/item/%s" % slug)
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise MarketError(self._friendly_error(exc))
        raw = data.get("data")
        if not isinstance(raw, dict):
            raw = (data.get("payload") or {}).get("item")
        if not isinstance(raw, dict):
            return None
        item = self._parse_short(raw)
        if not item:
            return None
        i18n = raw.get("i18n") or {}
        localized = i18n.get(self.language) or i18n.get("zh-hans") or i18n.get("zh-hant") or {}
        english = i18n.get("en") or {}
        item.description = _clean(localized.get("description") or english.get("description"))
        item.wiki_link = _clean(localized.get("wikiLink") or english.get("wikiLink"))
        return item

    # ------------------------------------------------------------------ 订单
    def top_orders(self, slug: str, limit: int = 5,
                   platform: Optional[str] = None,
                   force: bool = False) -> Tuple[List[Order], List[Order]]:
        key = ("orders", slug, int(limit), platform)
        if not force:
            cached = self.cache.get(key)
            if cached is not None:
                return list(cached[0]), list(cached[1])
        result = self._top_orders_uncached(slug, limit, platform)
        self.cache.set(key, result, self.order_cache_seconds)
        return list(result[0]), list(result[1])

    def _top_orders_uncached(self, slug: str, limit: int = 5,
                             platform: Optional[str] = None
                             ) -> Tuple[List[Order], List[Order]]:
        try:
            data = self._get("/v2/orders/item/%s/top" % slug, platform=platform)
        except HttpError as exc:
            if exc.status in (404, 500):
                return self._orders_fallback(slug, limit, platform)
            raise MarketError(self._friendly_error(exc))

        payload = data.get("data") if isinstance(data, dict) else None
        if not isinstance(payload, dict) or not ("sell" in payload or "buy" in payload):
            return self._orders_fallback(slug, limit, platform)

        sell = [Order.from_api(row, "sell") for row in (payload.get("sell") or [])]
        buy = [Order.from_api(row, "buy") for row in (payload.get("buy") or [])]
        sell = [order for order in sell if order.platinum > 0]
        buy = [order for order in buy if order.platinum > 0]
        sell.sort(key=lambda order: order.platinum)
        buy.sort(key=lambda order: -order.platinum)
        return sell[:limit], buy[:limit]

    def _orders_fallback(self, slug: str, limit: int,
                         platform: Optional[str] = None) -> Tuple[List[Order], List[Order]]:
        try:
            data = self._get("/v2/orders/item/%s" % slug, platform=platform)
        except HttpError as exc:
            raise MarketError(self._friendly_error(exc))
        rows = data.get("data") if isinstance(data, dict) else data
        if rows is None and isinstance(data, dict):
            rows = (data.get("payload") or {}).get("orders")
        if not isinstance(rows, list):
            raise MarketError("订单接口返回格式异常")
        sell, buy = [], []
        for row in rows:
            order = Order.from_api(row)
            if order.side not in ("sell", "buy") or order.platinum <= 0:
                continue
            if (order.status or "").lower() == "offline":
                continue
            (sell if order.side == "sell" else buy).append(order)
        sell.sort(key=lambda order: order.platinum)
        buy.sort(key=lambda order: -order.platinum)
        return sell[:limit], buy[:limit]

    # ------------------------------------------------------------------ 行情
    def statistics(self, slug: str, platform: Optional[str] = None,
                   force: bool = False) -> MarketStats:
        key = ("stats", slug, platform)
        if force:
            result = self._statistics_uncached(slug, platform)
            self.cache.set(key, result, self.stats_cache_seconds)
            return result
        return self.cache.get_or_set(
            key, lambda: self._statistics_uncached(slug, platform),
            self.stats_cache_seconds)

    def _statistics_uncached(self, slug: str,
                             platform: Optional[str] = None) -> MarketStats:
        try:
            data = self._get("/v1/items/%s/statistics" % slug, platform=platform)
        except HttpError as exc:
            if exc.status == 404:
                return MarketStats()
            raise MarketError(self._friendly_error(exc))

        payload = (data.get("payload") or data.get("data") or {})
        closed = payload.get("statistics_closed") or {}

        rows48 = [row for row in (closed.get("48hours") or [])
                  if _as_float(row.get("volume")) > 0]
        sell48 = [row for row in rows48
                  if (row.get("order_type") or "sell").lower() == "sell"]
        h48 = _aggregate(sell48 or rows48)

        rows90 = [row for row in (closed.get("90days") or [])
                  if _as_float(row.get("volume")) > 0]
        sell90 = [row for row in rows90
                  if (row.get("order_type") or "").lower() == "sell"]
        d90 = _aggregate(sell90 or rows90)
        history = _parse_history(closed.get("90days") or [])

        return MarketStats(h48=h48, d90=d90, updated_at=time.time(),
                           history=history)

    # ------------------------------------------------------------------ 套装拆件
    def set_breakdown(self, item: Item, platform: Optional[str] = None,
                      ttl: int = 300) -> List["PartQuote"]:
        """把套装拆成部件并分别查最低卖价 / 最高收价。

        每个部件一次 order 请求，结果缓存 5 分钟，避免群里反复查同一套。
        """
        detail = item
        if not detail.set_parts:
            detail = self.item_detail(item.slug) or item
        if not detail.set_parts:
            return []

        key = (detail.slug, platform or self.platform)
        cached = self._part_cache.get(key)
        if cached and time.time() - cached[0] < ttl:
            return list(cached[1])

        index = self.load_items()
        quotes: List[PartQuote] = []
        for part_id in detail.set_parts:
            part = index.by_id.get(part_id)
            if part is None or part.slug == detail.slug:
                continue
            try:
                sell, buy = self.top_orders(part.slug, limit=1, platform=platform)
            except MarketError as exc:
                self.log.warning("部件报价失败 %s：%s", part.slug, exc)
                sell, buy = [], []
            quotes.append(PartQuote(
                slug=part.slug,
                name=part.name,
                sell=sell[0].platinum if sell else None,
                buy=buy[0].platinum if buy else None,
            ))
        quotes.sort(key=lambda quote: quote.name)
        self._part_cache[key] = (time.time(), quotes)
        return quotes

    # ------------------------------------------------------------------ 组合
    def lookup(self, query: str, category: Optional[str] = None,
               limit: int = 5) -> List[SearchHit]:
        return self.load_items().search(query, category=category, limit=limit)

    # ------------------------------------------------------------------ 错误
    def _friendly_error(self, exc: Exception) -> str:
        if isinstance(exc, HttpError):
            if exc.status == 403:
                return "市场接口拒绝访问（403）。可能是网络/代理问题，或触发了风控，请稍后再试。"
            if exc.status == 404:
                return "市场接口找不到该物品（404）。"
            if exc.status == 429:
                return "市场接口请求过于频繁（429），请稍后再试。"
            if exc.status == 0:
                return "无法连接 warframe.market（%s）。请检查网络，或在配置里设置代理。" % exc
            return "市场接口异常（HTTP %d），请稍后再试。" % exc.status
        return "市场数据获取失败：%s" % exc


def _aggregate(rows: Sequence[Dict[str, Any]]) -> Optional[StatSummary]:
    if not rows:
        return None
    volume = 0
    weighted_avg = 0.0
    pairs: List[Tuple[float, float]] = []
    low = None
    high = None
    for row in rows:
        vol = _as_float(row.get("volume"))
        if vol <= 0:
            continue
        volume += vol
        weighted_avg += _as_float(row.get("avg_price")) * vol
        pairs.append((_as_float(row.get("median")), vol))
        low = _as_float(row.get("min_price")) if low is None \
            else min(low, _as_float(row.get("min_price")))
        high = _as_float(row.get("max_price")) if high is None \
            else max(high, _as_float(row.get("max_price")))
    if volume <= 0:
        return None

    pairs.sort(key=lambda pair: pair[0])
    half = volume / 2.0
    cumulative = 0.0
    median = pairs[-1][0]
    for value, vol in pairs:
        cumulative += vol
        if cumulative >= half:
            median = value
            break

    return StatSummary(
        volume=int(volume),
        avg=weighted_avg / volume,
        median=median,
        min_price=low or 0.0,
        max_price=high or 0.0,
    )


def _parse_history(rows: Sequence[Dict[str, Any]]) -> List[PricePoint]:
    """把 statistics_closed.90days 解析成逐日价格点（约 90 天）。"""
    by_date: Dict[str, PricePoint] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date = str(row.get("datetime") or "")[:10]
        if len(date) != 10 or date[4] != "-" or date[7] != "-":
            continue
        if (row.get("order_type") or "sell").lower() != "sell":
            continue
        volume = int(_as_float(row.get("volume")))
        if volume <= 0:
            continue
        avg = _as_float(row.get("avg_price"))
        median = _as_float(row.get("median"))
        if avg <= 0:
            avg = median or _as_float(row.get("closed_price"))
        point = PricePoint(
            date=date,
            avg=avg,
            volume=volume,
            min_price=_as_float(row.get("min_price")),
            max_price=_as_float(row.get("max_price")),
            median=median,
        )
        existing = by_date.get(date)
        if existing is None or point.volume > existing.volume:
            by_date[date] = point
    return sorted(by_date.values(), key=lambda item: item.date)
