"""最小可用的 RFC6455 WebSocket 客户端（仅标准库）。

只为 OneBot 11 连接 NapCat / LLOneBot / Lagrange 等服务端而实现：
握手、掩码、分片重组、ping/pong、close。不包含扩展（permessage-deflate）。

另外提供一个 `accept` 辅助函数，把已建立的 TCP 连接变成服务端 WebSocket，
仅用于本项目的自动化测试（mock OneBot 服务端）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import threading
import time
import urllib.parse
from typing import Dict, Optional, Tuple

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketError(Exception):
    """WebSocket 协议错误。"""


class ConnectionClosed(WebSocketError):
    def __init__(self, code: int = 1006, reason: str = ""):
        super().__init__("连接已关闭（%s %s）" % (code, reason or ""))
        self.code = code
        self.reason = reason


def accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _xor_mask(payload: bytes, key: bytes) -> bytes:
    if not payload:
        return b""
    return bytes(byte ^ key[index & 3] for index, byte in enumerate(payload))


def encode_frame(opcode: int, payload: bytes, mask: bool = True, fin: bool = True) -> bytes:
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    header = bytearray([first])
    mask_bit = 0x80 if mask else 0x00

    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)

    if mask:
        key = os.urandom(4)
        header += key
        payload = _xor_mask(payload, key)
    return bytes(header) + payload


class WebSocket:
    def __init__(self, sock: socket.socket, client_side: bool = True,
                 max_payload: int = 32 * 1024 * 1024):
        self._sock = sock
        self._client_side = client_side
        self._max_payload = max_payload
        self._buffer = b""
        self._closed = False
        self.last_rx = time.time()
        # 多个工作线程可能同时回复消息，必须保证一帧完整写出
        self._send_lock = threading.Lock()

    # ------------------------------------------------------------------ 属性
    @property
    def sock(self) -> socket.socket:
        return self._sock

    @property
    def closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------ 读取
    def _recv_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        if self._buffer:
            take = self._buffer[:remaining]
            self._buffer = self._buffer[len(take):]
            chunks.append(take)
            remaining -= len(take)
        while remaining > 0:
            try:
                chunk = self._sock.recv(min(remaining, 65536))
            except socket.timeout:
                # 读超时是正常的轮询信号，交给上层判断心跳，不能当成断线
                raise
            except OSError as exc:
                raise ConnectionClosed(1006, str(exc))
            if not chunk:
                raise ConnectionClosed(1006, "对端关闭了 TCP 连接")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def recv_frame(self) -> Tuple[bool, int, bytes]:
        header = self._recv_exact(2)
        fin = bool(header[0] & 0x80)
        if header[0] & 0x70:
            raise WebSocketError("收到带扩展位的帧，但不支持任何扩展")
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]

        if length > self._max_payload:
            raise WebSocketError("帧长度 %d 超出上限" % length)

        key = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = _xor_mask(payload, key)
        self.last_rx = time.time()
        return fin, opcode, payload

    def recv_message(self, timeout: Optional[float] = None) -> Tuple[int, bytes]:
        """返回 (opcode, payload)。控制帧在内部处理。超时抛 socket.timeout。"""
        self._sock.settimeout(timeout)
        fragments = []
        message_opcode = None

        while True:
            fin, opcode, payload = self.recv_frame()

            if opcode == OP_PING:
                self._send_control(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                code = 1005
                reason = ""
                if len(payload) >= 2:
                    code = struct.unpack("!H", payload[:2])[0]
                    reason = payload[2:].decode("utf-8", "replace")
                try:
                    self._send_control(OP_CLOSE, struct.pack("!H", 1000))
                except Exception:
                    pass
                self._closed = True
                raise ConnectionClosed(code, reason)
            if opcode in (OP_TEXT, OP_BINARY):
                if fragments:
                    raise WebSocketError("上一条分片消息尚未结束")
                message_opcode = opcode
                fragments = [payload]
            elif opcode == OP_CONT:
                if not fragments:
                    raise WebSocketError("收到孤立的续帧")
                fragments.append(payload)
            else:
                raise WebSocketError("未知 opcode 0x%X" % opcode)

            if fin:
                data = b"".join(fragments)
                fragments = []
                return message_opcode, data

    def recv_text(self, timeout: Optional[float] = None) -> str:
        opcode, data = self.recv_message(timeout=timeout)
        if opcode != OP_TEXT:
            raise WebSocketError("期望文本帧，收到 0x%X" % opcode)
        return data.decode("utf-8", "replace")

    # ------------------------------------------------------------------ 发送
    def _send_control(self, opcode: int, payload: bytes) -> None:
        self.send_bytes(payload, opcode=opcode)

    def send_bytes(self, payload: bytes, opcode: int = OP_BINARY) -> None:
        if self._closed:
            raise ConnectionClosed(1006, "连接已关闭")
        frame = encode_frame(opcode, payload, mask=self._client_side)
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError as exc:
                self._closed = True
                raise ConnectionClosed(1006, str(exc))

    def send_text(self, text: str) -> None:
        self.send_bytes(text.encode("utf-8"), opcode=OP_TEXT)

    def send_ping(self, payload: bytes = b"") -> None:
        self._send_control(OP_PING, payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if not self._closed:
            try:
                body = struct.pack("!H", code) + reason.encode("utf-8")[:120]
                self._send_control(OP_CLOSE, body)
            except Exception:
                pass
            self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


def _parse_ws_url(url: str) -> Tuple[str, str, int, str]:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("ws", "wss"):
        raise WebSocketError("不支持的协议：%s" % scheme)
    host = parsed.hostname
    if not host:
        raise WebSocketError("WebSocket 地址缺少主机名：%s" % url)
    port = parsed.port or (443 if scheme == "wss" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return scheme, host, port, path


def connect(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
    ssl_context: Optional[ssl.SSLContext] = None,
    max_payload: int = 32 * 1024 * 1024,
) -> WebSocket:
    scheme, host, port, path = _parse_ws_url(url)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        if scheme == "wss":
            context = ssl_context or ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=host)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            "GET %s HTTP/1.1" % path,
            "Host: %s" % (host if port in (80, 443) else "%s:%d" % (host, port)),
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: %s" % key,
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in (headers or {}).items():
            lines.append("%s: %s" % (name, value))
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")
        sock.sendall(request)

        buffer = b""
        while b"\r\n\r\n" not in buffer:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                raise WebSocketError("等待 WebSocket 握手响应超时")
            if not chunk:
                raise WebSocketError("服务端在握手阶段关闭了连接")
            buffer += chunk
            if len(buffer) > 64 * 1024:
                raise WebSocketError("握手响应头过大")

        header_bytes, leftover = buffer.split(b"\r\n\r\n", 1)
        header_text = header_bytes.decode("iso-8859-1")
        status_line = header_text.split("\r\n", 1)[0]
        if " 101 " not in status_line + " ":
            raise WebSocketError("握手失败：%s" % status_line)

        response_headers = {}
        for line in header_text.split("\r\n")[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                response_headers[name.strip().lower()] = value.strip()

        expected = accept_key(key)
        actual = response_headers.get("sec-websocket-accept", "")
        if actual != expected:
            raise WebSocketError("Sec-WebSocket-Accept 校验失败")

        socket_ws = WebSocket(sock, client_side=True, max_payload=max_payload)
        socket_ws._buffer = leftover
        return socket_ws
    except Exception:
        try:
            sock.close()
        except OSError:
            pass
        raise


def accept(sock: socket.socket, timeout: float = 10.0) -> WebSocket:
    """把已 accept 的 TCP 连接升级为服务端 WebSocket（测试用）。"""
    sock.settimeout(timeout)
    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebSocketError("客户端在握手阶段断开")
        buffer += chunk

    header_bytes, leftover = buffer.split(b"\r\n\r\n", 1)
    lines = header_bytes.decode("iso-8859-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

    key = headers.get("sec-websocket-key")
    if not key:
        raise WebSocketError("缺少 Sec-WebSocket-Key")

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(key)
    )
    sock.sendall(response.encode("ascii"))
    ws = WebSocket(sock, client_side=False)
    ws._buffer = leftover
    return ws
