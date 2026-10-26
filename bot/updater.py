"""后台自动更新：市场物品索引、紫卡武器表。

实时类数据（买卖单、行情、世界状态、维基）走短 TTL 缓存，
由 ``cache`` 配置控制，不在这里刷新。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("qqwarframe.updater")


def format_age(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "未加载"
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return "%d 分钟前" % int(seconds // 60)
    if seconds < 86400:
        return "%.1f 小时前" % (seconds / 3600)
    return "%.1f 天前" % (seconds / 86400)


class DataUpdater:
    def __init__(self, market, riven, config: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None,
                 cache_config: Optional[Dict[str, Any]] = None,
                 caches: Optional[list] = None,
                 arbi=None, board=None):
        self.market = market
        self.riven = riven
        self.arbi = arbi
        self.board = board
        self.log = logger or LOGGER
        self.cache_config = cache_config or {}
        self.caches = list(caches or [])
        config = config or {}
        self.enabled = bool(config.get("enabled", True))
        self.interval = max(300, int(config.get("interval_seconds", 21600)))
        self.on_start = bool(config.get("on_start", True))
        self.do_market = bool(config.get("market", True))
        self.do_riven = bool(config.get("riven", True))
        self.do_arbi = bool(config.get("arbi", True))
        self.manual_cooldown = max(0, int(config.get("manual_cooldown_seconds", 600)))

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._manual_lock = threading.Lock()
        self._last_manual = 0.0
        self.last_run = 0.0
        self.last_duration = 0.0
        self.last_results: Dict[str, str] = {}

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if not self.enabled:
            self.log.info("自动更新已关闭")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="data-updater",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        if self.on_start:
            self.refresh_all(force=False)
        while not self._stop.wait(self.interval):
            self.refresh_all(force=True)

    # ------------------------------------------------------------------ 刷新
    def refresh_all(self, force: bool = True) -> Dict[str, str]:
        if not self._lock.acquire(blocking=False):
            return {"state": "已在更新中"}
        started = time.time()
        results: Dict[str, str] = {}
        try:
            if force:
                for cache in self.caches:
                    try:
                        cache.clear()
                    except Exception:
                        pass
            if self.do_market:
                results["market"] = self._refresh_market(force)
            if self.do_riven:
                results["riven"] = self._refresh_riven(force)
            if self.do_arbi and self.arbi is not None:
                results["arbi"] = self._refresh_arbi(force)
            self.last_run = time.time()
            self.last_duration = self.last_run - started
            self.last_results = results
            self.log.info("数据更新完成（%.1fs）：%s", self.last_duration, results)
            return results
        finally:
            self._lock.release()

    def _refresh_market(self, force: bool) -> str:
        try:
            if force:
                count = self.market.refresh()
            else:
                count = len(self.market.load_items().items)
            return "%d 条" % count
        except Exception as exc:
            self.log.warning("自动更新市场物品失败：%s", exc)
            return "失败：%s" % exc

    def _refresh_riven(self, force: bool) -> str:
        try:
            if force:
                count = self.riven.refresh()
            else:
                count = len(self.riven.weapons())
            return "%d 把" % count
        except Exception as exc:
            self.log.warning("自动更新紫卡武器失败：%s", exc)
            return "失败：%s" % exc

    def _refresh_arbi(self, force: bool) -> str:
        if self.arbi is None:
            return "未启用"
        try:
            if force:
                count = self.arbi.refresh()
            else:
                count = self.arbi.data().rated_count
            return "%d 个已评级节点" % count
        except Exception as exc:
            self.log.warning("自动更新仲裁评级失败：%s", exc)
            return "失败：%s" % exc

    # ------------------------------------------------------------------ 手动
    def trigger(self) -> str:
        if not self.enabled:
            return "自动更新未启用（auto_update.enabled = false）。"
        now = time.time()
        with self._manual_lock:
            remaining = self.manual_cooldown - (now - self._last_manual)
            if remaining > 0:
                return "刚刚更新过，%d 分钟后再试。" % max(1, int(remaining // 60))
            if self._lock.locked():
                return "正在更新中，请稍候。"
            self._last_manual = now
        threading.Thread(target=self.refresh_all, kwargs={"force": True},
                         name="manual-update", daemon=True).start()
        return "已开始后台更新，完成后自动生效。"

    # ------------------------------------------------------------------ 状态
    def status_text(self, prefix: str = "#") -> str:
        lines = ["【数据更新状态】"]
        if self.enabled:
            lines.append("自动更新：每 %.1f 小时一次" % (self.interval / 3600))
        else:
            lines.append("自动更新：已关闭")

        if self.last_run:
            lines.append("上次自动更新：%s（耗时 %.1fs）" % (
                time.strftime("%m-%d %H:%M", time.localtime(self.last_run)),
                self.last_duration))
            if self.last_results:
                labels = {"market": "市场", "riven": "紫卡", "arbi": "评级"}
                detail = "；".join("%s %s" % (labels.get(key, key), value)
                                   for key, value in self.last_results.items())
                lines.append("结果：%s" % detail)
        else:
            lines.append("上次自动更新：尚未运行")

        lines.append("")
        lines.append("市场物品：%s" % self._market_status())
        lines.append("紫卡武器：%s" % self._riven_status())
        lines.append("仲裁评级：%s" % self._arbi_status())
        lines.append("价格榜：%s" % self._board_status())
        lines.append("世界状态 / 维基：每次查询实时获取")
        if self.cache_config.get("enabled", True):
            lines.append("短缓存：订单 %ss / 行情 %ss / 世界状态 %ss / 维基 %ss" % (
                self.cache_config.get("orders_seconds", 60),
                self.cache_config.get("stats_seconds", 900),
                self.cache_config.get("worldstate_seconds", 60),
                self.cache_config.get("wiki_seconds", 600)))
        lines.append("")
        lines.append("手动更新：%s更新（%d 分钟内只能一次）" % (
            prefix, self.manual_cooldown // 60))
        return "\n".join(lines)

    def _market_status(self) -> str:
        age = self.market.cache_age()
        if age < 0:
            return "未加载"
        return "%d 条（%s）" % (self.market.item_count(), format_age(age))

    def _riven_status(self) -> str:
        age = self.riven.cache_age()
        if age < 0:
            return "未加载"
        return "%d 把（%s）" % (self.riven.weapon_count(), format_age(age))

    def _arbi_status(self) -> str:
        if self.arbi is None:
            return "未启用"
        try:
            age = self.arbi.cache_age()
            if age < 0:
                return "未加载"
            return "%d 个已评级节点（%s）" % (
                self.arbi.data().rated_count, format_age(age))
        except Exception as exc:
            return "读取失败：%s" % exc

    def _board_status(self) -> str:
        if self.board is None:
            return "未启用"
        try:
            status = self.board.status()
        except Exception as exc:
            return "读取失败：%s" % exc
        junk = status.get("junk") or {}
        frames = status.get("frames") or {}
        riven = status.get("riven") or {}
        text = "垃圾 %d/%d ｜ 战甲 %d/%d ｜ 零洗紫卡 %d/%d" % (
            junk.get("ready", 0), junk.get("total", 0),
            frames.get("ready", 0), frames.get("total", 0),
            riven.get("ready", 0), riven.get("total", 0))
        if not status.get("running"):
            text += "（扫描已停止）"
        if status.get("banned"):
            text += "（被限流，%d 秒后继续）" % int(status["banned"])
        return text
