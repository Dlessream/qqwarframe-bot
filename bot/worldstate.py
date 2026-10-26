"""世界状态：Baro 奸商 / 突击 / 仲裁。

数据源默认 https://api.warframestat.us （可配置），带 language=zh。
该站点有 Cloudflare，通常需要走代理；失败时给出明确提示。
"""

from __future__ import annotations

import calendar
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import terms, worldfallback
from .cache import TTLCache
from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.worldstate")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 映射表放在 worldfallback 里（官方兜底源也用它），这里保留同名引用。
OFFICIAL_MISSIONS = worldfallback.OFFICIAL_MISSIONS
VOID_TIERS = worldfallback.VOID_TIERS


class WorldStateError(Exception):
    pass


def parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    text = text.replace("Z", "")
    if "." in text:
        text = text.split(".")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return calendar.timegm(parsed.timetuple())
        except ValueError:
            continue
    return 0.0


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    if days:
        return "%d 天 %d 小时" % (days, hours)
    if hours:
        return "%d 小时 %d 分" % (hours, minutes)
    return "%d 分" % minutes


def countdown(target: float) -> str:
    if not target:
        return "未知"
    remaining = target - time.time()
    if remaining <= 0:
        return "已结束"
    return format_duration(remaining)


@dataclass
class TraderItem:
    name: str
    ducats: int = 0
    credits: int = 0
    count: int = 1


@dataclass
class VoidTrader:
    active: bool = False
    character: str = "Baro Ki'Teer"
    location: str = ""
    start: float = 0.0
    end: float = 0.0
    inventory: List[TraderItem] = field(default_factory=list)


@dataclass
class SortieVariant:
    mission: str
    node: str
    modifier: str


@dataclass
class Sortie:
    boss: str = ""
    faction: str = ""
    end: float = 0.0
    variants: List[SortieVariant] = field(default_factory=list)


@dataclass
class Arbitration:
    node: str = ""
    mission: str = ""
    enemy: str = ""
    end: float = 0.0
    node_key: str = ""
    expired: bool = False


