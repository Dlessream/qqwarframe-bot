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
from collections import deque
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


def parse_riven_auctions(rows: Iterable[Dict[str, Any]],
                         names: Optional[Dict[str, str]] = None,
                         max_price: int = 50000) -> Optional[RivenQuote]:
    """从拍卖列表里挑最贵的零洗（re_rolls = 0）紫卡。

    max_price 用于过滤明显是挂错/占位的天价单（默认 5 万白金）。
    """
    best: Optional[RivenQuote] = None
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
        if best is not None and price <= best.price:
            continue
        owner = row.get("owner") or {}
        attributes = [text for text in (_fmt_attr(attr, names)
                                        for attr in (item.get("attributes") or [])
                                        if isinstance(attr, dict)) if text]
        best = RivenQuote(
            slug=str(item.get("url_name") or item.get("slug") or ""),
            price=price,
            buyout=bool(buyout_price),
            seller=str(owner.get("ingame_name") or owner.get("ingameName") or "?"),
            attributes=tuple(attributes),
            created=str(row.get("created") or "")[:19],
        )
    return best


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
        for offset in range(count):
            slug = slugs[(cursor + offset) % count]
            entry = quotes.get(slug)
            if entry is None or now - entry.get("t", 0) >= ttl:
                self._cursor[kind] = (cursor + offset + 1) % count
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
        quote = parse_riven_auctions(rows or [], names=self._attribute_names(),
                                     max_price=self.max_riven_price)
        with self._lock:
            if quote is None:
                existing = self._quotes[_KIND_RIVEN].get(slug)
                if existing:
                    existing["t"] = time.time()
                return
            self._quotes[_KIND_RIVEN][slug] = {
                "p": quote.price,
                "o": 1 if quote.buyout else 0,
                "s": quote.seller,
                "a": list(quote.attributes[:6]),
                "c": quote.created,
                "t": time.time(),
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
        with self._lock:
            entries = dict(self._quotes[_KIND_RIVEN])
        rows = [
            RivenQuote(slug=slug, price=_as_int(entry.get("p")),
                       buyout=bool(entry.get("o")), seller=str(entry.get("s") or "?"),
                       attributes=tuple(entry.get("a") or ()),
                       created=str(entry.get("c") or ""),
                       updated=_as_float(entry.get("t")))
            for slug, entry in entries.items() if _as_int(entry.get("p")) > 0
        ]
        rows.sort(key=lambda row: (-row.price, row.slug))
        return rows

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
            "riven": {"ready": riven_ready, "total": riven_total, "ttl": self.riven_ttl},
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
                if isinstance(entry, list) and len(entry) >= 6:
                    self._quotes[_KIND_RIVEN][slug] = {
                        "p": _as_int(entry[0]), "o": _as_int(entry[1]),
                        "s": str(entry[2]), "a": list(entry[3] or []),
                        "c": str(entry[4]), "t": _as_float(entry[5]),
                    }

    def _save(self, force: bool = False) -> None:
        if not self.cache_file:
            return
        if not force and not self._dirty:
            return
        with self._lock:
            payload = {
                "version": 1,
                "saved": time.time(),
                _KIND_JUNK: {slug: [entry.get("p", 0), entry.get("b", 0),
                                    entry.get("n", 0), round(entry.get("t", 0), 1)]
                             for slug, entry in self._quotes[_KIND_JUNK].items()},
                _KIND_FRAME: {slug: [entry.get("p", 0), entry.get("b", 0),
                                     entry.get("n", 0), round(entry.get("t", 0), 1)]
                              for slug, entry in self._quotes[_KIND_FRAME].items()},
                _KIND_RIVEN: {slug: [entry.get("p", 0), entry.get("o", 0),
                                     entry.get("s", ""), list(entry.get("a") or []),
                                     entry.get("c", ""), round(entry.get("t", 0), 1)]
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
