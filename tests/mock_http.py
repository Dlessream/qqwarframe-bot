"""测试用的本地 HTTP 服务（模拟 warframe.market 与中文维基）。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

HandlerFunc = Callable[[str, Dict[str, List[str]], Dict[str, str]], Tuple[int, bytes]]


def json_response(payload, status: int = 200) -> Tuple[int, bytes]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")


class MockServer:
    def __init__(self) -> None:
        self.requests: List[Dict] = []
        self._routes: Dict[str, HandlerFunc] = {}
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ 配置
    def add(self, path: str, handler: HandlerFunc) -> "MockServer":
        self._routes[path] = handler
        return self

    def add_json(self, path: str, payload, status: int = 200) -> "MockServer":
        return self.add(path, lambda query, headers, index: json_response(payload, status))

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                headers = {key.lower(): value for key, value in self.headers.items()}
                parent.requests.append({
                    "path": parsed.path,
                    "query": query,
                    "headers": headers,
                })
                route = parent._routes.get(parsed.path)
                if route is None:
                    status, body = 404, b'{"error":"not found"}'
                else:
                    status, body = route(query, headers, len(parent.requests))
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # noqa: A003
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        return "http://%s:%d" % (host, port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def count(self, path: str) -> int:
        return sum(1 for item in self.requests if item["path"] == path)

    def __enter__(self) -> "MockServer":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
