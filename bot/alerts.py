"""降价提醒的持久化存储。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("qqwarframe.alerts")


def chat_key(chat_type: str, chat_id: str) -> str:
    return "%s:%s" % ("g" if chat_type == "group" else "p", chat_id)


@dataclass
class Alert:
    id: str
    chat_type: str
    chat_id: str
    user_id: str
    slug: str
    name: str
    target: int
    platform: str
    created_at: float
    active: bool = True
    last_price: Optional[int] = None

    @property
    def key(self) -> str:
        return chat_key(self.chat_type, self.chat_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "chat_type": self.chat_type,
            "chat_id": self.chat_id,
            "user_id": self.user_id,
            "slug": self.slug,
            "name": self.name,
            "target": self.target,
            "platform": self.platform,
            "created_at": self.created_at,
            "active": self.active,
            "last_price": self.last_price,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Alert":
        return cls(
            id=str(raw.get("id") or ""),
            chat_type=str(raw.get("chat_type") or "group"),
            chat_id=str(raw.get("chat_id") or ""),
            user_id=str(raw.get("user_id") or ""),
            slug=str(raw.get("slug") or ""),
            name=str(raw.get("name") or raw.get("slug") or ""),
            target=int(raw.get("target") or 0),
            platform=str(raw.get("platform") or "pc"),
            created_at=float(raw.get("created_at") or 0),
            active=bool(raw.get("active", True)),
            last_price=int(raw["last_price"]) if raw.get("last_price") is not None else None,
        )


class AlertStore:
    def __init__(self, path: str, max_per_chat: int = 20):
        self.path = path
        self.max_per_chat = max_per_chat
        self._lock = threading.RLock()
        self._alerts: List[Alert] = []
        self._load()

    # ------------------------------------------------------------------ 持久化
    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            rows = raw.get("alerts") if isinstance(raw, dict) else raw
            self._alerts = [Alert.from_dict(row) for row in (rows or [])
                            if isinstance(row, dict)]
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取提醒文件失败：%s", exc)

    def _save(self) -> None:
        if not self.path:
            return
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp = self.path + ".tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump({"alerts": [alert.to_dict() for alert in self._alerts]},
                          handle, ensure_ascii=False, indent=1)
            os.replace(temp, self.path)
        except OSError as exc:
            LOGGER.warning("保存提醒失败：%s", exc)

    # ------------------------------------------------------------------ 操作
    def _new_id(self) -> str:
        existing = {alert.id for alert in self._alerts}
        for _ in range(20):
            candidate = uuid.uuid4().hex[:4]
            if candidate not in existing:
                return candidate
        return uuid.uuid4().hex[:6]

    def add(self, chat_type: str, chat_id: str, user_id: str, slug: str,
            name: str, target: int, platform: str) -> Alert:
        with self._lock:
            key = chat_key(chat_type, chat_id)
            current = [alert for alert in self._alerts if alert.active and alert.key == key]
            if len(current) >= self.max_per_chat:
                raise ValueError("本会话的提醒数量已达上限（%d 条）" % self.max_per_chat)
            for alert in current:
                if alert.slug == slug and alert.platform == platform:
                    raise ValueError("已经提醒过「%s」了（#%s，目标 %dp）"
                                     % (alert.name, alert.id, alert.target))
            alert = Alert(
                id=self._new_id(),
                chat_type=chat_type,
                chat_id=chat_id,
                user_id=user_id,
                slug=slug,
                name=name,
                target=target,
                platform=platform,
                created_at=time.time(),
            )
            self._alerts.append(alert)
            self._save()
            return alert

    def remove(self, chat_type: str, chat_id: str, ident: str) -> int:
        with self._lock:
            key = chat_key(chat_type, chat_id)
            removed = 0
            for alert in self._alerts:
                if alert.key != key or not alert.active:
                    continue
                if ident == "all" or alert.id == ident:
                    alert.active = False
                    removed += 1
            if removed:
                self._save()
            return removed

    def list_for(self, chat_type: str, chat_id: str) -> List[Alert]:
        with self._lock:
            key = chat_key(chat_type, chat_id)
            return [alert for alert in self._alerts if alert.active and alert.key == key]

    def active(self) -> List[Alert]:
        with self._lock:
            return [alert for alert in self._alerts if alert.active]

    def update(self, alert: Alert) -> None:
        with self._lock:
            for index, existing in enumerate(self._alerts):
                if existing.id == alert.id:
                    self._alerts[index] = alert
                    break
            self._save()

    def set_price(self, alert: Alert, price: Optional[int]) -> None:
        with self._lock:
            alert.last_price = price
            self.update(alert)
