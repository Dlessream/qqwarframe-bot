"""极简 HTTP 客户端（仅标准库）。

- gzip / deflate 解压
- 4xx/5xx 明确抛错，429 与 5xx 自动重试
- 可配置代理、超时、UA
- 返回 JSON / 文本
"""

from __future__ import annotations

import gzip
import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, Optional

LOGGER = logging.getLogger("qqwarframe.http")

DEFAULT_UA = "QQWarframeBot/1.0"


def _ssl_context() -> ssl.SSLContext:
    """优先用 certifi 的 CA，避免 Windows 证书库被限制时握手失败。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class HttpError(Exception):
    """HTTP 请求失败。"""

    def __init__(self, message: str, status: int = 0, url: str = "", body: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body

    def is_retryable(self) -> bool:
        return self.status == 429 or 500 <= self.status < 600 or self.status == 0


class HttpClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        timeout: float = 15.0,
        proxy: str = "",
        retries: int = 2,
        insecure_tls: bool = False,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.user_agent = user_agent or DEFAULT_UA
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.log = logger or LOGGER

        handlers = []
        if proxy and proxy.strip().lower() in ("none", "direct", "off"):
            # 强制直连：忽略系统代理与 HTTP(S)_PROXY
            handlers.append(urllib.request.ProxyHandler({}))
        elif proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            # 不指定时沿用系统代理 / 环境变量，方便使用 Clash、v2rayN 等
            handlers.append(urllib.request.ProxyHandler())
        if insecure_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        else:
            handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))

        self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------------ 基础
    def _decode_body(self, raw: bytes, encoding: str) -> bytes:
        encoding = (encoding or "").lower().strip()
        if not raw:
            return raw
        try:
            if "gzip" in encoding:
                return gzip.decompress(raw)
            if "deflate" in encoding:
                try:
                    return zlib.decompress(raw)
                except zlib.error:
                    return zlib.decompress(raw, -zlib.MAX_WBITS)
        except (OSError, zlib.error) as exc:
            self.log.warning("响应解压失败(%s)：%s", encoding, exc)
        return raw

    def request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        method: str = "GET",
        body: Optional[bytes] = None,
    ) -> bytes:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        }
        if headers:
            request_headers.update({str(k): str(v) for k, v in headers.items()})

        last_error: Optional[Exception] = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=body, headers=request_headers,
                                         method=method)
            try:
                with self._opener.open(req, timeout=self.timeout) as response:
                    raw = response.read()
                    return self._decode_body(raw, response.headers.get("Content-Encoding", ""))
            except urllib.error.HTTPError as exc:
                body_text = ""
                try:
                    body_text = exc.read().decode("utf-8", "replace")[:500]
                except Exception:
                    pass
                last_error = HttpError(
                    "HTTP %d %s" % (exc.code, exc.reason), status=exc.code,
                    url=url, body=body_text)
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt < attempts - 1:
                        delay = 0.8 * (2 ** attempt)
                        self.log.warning("请求 %s 返回 %d，%.1fs 后重试", url, exc.code, delay)
                        time.sleep(delay)
                        continue
                raise last_error
            except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = HttpError("网络错误：%s" % reason, status=0, url=url)
                if attempt < attempts - 1:
                    delay = 0.8 * (2 ** attempt)
                    self.log.warning("请求 %s 失败（%s），%.1fs 后重试", url, reason, delay)
                    time.sleep(delay)
                    continue
                raise last_error

        raise last_error or HttpError("请求失败", url=url)

    # ------------------------------------------------------------------ 便捷
    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        raw = self.request(url, headers=headers)
        if not raw:
            raise HttpError("响应为空", url=url)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            snippet = raw[:120].decode("utf-8", "replace")
            raise HttpError("响应不是合法 JSON：%s（%s）" % (exc, snippet), url=url)

    def get_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        return self.request(url, headers=headers).decode("utf-8", "replace")

    def build_url(self, base: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        url = base.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None})
        return url