class WorldStateClient:
    def __init__(self, config: Dict[str, Any],
                 cache_config: Optional[Dict[str, Any]] = None):
        self.base_url = str(config.get("base_url", "https://api.warframestat.us")).rstrip("/")
        self.language = str(config.get("language", "zh"))
        cache_cfg = cache_config or {}
        self.cache = TTLCache(cache_cfg, prefix="worldstate")
        self.cache_seconds = int(cache_cfg.get("worldstate_seconds", 60))
        self.http = HttpClient(
            user_agent=config.get("user_agent", BROWSER_UA),
            timeout=config.get("timeout_seconds", 12),
            proxy=config.get("proxy", ""),
            retries=config.get("retries", 1),
            logger=LOGGER,
        )
        self.official_url = str(
            config.get("official_url") or
            "https://api.warframe.com/cdn/worldState.php")
        self.official_proxy = str(config.get("official_proxy") or "none")
        self.prefer_official_fissures = bool(
            config.get("prefer_official_fissures", True))
        self.official_http = HttpClient(
            user_agent=config.get("user_agent", BROWSER_UA),
            timeout=config.get("timeout_seconds", 12),
            proxy=self.official_proxy,
            retries=0,
            logger=LOGGER,
        )
        # ------------------------------------------------ 官方兜底（直连可用）
        # 注意：默认关闭；config.py 的 DEFAULTS 里是 True，正常经 load_config
        # 读取的配置都会打开，直接构造 dict 的场景（含单元测试）保持原行为。
        self.official_fallback = bool(config.get("official_fallback", False))
        self.fallback_ttl = int(config.get("fallback_ttl_seconds", 30))
        self.fallback_cooldown = int(config.get("fallback_cooldown_seconds", 600))
        self.oracle_url = str(
            config.get("oracle_url") or worldfallback.DEFAULT_ORACLE_URL)
        self.oracle_http = HttpClient(
            user_agent=config.get("user_agent", BROWSER_UA),
            timeout=config.get("timeout_seconds", 12),
            proxy=str(config.get("oracle_proxy") or "none"),
            retries=0,
            logger=LOGGER,
        )
        self._fallback: Optional[worldfallback.OfficialWorldState] = None
        self._ws_down_until = 0.0
        self._nodes: Optional[Dict[str, Dict[str, str]]] = None
        # 本次数据是否来自官方兜底（兜底源不含虚空风暴节点名）
        self.using_fallback = False

    # ---------------------------------------------------------------- 数据源
    def _fallback_state(self) -> Dict[str, Any]:
        """官方 CDN 拼出的 warframestat 同构文档。"""
        if not self.official_fallback:
            raise WorldStateError("世界状态接口不可用，且未启用官方兜底数据源。")
        if self._fallback is None:
            self._fallback = worldfallback.OfficialWorldState(
                http=self.official_http,
                nodes=self._load_nodes(),
                oracle_http=self.oracle_http,
                official_url=self.official_url,
                oracle_url=self.oracle_url,
                ttl=self.fallback_ttl,
                logger=LOGGER,
            )
        try:
            return self._fallback.state()
        except worldfallback.FallbackError as exc:
            raise WorldStateError(str(exc))

    def _fallback_field(self, path: str, exc: Optional[HttpError] = None) -> Any:
        try:
            data = self._fallback_state()
        except WorldStateError:
            if exc is not None:
                raise WorldStateError(self._friendly(exc))
            raise
        self.using_fallback = True
        if not path:
            return data
        value = data.get(path)
        if value is None:
            reason = self._friendly(exc) if exc is not None else "接口不可用"
            raise WorldStateError(
                "%s官方 API 也没有这项数据（该功能需要 warframestat）。" % reason)
        return value

    def _mark_down(self, exc: HttpError) -> None:
        self._ws_down_until = time.time() + max(30, self.fallback_cooldown)
        LOGGER.warning("世界状态主源不可用（%s），%d 秒内直接走官方兜底",
                       exc, max(30, self.fallback_cooldown))

    def source_name(self) -> str:
        """当前生效的数据源，用于日志与自检输出。"""
        if self._ws_down_until and time.time() < self._ws_down_until:
            return "official"
        return "warframestat"

    def _get(self, path: str) -> Any:
        key = ("ws", path)
        return self.cache.get_or_set(
            key, lambda: self._get_uncached(path), self.cache_seconds)

    def _get_uncached(self, path: str) -> Any:
        if self._ws_down_until and time.time() < self._ws_down_until:
            return self._fallback_field(path)
        url = "%s/pc/%s" % (self.base_url, path)
        params = "?language=%s" % self.language if self.language else ""
        try:
            return self.http.get_json(url + params)
        except HttpError as exc:
            if exc.status in (400, 404) and params:
                try:
                    return self.http.get_json(url)
                except HttpError:
                    pass
            if self.official_fallback:
                self._mark_down(exc)
                return self._fallback_field(path, exc)
            raise WorldStateError(self._friendly(exc))
        except Exception as exc:  # pragma: no cover - 防御
            raise WorldStateError("世界状态接口访问失败：%s" % exc)

    def _friendly(self, exc: HttpError) -> str:
        if exc.status == 403:
            return "世界状态接口被 Cloudflare 拦截（403），官方兜底源也不可用。"
        if exc.status == 0:
            return "无法连接世界状态接口，官方兜底源也不可用。"
        return "世界状态接口异常（HTTP %d），官方兜底源也不可用。" % exc.status

    def fetch(self, path: str) -> Any:
        """读取任意 warframestat 子路径（带短缓存）。"""
        return self._get(path)

    def _load_nodes(self) -> Dict[str, Dict[str, str]]:
        if self._nodes is not None:
            return self._nodes
        path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "sol_nodes.json")
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            self._nodes = raw if isinstance(raw, dict) else {}
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取节点表失败：%s", exc)
            self._nodes = {}
        return self._nodes

    @staticmethod
    def _official_expiry(row: Dict[str, Any]) -> float:
        value = ((row.get("Expiry") or {}).get("$date") or {}).get("$numberLong")
        try:
            return float(value) / 1000.0
        except (TypeError, ValueError):
            return 0.0

    def official_fissures(self) -> List[Dict[str, Any]]:
        data = self.official_http.get_json(self.official_url)
        return worldfallback.official_fissures(data, self._load_nodes())

    def fissures(self) -> List[Dict[str, Any]]:
        """优先官方 ActiveMissions；失败/为空时回退 warframestat。"""
        if self.prefer_official_fissures:
            try:
                data = self.cache.get_or_set(
                    "official_fissures", self.official_fissures, 30)
                if data:
                    return data
            except Exception as exc:
                LOGGER.warning("官方裂缝源失败，回退 warframestat：%s", exc)
        data = self.fetch("fissures")
        return data if isinstance(data, list) else []

    # ------------------------------------------------------------------ 奸商
    def void_trader(self) -> VoidTrader:
        data = self._get("voidTrader")
        if not isinstance(data, dict):
            raise WorldStateError("奸商数据格式异常。")
        start = parse_timestamp(data.get("activation"))
        end = parse_timestamp(data.get("expiry"))
        now = time.time()
        inventory = []
        for row in data.get("inventory") or []:
            if not isinstance(row, dict):
                continue
            inventory.append(TraderItem(
                name=str(row.get("item") or row.get("type") or "?"),
                ducats=int(row.get("ducats") or 0),
                credits=int(row.get("credits") or 0),
                count=int(row.get("count") or 1),
            ))
        return VoidTrader(
            active=bool(start and end and start <= now <= end),
            character=str(data.get("character") or "Baro Ki'Teer"),
            location=terms.localize_location(str(data.get("location") or "")),
            start=start,
            end=end,
            inventory=inventory,
        )

    # ------------------------------------------------------------------ 突击
    def sortie(self) -> Sortie:
        data = self._get("sortie")
        if not isinstance(data, dict):
            raise WorldStateError("突击数据格式异常。")
        variants = []
        for row in data.get("variants") or []:
            if not isinstance(row, dict):
                continue
            variants.append(SortieVariant(
                mission=terms.localize_mission(str(row.get("missionType") or "")),
                node=terms.localize_node(str(row.get("node") or "")),
                modifier=terms.localize_text(
                    str(row.get("modifierDescription") or row.get("modifier") or "")),
            ))
        return Sortie(
            boss=terms.localize_boss(str(data.get("boss") or "")),
            faction=terms.localize_faction(str(data.get("faction") or "")),
            end=parse_timestamp(data.get("expiry")),
            variants=variants,
        )

    # ------------------------------------------------------------------ 仲裁
    def arbitration(self) -> Arbitration:
        data = self._get("arbitration")
        if not isinstance(data, dict):
            raise WorldStateError("仲裁数据格式异常。")
        return Arbitration(
            node=terms.localize_node(str(data.get("node") or "")),
            mission=terms.localize_mission(str(data.get("type") or "")),
            enemy=terms.localize_faction(str(data.get("enemy") or "")),
            end=parse_timestamp(data.get("expiry")),
            node_key=str(data.get("nodeKey") or ""),
            expired=bool(data.get("expired")),
        )
