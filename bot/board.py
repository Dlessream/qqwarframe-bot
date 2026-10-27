"""warframe.market 价格榜：金/银/铜垃圾、Prime 战甲、零洗紫卡。

WM 只开放「逐件」接口，没有一次拿全市场价格的批量接口：

    GET /v2/orders/item/{slug}/top          单件买卖单（金/银垃圾、战甲）
    GET /v1/auctions/search?type=riven…     单把武器的紫卡拍卖

所以这里做「后台滚动扫描 + 本地快照」：一个后台线程按 TTL 循环刷新，
每次只发一个请求（默认 1 秒 1 个，远低于 WM 的 3 次/秒上限），
命令层只读快照，绝不会因为查询把群消息卡住。

杜卡特分级（与游戏内一致）：
    100 = 金垃圾、45 = 银垃圾、15 = 铜垃圾、65 = 假金、25 = 假银
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

from .net import HttpClient, HttpError
from .riven import ATTRIBUTE_NAMES

LOGGER = logging.getLogger("qqwarframe.board")

# 杜卡特 -> 俗称（顺序即扫描优先级）
JUNK_RARITIES: Tuple[int, ...] = (100, 45, 15, 65, 25)
JUNK_LABELS: Dict[int, str] = {
    100: "金垃圾",
    45: "银垃圾",
    15: "铜垃圾",
    65: "假金垃圾",
    25: "假银垃圾",
}

_KIND_JUNK = "junk"
_KIND_FRAME = "frames"
_KIND_RIVEN = "riven"


def junk_ducats(label: str) -> Optional[int]:
    """「金垃圾」/「金」/「100」 → 杜卡特数值。"""
    text = str(label or "").strip()
    short = text[:-2] if text.endswith("垃圾") else text
    for ducats, name in JUNK_LABELS.items():
        if text in (name, str(ducats)) or (short and short == name.replace("垃圾", "")):
            return ducats
    return None


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


def _riven_flags(entry: Dict[str, Any]) -> int:
    """紫卡缓存的标志位：bit0=老缓存待重扫，bit1=已按新接口完整抓过。"""
    return (1 if entry.get("v") else 0) | (2 if entry.get("f") else 0)


@dataclass
class BoardQuote:
    """一件物品的当前报价快照。"""

    slug: str
    price: int = 0          # 最低在售（在线/游戏中优先）
    buy: int = 0            # 最高收购
    orders: int = 0         # 最低价挂单数量
    updated: float = 0.0
    ducats: int = 0


@dataclass
class RivenQuote:
    """一把武器的「最贵零洗紫卡」快照。"""

    slug: str
    price: int = 0
    buyout: bool = False
    seller: str = ""
    attributes: Tuple[str, ...] = ()
    created: str = ""
    updated: float = 0.0
    rank: int = 0


def _online(order: Dict[str, Any]) -> bool:
    status = str((order.get("user") or {}).get("status") or "").lower()
    return status in ("ingame", "online")


def parse_top_orders(payload: Dict[str, Any]) -> Tuple[int, int, int]:
    """解析 /v2/orders/item/{slug}/top：返回 (最低在售, 最高收购, 同价挂单数)。

    离线挂单不参与最低价，除非没有任何在线卖家——这样不会把「人不在」
    的低价当成真实可成交价。
    """
    sell = [row for row in (payload.get("sell") or []) if _as_int(row.get("platinum")) > 0]
    buy = [row for row in (payload.get("buy") or []) if _as_int(row.get("platinum")) > 0]
    pool = [row for row in sell if _online(row)] or sell
    price = min((_as_int(row.get("platinum")) for row in pool), default=0)
    orders = sum(1 for row in pool if _as_int(row.get("platinum")) == price) if price else 0
    best_buy = max((_as_int(row.get("platinum")) for row in buy), default=0)
    return price, best_buy, orders


def _fmt_attr(attr: Dict[str, Any], names: Optional[Dict[str, str]] = None) -> str:
    url_name = str(attr.get("url_name") or "")
    table = names or ATTRIBUTE_NAMES
    label = table.get(url_name) or ATTRIBUTE_NAMES.get(url_name) \
        or url_name.replace("_", " ")
    value = abs(_as_float(attr.get("value")))
    positive = bool(attr.get("positive", True))
    if not value:
        return ""
    return "%s %s%.1f%%" % (label, "+" if positive else "-", value)


def riven_candidates(rows: Iterable[Dict[str, Any]],
                     names: Optional[Dict[str, str]] = None,
                     max_price: int = 50000) -> List[RivenQuote]:
    """把一次拍卖查询结果整理成候选列表，按价格从高到低。

    口径（按玩家习惯的「武器零洗最贵的卡」）：

    - 只认**零洗**（re_rolls = 0），洗过的卡不算；
    - **一口价优先**：只有起拍价的挂单是「竞价地板」，不是能直接买的价格，
      所以只要该武器存在任何一口价挂单，就只用一口价候选；
    - **满级优先**：0 级卡只显示几十分之一的词条数值（例如暴伤 +7%），
      价格也不代表词条价值，只要该武器有 8 级挂单就只从 8 级里挑；
    - max_price 是绝对兜底，用来挡住明显挂错的天价单（0 表示不限）。
    """
    quotes: List[RivenQuote] = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("closed") or row.get("private"):
            continue
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        if _as_int(item.get("re_rolls")) != 0:
            continue
        buyout_price = _as_int(row.get("buyout_price"))
        starting = _as_int(row.get("starting_price"))
        price = buyout_price or starting
        if price <= 0 or (max_price and price > max_price):
            continue
        owner = row.get("owner") or {}
        attributes = [text for text in (_fmt_attr(attr, names)
                                        for attr in (item.get("attributes") or [])
                                        if isinstance(attr, dict)) if text]
        quotes.append(RivenQuote(
            slug=str(item.get("url_name") or item.get("slug") or ""),
            price=price,
            buyout=bool(buyout_price),
            seller=str(owner.get("ingame_name") or owner.get("ingameName") or "?"),
            attributes=tuple(attributes),
            created=str(row.get("created") or "")[:19],
            rank=_as_int(item.get("mod_rank")),
        ))
    quotes.sort(key=lambda quote: (-quote.price, quote.slug))
    buyouts = [quote for quote in quotes if quote.buyout]
    if buyouts:
        quotes = buyouts
    maxed = [quote for quote in quotes if quote.rank >= 8]
    if maxed:
        quotes = maxed
    return quotes


def parse_riven_auctions(rows: Iterable[Dict[str, Any]],
                         names: Optional[Dict[str, str]] = None,
                         max_price: int = 50000) -> Optional[RivenQuote]:
    """从拍卖列表里挑最贵的零洗（re_rolls = 0）紫卡。"""
    quotes = riven_candidates(rows, names=names, max_price=max_price)
    return quotes[0] if quotes else None


class BoardClient:
    """后台滚动扫描 + 本地价格快照。"""

    def __init__(self, market, riven=None, config: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None,
                 http: Optional[HttpClient] = None):
        self.market = market
        self.riven = riven
        self.log = logger or LOGGER
        config = config or {}
        self.enabled = bool(config.get("enabled", True))
        self.cache_file = str(config.get("cache_file", "data/board_cache.json"))
        self.base_interval = max(0.5, float(config.get("request_interval", 5.0) or 5.0))
        self.max_interval = max(self.base_interval,
                                float(config.get("max_request_interval", 20.0) or 20.0))
        self.interval = self.base_interval
        raw_rarities = config.get("junk_rarities")
        if isinstance(raw_rarities, (list, tuple)) and raw_rarities:
            self.rarities = tuple(ducats for ducats in JUNK_RARITIES
                                  if ducats in {_as_int(value) for value in raw_rarities})
        else:
            self.rarities = tuple(ducats for ducats in JUNK_RARITIES
                                  if ducats in (100, 45))
        self.junk_ttl = max(300, int(config.get("junk_seconds", 7200)))
        self.frame_ttl = max(300, int(config.get("frame_seconds", 7200)))
        self.riven_ttl = max(600, int(config.get("riven_seconds", 28800)))
        self.max_riven_price = max(0, int(config.get("max_riven_price", 50000)))
        # 钓鱼价识别：同一个精确价格刷在 >= N 把武器上，或同一个卖家在 >= M 把
        # 武器上挂高价零洗，基本就是「议价请私聊」的占位单，不参与排名。
        self.bait_price_floor = max(0, int(config.get("bait_price_floor", 3000)))
        self.bait_price_weapons = max(2, int(config.get("bait_price_weapons", 5)))
        self.bait_price_ratio = max(1.0, float(config.get("bait_price_ratio", 2.0) or 2.0))
        self.bait_seller_weapons = max(2, int(config.get("bait_seller_weapons", 4)))
        self.riven_keep = max(3, int(config.get("riven_candidates_keep", 12)))
        self.scan_junk = bool(config.get("junk_scan", True))
        self.scan_riven = bool(config.get("riven_scan", True))
        self.ban_seconds = max(30, int(config.get("ban_seconds", 180)))
        self.window_seconds = max(60, int(config.get("window_seconds", 300)))
        self.max_per_window = max(5, int(config.get("max_requests_per_window", 38)))
        self.save_every = max(5, int(config.get("save_every", 25)))

        market_cfg = getattr(market, "cfg", {}) or {}
        self.base_url = str(getattr(market, "base_url", "") or market_cfg.get("base_url")
                            or "https://api.warframe.market").rstrip("/")
        self.http = http or HttpClient(
            user_agent=market_cfg.get("user_agent", "QQWarframeBot/1.0"),
            timeout=market_cfg.get("timeout_seconds", 15),
            proxy=market_cfg.get("proxy", ""),
            retries=0,  # 429 由本模块自己退避，避免 HttpClient 内部连打
            logger=self.log,
        )

        self._lock = threading.RLock()
        self._quotes: Dict[str, Dict[str, Any]] = {
            _KIND_JUNK: {}, _KIND_FRAME: {}, _KIND_RIVEN: {},
        }
        self._targets: Dict[str, List[str]] = {_KIND_JUNK: [], _KIND_FRAME: [], _KIND_RIVEN: []}
        self._junk_by_ducats: Dict[int, List[str]] = {key: [] for key in JUNK_RARITIES}
        self._cursor: Dict[str, int] = {_KIND_JUNK: 0, _KIND_FRAME: 0, _KIND_RIVEN: 0}
        self._targets_at = 0.0
        self._attr_names: Optional[Dict[str, str]] = None
        self._legacy_riven = 0
        self._ban_until = 0.0
        self._ok_streak = 0
        self._recent: Deque[float] = deque()
        self._dirty = 0
        self._saved_at = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.requests = 0
        self.errors = 0
        self.last_error = ""
        self.started_at = 0.0
        self._load()

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if not self.enabled:
            self.log.info("价格榜扫描已关闭（board.enabled = false）")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self.started_at = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="board-scan", daemon=True)
        self._thread.start()
        self.log.info("价格榜扫描已启动：间隔 %.1fs，垃圾 %ds / 战甲 %ds / 紫卡 %ds",
                      self.interval, self.junk_ttl, self.frame_ttl, self.riven_ttl)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._save(force=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._ban_until > time.time():
                    self._stop.wait(min(60.0, self._ban_until - time.time()) + 1)
                    continue
                delay = self._budget_delay()
                if delay > 0:
                    self._stop.wait(delay)
                    continue
                if self.step():
                    self._stop.wait(self.interval + random.uniform(0, 0.2))
                else:
                    self._stop.wait(20)
            except Exception as exc:  # pragma: no cover - 防御
                self.errors += 1
                self.last_error = str(exc)
                self.log.exception("价格榜扫描出错：%s", exc)
                self._stop.wait(30)

    def _budget_delay(self) -> float:
        """滑动窗口限速：WM 的 Cloudflare 大约 5 分钟内几十次就会 1015。"""
        now = time.time()
        while self._recent and now - self._recent[0] > self.window_seconds:
            self._recent.popleft()
        if len(self._recent) < self.max_per_window:
            return 0.0
        return max(1.0, self.window_seconds - (now - self._recent[0]) + 0.5)

    # ------------------------------------------------------------------ 扫描
    def step(self) -> bool:
        """刷新一件数据；返回是否真的发了请求。"""
        target = self._next_target()
        if target is None:
            return False
        kind, slug = target
        if kind == _KIND_RIVEN:
            self._fetch_riven(slug)
        else:
            self._fetch_top(kind, slug)
        self.requests += 1
        self._recent.append(time.time())
        self._dirty += 1
        if self._dirty >= self.save_every or time.time() - self._saved_at > 300:
            self._save()
        return True

    def _next_target(self) -> Optional[Tuple[str, str]]:
        self._ensure_targets()
        now = time.time()
        candidates: List[Tuple[float, str, List[str], int]] = []
        plans = (
            (_KIND_JUNK, self._targets[_KIND_JUNK], self.junk_ttl),
            (_KIND_FRAME, self._targets[_KIND_FRAME], self.frame_ttl),
            (_KIND_RIVEN, self._targets[_KIND_RIVEN], self.riven_ttl),
        )
        for kind, slugs, ttl in plans:
            if not slugs:
                continue
            stale = self._stale_count(kind, slugs, ttl, now)
            if stale:
                candidates.append((stale / float(len(slugs)), kind, slugs, ttl))
        if not candidates:
            return None
        # 逾期比例最高的优先，天然把垃圾/战甲/紫卡轮起来扫描
        candidates.sort(key=lambda row: -row[0])
        _ratio, kind, slugs, ttl = candidates[0]
        slug = self._next_stale(kind, slugs, ttl, now)
        return (kind, slug) if slug else None

    def _stale_count(self, kind: str, slugs: Sequence[str], ttl: int, now: float) -> int:
        quotes = self._quotes[kind]
        return sum(1 for slug in slugs
                   if slug not in quotes or now - quotes[slug].get("t", 0) >= ttl)

    def _next_stale(self, kind: str, slugs: Sequence[str], ttl: int,
                    now: float) -> Optional[str]:
        quotes = self._quotes[kind]
        count = len(slugs)
        cursor = self._cursor.get(kind, 0) % count
        order = [(cursor + offset) % count for offset in range(count)]
        if kind == _KIND_RIVEN:
            # 老格式（只有一条候选）先补：榜单上那些可疑的高价就藏在这里
            for position in order:
                entry = quotes.get(slugs[position])
                if entry is None or (entry.get("v")
                                     and len(entry.get("c") or []) <= 1):
                    self._cursor[kind] = (position + 1) % count
                    return slugs[position]
        for position in order:
            slug = slugs[position]
            entry = quotes.get(slug)
            if entry is None or now - entry.get("t", 0) >= ttl:
                self._cursor[kind] = (position + 1) % count
                return slug
        return None

    def _ensure_targets(self) -> None:
        with self._lock:
            ready = bool(self._targets[_KIND_JUNK] or self._targets[_KIND_FRAME])
            if ready and time.time() - self._targets_at < 21600:
                return
        # 拉索引/武器表可能要几秒，放到锁外，别卡住查询线程。
        try:
            index = self.market.index
        except Exception as exc:
            self.log.info("价格榜等待物品索引：%s", exc)
            return
        junk: List[str] = []
        frames: List[str] = []
        by_ducats: Dict[int, List[str]] = {key: [] for key in self.rarities}
        for item in index.items:
            tags = item.tag_set
            if "prime" not in tags:
                continue
            if "set" in tags:
                if "warframe" in tags:
                    frames.append(item.slug)
                continue
            if "mod" in tags or "arcane_enhancement" in tags:
                continue
            if item.ducats in by_ducats and ("component" in tags or "blueprint" in tags):
                junk.append(item.slug)
                by_ducats[item.ducats].append(item.slug)
        # 各档位轮流排：首次部署/重启后金垃圾和银垃圾都能在十几分钟内出榜单，
        # 而不是把 345 件金垃圾扫完才轮到银垃圾。
        lanes = [sorted(by_ducats[key]) for key in self.rarities if by_ducats.get(key)]
        depth = max((len(lane) for lane in lanes), default=0)
        junk = [lane[level] for level in range(depth) for lane in lanes
                if level < len(lane)]
        frames.sort()
        riven_targets: List[str] = []
        if self.scan_riven and self.riven is not None:
            try:
                weapons = self.riven.weapons()
                # 倾向越低 = 越主流的武器，紫卡越贵；
                # 先扫这些，榜单在前十几分钟就有参考价值。
                riven_targets = sorted(
                    weapons.keys(),
                    key=lambda slug: (weapons[slug].disposition_raw or 9.0, slug))
            except Exception as exc:
                self.log.warning("价格榜读取紫卡武器失败：%s", exc)
        with self._lock:
            self._targets[_KIND_JUNK] = junk if self.scan_junk else []
            self._targets[_KIND_FRAME] = frames
            self._targets[_KIND_RIVEN] = riven_targets
            self._junk_by_ducats = by_ducats
            self._targets_at = time.time()
        self.log.info("价格榜目标：垃圾 %d 件 / 战甲 %d 套 / 紫卡武器 %d 把",
                      len(junk), len(frames), len(riven_targets))

    def refresh_targets(self) -> None:
        with self._lock:
            self._targets_at = 0.0
        self._ensure_targets()

    # ------------------------------------------------------------------ 抓取
    def _get_json(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            data = self.http.get_json(self.base_url + path, headers=self.market._headers())
        except HttpError as exc:
            if exc.status == 429:
                # WM 用 Cloudflare 限流，连续请求三十来次就会 1015；
                # 这里自适应放慢，稳定后再慢慢恢复。
                self.interval = min(self.max_interval, self.interval * 1.6)
                self._ok_streak = 0
                self._ban_until = time.time() + self.ban_seconds
                self.log.warning("价格榜被限流（429），暂停 %ds，之后每 %.1fs 一次",
                                 self.ban_seconds, self.interval)
                return None
            self.errors += 1
            self.last_error = str(exc)
            if exc.status not in (404,):
                self.log.info("价格榜请求失败 %s：%s", path, exc)
            return {}
        except Exception as exc:  # pragma: no cover - 防御
            self.errors += 1
            self.last_error = str(exc)
            self.log.warning("价格榜请求异常 %s：%s", path, exc)
            return None
        self._ok_streak += 1
        if self._ok_streak >= 200 and self.interval > self.base_interval:
            self.interval = max(self.base_interval, round(self.interval * 0.9, 2))
            self._ok_streak = 0
        return data if isinstance(data, dict) else {}

    def _fetch_top(self, kind: str, slug: str) -> None:
        data = self._get_json("/v2/orders/item/%s/top" % slug)
        if data is None:
            return
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        price, buy, orders = parse_top_orders(payload or {})
        with self._lock:
            previous = self._quotes[kind].get(slug) or {}
            self._quotes[kind][slug] = {
                "p": price or _as_int(previous.get("p")),
                "b": buy or _as_int(previous.get("b")),
                "n": orders,
                "t": time.time(),
            }

    def _fetch_riven(self, slug: str) -> None:
        path = ("/v1/auctions/search?type=riven&weapon_url_name=%s&sort_by=price_desc"
                % slug)
        data = self._get_json(path)
        if data is None:
            return
        payload = data.get("payload") or data.get("data") or {}
        rows = payload.get("auctions") if isinstance(payload, dict) else payload
        quotes = riven_candidates(rows or [], names=self._attribute_names(),
                                 max_price=self.max_riven_price)
        with self._lock:
            if not quotes:
                # 接口通了但一张合格的零洗卡都没有：记成「已抓过、无报价」，
                # 别继续拿老缓存里的可疑单条价格充数。
                if slug in self._quotes[_KIND_RIVEN]:
                    self._quotes[_KIND_RIVEN][slug] = {
                        "t": time.time(), "f": 1, "c": []}
                return
            # 存多个候选而不是只存冠军：冠军可能是钓鱼价，查询时再往下顺延。
            # f=1 表示这条是按新接口完整抓过的（候选已补全），
            # 即使只抓到 1 条也说明市场就这么多，可以照价上榜。
            self._quotes[_KIND_RIVEN][slug] = {
                "t": time.time(),
                "f": 1,
                "c": [[quote.price, quote.rank, quote.seller,
                       list(quote.attributes[:6]), quote.created,
                       1 if quote.buyout else 0]
                      for quote in quotes[:self.riven_keep]],
            }

    # ------------------------------------------------------------------ 查询
    def _attribute_names(self) -> Dict[str, str]:
        if self._attr_names is not None:
            return self._attr_names
        names: Dict[str, str] = dict(ATTRIBUTE_NAMES)
        if self.riven is not None:
            try:
                names.update(self.riven.attributes())
            except Exception as exc:  # 词条表拿不到不影响主流程
                self.log.info("价格榜读取紫卡词条失败：%s", exc)
        self._attr_names = names
        return names

    def item_name(self, slug: str) -> str:
        try:
            item = self.market.index.get(slug)
        except Exception:
            item = None
        if item is not None and item.name:
            return item.name
        return slug.replace("_", " ")

    def junk_quotes(self, ducats: int) -> List[BoardQuote]:
        """该杜卡特档位的所有已扫描部件，按最低售价从低到高。"""
        slugs = list(self._junk_by_ducats.get(ducats) or [])
        if not slugs:
            self._ensure_targets()
            slugs = list(self._junk_by_ducats.get(ducats) or [])
        with self._lock:
            quotes = self._quotes[_KIND_JUNK]
            rows = [self._junk_quote(slug, quotes[slug], ducats)
                    for slug in slugs if slug in quotes]
        rows = [row for row in rows if row.price > 0]
        rows.sort(key=lambda row: (row.price, row.slug))
        return rows

    def _junk_quote(self, slug: str, entry: Dict[str, Any], ducats: int) -> BoardQuote:
        return BoardQuote(slug=slug, price=_as_int(entry.get("p")),
                          buy=_as_int(entry.get("b")), orders=_as_int(entry.get("n")),
                          updated=_as_float(entry.get("t")), ducats=ducats)

    def junk_stats(self, ducats: int) -> Dict[str, Any]:
        rows = self.junk_quotes(ducats)
        prices = sorted(row.price for row in rows)
        return {
            "ducats": ducats,
            "label": JUNK_LABELS.get(ducats, "垃圾"),
            "ready": len(rows),
            "total": len(self._junk_by_ducats.get(ducats) or []),
            "min": prices[0] if prices else 0,
            "median": prices[len(prices) // 2] if prices else 0,
            "max": prices[-1] if prices else 0,
            "updated": max((row.updated for row in rows), default=0.0),
            "orders": sum(row.orders for row in rows[:10]),
        }

    def frame_quotes(self) -> List[BoardQuote]:
        return self._ranked(_KIND_FRAME)

    def riven_quotes(self) -> List[RivenQuote]:
        """每把武器取一张最贵的可信零洗卡，再按价格从高到低。

        可信 = 剔除钓鱼/占位价：WM 上大量挂单是「议价」性质的整数天价
        （50000、11111 刷在几十把武器上），不剔掉就会整榜都是它们。
        """
        with self._lock:
            entries = {slug: (_as_float(entry.get("t")),
                              [list(row) for row in (entry.get("c") or [])],
                              bool(entry.get("v")))
                       for slug, entry in self._quotes[_KIND_RIVEN].items()}
        if not entries:
            return []
        floor = self.bait_price_floor
        price_weapons: Counter = Counter()
        seller_weapons: Counter = Counter()
        for _ts, rows, _legacy in entries.values():
            high = [_as_int(row[0]) for row in rows if _as_int(row[0]) >= floor]
            for price in set(high):
                price_weapons[price] += 1
            for seller in {str(row[2] or "") for row in rows
                           if _as_int(row[0]) >= floor} - {""}:
                seller_weapons[seller] += 1
        quotes: List[RivenQuote] = []
        for slug, (updated, rows, legacy) in entries.items():
            prices = [_as_int(row[0]) for row in rows]
            if legacy and len(prices) == 1 and prices[0] >= floor:
                # 老缓存只有一条候选，判断不了是不是议价占位单；
                # 这种高价先不上榜，等重扫补全候选（下方 _next_stale 会优先扫它）
                continue
            for index, row in enumerate(rows):
                price = prices[index]
                if price <= 0:
                    continue
                below = prices[index + 1] if index + 1 < len(prices) else 0
                if self._looks_like_bait(price, str(row[2] or ""), below,
                                         price_weapons, seller_weapons):
                    continue
                quotes.append(RivenQuote(
                    slug=slug, price=price, rank=_as_int(row[1]),
                    buyout=bool(_as_int(row[5])), seller=str(row[2] or "?"),
                    attributes=tuple(row[3] or ()), created=str(row[4] or ""),
                    updated=updated))
                break
        quotes.sort(key=lambda quote: (-quote.price, quote.slug))
        return quotes

    def _looks_like_bait(self, price: int, seller: str, below: int,
                         price_weapons: Counter,
                         seller_weapons: Counter) -> bool:
        """判断一条挂单是不是「议价占位」而不是真实售价。

        两个信号：同一个精确价格刷在多把武器上；同一个卖家在多把武器上挂高价。
        为了不误伤「很多武器都刚好 3500」这种正常撞价，价格只有在明显高于
        该武器次高价（below 的 bait_price_ratio 倍以上）时才算异常。
        """
        if not self.bait_price_floor or price < self.bait_price_floor:
            return False
        weapons_at_price = price_weapons.get(price, 0)
        outlier = bool(below) and price >= below * self.bait_price_ratio
        if weapons_at_price >= self.bait_price_weapons and (outlier or not below):
            return True
        if outlier and weapons_at_price >= 3:
            return True
        if seller and seller_weapons.get(seller, 0) >= self.bait_seller_weapons:
            return outlier or weapons_at_price >= 3
        return False

    def riven_bait_stats(self, limit: int = 6) -> List[Dict[str, Any]]:
        """给控制台看的「被判为钓鱼价」清单，方便确认过滤没误伤。"""
        with self._lock:
            entries = {slug: [list(row) for row in (entry.get("c") or [])]
                       for slug, entry in self._quotes[_KIND_RIVEN].items()
                       if len(entry.get("c") or []) > 1}
        floor = self.bait_price_floor
        price_weapons: Counter = Counter()
        seller_weapons: Counter = Counter()
        for rows in entries.values():
            for price in {_as_int(row[0]) for row in rows if _as_int(row[0]) >= floor}:
                price_weapons[price] += 1
            for seller in {str(row[2] or "") for row in rows
                           if _as_int(row[0]) >= floor} - {""}:
                seller_weapons[seller] += 1
        dropped: List[Dict[str, Any]] = []
        for slug, rows in entries.items():
            prices = [_as_int(row[0]) for row in rows]
            for index, row in enumerate(rows):
                price = prices[index]
                below = prices[index + 1] if index + 1 < len(prices) else 0
                if price <= 0 or not self._looks_like_bait(
                        price, str(row[2] or ""), below, price_weapons,
                        seller_weapons):
                    continue
                dropped.append({"slug": slug, "price": price,
                                "seller": str(row[2] or ""),
                                "weapons_at_price": price_weapons.get(price, 0),
                                "seller_weapons": seller_weapons.get(str(row[2] or ""), 0)})
                break
        dropped.sort(key=lambda row: -row["price"])
        return dropped[:limit]

    def _ranked(self, kind: str) -> List[BoardQuote]:
        with self._lock:
            entries = dict(self._quotes[kind])
        rows = [BoardQuote(slug=slug, price=_as_int(entry.get("p")),
                           buy=_as_int(entry.get("b")), orders=_as_int(entry.get("n")),
                           updated=_as_float(entry.get("t")))
                for slug, entry in entries.items() if _as_int(entry.get("p")) > 0]
        rows.sort(key=lambda row: (-row.price, row.slug))
        return rows

    def coverage(self, kind: str) -> Tuple[int, int]:
        """(已扫描, 目标数)，用于「正在准备数据」提示。"""
        if kind == _KIND_JUNK:
            if not self._junk_by_ducats or not any(self._junk_by_ducats.values()):
                self._ensure_targets()
            ready = sum(len([slug for slug in slugs if slug in self._quotes[_KIND_JUNK]])
                        for slugs in self._junk_by_ducats.values())
            total = sum(len(slugs) for slugs in self._junk_by_ducats.values())
            return ready, total
        if not self._targets.get(kind):
            self._ensure_targets()
        slugs = self._targets.get(kind) or []
        ready = sum(1 for slug in slugs if slug in self._quotes[kind])
        return ready, len(slugs)

    def junk_coverage(self, ducats: int) -> Tuple[int, int]:
        slugs = self._junk_by_ducats.get(ducats) or []
        if not slugs:
            self._ensure_targets()
            slugs = self._junk_by_ducats.get(ducats) or []
        ready = sum(1 for slug in slugs if slug in self._quotes[_KIND_JUNK])
        return ready, len(slugs)

    def frame_coverage(self) -> Tuple[int, int]:
        return self.coverage(_KIND_FRAME)

    def riven_coverage(self) -> Tuple[int, int]:
        return self.coverage(_KIND_RIVEN)

    def status(self) -> Dict[str, Any]:
        junk_ready, junk_total = self.coverage(_KIND_JUNK)
        frame_ready, frame_total = self.coverage(_KIND_FRAME)
        riven_ready, riven_total = self.coverage(_KIND_RIVEN)
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "banned": max(0.0, self._ban_until - time.time()),
            "interval": round(self.interval, 2),
            "requests": self.requests,
            "errors": self.errors,
            "last_error": self.last_error,
            "junk": {"ready": junk_ready, "total": junk_total, "ttl": self.junk_ttl},
            "frames": {"ready": frame_ready, "total": frame_total, "ttl": self.frame_ttl},
            "riven": {"ready": riven_ready, "total": riven_total,
                      "ttl": self.riven_ttl,
                      "bait_floor": self.bait_price_floor,
                      "bait": self.riven_bait_stats(6)},
        }

    # ------------------------------------------------------------------ 持久化
    def _load(self) -> None:
        if not self.cache_file or not os.path.isfile(self.cache_file):
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            self.log.warning("读取价格榜快照失败：%s", exc)
            return
        if not isinstance(raw, dict):
            return
        for kind in (_KIND_JUNK, _KIND_FRAME):
            rows = raw.get(kind)
            if isinstance(rows, dict):
                for slug, entry in rows.items():
                    if isinstance(entry, list) and len(entry) >= 4:
                        self._quotes[kind][slug] = {
                            "p": _as_int(entry[0]), "b": _as_int(entry[1]),
                            "n": _as_int(entry[2]), "t": _as_float(entry[3]),
                        }
        rows = raw.get(_KIND_RIVEN)
        if isinstance(rows, dict):
            for slug, entry in rows.items():
                if not isinstance(entry, list):
                    continue
                if len(entry) >= 6 and not isinstance(entry[1], list):
                    # v1 旧格式：[价格, 一口价, 卖家, 词条, 时间, 抓取时间]
                    # 只有一条候选、判断不了是不是钓鱼价，先留着显示，
                    # 但把时间置 0 让它立刻进入重扫队列，按限流慢慢补全候选。
                    self._quotes[_KIND_RIVEN][slug] = {
                        "t": 0.0,
                        "v": 1,
                        "c": [[_as_int(entry[0]), 0, str(entry[2]),
                               list(entry[3] or []), str(entry[4]),
                               1 if _as_int(entry[1]) else 0]],
                    }
                    self._legacy_riven += 1
                    continue
                if len(entry) >= 2 and isinstance(entry[1], list):
                    # v2：[抓取时间, [[价格, 等级, 卖家, 词条, 上架时间, 一口价], ...], 标志位]
                    # 标志位 bit0=老缓存待重扫，bit1=已按新接口完整抓过。
                    # 没有标志位且只有一条候选的，都是迁移前只存了冠军价的条目：
                    # 那条高价很可能是议价占位单，先当老缓存处理（不上榜 + 优先重扫）。
                    # 便宜的单条价格不算可疑，照常展示（按 TTL 慢慢补候选）。
                    candidates = []
                    for row in entry[1]:
                        if not isinstance(row, list) or len(row) < 6:
                            continue
                        candidates.append([_as_int(row[0]), _as_int(row[1]),
                                           str(row[2] or ""), list(row[3] or []),
                                           str(row[4] or ""), _as_int(row[5])])
                    if candidates:
                        flags = _as_int(entry[2]) if len(entry) >= 3 else 0
                        legacy = bool(flags & 1)
                        full = bool(flags & 2)
                        if (not flags and len(candidates) <= 1
                                and _as_int(candidates[0][0]) >= self.bait_price_floor):
                            legacy = True
                        if (legacy and len(candidates) <= 1
                                and _as_int(candidates[0][0]) < self.bait_price_floor):
                            # 便宜的单条价格不可疑：撤销待核实标记，别排进重扫队列
                            legacy = False
                        if legacy and not full:
                            self._legacy_riven += 1
                        record = {"t": _as_float(entry[0]), "c": candidates}
                        if legacy and not full:
                            # 立刻进重扫队列（_next_stale 会优先挑它）
                            record["t"] = 0.0
                            record["v"] = 1
                        if full:
                            record["f"] = 1
                        self._quotes[_KIND_RIVEN][slug] = record
        if self._legacy_riven:
            self.log.info("紫卡缓存是旧格式（%d 把），已标记为待重扫", self._legacy_riven)

    def _save(self, force: bool = False) -> None:
        if not self.cache_file:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            payload = {
                "version": 2,
                "saved": time.time(),
                _KIND_JUNK: {slug: [entry.get("p", 0), entry.get("b", 0),
                                    entry.get("n", 0), round(entry.get("t", 0), 1)]
                             for slug, entry in self._quotes[_KIND_JUNK].items()},
                _KIND_FRAME: {slug: [entry.get("p", 0), entry.get("b", 0),
                                     entry.get("n", 0), round(entry.get("t", 0), 1)]
                              for slug, entry in self._quotes[_KIND_FRAME].items()},
                _KIND_RIVEN: {slug: ([round(entry.get("t", 0), 1),
                                      [[_as_int(row[0]), _as_int(row[1]),
                                        str(row[2] or ""), list(row[3] or []),
                                        str(row[4] or ""), _as_int(row[5])]
                                       for row in (entry.get("c") or [])]]
                                      + ([_riven_flags(entry)]
                                         if (entry.get("v") or entry.get("f")) else []))
                              for slug, entry in self._quotes[_KIND_RIVEN].items()},
            }
            self._dirty = 0
            self._saved_at = time.time()
        directory = os.path.dirname(os.path.abspath(self.cache_file))
        if directory:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as exc:
                self.log.warning("创建价格榜目录失败：%s", exc)
                return
        temp_path = self.cache_file + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(temp_path, self.cache_file)
        except OSError as exc:
            self.log.warning("写入价格榜快照失败：%s", exc)
            try:
                os.remove(temp_path)
            except OSError:
                pass
