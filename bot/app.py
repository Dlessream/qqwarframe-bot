"""应用装配：过滤消息、限流、路由、图片/文字发送、降价提醒。"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from . import imagecard, present
from .alerts import AlertStore
from .arbi import ArbiClient
from .commands import CommandRouter, Context, strip_cq
from .market import Item, MarketClient, MarketError
from .onebot import OneBotClient, OneBotError, escape_cq
from .replies import Reply
from .render import chunk_text
from .riven import RivenClient
from .updater import DataUpdater
from .wiki import WikiClient
from .worldstate import WorldStateClient, WorldStateError

LOGGER = logging.getLogger("qqwarframe.app")


class BotApp:
    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        self.cfg = config
        self.log = logger or LOGGER
        self.behavior = config.get("behavior", {})
        self.prefixes: List[str] = list(self.behavior.get("prefixes") or ["#"])
        self.self_id = ""
        self._stop = threading.Event()

        image_cfg = config.get("image", {})
        self.image_enabled = bool(image_cfg.get("enabled", True))
        if self.image_enabled:
            imagecard.configure(str(image_cfg.get("font", "")),
                                str(image_cfg.get("font_bold", "")))
            if not imagecard.available():
                self.image_enabled = False
                self.log.warning(
                    "Pillow 或中文字体不可用，图片将回退为文字。"
                    "Linux 可安装 fonts-noto-cjk，或在 image.font / QQWF_FONT 指定字体路径")

        self.market = MarketClient(config.get("warframe_market", {}), logger=self.log,
                                   cache_config=config.get("cache", {}))
        self.wiki = WikiClient(config.get("wiki", {}), logger=self.log,
                               cache_config=config.get("cache", {}))
        self.riven = RivenClient(
            self.market, self.log,
            cache_config=config.get("cache", {}),
            disposition_override=config.get("riven", {}).get("disposition_override"))

        arbi_cfg = config.get("arbi", {})
        self.arbi = (ArbiClient(arbi_cfg, logger=self.log)
                     if arbi_cfg.get("enabled", True) else None)

        world_cfg = config.get("worldstate", {})
        self.worldstate = (WorldStateClient(world_cfg, cache_config=config.get("cache", {}))
                           if world_cfg.get("enabled", True) else None)

        caches = [self.market.cache, self.wiki.cache, self.riven.cache]
        if self.worldstate is not None:
            caches.append(self.worldstate.cache)
        self.updater = DataUpdater(
            self.market, self.riven,
            config.get("auto_update", {}), logger=self.log,
            cache_config=config.get("cache", {}), caches=caches,
            arbi=self.arbi)

        alert_cfg = config.get("alerts", {})
        self.alerts: Optional[AlertStore] = None
        if alert_cfg.get("enabled", True):
            self.alerts = AlertStore(str(alert_cfg.get("file", "data/alerts.json")),
                                     int(alert_cfg.get("max_per_chat", 20)))
        self.alert_interval = max(60, int(alert_cfg.get("check_seconds", 300)))

        self.router = CommandRouter(
            self.market, self.wiki, config, logger=self.log,
            riven=self.riven, worldstate=self.worldstate, alerts=self.alerts,
            arbi=self.arbi, updater=self.updater)
        self.onebot = OneBotClient(config.get("onebot", {}), self.on_event, logger=self.log)

        self._last_reply: Dict[str, float] = {}
        self._chat_bucket: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._alert_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        if self.behavior.get("preload_items", True):
            thread = threading.Thread(target=self._prewarm, name="prewarm", daemon=True)
            thread.start()
        if self.updater is not None:
            self.updater.start()
        if self.alerts is not None:
            self._alert_thread = threading.Thread(target=self._alert_loop,
                                                  name="alerts", daemon=True)
            self._alert_thread.start()

    def run(self) -> None:
        self.start()
        try:
            self.onebot.run_forever(self._stop)
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        if self.updater is not None:
            self.updater.stop()
        self.onebot.close()
        self.log.info("机器人已停止")

    def _prewarm(self) -> None:
        started = time.time()
        try:
            index = self.market.load_items()
            self.log.info("物品索引预热完成：%d 条，用时 %.1f 秒", len(index), time.time() - started)
        except Exception as exc:
            self.log.warning("物品索引预热失败（首次查询时会重试）：%s", exc)

    # ------------------------------------------------------------------ 事件
    def on_event(self, event: Dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        message_type = str(event.get("message_type") or "")
        if message_type not in ("group", "private"):
            return

        self.self_id = str(event.get("self_id") or self.self_id)
        user_id = str(event.get("user_id") or "")
        group_id = str(event.get("group_id") or "")
        if not user_id or (self.self_id and user_id == self.self_id):
            return

        is_group = message_type == "group"
        if is_group and not self._group_allowed(group_id):
            return
        if not is_group and not self.behavior.get("allow_private", True):
            return

        text, at_me = self._extract_text(event)
        text = text.strip()
        if not text:
            return

        parsed = self.router.parse(text)
        if parsed is None:
            if at_me and self.behavior.get("respond_to_at", True):
                text = self.prefixes[0] + text
                parsed = self.router.parse(text)
            if parsed is None:
                return

        chat_key = "g:%s" % group_id if is_group else "p:%s" % user_id
        user_key = "%s/u:%s" % (chat_key, user_id)
        if not self._allow(chat_key, user_key):
            self.log.debug("触发限流：%s", user_key)
            return

        context = Context(
            user_id=user_id,
            group_id=group_id,
            is_group=is_group,
            self_id=self.self_id,
            prefix=self.prefixes[0],
        )

        try:
            replies = self.router.handle(text, context)
        except Exception as exc:  # pragma: no cover - 防御
            self.log.exception("处理命令失败：%s", exc)
            replies = [present.error_reply(
                "查询出错了，请稍后再试。若持续失败，请运行 python selftest.py 自检。")]

        if replies:
            self._reply(replies, is_group, group_id, user_id)

    def _group_allowed(self, group_id: str) -> bool:
        whitelist = self.behavior.get("whitelist_groups") or []
        blacklist = self.behavior.get("blacklist_groups") or []
        if whitelist and group_id not in {str(item) for item in whitelist}:
            return False
        return group_id not in {str(item) for item in blacklist}

    def _extract_text(self, event: Dict[str, Any]) -> Tuple[str, bool]:
        segments = event.get("message")
        at_me = False
        if isinstance(segments, list):
            parts: List[str] = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                kind = segment.get("type")
                data = segment.get("data") or {}
                if kind == "text":
                    parts.append(str(data.get("text") or ""))
                elif kind == "at":
                    if self.self_id and str(data.get("qq")) == self.self_id:
                        at_me = True
            text = "".join(parts)
            if text.strip():
                return text, at_me

        raw_source = str(event.get("raw_message") or event.get("message") or "")
        raw = strip_cq(raw_source)
        if self.self_id and ("[CQ:at,qq=%s]" % self.self_id) in raw_source:
            at_me = True
        return raw, at_me

    # ------------------------------------------------------------------ 限流
    def _allow(self, chat_key: str, user_key: str) -> bool:
        now = time.time()
        cooldown = float(self.behavior.get("cooldown_seconds", 3))
        limit = int(self.behavior.get("max_replies_per_minute", 20))

        with self._lock:
            last = self._last_reply.get(user_key, 0.0)
            if cooldown > 0 and now - last < cooldown:
                return False

            bucket = self._chat_bucket.setdefault(chat_key, collections.deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= limit:
                return False

            self._last_reply[user_key] = now
            bucket.append(now)
            if len(self._last_reply) > 5000:
                cutoff = now - 3600
                self._last_reply = {
                    key: value for key, value in self._last_reply.items() if value > cutoff}
            return True

    # ------------------------------------------------------------------ 发送
    def _reply(self, replies: List[Reply], is_group: bool,
               group_id: str, user_id: str) -> None:
        first = True
        for reply in replies:
            at_user = user_id if (is_group and first
                                  and self.behavior.get("at_sender", True)) else ""
            sent = self._deliver(is_group, group_id if is_group else user_id,
                                 reply, at_user=at_user)
            if sent:
                first = False

    def _deliver(self, is_group: bool, chat_id: str, reply: Reply,
                 at_user: str = "") -> bool:
        prefix = "[CQ:at,qq=%s] " % at_user if at_user else ""
        if self.image_enabled and reply.image is not None:
            try:
                png = reply.image()
            except Exception as exc:
                self.log.warning("图片生成失败，回退文字：%s", exc)
                png = None
            if png:
                try:
                    if is_group:
                        self.onebot.send_group_image(chat_id, png, prefix)
                    else:
                        self.onebot.send_private_image(chat_id, png, prefix)
                    return True
                except OneBotError as exc:
                    self.log.warning("图片发送失败，回退文字：%s", exc)

        if not reply.text:
            return False

        max_chars = int(self.behavior.get("max_reply_chars", 1200))
        chunks = chunk_text(reply.text, max_chars)
        for index, chunk in enumerate(chunks):
            text = escape_cq(chunk)
            if len(chunks) > 1:
                text = "(%d/%d) %s" % (index + 1, len(chunks), text)
            if prefix and index == 0:
                text = prefix + text
            try:
                if is_group:
                    self.onebot.send_group_text(chat_id, text)
                else:
                    self.onebot.send_private_text(chat_id, text)
            except OneBotError as exc:
                self.log.warning("发送消息失败：%s", exc)
                return False
            if len(chunks) > 1:
                time.sleep(0.35)
        return True

    # ------------------------------------------------------------------ 提醒
    def _alert_loop(self) -> None:
        if self._stop.wait(min(60, self.alert_interval)):
            return
        while not self._stop.is_set():
            try:
                self._check_alerts()
            except Exception as exc:  # pragma: no cover - 防御
                self.log.exception("检查提醒时出错：%s", exc)
            if self._stop.wait(self.alert_interval):
                return

    def _check_alerts(self) -> None:
        assert self.alerts is not None
        pending = self.alerts.active()[:50]
        if not pending:
            return
        self.log.info("检查 %d 条降价提醒…", len(pending))
        for alert in pending:
            if self._stop.is_set():
                return
            try:
                sell, _ = self.market.top_orders(alert.slug, limit=1,
                                                 platform=alert.platform)
            except MarketError as exc:
                self.log.warning("提醒查询失败 %s：%s", alert.slug, exc)
                continue

            price = sell[0].platinum if sell else None
            if price is not None and price != alert.last_price:
                self.alerts.set_price(alert, price)
            if price is None or price > alert.target:
                continue

            item = None
            try:
                item = self.market.index.get(alert.slug)
            except Exception:
                item = None
            item = item or Item(slug=alert.slug, name=alert.name)
            reply = present.alert_fired_reply(item, sell[0], alert)
            chat_id = alert.chat_id
            self._deliver(alert.chat_type == "group", chat_id, reply)
            alert.active = False
            self.alerts.update(alert)
            self.log.info("提醒 #%s 已触发：%s %dp <= %dp",
                          alert.id, alert.name, price, alert.target)
            time.sleep(0.4)
