"""仲裁评级：数据来自 arbi.wf.wiki 的静态 JSON。

  /data/tierlist.default.json    节点 → 梯队（S / A+ / A / A- / B / C）
  /data/arbys.nodes.zh.json      节点 → 名称、星球、任务、派系、等级
  /data/arbys.schedule.v2.json   仲裁轮换表（用于官方 API 过期时兜底）

只读解析，不执行页面脚本。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import terms
from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.arbi")

BASE_URL = "https://arbi.wf.wiki"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class ArbiError(Exception):
    """仲裁评级数据不可用。"""


@dataclass(frozen=True)
class ArbiNode:
    key: str
    name: str
    system: str
    mission: str
    faction: str
    min_level: int = 0
    max_level: int = 0

    @property
    def display_name(self) -> str:
        if self.system:
            return "%s（%s）" % (self.name, self.system)
        return self.name

    @property
    def level_text(self) -> str:
        if self.max_level:
            return "%d - %d" % (self.min_level, self.max_level)
        return "—"


@dataclass
class ArbiData:
    nodes: Dict[str, ArbiNode]
    tiers: Dict[str, str]
    order: List[str]

    def rating(self, key: str) -> str:
        return self.tiers.get(key, "未评级")

    @property
    def rated_count(self) -> int:
        return len(self.tiers)

    def groups(self) -> List[Tuple[str, List[ArbiNode]]]:
        result = []
        for tier in self.order:
            nodes = [self.nodes[key] for key, value in self.tiers.items()
                     if value == tier and key in self.nodes]
            if nodes:
                nodes.sort(key=lambda node: node.name.lower())
                result.append((tier, nodes))
        return result


def _clean(value: Any) -> str:
    return str(value or "").strip()


def parse_data(tier_raw: Dict[str, Any],
               nodes_raw: Dict[str, Any]) -> ArbiData:
    nodes: Dict[str, ArbiNode] = {}
    for key, row in (nodes_raw.get("nodes") or {}).items():
        if not isinstance(row, dict):
            continue
        nodes[str(key)] = ArbiNode(
            key=str(key),
            name=_clean(row.get("nameZh")),
            system=_clean(row.get("systemNameZh")),
            mission=_clean(row.get("missionNameZh")),
            faction=_clean(row.get("factionNameZh")),
            min_level=int(row.get("minEnemyLevel") or 0),
            max_level=int(row.get("maxEnemyLevel") or 0),
        )
    tiers: Dict[str, str] = {}
    for tier, keys in (tier_raw.get("tierBuckets") or {}).items():
        if tier == "未评级":
            continue
        for key in keys or []:
            tiers[str(key)] = str(tier)
    order = [str(tier) for tier in (tier_raw.get("tiers") or [])
             if tier != "未评级"]
    return ArbiData(nodes=nodes, tiers=tiers, order=order)


class ArbiClient:
    def __init__(self, config: Dict[str, Any],
                 logger: Optional[logging.Logger] = None):
        self.base_url = str(config.get("base_url", BASE_URL)).rstrip("/")
        self.log = logger or LOGGER
        self.cache_seconds = max(300, int(config.get("cache_seconds", 21600)))
        self.http = HttpClient(
            user_agent=config.get("user_agent", BROWSER_UA),
            timeout=config.get("timeout_seconds", 20),
            proxy=config.get("proxy", ""),
            retries=config.get("retries", 1),
            logger=self.log,
        )
        self._lock = threading.Lock()
        self._data: Optional[ArbiData] = None
        self._data_at = 0.0
        self._schedule: Optional[Dict[str, Any]] = None
        self._schedule_at = 0.0

    # ------------------------------------------------------------------ HTTP
    def _get_json(self, path: str) -> Dict[str, Any]:
        try:
            data = self.http.get_json(self.base_url + path)
        except HttpError as exc:
            raise ArbiError("arbi.wf.wiki 访问失败（%s）" % exc)
        if not isinstance(data, dict):
            raise ArbiError("arbi.wf.wiki 返回格式异常。")
        return data

    # ------------------------------------------------------------------ 数据
    def data(self, force: bool = False) -> ArbiData:
        with self._lock:
            if (not force and self._data is not None
                    and time.time() - self._data_at < self.cache_seconds):
                return self._data
        tier_raw = self._get_json("/data/tierlist.default.json")
        nodes_raw = self._get_json("/data/arbys.nodes.zh.json")
        data = parse_data(tier_raw, nodes_raw)
        if not data.nodes:
            raise ArbiError("arbi.wf.wiki 节点数据为空。")
        with self._lock:
            self._data = data
            self._data_at = time.time()
        return data

    def refresh(self) -> int:
        return self.data(force=True).rated_count

    def cache_age(self) -> float:
        with self._lock:
            return (time.time() - self._data_at) if self._data_at else -1.0

    def node(self, key: str) -> Optional[ArbiNode]:
        return self.data().nodes.get(key)

    def rating(self, key: str) -> str:
        return self.data().rating(key)

    def groups(self) -> List[Tuple[str, List[ArbiNode]]]:
        return self.data().groups()

    # ------------------------------------------------------------------ 轮换
    def _schedule_data(self) -> Dict[str, Any]:
        with self._lock:
            if (self._schedule is not None
                    and time.time() - self._schedule_at < self.cache_seconds):
                return self._schedule
        raw = self._get_json("/data/arbys.schedule.v2.json")
        with self._lock:
            self._schedule = raw
            self._schedule_at = time.time()
        return raw

    def current_node(self, now: Optional[float] = None
                     ) -> Optional[Tuple[ArbiNode, float]]:
        try:
            schedule = self._schedule_data()
            keys = schedule.get("nodes") or []
            seq = schedule.get("seq") or []
            start = int(schedule.get("startTs") or 0)
            step = int(schedule.get("stepSec") or 3600)
            if not keys or not seq or step <= 0:
                return None
            now = time.time() if now is None else now
            index = int((now - start) // step)
            key = str(keys[int(seq[index % len(seq)])])
            node = self.node(key)
            if node is None:
                return None
            expiry = start + (index + 1) * step
            return node, float(expiry)
        except ArbiError as exc:
            self.log.warning("仲裁轮换数据不可用：%s", exc)
            return None

    # ------------------------------------------------------------------ 查找
    def find_node(self, text: str) -> Optional[ArbiNode]:
        text = (text or "").strip()
        if not text:
            return None
        data = self.data()
        if text in data.nodes:
            return data.nodes[text]

        match = re.match(r"^(.+?)\s*[（(]\s*(.+?)\s*[)）]$", text)
        if match:
            name = match.group(1).strip().lower()
            system = match.group(2).strip()
            system = terms.PLANETS.get(system, system)
            for node in data.nodes.values():
                if node.name.lower() == name and node.system == system:
                    return node
        else:
            name = text.lower()

        matches = [node for node in data.nodes.values()
                   if node.name.lower() == name]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
        return None
