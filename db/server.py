#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qqwarframe-db：独立的只读数据库 HTTP 服务。

不依赖 bot 包，也不需要启动 QQ/OneBot。只读打开 db/build.py 生成的 SQLite，
通过 HTTP 提供状态、搜索、物品、套装部件和校验接口。

启动：
    python db/server.py --db data/warframe.db --host 127.0.0.1 --port 8090

带 token：
    python db/server.py --token 你的token

接口：
    GET /                   网页管理界面
    GET /health
    GET /stats
    GET /sources
    GET /search?q=女枪&limit=10
    GET /item?q=mesa_prime_set
    GET /parts?q=女枪
    GET /validate
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

try:  # 作为包导入（python -m db.server / unittest）
    from . import query
except ImportError:  # 独立目录直接 python server.py
    import query  # type: ignore


LOGGER = logging.getLogger("qqwarframe.db.service")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "data", "warframe.db")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class DatabaseService:
    """服务端业务逻辑；所有方法只读。"""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = os.path.abspath(db_path)

    def available(self) -> bool:
        return os.path.isfile(self.db_path)

    def _connection(self):
        return query.connect(self.db_path)

    def health(self) -> Dict[str, Any]:
        if not self.available():
            return {"ok": False, "available": False,
                    "message": "数据库不存在", "path": self.db_path}
        try:
            snapshot = self.stats()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "available": False,
                    "message": str(exc), "path": self.db_path}
        return {"ok": True, "available": True,
                "schema_version": snapshot["meta"].get("schema_version"),
                "built_at": snapshot["meta"].get("built_at"),
                "items": snapshot["key"].get("items", 0)}

    def stats(self) -> Dict[str, Any]:
        conn = self._connection()
        try:
            result = query.stats(conn)
        finally:
            conn.close()
        result.pop("db", None)
        return result

    def sources(self) -> Dict[str, Any]:
        conn = self._connection()
        try:
            return {"sources": query.sources(conn)}
        finally:
            conn.close()

    def search(self, text: str, limit: int = 10) -> Dict[str, Any]:
        conn = self._connection()
        try:
            return {"query": text,
                    "items": query.search_items(conn, text, limit=limit)}
        finally:
            conn.close()

    def item(self, text: str) -> Dict[str, Any]:
        conn = self._connection()
        try:
            return query.item_detail(conn, text)
        finally:
            conn.close()

    def parts(self, text: str) -> Dict[str, Any]:
        conn = self._connection()
        try:
            return query.set_parts(conn, text)
        finally:
            conn.close()

    def validate(self) -> Dict[str, Any]:
        conn = self._connection()
        try:
            return query.validate(conn)
        finally:
            conn.close()


class DatabaseServer:
    def __init__(self, db_path: str = DEFAULT_DB, host: str = "127.0.0.1",
                 port: int = 8090, token: str = ""):
        self.service = DatabaseService(db_path)
        self.host = host
        self.requested_port = int(port)
        self.token = str(token or "")
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.port = int(port)

    def _authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.token:
            return True
        supplied = (handler.headers.get("X-API-Token")
                    or handler.headers.get("X-DB-Token") or "")
        if not supplied:
            auth = handler.headers.get("Authorization") or ""
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        return supplied == self.token

    def start(self) -> None:
        if self.httpd is not None:
            return
        service = self.service
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "QQWarframeDB/1.0"
            sys_version = ""

            def log_message(self, fmt, *args):
                LOGGER.debug("http: " + fmt, *args)

            def _json(self, payload: Dict[str, Any],
                      status: int = HTTPStatus.OK) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _error(self, message: str, status: int) -> None:
                self._json({"ok": False, "error": message}, status)

            def _file(self, filename: str, content_type: str) -> None:
                path = os.path.join(WEB_DIR, filename)
                try:
                    with open(path, "rb") as handle:
                        body = handle.read()
                except OSError:
                    self._error("not found", HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _require_db(self) -> bool:
                if service.available():
                    return True
                self._error("数据库不存在，先运行 python db/build.py",
                            HTTPStatus.SERVICE_UNAVAILABLE)
                return False

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                params = parse_qs(parsed.query)
                if path in ("/", "/index.html"):
                    self._file("index.html", "text/html; charset=utf-8")
                    return
                if path == "/static/style.css":
                    self._file("style.css", "text/css; charset=utf-8")
                    return
                if path == "/static/app.js":
                    self._file("app.js", "application/javascript; charset=utf-8")
                    return
                if path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                if path == "/health":
                    result = service.health()
                    self._json(result, HTTPStatus.OK if result["ok"]
                               else HTTPStatus.SERVICE_UNAVAILABLE)
                    return

                if not server._authorized(self):
                    self._error("unauthorized", HTTPStatus.UNAUTHORIZED)
                    return
                if not self._require_db():
                    return

                try:
                    if path == "/stats":
                        self._json({"ok": True, **service.stats()})
                        return
                    if path == "/sources":
                        self._json({"ok": True, **service.sources()})
                        return
                    if path == "/validate":
                        result = service.validate()
                        self._json({"ok": bool(result.get("ok")), **result},
                                   HTTPStatus.OK if result.get("ok")
                                   else HTTPStatus.UNPROCESSABLE_ENTITY)
                        return
                    if path in ("/search", "/item", "/parts"):
                        text = (params.get("q") or [""])[0].strip()
                        if not text:
                            self._error("缺少 q 参数", HTTPStatus.BAD_REQUEST)
                            return
                        if path == "/search":
                            try:
                                limit = int((params.get("limit") or ["10"])[0])
                            except ValueError:
                                limit = 10
                            result = service.search(text, max(1, min(50, limit)))
                            self._json({"ok": True, **result})
                        elif path == "/item":
                            self._json({"ok": True, "item": service.item(text)})
                        else:
                            self._json({"ok": True, **service.parts(text)})
                        return
                    self._error("not found", HTTPStatus.NOT_FOUND)
                except query.QueryError as exc:
                    self._error(str(exc), HTTPStatus.NOT_FOUND)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("数据库接口失败")
                    self._error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

        self.httpd = ThreadingHTTPServer((self.host, self.requested_port),
                                         Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       name="qqwarframe-db", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.httpd = None
        self.thread = None

    def serve_forever(self) -> None:
        self.start()
        assert self.thread is not None
        LOGGER.info("数据库服务已启动：http://%s:%s", self.host, self.port)
        try:
            self.thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="qqwarframe-db 独立只读 HTTP 服务")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite 文件路径")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8090, help="监听端口，0 为随机")
    parser.add_argument("--token", default=os.environ.get("DB_API_TOKEN", ""),
                        help="可选 API token（也读 DB_API_TOKEN）")
    parser.add_argument("--quiet", action="store_true", help="减少日志")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.token:
        LOGGER.warning("监听 %s 但未设置 token，建议只绑定 127.0.0.1", args.host)
    server = DatabaseServer(args.db, args.host, args.port, args.token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
