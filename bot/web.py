"""轻量 Web 控制台：状态、日志、更新检查与重启确认。"""

from __future__ import annotations

import json
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")


class AdminServer:
    def __init__(self, app, config: Dict[str, Any],
                 logger: Optional[logging.Logger] = None):
        self.app = app
        self.config = dict(config or {})
        self.log = logger or logging.getLogger(__name__)
        self.host = str(self.config.get("host") or "127.0.0.1")
        port_value = self.config.get("port", 8080)
        self.port = 8080 if port_value in (None, "") else int(port_value)
        self.token = str(self.config.get("token") or "")
        self.log_file = str(self.config.get("log_file") or "logs/bot.log")
        self.max_log_lines = max(20, min(500, int(
            self.config.get("log_lines") or 200)))
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.actual_port = self.port

    def start(self) -> None:
        if not self.config.get("enabled", True):
            self.log.info("Web 控制台已关闭")
            return
        if not self.token and self.host not in ("127.0.0.1", "localhost", "::1"):
            self.log.warning(
                "Web 控制台绑定 %s 且未设置 web.token，存在未授权访问风险；"
                "建议设置 WEB_ADMIN_TOKEN 或改回 127.0.0.1", self.host)

        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "QQWarframeAdmin/1.0"

            def log_message(self, fmt, *args):
                server.log.debug("web: " + fmt, *args)

            def _authorized(self) -> bool:
                if not server.token:
                    return True
                supplied = self.headers.get("X-Admin-Token", "")
                if not supplied:
                    query = parse_qs(urlparse(self.path).query)
                    supplied = (query.get("token") or [""])[0]
                return supplied == server.token

            def _json(self, payload: Dict[str, Any],
                      status: int = HTTPStatus.OK) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _unauthorized(self) -> None:
                self._json({"ok": False, "message": "需要控制台令牌。"},
                           HTTPStatus.UNAUTHORIZED)

            def _file(self, path: str, content_type: str) -> None:
                try:
                    with open(path, "rb") as handle:
                        body = handle.read()
                except OSError:
                    self._json({"ok": False, "message": "文件不存在。"},
                               HTTPStatus.NOT_FOUND)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                if path in ("/", "/index.html"):
                    self._file(os.path.join(WEB_DIR, "index.html"),
                               "text/html; charset=utf-8")
                    return
                if path == "/static/style.css":
                    self._file(os.path.join(WEB_DIR, "style.css"),
                               "text/css; charset=utf-8")
                    return
                if path == "/static/app.js":
                    self._file(os.path.join(WEB_DIR, "app.js"),
                               "application/javascript; charset=utf-8")
                    return
                if path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.end_headers()
                    return
                if path == "/api/status":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    self._json({"ok": True, **server.app.status_snapshot()})
                    return
                if path == "/api/logs":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    query = parse_qs(parsed.query)
                    try:
                        lines = int((query.get("lines") or ["120"])[0])
                    except ValueError:
                        lines = 120
                    lines = max(20, min(server.max_log_lines, lines))
                    self._json({"ok": True, "lines": server.read_logs(lines)})
                    return
                if path == "/api/update/status":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    checker = getattr(server.app, "update_checker", None)
                    self._json({"ok": True, "update": checker.snapshot() if checker else {}})
                    return
                if path == "/api/groups":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    self._json({"ok": True, **server.app.group_policy_snapshot()})
                    return
                if path == "/api/aliases":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    self._json({"ok": True, **server.app.alias_snapshot()})
                    return
                if path == "/api/arbi":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    try:
                        payload = server.app.arbi_snapshot()
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json({"ok": True, **payload})
                    return
                if path == "/api/arbi-schedule":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    params = parse_qs(parsed.query)
                    try:
                        days = int(params.get("days", ["7"])[0])
                    except ValueError:
                        days = 7
                    try:
                        payload = server.app.arbi_schedule(days)
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json({"ok": True, **payload})
                    return
                if path == "/api/alias-groups":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    self._json({"ok": True, **server.app.alias_groups()})
                    return
                if path == "/api/mission-colors":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    self._json({"ok": True, **server.app.mission_colors_snapshot()})
                    return
                if path == "/api/catalog":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    params = parse_qs(parsed.query)
                    try:
                        result = server.app.catalog_groups(
                            params.get("category", ["all"])[0],
                            params.get("q", [""])[0],
                            int(params.get("offset", ["0"])[0] or 0),
                            int(params.get("limit", ["40"])[0] or 40))
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json({"ok": True, **result})
                    return
                if path == "/api/items":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    try:
                        items = server.app.search_market_items(query)
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json({"ok": True, "items": items})
                    return
                if path == "/api/translate":
                    if not self._authorized():
                        self._unauthorized()
                        return
                    query = parse_qs(parsed.query).get("q", [""])[0]
                    try:
                        result = server.app.translate_item(query)
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json({"ok": True, **result})
                    return
                self._json({"ok": False, "message": "Not found"},
                           HTTPStatus.NOT_FOUND)

            def do_POST(self):
                if not self._authorized():
                    self._unauthorized()
                    return
                parsed = urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                if length > 65536:
                    self._json({"ok": False, "message": "请求体过大。"},
                               HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                    return
                body = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except Exception:
                    self._json({"ok": False, "message": "JSON 格式错误。"},
                               HTTPStatus.BAD_REQUEST)
                    return

                if parsed.path == "/api/update/check":
                    checker = getattr(server.app, "update_checker", None)
                    if checker is None:
                        self._json({"ok": False, "message": "更新检查未启用。"})
                        return
                    update = checker.check_now()
                    self._json({"ok": True, "update": update})
                    return

                if parsed.path == "/api/update/apply":
                    if not data.get("confirm"):
                        self._json({"ok": False, "message": "缺少确认参数。"},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    result = server.app.apply_update()
                    self._json({"ok": bool(result.get("ok")), **result})
                    return

                if parsed.path == "/api/update/dismiss":
                    kind = str(data.get("kind") or "")
                    checker = getattr(server.app, "update_checker", None)
                    if checker is not None:
                        if kind == "local":
                            checker.acknowledge_local()
                        elif kind == "remote":
                            checker.dismiss_remote()
                    self._json({"ok": True})
                    return

                if parsed.path == "/api/restart":
                    if not data.get("confirm"):
                        self._json({"ok": False, "message": "缺少确认参数。"},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    server.app.request_restart()
                    self._json({"ok": True, "message": "正在重启服务。"})
                    return

                if parsed.path == "/api/groups":
                    try:
                        result = server.app.update_group_policy(
                            data.get("whitelist") or [],
                            data.get("blacklist") or [],
                            data.get("mode"))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/groups/allow":
                    try:
                        result = server.app.allow_group(
                            str(data.get("group_id") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/groups/deny":
                    try:
                        result = server.app.deny_group(
                            str(data.get("group_id") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/groups/mode":
                    try:
                        result = server.app.set_group_policy_mode(
                            str(data.get("mode") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/aliases":
                    try:
                        if isinstance(data.get("aliases"), list):
                            result = server.app.save_aliases(
                                data.get("aliases") or [],
                                str(data.get("target") or ""))
                        else:
                            result = server.app.save_alias(
                                str(data.get("alias") or ""),
                                str(data.get("target") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/aliases/delete":
                    try:
                        result = server.app.delete_alias(
                            str(data.get("alias") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/alias-groups":
                    try:
                        result = server.app.save_alias_group(
                            str(data.get("slug") or ""),
                            data.get("aliases") or [])
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/mission-colors":
                    try:
                        if data.get("reset"):
                            result = server.app.reset_mission_colors()
                        elif isinstance(data.get("colors"), dict):
                            result = server.app.save_mission_colors(
                                data.get("colors") or {})
                        else:
                            result = server.app.save_mission_color(
                                str(data.get("name") or ""),
                                str(data.get("color") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.INTERNAL_SERVER_ERROR)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/data/refresh":
                    try:
                        result = server.app.refresh_market_data()
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json(result)
                    return

                if parsed.path == "/api/arbi":
                    try:
                        result = server.app.save_arbi_rating(
                            str(data.get("key") or ""),
                            str(data.get("tier") or ""))
                    except ValueError as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_REQUEST)
                        return
                    except Exception as exc:
                        self._json({"ok": False, "message": str(exc)},
                                   HTTPStatus.BAD_GATEWAY)
                        return
                    self._json(result)
                    return

                self._json({"ok": False, "message": "Not found"},
                           HTTPStatus.NOT_FOUND)

        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
            self._httpd.daemon_threads = True
            self.actual_port = self._httpd.server_address[1]
        except OSError as exc:
            self.log.warning("Web 控制台启动失败（%s:%s）：%s",
                             self.host, self.port, exc)
            self._httpd = None
            return
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="web-admin", daemon=True)
        self._thread.start()
        self.log.info("Web 控制台：http://%s:%s", self.host, self.actual_port)

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass

    def read_logs(self, lines: int = 120) -> str:
        path = self.log_file
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 128 * 1024))
                raw = handle.read()
        except OSError:
            return ""
        text = raw.decode("utf-8", "replace")
        return "\n".join(text.splitlines()[-lines:])
