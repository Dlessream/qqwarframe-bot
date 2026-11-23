"""QQ 开放平台官方机器人网关（WebSocket 反向 + Webhook 正向）。

对外接口与旧 OneBotClient 保持一致，bot/app.py 的业务逻辑无需感知协议差异：
- run_forever(stop_event) / connected / close()
- send_group_text / send_private_text / send_group_image / send_private_image / get_login_info

业务层传进来的 at 仍是 `[CQ:at,qq=openid]`，本层负责翻译成官方 `<@openid>`；
图片先上传富媒体拿 file_info，再以 msg_type=7 发送。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import quote

from . import ws
from .net import HttpClient, HttpError
from .qqevent import to_onebot_event
from .qqsignature import validation_response

LOGGER = logging.getLogger("qqwarframe.qqofficial")

PROD_API = "https://api.bot.qq.com"
SANDBOX_API = "https://sandbox.api.bot.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"

_CQ_AT_RE = re.compile(r"\[CQ:at,qq=([^\],]+)\]")


class QQOfficialError(Exception):
    """调用 QQ 开放平台接口失败。"""


class _TokenManager:
    """Access Token 缓存：官方有效期 7200s，到期前 60s 刷新。"""

    def __init__(self, http: HttpClient, app_id: str, app_secret: str,
                 token_url: str, logger: logging.Logger):
        self.http = http
        self.app_id = str(app_id or "")
        self.app_secret = str(app_secret or "")
        self.token_url = token_url or TOKEN_URL
        self.log = logger
        self._token = ""
        self._expires = 0.0
        self._lock = threading.Lock()

    def get(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._expires:
                return self._token
            if not self.app_id or not self.app_secret:
                raise QQOfficialError("未配置 qqbot.app_id / qqbot.app_secret")
            body = json.dumps({
                "appId": self.app_id,
                "clientSecret": self.app_secret,
            }).encode("utf-8")
            raw = self.http.request(self.token_url, {
                "Content-Type": "application/json; charset=utf-8",
            }, method="POST", body=body)
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
            except ValueError as exc:
                raise QQOfficialError("获取 Access Token 返回非 JSON：%s" % exc)
            if not isinstance(data, dict):
                raise QQOfficialError("获取 Access Token 返回格式异常")
            error = data.get("err_code") or data.get("code")
            token = str(data.get("access_token") or "")
            if error and not token:
                raise QQOfficialError("获取 Access Token 失败：%s %s" % (
                    error, data.get("message") or ""))
            if not token:
                raise QQOfficialError("获取 Access Token 失败：返回中没有 access_token")
            try:
                lifetime = int(data.get("expires_in") or 7200)
            except (TypeError, ValueError):
                lifetime = 7200
            self._token = token
            self._expires = time.time() + max(60, lifetime - 60)
            self.log.info("QQ 官方 Access Token 已更新，有效期 %ds", lifetime)
            return token

    def valid(self) -> bool:
        return bool(self._token and time.time() < self._expires)


class QQOfficialClient:
    def __init__(self, config: Dict[str, Any],
                 on_event: Callable[[Dict[str, Any]], None],
                 logger: Optional[logging.Logger] = None):
        self.cfg = dict(config or {})
        self.on_event = on_event
        self.log = logger or LOGGER
        self.transport = str(self.cfg.get("transport") or "websocket").lower()
        if self.transport not in ("websocket", "webhook"):
            self.transport = "websocket"
        self.enabled = bool(self.cfg.get("enabled", True))

        self.app_id = str(self.cfg.get("app_id") or "")
        self.app_secret = str(self.cfg.get("app_secret") or "")
        self.app_token = str(self.cfg.get("app_token") or "")
        self.env = str(self.cfg.get("env") or "prod").lower()
        self.api_base = str(self.cfg.get("api_base") or "").rstrip("/") or (
            SANDBOX_API if self.env == "sandbox" else PROD_API)
        self.gateway_url = str(self.cfg.get("gateway_url") or "").strip()
        self.callback_path = str(self.cfg.get("callback_path") or "/qq/callback")
        self.self_id = str(self.cfg.get("self_id") or self.app_id)
        self.intents = int(self.cfg.get("intents") or (1 << 25))
        self.shard = list(self.cfg.get("shard") or [0, 1])
        self.proxy = str(self.cfg.get("proxy") or "none")
        self.timeout = float(self.cfg.get("api_timeout_seconds", 15) or 15)
        self.min_delay = float(self.cfg.get("reconnect_min_seconds", 1) or 1)
        self.max_delay = float(self.cfg.get("reconnect_max_seconds", 60) or 60)
        self.signature_tolerance = int(self.cfg.get("signature_tolerance_seconds", 300))
        self.event_log = None  # 由 app.py 注入，供网页显示最近收发记录

        self.http = HttpClient(timeout=self.timeout, retries=1, proxy=self.proxy,
                               logger=self.log, auto_switch=False)
        self.tokens = _TokenManager(self.http, self.app_id, self.app_secret,
                                    str(self.cfg.get("token_url") or TOKEN_URL),
                                    self.log)

        self._ws: Optional[ws.WebSocket] = None
        self._connected = False
        self._webhook_ready = False
        self._session_id = ""
        self._seq: Optional[int] = None
        self._heartbeat_ms = 45000
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._last_error = ""
        self._connected_at = 0.0
        self._reply_ctx: Dict[str, Dict[str, Any]] = {}
        self._ctx_seq: Dict[str, int] = {}
        self._ctx_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4,
                                            thread_name_prefix="qqofficial")

    # ------------------------------------------------------------------ 状态
    @property
    def connected(self) -> bool:
        if self.transport == "webhook":
            return self.enabled and self._webhook_ready
        sock = self._ws
        return bool(self._connected and sock is not None and not sock.closed)

    @property
    def url(self) -> str:
        if self.transport == "webhook":
            return self.callback_path
        return self.gateway_url or ("%s/gateway" % self.api_base)

    def status(self) -> Dict[str, Any]:
        return {
            "transport": self.transport,
            "connected": self.connected,
            "mode": self.transport,
            "app_id": self.app_id,
            "self_id": self.self_id,
            "connected_at": self._connected_at,
            "last_error": self._last_error,
            "callback_path": self.callback_path,
            "session_id": self._session_id,
            "seq": self._seq,
            "heartbeat_ms": self._heartbeat_ms,
            "token_valid": self.tokens.valid(),
            "api_base": self.api_base,
        }

    def _log_event(self, kind: str, summary: str, ok: bool = True,
                   error: str = "") -> None:
        if self.event_log is None:
            return
        try:
            self.event_log(kind, summary, ok, error)
        except Exception:  # pragma: no cover - 防御
            pass

    def _fail(self, exc: Exception, summary: str = "") -> None:
        self._last_error = str(exc)
        self._log_event("error", summary or str(exc), False, str(exc))

    # ------------------------------------------------------------------ 主循环
    def run_forever(self, stop_event: threading.Event) -> None:
        if not self.enabled:
            self.log.info("QQ 官方机器人未启用")
            stop_event.wait()
            return
        if not self.app_id or not self.app_secret:
            message = "尚未配置 qqbot.app_id / app_secret，官方机器人暂不连接；可在网页控制台填写后重启。"
            self._last_error = message
            self.log.warning(message)
            self._log_event("error", message, False, message)
            stop_event.wait()
            return
        if self.transport == "webhook":
            self._connected_at = time.time()
            self.log.info("QQ 官方机器人使用 Webhook 正向回调：%s", self.callback_path)
            self._log_event("system", "Webhook 回调已就绪：%s" % self.callback_path)
            self._webhook_ready = True
            stop_event.wait()
            self._webhook_ready = False
            return

        delay = self.min_delay
        while not stop_event.is_set():
            socket_ws = None
            try:
                url = self._gateway_url()
                token = self.tokens.get()
                socket_ws = ws.connect(url, timeout=10)
                self._ws = socket_ws
                self.log.info("已连接 QQ 官方网关：%s", url)
                self._read_loop(socket_ws, stop_event, token)
            except (ConnectionClosed, socket.timeout) as exc:
                if not stop_event.is_set():
                    self._fail(exc, "QQ 官方网关连接断开")
                    self.log.warning("QQ 官方网关连接断开：%s；%.0f 秒后重连", exc, delay)
            except HttpError as exc:
                self._fail(exc, "获取 QQ 官方网关地址失败")
                self.log.warning("获取 QQ 官方网关失败：%s；%.0f 秒后重试", exc, delay)
            except QQOfficialError as exc:
                self._fail(exc)
                self.log.warning("QQ 官方机器人连接失败：%s；%.0f 秒后重试", exc, delay)
            except Exception as exc:  # pragma: no cover - 防御
                self._fail(exc)
                self.log.exception("QQ 官方网关异常：%s", exc)
            finally:
                self._connected = False
                self._stop_heartbeat()
                if socket_ws is not None:
                    try:
                        socket_ws.close()
                    except Exception:
                        pass
                self._ws = None
            if stop_event.is_set():
                break
            stop_event.wait(delay)
            delay = min(delay * 2, self.max_delay)
            if self.connected:
                delay = self.min_delay

    def _gateway_url(self) -> str:
        if self.gateway_url:
            return self.gateway_url
        token = self.tokens.get()
        try:
            raw = self.http.get_json(
                "%s/gateway" % self.api_base,
                {"Authorization": "QQBot %s" % token})
            url = str((raw or {}).get("url") or "")
            if url.startswith("ws://") or url.startswith("wss://"):
                return url
        except Exception as exc:
            self.log.warning("获取 /gateway 失败，使用默认地址：%s", exc)
        return ("wss://sandbox.api.bot.qq.com/websocket/" if self.env == "sandbox"
                else "wss://api.bot.qq.com/websocket/")

    def _ws_token(self, access_token: str) -> str:
        template = str(self.cfg.get("ws_token_template") or "QQBot {token}")
        if self.app_token and "{" not in template:
            return "Bot %s.%s" % (self.app_id, self.app_token)
        return template.replace("{token}", access_token).replace(
            "{app_id}", self.app_id).replace("{app_token}", self.app_token)

    def _send_ws(self, payload: Dict[str, Any]) -> None:
        socket_ws = self._ws
        if socket_ws is None or socket_ws.closed:
            raise ConnectionClosed(1006, "WebSocket 已关闭")
        socket_ws.send_text(json.dumps(payload, ensure_ascii=False))

    def _identify(self, access_token: str) -> None:
        if self._session_id and self._seq is not None:
            self._send_ws({"op": 6, "d": {
                "token": self._ws_token(access_token),
                "session_id": self._session_id,
                "seq": self._seq,
            }})
            self._log_event("system", "恢复 QQ 官方网关会话 seq=%s" % self._seq)
            return
        self._send_ws({"op": 2, "d": {
            "token": self._ws_token(access_token),
            "intents": self.intents,
            "shard": self.shard,
            "properties": {
                "$os": "linux",
                "$browser": "qqwarframe-bot",
                "$device": "qqwarframe-bot",
            },
        }})
        self._log_event("system", "已发送 QQ 官方网关鉴权（intents=%d）" % self.intents)

    def _read_loop(self, socket_ws: ws.WebSocket, stop_event: threading.Event,
                   access_token: str) -> None:
        last_rx = time.time()
        timeout = max(60.0, float(self.cfg.get("heartbeat_timeout_seconds", 90) or 90))
        self._heartbeat_stop.clear()
        while not stop_event.is_set():
            try:
                opcode, data = socket_ws.recv_message(timeout=1.0)
            except socket.timeout:
                if time.time() - last_rx > timeout:
                    raise ConnectionClosed(1006, "超过 %.0f 秒未收到网关数据" % timeout)
                continue
            last_rx = time.time()
            if opcode != ws.OP_TEXT:
                continue
            try:
                payload = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                self.log.warning("收到无法解析的网关消息：%r", data[:200])
                continue
            if not isinstance(payload, dict):
                continue
            op = payload.get("op")

            if op == 10:
                hello = payload.get("d") if isinstance(payload.get("d"), dict) else {}
                self._heartbeat_ms = int(hello.get("heartbeat_interval") or 45000)
                self._identify(access_token)
                self._start_heartbeat()
            elif op == 0:
                if isinstance(payload.get("s"), int):
                    self._seq = payload["s"]
                name = str(payload.get("t") or "")
                if name == "READY":
                    ready = payload.get("d") if isinstance(payload.get("d"), dict) else {}
                    self._session_id = str(ready.get("session_id") or "")
                    if not self.cfg.get("self_id"):
                        user = ready.get("user") if isinstance(ready.get("user"), dict) else {}
                        self.self_id = str(user.get("id") or self.self_id)
                    self._connected = True
                    self._connected_at = time.time()
                    self._last_error = ""
                    self.log.info("QQ 官方网关鉴权成功，机器人 ID=%s", self.self_id)
                    self._log_event("system", "网关已连接，机器人 ID=%s" % self.self_id)
                elif name == "RESUMED":
                    self._connected = True
                    self._connected_at = time.time()
                    self._log_event("system", "会话已恢复")
                else:
                    self._dispatch(payload)
            elif op == 1:
                try:
                    self._send_ws({"op": 1, "d": payload.get("d")})
                except Exception:
                    pass
            elif op == 9:
                self._session_id = ""
                self._seq = None
                raise ConnectionClosed(4009, "网关拒绝会话，改为重新鉴权")
            elif op == 7:
                raise ConnectionClosed(4000, "网关要求重连")
            elif op == 11:
                pass

    def _dispatch(self, payload: Dict[str, Any]) -> None:
        event = to_onebot_event(payload, self.self_id, self.app_id)
        if event is None:
            return
        self.remember_reply_context(event)
        self._log_event("in", "%s / %s" % (
            event.get("qq_event") or event.get("sub_type") or "message",
            (event.get("raw_message") or "")[:60]))
        try:
            self._executor.submit(self._safe_event, event)
        except RuntimeError:  # 解释器关闭中
            pass

    def _safe_event(self, event: Dict[str, Any]) -> None:
        try:
            self.on_event(event)
        except Exception as exc:  # pragma: no cover - 防御
            self.log.exception("处理 QQ 官方事件时出错：%s", exc)

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_stop.clear()

        def loop() -> None:
            interval = max(5.0, self._heartbeat_ms / 1000.0)
            while not self._heartbeat_stop.wait(interval):
                try:
                    self._send_ws({"op": 1, "d": self._seq})
                except Exception:
                    return

        self._heartbeat_thread = threading.Thread(
            target=loop, name="qqofficial-heartbeat", daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()

    # ------------------------------------------------------------------ Webhook
    def callback_validation(self, payload: Dict[str, Any]) -> Dict[str, str]:
        result = validation_response(self.app_secret, payload)
        self._log_event("system", "Webhook 回调地址验证通过")
        return result

    def handle_webhook(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """处理已验签的回调；返回 op=12 ACK。"""
        if not isinstance(payload, dict):
            return {"op": 12}
        op = payload.get("op")
        if op == 13:
            return self.callback_validation(payload)
        if op == 0:
            self._dispatch(payload)
        return {"op": 12}

    # ------------------------------------------------------------------ 被动回复上下文
    def remember_reply_context(self, event: Dict[str, Any]) -> None:
        chat_id = str(event.get("group_id") or event.get("user_id") or "")
        msg_id = str(event.get("qq_msg_id") or "")
        if not chat_id or not msg_id:
            return
        with self._ctx_lock:
            self._reply_ctx[chat_id] = {
                "msg_id": msg_id,
                "event_id": str(event.get("qq_event_id") or ""),
                "at": time.time(),
                "seq": 0,
            }
            if len(self._reply_ctx) > 1000:
                cutoff = time.time() - 3600
                self._reply_ctx = {
                    key: value for key, value in self._reply_ctx.items()
                    if value["at"] > cutoff}

    def reply_context(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """在处理消息时立刻快照回复上下文，避免并发消息互相覆盖 msg_id。"""
        chat_id = str(event.get("group_id") or event.get("user_id") or "")
        msg_id = str(event.get("qq_msg_id") or "")
        if not chat_id or not msg_id:
            return {}
        with self._ctx_lock:
            key = "%s/%s" % (chat_id, msg_id)
            seq = int(self._ctx_seq.get(key) or 0) + 1
            self._ctx_seq[key] = seq
            if len(self._ctx_seq) > 1000:
                self._ctx_seq = dict(list(self._ctx_seq.items())[-500:])
        result = {"msg_id": msg_id, "msg_seq": seq}
        if event.get("qq_event_id"):
            result["event_id"] = str(event["qq_event_id"])
        return result

    def _reply_fields(self, chat_id: str,
                      reply_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """优先使用消息自带的上下文；订阅推送等无上下文时用最近一条消息。"""
        if reply_ctx:
            return {key: value for key, value in reply_ctx.items()
                    if key in ("msg_id", "event_id", "msg_seq")}
        with self._ctx_lock:
            item = self._reply_ctx.get(str(chat_id))
            if not item or time.time() - item["at"] > 3500:
                return {}
            item["seq"] = int(item.get("seq") or 0) + 1
            result = {"msg_id": item["msg_id"], "msg_seq": item["seq"]}
            if item.get("event_id"):
                result["event_id"] = item["event_id"]
            return dict(result)

    # ------------------------------------------------------------------ REST
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "QQBot %s" % self.tokens.get(),
            "Content-Type": "application/json; charset=utf-8",
        }

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = self.api_base + path
        raw = self.http.request(url, self._headers(), method="POST",
                                body=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise QQOfficialError("%s 返回非 JSON" % path)
        if not isinstance(data, dict):
            return {}
        err = data.get("err_code") or data.get("code")
        if err and str(err) != "0":
            raise QQOfficialError("%s 失败：%s %s" % (
                path, err, data.get("message") or ""))
        return data

    def _get(self, path: str) -> Dict[str, Any]:
        raw = self.http.get_json(self.api_base + path, self._headers())
        if not isinstance(raw, dict):
            return {}
        err = raw.get("err_code") or raw.get("code")
        if err and str(err) != "0":
            raise QQOfficialError("%s 失败：%s %s" % (
                path, err, raw.get("message") or ""))
        return raw

    @staticmethod
    def _mentions(message: str) -> str:
        """把内部 CQ at 转成官方 `<@openid>`。"""
        return _CQ_AT_RE.sub(lambda m: "<@%s>" % m.group(1), str(message or ""))

    def send_group_text(self, group_id: Any, message: str,
                        reply_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "msg_type": 0,
            "content": self._mentions(message),
        }
        body.update(self._reply_fields(group_id, reply_ctx))
        result = self._post("/v2/groups/%s/messages" % quote(str(group_id), safe=""), body)
        self._log_event("out", "群 %s 文本：%s" % (group_id, str(message)[:50]))
        return result

    def send_private_text(self, user_id: Any, message: str,
                          reply_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "msg_type": 0,
            "content": self._mentions(message),
        }
        body.update(self._reply_fields(user_id, reply_ctx))
        result = self._post("/v2/users/%s/messages" % quote(str(user_id), safe=""), body)
        self._log_event("out", "私聊 %s 文本：%s" % (user_id, str(message)[:50]))
        return result

    def _upload_image(self, kind: str, chat_id: Any, png: bytes) -> str:
        if kind == "group":
            path = "/v2/groups/%s/files" % quote(str(chat_id), safe="")
        else:
            path = "/v2/users/%s/files" % quote(str(chat_id), safe="")
        body = {
            "file_type": 1,
            "file_name": "image.png",
            "file_data": base64.b64encode(png).decode("ascii"),
            "srv_send_msg": False,
        }
        data = self._post(path, body)
        info = str(data.get("file_info") or "")
        if not info:
            raise QQOfficialError("图片上传成功但返回中没有 file_info")
        return info

    def _send_image(self, kind: str, chat_id: Any, png: bytes,
                    prefix: str = "",
                    reply_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        mention = self._mentions(prefix).strip()
        if mention:
            self.send_group_text(chat_id, mention, reply_ctx) if kind == "group" \
                else self.send_private_text(chat_id, mention, reply_ctx)
        info = self._upload_image(kind, chat_id, png)
        body: Dict[str, Any] = {
            "msg_type": 7,
            "media": {"file_info": info},
        }
        body.update(self._reply_fields(chat_id, reply_ctx))
        if kind == "group":
            path = "/v2/groups/%s/messages" % quote(str(chat_id), safe="")
        else:
            path = "/v2/users/%s/messages" % quote(str(chat_id), safe="")
        result = self._post(path, body)
        self._log_event("out", "%s %s 图片 %d 字节" % (kind, chat_id, len(png)))
        return result

    def send_group_image(self, group_id: Any, png: bytes,
                         prefix: str = "",
                         reply_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._send_image("group", group_id, png, prefix, reply_ctx)

    def send_private_image(self, user_id: Any, png: bytes,
                           prefix: str = "",
                           reply_ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._send_image("private", user_id, png, prefix, reply_ctx)

    def get_login_info(self) -> Dict[str, Any]:
        return self._get("/users/@me")

    def close(self) -> None:
        self._stop_heartbeat()
        sock = self._ws
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        self._connected = False
        self._webhook_ready = False
        self._executor.shutdown(wait=False)


class ConnectionClosed(Exception):
    """网关连接需要重连。"""
