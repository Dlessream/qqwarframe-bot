"""仲裁评级：数据来自 arbi.wf.wiki 的静态 JSON。

  /data/tierlist.default.json    节点 → 梯队（S / A+ / A / A- / B / C）
  /data/arbys.nodes.zh.json      节点 → 名称、星球、任务、派系、等级
  /data/arbys.schedule.v2.json   仲裁轮换表（用于官方 API 过期时兜底）

只读解析，不执行页面脚本。

梯队优先级：社区梯队 > 人工评级（控制台填的）> 本地推算（arbirate）。
社区梯队只覆盖十几个热门节点，其余靠后两级补上，所以任何节点都能给出评级。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import arbirate, terms
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
    mission_type: str = ""

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


RATING_SOURCE_LABEL = {
    "community": "社区梯队",
    "manual": "人工评级",
    "local": "本地推算",
}


@dataclass
class ArbiData:
    nodes: Dict[str, ArbiNode]
    tiers: Dict[str, str]
    order: List[str]
    # 控制台填的人工评级与是否启用本地推算，由 ArbiClient 注入
    overrides: Dict[str, str] = field(default_factory=dict)
    use_local: bool = True

    def community_tier(self, key: str) -> str:
        return self.tiers.get(key, "")

    def rating(self, key: str) -> str:
        """社区 > 人工 > 本地推算；都没有才算未评级。"""
        tier, _source = self.resolve(key)
        return tier or "未评级"

    def resolve(self, key: str) -> Tuple[str, str]:
        """返回 (梯队, 来源)。来源为 community / manual / local / 空。"""
        community = self.tiers.get(key, "")
        if community:
            return community, "community"
        manual = self.overrides.get(key, "")
        if manual:
            return manual, "manual"
        if self.use_local:
            node = self.nodes.get(key)
            local = arbirate.local_rating(node)
            if local:
                return local, "local"
        return "", ""

    def rating_label(self, key: str) -> str:
        """给卡片用的完整写法：A+（本地推算）。"""
        tier, source = self.resolve(key)
        if not tier:
            return "未评级"
        label = RATING_SOURCE_LABEL.get(source, "")
        return "%s（%s）" % (tier, label) if label else tier

    @property
    def rated_count(self) -> int:
        return len(self.tiers)

    def rated(self) -> Dict[str, str]:
        """社区梯队 + 人工评级（权威来源，不含本地推算）。"""
        merged = dict(self.overrides)
        merged.update(self.tiers)
        return merged

    def counts(self) -> Dict[str, int]:
        result = {"community": 0, "manual": 0, "local": 0, "none": 0}
        for key in self.nodes:
            _tier, source = self.resolve(key)
            result[source or "none"] = result.get(source or "none", 0) + 1
        return result

    def orders(self) -> List[str]:
        order = [tier for tier in self.order if tier]
        for tier in arbirate.TIER_ORDER:
            if tier not in order:
                order.append(tier)
        return order

    def groups(self) -> List[Tuple[str, List[ArbiNode]]]:
        """按社区梯队 / 人工评级分组（本地推算不混进来）。"""
        rated = self.rated()
        result = []
        for tier in self.orders():
            nodes = [self.nodes[key] for key, value in rated.items()
                     if value == tier and key in self.nodes]
            if nodes:
                nodes.sort(key=lambda node: node.name.lower())
                result.append((tier, nodes))
        return result

    def local_groups(self) -> List[Tuple[str, List[ArbiNode]]]:
        """只按本地推算分组：社区和人工都没收录的节点。"""
        rows = []
        for key, node in self.nodes.items():
            tier, source = self.resolve(key)
            if source == "local" and tier:
                rows.append((tier, node))
        result = []
        for tier in self.orders():
            nodes = [node for value, node in rows if value == tier]
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
            mission_type=_clean(row.get("missionType")),
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
        self.ratings_file = str(config.get("ratings_file", "data/arbi_ratings.json"))
        self.use_local = bool(config.get("local_rating", True))
        self._overrides: Dict[str, str] = {}
        self._overrides_at = -1.0

    # ------------------------------------------------------------------ 人工评级
    def overrides(self) -> Dict[str, str]:
        """读控制台填的人工评级；文件改过才重新读。"""
        try:
            stamp = os.path.getmtime(self.ratings_file)
        except OSError:
            stamp = -1.0
        with self._lock:
            if stamp == self._overrides_at:
                return dict(self._overrides)
        mapping = arbirate.load_overrides(self.ratings_file)
        with self._lock:
            self._overrides = mapping
            self._overrides_at = stamp
        return dict(mapping)

    def set_rating(self, key: str, tier: str) -> Dict[str, Any]:
        """写一个人工评级；tier 传空表示删掉这条。"""
        key = str(key or "").strip()
        if not key:
            raise ValueError("缺少节点编号。")
        mapping = self.overrides()
        if key not in self.data().nodes and not tier:
            raise ValueError("未知节点：%s" % key)
        tier = arbirate.normalize_tier(tier)
        if tier:
            mapping[key] = tier
        else:
            mapping.pop(key, None)
        arbirate.save_overrides(self.ratings_file, mapping)
        with self._lock:
            self._overrides = dict(mapping)
            self._overrides_at = -1.0
            if self._data is not None:
                self._data.overrides = dict(mapping)
        node = self.data().nodes.get(key)
        return {
            "ok": True,
            "key": key,
            "tier": self.data().rating(key),
            "source": self.data().resolve(key)[1],
            "name": node.name if node else "",
            "text": "%s → %s" % (node.name if node else key,
                                 self.data().rating_label(key)),
        }

    def clear_rating(self, key: str) -> Dict[str, Any]:
        return self.set_rating(key, "")

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
        cached = None
        with self._lock:
            if (not force and self._data is not None
                    and time.time() - self._data_at < self.cache_seconds):
                cached = self._data
        # overrides() 自己会加锁，不能在上面的 with 里调用（Lock 不可重入）
        overrides = self.overrides()
        if cached is not None:
            cached.overrides = overrides
            return cached
        tier_raw = self._get_json("/data/tierlist.default.json")
        nodes_raw = self._get_json("/data/arbys.nodes.zh.json")
        data = parse_data(tier_raw, nodes_raw)
        if not data.nodes:
            raise ArbiError("arbi.wf.wiki 节点数据为空。")
        data.overrides = overrides
        data.use_local = self.use_local
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

    def rating_label(self, key: str) -> str:
        return self.data().rating_label(key)

    def rating_source(self, key: str) -> str:
        """community / manual / local / 空。"""
        return self.data().resolve(key)[1]

    def groups(self) -> List[Tuple[str, List[ArbiNode]]]:
        return self.data().groups()

    def nodes_snapshot(self) -> Dict[str, Any]:
        """控制台用的节点列表：社区 / 人工 / 本地 / 最终都给出。"""
        data = self.data()
        rows = arbirate.summarize(list(data.nodes.values()),
                                  data.overrides, data.tiers)
        return {
            "nodes": rows,
            "counts": data.counts(),
            "tiers": arbirate.TIER_ORDER,
            "local_rating": data.use_local,
            "ratings_file": self.ratings_file,
        }

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

    def schedule_entries(self, start_ts: float,
                         end_ts: float) -> List[Tuple[float, float, ArbiNode, str]]:
        """按轮换表返回 [start_ts, end_ts) 内的仲裁节点。"""
        try:
            schedule = self._schedule_data()
            keys = schedule.get("nodes") or []
            seq = schedule.get("seq") or []
            start = int(schedule.get("startTs") or 0)
            step = int(schedule.get("stepSec") or 3600)
            if not keys or not seq or step <= 0:
                return []
            data = self.data()
            first = max(0, int((start_ts - start) // step))
            last = int((end_ts - start) // step) + 1
            entries = []
            for index in range(first, min(last, first + 500)):
                ts = start + index * step
                if ts >= end_ts:
                    break
                if ts + step <= start_ts:
                    continue
                key = str(keys[int(seq[index % len(seq)])])
                node = data.nodes.get(key)
                if node is None:
                    continue
                entries.append((float(ts), float(ts + step), node,
                                data.rating(key)))
            return entries
        except ArbiError as exc:
            self.log.warning("仲裁轮换数据不可用：%s", exc)
            return []

    def today(self, now: Optional[float] = None
              ) -> List[Tuple[float, float, ArbiNode, str]]:
        """今日仲裁：从现在起 24 小时内的轮换，已经过时的不再列出。"""
        now = time.time() if now is None else now
        return self.schedule_entries(now, now + 86400)

    def upcoming(self, days: int = 7, now: Optional[float] = None
                 ) -> List[Tuple[float, float, ArbiNode, str]]:
        """未来 N 天（1 - 7）的完整仲裁排期，含当前这一场。"""
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 7
        days = max(1, min(days, 7))
        now = time.time() if now is None else now
        # 恰好 days × 24 场：末尾那一场可能已经跨到下一天的同一时刻
        return self.schedule_entries(now, now + days * 86400)[:days * 24]

    def effective(self, now: Optional[float] = None, hours: int = 24
                  ) -> List[Tuple[float, float, ArbiNode, str]]:
        now = time.time() if now is None else now
        rank = {"S": 0, "A+": 1, "A": 2, "A-": 3}
        entries = [entry for entry in self.schedule_entries(
            now, now + hours * 3600) if entry[3] in rank]
        entries.sort(key=lambda entry: (rank[entry[3]], entry[0]))
        return entries

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
