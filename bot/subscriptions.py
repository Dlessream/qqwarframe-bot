"""世界状态订阅：群里订阅某类内容，出现新的就推送到群。

和「降价提醒」（`alerts.py`）的区别：
- `#提醒 绝路 50` 是价格提醒，盯着某个物品的价格；
- `#订阅 裂缝` 是事件订阅，由后台线程轮询世界状态，新裂缝/新风暴/新仲裁/
  突击刷新/回廊刷新/轮换更新/警报/入侵/特惠一出现就推送。

同一个事件只推一次：按事件 key 去重，key 落盘保存（`data/subscriptions.json`）。
进程刚启动的第一次轮询只记「基线」，不推送，避免一重启就把当前列表刷一遍。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import present
from . import terms
from .replies import Reply
from .worldstate import Arbitration, parse_timestamp

LOGGER = logging.getLogger("qqwarframe.subscriptions")

# 订阅类型 → (中文名, 命令里可用的别名)
KINDS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "fissure": ("裂缝", ("裂缝", "裂隙")),
    "storm": ("虚空风暴", ("风暴", "虚空风暴", "九重天", "storm")),
    "arbitration": ("仲裁", ("仲裁",)),
    "sortie": ("突击", ("突击",)),
    "circuit": ("回廊", ("回廊", "灵化", "无尽回廊")),
    "rotation": ("轮换", ("轮换", "商店", "言录使", "格拉斯", "埃莉诺")),
    "alert": ("警报", ("警报",)),
    "invasion": ("入侵", ("入侵",)),
    "deal": ("特惠", ("特惠", "折扣", "每日特惠")),
}

KIND_ORDER = ("fissure", "storm", "arbitration", "sortie", "circuit",
              "rotation", "alert", "invasion", "deal")


def kind_label(kind: str) -> str:
    return KINDS.get(kind, (kind, ()))[0]


def kind_names() -> str:
    return " / ".join(kind_label(kind) for kind in KIND_ORDER)


def parse_kind(text: str) -> Tuple[Optional[str], str]:
    """`裂缝 生存` → ("fissure", "生存")；认不出返回 (None, 原文)。"""
    raw = (text or "").strip()
    if not raw:
        return None, ""
    parts = raw.split(None, 1)
    head = parts[0]
    keyword = parts[1].strip() if len(parts) > 1 else ""
    low = head.lower()
    for kind, (_label, aliases) in KINDS.items():
        if low == kind or low in aliases:
            return kind, keyword
    # 允许「裂缝生存」这种连写
    for kind, (_label, aliases) in KINDS.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if low.startswith(alias) and len(low) > len(alias):
                tail = low[len(alias):]
                if keyword:
                    tail = "%s %s" % (tail, keyword)
                return kind, tail.strip()
    return None, raw


def _match(keyword: str, text: str) -> bool:
    """筛选词全都要命中（空筛选=全部）。"""
    keyword = (keyword or "").strip().lower()
    if not keyword:
        return True
    haystack = (text or "").lower()
    return all(token in haystack for token in keyword.split())


@dataclass
class Subscription:
    id: str
    chat_type: str
    chat_id: str
    user_id: str
    kind: str
    keyword: str = ""
    created_at: float = 0.0
    active: bool = True
    seen: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        label = kind_label(self.kind)
        return "%s·%s" % (label, self.keyword) if self.keyword else label

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "chat_type": self.chat_type,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "kind": self.kind,
            "keyword": self.keyword,
            "created_at": self.created_at,
            "active": self.active,
            "seen": list(self.seen),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Subscription":
        return cls(
            id=str(raw.get("id") or ""),
            chat_type=str(raw.get("chat_type") or "group"),
            chat_id=str(raw.get("chat_id") or ""),
            user_id=str(raw.get("user_id") or ""),
            kind=str(raw.get("kind") or ""),
            keyword=str(raw.get("keyword") or ""),
            created_at=float(raw.get("created_at") or 0),
            active=bool(raw.get("active", True)),
            seen=[str(item) for item in (raw.get("seen") or [])],
        )


class SubscriptionStore:
    """订阅的持久化存储（按群保存，落盘 JSON）。"""

    def __init__(self, path: str, max_per_chat: int = 20,
                 seen_limit: int = 400):
        self.path = path
        self.max_per_chat = max_per_chat
        self.seen_limit = seen_limit
        self._lock = threading.RLock()
        self._subs: List[Subscription] = []
        self._load()

    # ------------------------------------------------------------------ 持久化
    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            rows = raw.get("subscriptions") if isinstance(raw, dict) else raw
            self._subs = [Subscription.from_dict(row) for row in (rows or [])
                          if isinstance(row, dict)]
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取订阅文件失败：%s", exc)

    def _save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp = self.path + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump({"updated": time.time(),
                           "subscriptions": [sub.to_dict() for sub in self._subs]},
                          handle, ensure_ascii=False, indent=1)
            os.replace(temp, self.path)
        except OSError as exc:
            LOGGER.warning("保存订阅失败：%s", exc)

    # ------------------------------------------------------------------ 查询
    def list_for(self, chat_type: str, chat_id: str) -> List[Subscription]:
        with self._lock:
            return [sub for sub in self._subs
                    if sub.active and sub.chat_type == chat_type
                    and sub.chat_id == str(chat_id)]

    def active(self) -> List[Subscription]:
        with self._lock:
            return [sub for sub in self._subs if sub.active]

    # ------------------------------------------------------------------ 增删
    def add(self, chat_type: str, chat_id: str, user_id: str, kind: str,
            keyword: str = "") -> Tuple[Subscription, bool]:
        """返回 (订阅, 是否新建)；同群同类型同筛选视为同一条。"""
        keyword = (keyword or "").strip()
        with self._lock:
            for sub in self._subs:
                if (sub.active and sub.chat_type == chat_type
                        and sub.chat_id == str(chat_id) and sub.kind == kind
                        and sub.keyword == keyword):
                    return sub, False
            existing = [sub for sub in self._subs
                        if sub.active and sub.chat_type == chat_type
                        and sub.chat_id == str(chat_id)]
            if len(existing) >= self.max_per_chat:
                raise ValueError("这个群的订阅已经有 %d 条了，先取消几条吧。" %
                                 self.max_per_chat)
            sub = Subscription(
                id=self._new_id(), chat_type=chat_type, chat_id=str(chat_id),
                user_id=str(user_id), kind=kind, keyword=keyword,
                created_at=time.time())
            self._subs.append(sub)
            self._save()
            return sub, True

    def remove(self, chat_type: str, chat_id: str, ident: str) -> List[Subscription]:
        """按编号、类型名或 `all` 取消；返回被取消的订阅。"""
        ident = (ident or "").strip().lstrip("#").lower()
        with self._lock:
            target = [sub for sub in self._subs
                      if sub.active and sub.chat_type == chat_type
                      and sub.chat_id == str(chat_id)]
            if not target:
                return []
            if ident in ("all", "全部", "所有"):
                removed = target
            else:
                removed = [sub for sub in target if sub.id.lower() == ident]
                if not removed:
                    for sub in target:
                        if kind_label(sub.kind) == ident or sub.kind == ident:
                            removed.append(sub)
                if not removed:
                    for sub in target:
                        if _match(ident, sub.keyword.lower()):
                            removed.append(sub)
            for sub in removed:
                sub.active = False
            if removed:
                self._save()
            return removed

    def mark_seen(self, sub: Subscription, keys: Sequence[str]) -> None:
        with self._lock:
            merged = list(dict.fromkeys([str(key) for key in keys if key]
                                        + list(sub.seen)))
            sub.seen = merged[:self.seen_limit]
            self._save()

    def _new_id(self) -> str:
        existing = {sub.id for sub in self._subs}
        for _ in range(20):
            candidate = uuid.uuid4().hex[:4]
            if candidate not in existing:
                return candidate
        return uuid.uuid4().hex[:6]


class SubscriptionService:
    """轮询世界状态，把新事件变成待推送的 Reply。"""

    def __init__(self, store: SubscriptionStore, worldstate=None,
                 rotation=None, arbi=None, logger: Optional[logging.Logger] = None,
                 push_limit: int = 6):
        self.store = store
        self.worldstate = worldstate
        self.rotation = rotation
        self.arbi = arbi
        self.log = logger or LOGGER
        self.push_limit = push_limit
        self._cache: Dict[str, Any] = {}
        self._primed = False

    # ------------------------------------------------------------------ 取数
    def _fissures(self) -> List[Dict[str, Any]]:
        if "fissures" not in self._cache:
            rows: List[Dict[str, Any]] = []
            try:
                if self.worldstate is not None:
                    rows = list(self.worldstate.fissures() or [])
            except Exception as exc:  # noqa: BLE001 - 单个源失败不影响其它类型
                self.log.warning("订阅取裂缝失败：%s", exc)
            self._cache["fissures"] = rows
        return self._cache["fissures"]

    def _storms(self) -> List[Dict[str, Any]]:
        if "storms" not in self._cache:
            rows: List[Dict[str, Any]] = []
            try:
                if self.worldstate is not None:
                    rows = list(self.worldstate.void_storms() or [])
            except Exception as exc:  # noqa: BLE001
                self.log.warning("订阅取虚空风暴失败：%s", exc)
            self._cache["storms"] = rows
        return self._cache["storms"]

    def _field(self, path: str):
        key = "field:%s" % path
        if key not in self._cache:
            value: Any = None
            try:
                if self.worldstate is not None:
                    value = self.worldstate.fetch(path)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("订阅取 %s 失败：%s", path, exc)
            self._cache[key] = value
        return self._cache[key]

    # ------------------------------------------------------------------ 事件
    def events(self, kind: str) -> List[Dict[str, Any]]:
        """某类订阅当前的事件列表：[{key, raw, text, hard}]。"""
        now = time.time()
        if kind in ("fissure", "storm"):
            rows = self._storms() if kind == "storm" else self._fissures()
            events = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if parse_timestamp(row.get("expiry")) <= now:
                    continue
                storm = bool(row.get("isStorm"))
                if kind == "fissure" and storm:
                    continue
                if kind == "storm" and not storm:
                    continue
                events.append({
                    "key": "%s|%s|%s" % (row.get("id") or row.get("nodeKey")
                                         or row.get("node") or "?",
                                         row.get("missionType") or "",
                                         row.get("expiry") or ""),
                    "raw": row,
                    "hard": bool(row.get("isHard")),
                    # 筛选词按卡面中文匹配：节点/任务/遗物等级/敌人
                    "text": " ".join((
                        terms.localize_node(str(row.get("node") or "")),
                        terms.localize_mission(str(row.get("missionType") or "")),
                        terms.localize_relic_tier(str(row.get("tier") or "")),
                        terms.localize_faction(str(row.get("enemy") or "")),
                    )),
                })
            return events

        if kind == "rotation":
            return self._rotation_events()

        if kind in ("alert", "invasion", "deal"):
            rows = self._field({"alert": "alerts", "invasion": "invasions",
                                "deal": "dailyDeals"}[kind])
            events = []
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                if kind == "alert":
                    key = str(row.get("id") or "%s|%s" % (row.get("node"), row.get("expiry")))
                    mission = row.get("mission") or {}
                    text = " ".join(str(item or "") for item in (
                        mission.get("node"), mission.get("type"),
                        mission.get("faction")))
                elif kind == "invasion":
                    key = str(row.get("id") or "%s|%s" % (row.get("node"), row.get("expiry")))
                    text = str(row.get("node") or "")
                else:
                    key = "%s|%s" % (row.get("item") or row.get("type") or "?",
                                     row.get("expiry") or "")
                    text = str(row.get("item") or row.get("type") or "")
                events.append({"key": key, "raw": row, "hard": False, "text": text})
            return events

        if kind == "sortie":
            raw = self._field("sortie")
            if not isinstance(raw, dict) or not raw.get("expiry"):
                return []
            variants = " ".join(
                "%s %s %s" % (item.get("missionType") or "", item.get("node") or "",
                              item.get("modifierDescription") or "")
                for item in (raw.get("variants") or []) if isinstance(item, dict))
            return [{
                "key": "%s|%s" % (raw.get("boss") or "?", raw.get("expiry")),
                "raw": raw, "hard": False,
                "text": "%s %s %s" % (raw.get("boss") or "", raw.get("faction") or "", variants),
            }]

        if kind == "arbitration":
            raw = self._field("arbitration")
            key_text = str((raw or {}).get("nodeKey") or "") if isinstance(raw, dict) else ""
            if (not isinstance(raw, dict) or not key_text
                    or key_text == "SolNode000" or raw.get("expired")):
                # 官方兜底源只给 SolNode000 占位，按本地轮换表补当前仲裁
                raw = self._arbi_now() or raw
            if not isinstance(raw, dict) or not raw.get("expiry"):
                return []
            if raw.get("expired"):
                return []
            return [{
                "key": "%s|%s" % (raw.get("nodeKey") or raw.get("node") or "?",
                                  raw.get("expiry")),
                "raw": raw, "hard": False,
                "text": "%s %s %s" % (raw.get("node") or "", raw.get("type") or "",
                                      raw.get("enemy") or ""),
            }]

        if kind == "circuit":
            raw = self._field("duviriCycle")
            if not isinstance(raw, dict) or not raw.get("choices"):
                return []
            choices = []
            for group in raw.get("choices") or []:
                if isinstance(group, dict):
                    choices.extend(str(item) for item in (group.get("choices") or []))
            return [{
                "key": "%s|%s" % (raw.get("expiry") or "", "、".join(choices)),
                "raw": raw, "hard": False,
                "text": " ".join(choices),
            }]

        return []

    def _arbi_now(self) -> Optional[Dict[str, Any]]:
        """官方兜底不给仲裁时，按本地 arbi 轮换表算当前/下一场。"""
        if self.arbi is None:
            return None
        try:
            entries = self.arbi.today()
        except Exception as exc:  # noqa: BLE001
            self.log.warning("订阅算仲裁失败：%s", exc)
            return None
        now = time.time()
        picked = None
        for start, end, node, _tier in entries:
            if start <= now < end:
                picked = (end, node)
                break
        if picked is None and entries:
            _start, end, node, _tier = entries[0]
            picked = (end, node)
        if picked is None:
            return None
        end, node = picked
        return {
            "node": node.display_name,
            "nodeKey": node.key,
            "type": node.mission_type or node.mission,
            "enemy": node.faction,
            "expiry": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end)),
            "expired": False,
            "source": "arbi",
        }

    def _rotation_events(self) -> List[Dict[str, Any]]:
        if self.rotation is None:
            return []
        try:
            snap = self.rotation.snapshot()
        except Exception as exc:  # noqa: BLE001
            self.log.warning("订阅取轮换失败：%s", exc)
            return []
        data = snap.get("data") or {}
        if not data:
            return []
        payload = json.dumps(data, sort_keys=True, ensure_ascii=False)
        digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]
        return [{"key": "rotation:%s" % digest, "raw": {}, "hard": False,
                 "text": payload[:4000]}]

    # ------------------------------------------------------------------ 推送
    def seed(self, sub: Subscription) -> None:
        """新建订阅时把当前事件记为「已见」，避免一次性刷一屏。"""
        try:
            events = self.events(sub.kind)
        except Exception as exc:  # noqa: BLE001
            self.log.warning("初始化订阅基线失败（%s）：%s", sub.kind, exc)
            return
        self.store.mark_seen(sub, [event["key"] for event in events])

    def poll(self) -> List[Tuple[str, Reply, str]]:
        """检查一轮；返回 [(群号, 待推送 Reply, 订阅人 QQ), ...]。

        订阅人用来在推送时 @ 一下（普通回复不 @，只有订阅推送 @）。
        """
        self._cache.clear()
        subs = self.store.active()
        if not subs:
            return []
        seed_only = not self._primed
        pushes: List[Tuple[str, Reply, str]] = []
        for sub in subs:
            try:
                events = self.events(sub.kind)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("订阅轮询失败（%s）：%s", sub.kind, exc)
                continue
            keys = [event["key"] for event in events]
            if seed_only:
                self.store.mark_seen(sub, keys)
                continue
            seen = set(sub.seen)
            unseen = [key for key in keys if key not in seen]
            fresh = [event for event in events
                     if event["key"] not in seen and _match(sub.keyword, event["text"])]
            if fresh:
                try:
                    replies = self.render(sub.kind, fresh[:self.push_limit])
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("订阅出图失败（%s）：%s", sub.kind, exc)
                    replies = []
                for reply in replies:
                    pushes.append((sub.chat_id, reply, sub.user_id))
            if unseen:
                self.store.mark_seen(sub, keys)
        self._primed = True
        return pushes

    def render(self, kind: str, events: List[Dict[str, Any]]) -> List[Reply]:
        raws = [event["raw"] for event in events]
        if kind == "fissure":
            replies = []
            normal = [event["raw"] for event in events if not event["hard"]]
            hard = [event["raw"] for event in events if event["hard"]]
            if normal:
                replies.append(present.fissures_reply(normal))
            if hard:
                replies.append(present.fissures_reply(hard, hard=True))
            return replies
        if kind == "storm":
            return [present.storms_reply(raws)]
        if kind == "alert":
            return [present.alerts_reply(raws)]
        if kind == "invasion":
            return [present.invasions_reply(raws)]
        if kind == "deal":
            return [present.deals_reply(raws)]
        if kind == "circuit":
            return [present.circuit_reply(raws[0])]
        if kind == "sortie" and self.worldstate is not None:
            return [present.sortie_reply(self.worldstate.sortie())]
        if kind == "arbitration":
            raw = raws[0]
            arbitration = Arbitration(
                node=terms.localize_node(str(raw.get("node") or "—")),
                mission=terms.localize_mission(str(raw.get("type") or "")),
                enemy=terms.localize_faction(str(raw.get("enemy") or "")),
                end=parse_timestamp(raw.get("expiry")),
                node_key=str(raw.get("nodeKey") or ""))
            return [present.arbitration_reply(arbitration)]
        if kind == "rotation" and self.rotation is not None:
            return [present.rotation_overview_reply(
                self.rotation.acrithis_view(), self.rotation.glast_view(),
                self.rotation.eleanor_view())]
        return []
