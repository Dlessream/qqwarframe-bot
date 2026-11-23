"""QQ 开放平台 Webhook 的 Ed25519 签名校验。

官方约定（与 Discord 类似但种子来自 BotSecret）：
- 把 BotSecret 反复拼接until长度 >= 32 字节，取前 32 字节作为 Ed25519 seed；
- 普通事件：msg = X-Signature-Timestamp + raw_body，签名取十六进制；
- 回调地址验证(op=13)：msg = event_ts + plain_token，返回签名给平台。

依赖 cryptography；缺失时所有校验返回失败并给出明确提示，不会静默放行。
"""

from __future__ import annotations

import binascii
import logging
import time
from typing import Any, Dict, Optional, Tuple

LOGGER = logging.getLogger("qqwarframe.qqsignature")

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey,
    )
    _CRYPTO_ERROR = ""
except ImportError as exc:  # pragma: no cover - 取决于部署环境
    InvalidSignature = Exception  # type: ignore[assignment,misc]
    Ed25519PrivateKey = None  # type: ignore[assignment]
    Ed25519PublicKey = None  # type: ignore[assignment]
    _CRYPTO_ERROR = str(exc)


class SignatureError(Exception):
    """签名校验失败。"""


def available() -> bool:
    return bool(Ed25519PrivateKey)


def _seed(secret: str) -> bytes:
    raw = str(secret or "").encode("utf-8")
    if not raw:
        raise SignatureError("未配置 qqbot.app_secret，无法校验回调签名")
    while len(raw) < 32:
        raw = raw * 2
    return raw[:32]


def private_key(secret: str):
    if not available():
        raise SignatureError("缺少 cryptography 依赖，无法校验 QQ 官方回调签名")
    try:
        return Ed25519PrivateKey.from_private_bytes(_seed(secret))
    except Exception as exc:
        raise SignatureError("BotSecret 无法生成签名密钥：%s" % exc)


def public_key(secret: str):
    return private_key(secret).public_key()


def sign(secret: str, message: bytes) -> str:
    """生成十六进制签名（回调地址验证用）。"""
    return binascii.hexlify(private_key(secret).sign(message)).decode("ascii")


def verify(secret: str, message: bytes, signature: str) -> bool:
    """校验十六进制签名。"""
    try:
        sig = binascii.unhexlify(str(signature or "").strip())
    except (binascii.Error, ValueError):
        return False
    if len(sig) != 64 or sig[63] & 0xE0:
        return False
    try:
        public_key(secret).verify(sig, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def validation_response(secret: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """op=13 回调验证：返回 plain_token + 签名。"""
    data = payload.get("d") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise SignatureError("回调验证请求缺少 d 字段")
    token = str(data.get("plain_token") or "")
    ts = str(data.get("event_ts") or "")
    if not token or not ts:
        raise SignatureError("回调验证请求缺少 plain_token / event_ts")
    message = (ts + token).encode("utf-8")
    return {"plain_token": token, "signature": sign(secret, message)}


def verify_request(headers: Any, raw_body: bytes, secret: str,
                   tolerance_seconds: int = 300) -> Tuple[bool, str]:
    """校验普通回调请求；返回 (是否通过, 错误说明)。"""
    if not available():
        return False, "缺少 cryptography 依赖，无法校验回调签名"
    signature = ""
    timestamp = ""
    try:
        signature = str(headers.get("X-Signature-Ed25519") or "")
        timestamp = str(headers.get("X-Signature-Timestamp") or "")
    except AttributeError:
        pass
    if not signature:
        return False, "缺少 X-Signature-Ed25519 头"
    if not timestamp:
        return False, "缺少 X-Signature-Timestamp 头"
    if not timestamp.isdigit():
        return False, "X-Signature-Timestamp 不是秒级时间戳"
    if tolerance_seconds > 0 and abs(time.time() - int(timestamp)) > tolerance_seconds:
        return False, "回调时间戳超出允许窗口"
    message = timestamp.encode("ascii") + (raw_body or b"")
    if not verify(secret, message, signature):
        return False, "回调签名校验失败"
    return True, ""
