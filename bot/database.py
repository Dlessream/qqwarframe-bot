"""SQLite 数据库的只读入口。

数据库由 `db/build.py` 生成。bot 运行时仍以 JSON 为主；这里提供状态、
搜索和校验，方便 Web 控制台/运维查看，不改变现有查询逻辑。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("qqwarframe.database")


class DatabaseService:
    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 root: Optional[str] = None,
                 logger: Optional[logging.Logger] = None):
        cfg = config or {}
        self.log = logger or LOGGER
        self.enabled = bool(cfg.get("enabled", True))
        self.root = root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        path = str(cfg.get("path") or "data/warframe.db")
        if not os.path.isabs(path):
            path = os.path.join(self.root, path)
        self.path = os.path.abspath(path)

    def available(self) -> bool:
        return self.enabled and os.path.isfile(self.path)

    def _query(self):
        from db import query
        return query

    def snapshot(self) -> Dict[str, Any]:
        if not self.available():
            return {
                "available": False,
                "enabled": self.enabled,
                "path": self.path,
                "message": "数据库尚未生成，先运行 python db/build.py",
            }
        try:
            query = self._query()
            conn = query.connect(self.path)
            try:
                result = query.stats(conn)
            finally:
                conn.close()
            return {
                "available": True,
                "enabled": True,
                "path": self.path,
                "size": os.path.getsize(self.path),
                "schema_version": (result.get("meta") or {}).get("schema_version"),
                "built_at": (result.get("meta") or {}).get("built_at"),
                "total_rows": result.get("total_rows", 0),
                "items": (result.get("key") or {}).get("items", 0),
                "sources": len(result.get("sources") or []),
                "key": result.get("key") or {},
            }
        except Exception as exc:
            self.log.warning("读取数据库状态失败：%s", exc)
            return {"available": False, "enabled": self.enabled,
                    "path": self.path, "message": str(exc)}

    def search(self, text: str, limit: int = 10) -> Dict[str, Any]:
        if not self.available():
            raise RuntimeError("数据库尚未生成，先运行 python db/build.py")
        query = self._query()
        conn = query.connect(self.path)
        try:
            rows = query.search_items(conn, text, limit=limit)
        finally:
            conn.close()
        return {"query": text, "items": rows}

    def validate(self) -> Dict[str, Any]:
        if not self.available():
            return {"ok": False, "errors": ["数据库尚未生成"],
                    "warnings": [], "integrity": "missing"}
        query = self._query()
        conn = query.connect(self.path)
        try:
            return query.validate(conn)
        finally:
            conn.close()
