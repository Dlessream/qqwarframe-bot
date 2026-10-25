"""OneBot 11 连接层：正向 WebSocket 客户端。

适配 NapCat / LLOneBot / Lagrange / go-cqhttp 等实现了 OneBot 11 的服务端。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional, Tuple

from . import ws
from .ws import ConnectionClosed, WebSocketError

LOGGER = logging.getLogger("qqwarframe.onebot")


class OneBotError(Exception):
    """调用 OneBot 接口失败。"""


def escape_cq(text: str) -> str:
    """转义 CQ 码元字符，避免消息内容被当成 CQ 码执行。"""
    return (text or "").replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")


def image_cq(png: bytes) -> str:
    """把 PNG 编码成 OneBot 通用的 base64 图片 CQ 码。"""
    return "[CQ:image,file=base64://%s]" % base64.b64encode(png).decode("ascii")


def _write_temp_image(png: bytes) -> str:
    handle, path = tempfile.mkstemp(prefix="qqwarframe_", suffix=".png")
    try:
        with os.fdopen(handle, "wb") as file_obj:
            file_obj.write(png)
    except Exception:
        try:
            os.close(handle)
        except OSError:
            pass
        raise
    return path


def _id_value(raw: Any) -> Any:
    text = str(raw)
    return int(text) if text.isdigit() else text


class OneBotClient:
    def __init__(self, config: Dict[str, Any], on_event: Callable[[Dict[str, Any]], None],
                 logger: Optional[logging.Logger] = None):
        self.cfg = config
        self.url = str(config["ws_url"])
        self.token = str(config.get("access_token") or "")
        self.timeout = float(config.get("api_timeout_seconds", 15))
        self.heartbeat = float(config.get("heartbeat_seconds", 25))
        self.heartbeat_timeout = float(config.get("heartbeat_timeout_seconds", 75))
        self.min_delay = float(config.get("reconnect_min_seconds", 1))
        self.max_delay = float(config.get("reconnect_max_seconds", 60))
        self.on_event = on_event
        self.log = logger or LOGGER

        self._ws: Optional[ws.WebSocket] = None
        self._pending: Dict[str, Tuple[threading.Event, Dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="onebot")

    # ------------------------------------------------------------------ 状态
    @property
    def connected(self) -> bool:
        sock = self._ws
        return sock is not None and not sock.closed

    # ------------------------------------------------------------------ 主循环
    def run_forever(self, stop_event: threading.Event) -> None:
        delay = self.min_delay
        while not stop_event.is_set():
            socket_ws = None
            try:
                socket_ws = self._connect()
            except Exception as exc:
                self.log.warning("连接 OneBot 失败：%s；%.0f 秒后重试", exc, delay)
                stop_event.wait(delay)
                delay = min(delay * 2, self.max_delay)
                continue

            self._ws = socket_ws
            delay = self.min_delay
            self.log.info("已连接 OneBot 服务：%s", self.url)
            try:
                self._read_loop(socket_ws, stop_event)
            except ConnectionClosed as exc:
                if not stop_event.is_set():
                    self.log.warning("OneBot 连接断开：%s", exc)
            except Exception as exc:  # pragma: no cover - 防御
                self.log.exception("OneBot 读取异常：%s", exc)
            finally:
                self._ws = None
                self._fail_pending("连接已断开")
                try:
                    socket_ws.close()
                except Exception:
                    pass

            if stop_event.is_set():
                break
            stop_event.wait(delay)
            delay = min(delay * 2, self.max_delay)

    def _connect(self) -> ws.WebSocket:
        headers = {}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return ws.connect(self.url, headers=headers, timeout=10)

    def _read_loop(self, socket_ws: ws.WebSocket, stop_event: threading.Event) -> None:
        last_rx = time.time()
        last_ping = 0.0
        while not stop_event.is_set():
            try:
                opcode, data = socket_ws.recv_message(timeout=1.0)
            except socket.timeout:
                now = time.time()
                if now - last_rx > self.heartbeat_timeout:
                    raise ConnectionClosed(1006, "超过 %.0f 秒没有收到任何数据" %
                                           self.heartbeat_timeout)
                if now - last_ping >= self.heartbeat:
                    socket_ws.send_ping(b"hb")
                    last_ping = now
                continue

            last_rx = time.time()
            if opcode != ws.OP_TEXT:
                continue
            try:
                payload = json.loads(data.decode("utf-8", "replace"))
            except ValueError:
                self.log.warning("收到无法解析的 JSON：%r", data[:200])
                continue
            if isinstance(payload, dict):
                self._handle_payload(payload)

    # ------------------------------------------------------------------ 分发
    def _handle_payload(self, payload: Dict[str, Any]) -> None:
        echo = payload.get("echo")
        if echo is not None:
            self._resolve(echo, payload)
            return
        post_type = payload.get("post_type")
        if post_type == "message":
            try:
                self._executor.submit(self._safe_event, payload)
            except RuntimeError:  # 解释器关闭中
                pass
        elif post_type == "meta_event":
            meta = payload.get("meta_event_type")
            if meta == "lifecycle":
                self.log.info("OneBot 生命周期事件：%s", payload.get("sub_type"))
        elif post_type in ("notice", "request"):
            self.log.debug("忽略事件：%s", post_type)

    def _safe_event(self, payload: Dict[str, Any]) -> None:
        try:
            self.on_event(payload)
        except Exception as exc:  # pragma: no cover - 防御
            self.log.exception("处理消息时出错：%s", exc)

    # ------------------------------------------------------------------ 调用
    def call(self, action: str, params: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None) -> Dict[str, Any]:
        socket_ws = self._ws
        if socket_ws is None or socket_ws.closed:
            raise OneBotError("尚未连接到 OneBot 服务")

        echo = uuid.uuid4().hex
        event = threading.Event()
        slot: Dict[str, Any] = {}
        with self._lock:
            self._pending[echo] = (event, slot)

        payload = {"action": action, "params": params or {}, "echo": echo}
        try:
            socket_ws.send_text(json.dumps(payload, ensure_ascii=False))
        except (ConnectionClosed, WebSocketError) as exc:
            with self._lock:
                self._pending.pop(echo, None)
            raise OneBotError("发送请求失败：%s" % exc)

        if not event.wait(timeout or self.timeout):
            with self._lock:
                self._pending.pop(echo, None)
            raise OneBotError("调用 %s 超时（%.0f 秒）" % (action, timeout or self.timeout))

        response = slot.get("response") or {}
        status = str(response.get("status") or "")
        retcode = response.get("retcode")
        if status == "failed" or (retcode is not None and retcode not in (0, 1)):
            detail = response.get("wording") or response.get("message") or response.get("data")
            raise OneBotError("%s 返回失败：%s" % (action, detail or retcode))
        return response.get("data") or {}

    def _resolve(self, echo: str, payload: Dict[str, Any]) -> None:
        with self._lock:
            item = self._pending.pop(echo, None)
        if item:
            event, slot = item
            slot["response"] = payload
            event.set()

    def _fail_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for event, slot in pending:
            slot["response"] = {"status": "failed", "wording": reason, "retcode": -1}
            event.set()

    # ------------------------------------------------------------------ 便捷
    def send_group_text(self, group_id: Any, message: str) -> Dict[str, Any]:
        return self.call("send_group_msg", {
            "group_id": _id_value(group_id),
            "message": message,
        })

    def send_private_text(self, user_id: Any, message: str) -> Dict[str, Any]:
        return self.call("send_private_msg", {
            "user_id": _id_value(user_id),
            "message": message,
        })

    def send_group_image(self, group_id: Any, png: bytes, prefix: str = "") -> Dict[str, Any]:
        return self._send_image("group", group_id, png, prefix)

    def send_private_image(self, user_id: Any, png: bytes, prefix: str = "") -> Dict[str, Any]:
        return self._send_image("private", user_id, png, prefix)

    def _send_image(self, kind: str, chat_id: Any, png: bytes,
                    prefix: str = "") -> Dict[str, Any]:
        """先发 base64；某些实现不接受长 base64 时退回本地临时文件。"""
        try:
            return self._send_image_message(kind, chat_id, prefix + image_cq(png))
        except OneBotError as exc:
            self.log.info("base64 图片发送失败（%s），尝试临时文件", exc)
            path = _write_temp_image(png)
            try:
                file_cq = "[CQ:image,file=file:///%s]" % path.replace("\\", "/")
                return self._send_image_message(kind, chat_id, prefix + file_cq)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _send_image_message(self, kind: str, chat_id: Any, message: str) -> Dict[str, Any]:
        if kind == "group":
            return self.send_group_text(chat_id, message)
        return self.send_private_text(chat_id, message)

    def get_login_info(self) -> Dict[str, Any]:
        return self.call("get_login_info", {})

    def close(self) -> None:
        socket_ws = self._ws
        if socket_ws is not None:
            try:
                socket_ws.close()
            except Exception:
                pass
        self._executor.shutdown(wait=False)
