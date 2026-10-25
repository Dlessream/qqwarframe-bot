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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import terms
from .cache import TTLCache
from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.worldstate")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

OFFICIAL_MISSIONS = {
    "MT_EXTERMINATION": "Extermination", "MT_DEFENSE": "Defense",
    "MT_SURVIVAL": "Survival", "MT_RESCUE": "Rescue",
    "MT_TERRITORY": "Interception", "MT_ARTIFACT": "Excavation",
    "MT_SABOTAGE": "Sabotage", "MT_CAPTURE": "Capture",
    "MT_SPY": "Spy", "MT_MOBILE_DEFENSE": "Mobile Defense",
    "MT_ASSASSINATION": "Assassination", "MT_EXCAVATE": "Excavation",
    "MT_HIJACK": "Hijack", "MT_INTEL": "Spy",
    "MT_ALCHEMY": "Alchemy", "MT_VOID_CASCADE": "Void Cascade",
}

VOID_TIERS = {
    "VoidT1": "Lith", "VoidT2": "Meso", "VoidT3": "Neo",
    "VoidT4": "Axi", "VoidT5": "Requiem", "VoidT6": "Omnia",
}


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
        self._nodes: Optional[Dict[str, Dict[str, str]]] = None

    def _get(self, path: str) -> Any:
        key = ("ws", path)
        return self.cache.get_or_set(
            key, lambda: self._get_uncached(path), self.cache_seconds)

    def _get_uncached(self, path: str) -> Any:
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
            raise WorldStateError(self._friendly(exc))
        except Exception as exc:  # pragma: no cover - 防御
            raise WorldStateError("世界状态接口访问失败：%s" % exc)

    def _friendly(self, exc: HttpError) -> str:
        if exc.status == 403:
            return "世界状态接口被 Cloudflare 拦截（403），请确认代理可用。"
        if exc.status == 0:
            return "无法连接世界状态接口，请检查网络/代理。"
        return "世界状态接口异常（HTTP %d）。" % exc.status

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
        rows = data.get("ActiveMissions") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return []
        nodes = self._load_nodes()
        result = []
        now = time.time()
        for row in rows:
            if not isinstance(row, dict):
                continue
            end = self._official_expiry(row)
            if end <= now:
                continue
            node_id = str(row.get("Node") or "")
            node = nodes.get(node_id) or {}
            node_name = terms.localize_node(str(node.get("name") or node_id))
            mission_key = str(row.get("MissionType") or "")
            mission = OFFICIAL_MISSIONS.get(mission_key, mission_key.replace("MT_", "").title())
            tier = VOID_TIERS.get(str(row.get("Modifier") or ""), "—")
            result.append({
                "id": node_id,
                "node": node_name,
                "nodeKey": node_name,
                "missionType": mission,
                "enemy": terms.localize_faction(str(node.get("enemy") or "")),
                "tier": tier,
                "expiry": datetime.fromtimestamp(
                    end, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
                "isHard": bool(row.get("Hard")),
                "isStorm": False,
                "source": "official",
            })
        result.sort(key=lambda item: (item["isHard"], item["expiry"]))
        return result

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
