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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any, Dict, List, Optional, Tuple

LOGGER = logging.getLogger("qqwarframe.http")

DEFAULT_UA = "QQWarframeBot/1.0"

# 全局兜底代理链：由 bot.netadapt 探测后写入，
# 各数据源新建 HttpClient 时若没显式指定 proxy_fallbacks，就沿用这一份。
_GLOBAL_FALLBACKS: List[str] = []
_GLOBAL_LOCK = threading.Lock()


def set_global_fallbacks(values: Optional[List[str]]) -> None:
    """设置全局兜底代理（按优先级）。"""
    global _GLOBAL_FALLBACKS
    with _GLOBAL_LOCK:
        _GLOBAL_FALLBACKS = [str(item) for item in (values or [])]


def global_fallbacks() -> List[str]:
    with _GLOBAL_LOCK:
        return list(_GLOBAL_FALLBACKS)


def _dedupe(values: Any) -> List[str]:
    """去掉空值与重复项，保持顺序。"""
    result: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in ("none", "direct", "off"):
            text = "none"
        if text not in result:
            result.append(text)
    return result


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
    """HTTP 客户端。

    除了固定代理，还支持一条**代理链**：请求失败（网络不通 / 403 / 5xx 等）
    时自动换下一个代理重试，成功后记住它，后续优先使用——服务器换网络、
    代理挂了都不用手改配置。
    """

    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        timeout: float = 15.0,
        proxy: str = "",
        retries: int = 2,
        insecure_tls: bool = False,
        logger: Optional[logging.Logger] = None,
        proxy_fallbacks: Optional[List[str]] = None,
        auto_switch: bool = True,
    ) -> None:
        self.user_agent = user_agent or DEFAULT_UA
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.insecure_tls = bool(insecure_tls)
        self.log = logger or LOGGER
        self.auto_switch = bool(auto_switch)
        fallbacks = proxy_fallbacks if proxy_fallbacks is not None else global_fallbacks()
        # 没配代理时也把直连放进链尾，保证「代理挂了还能直连」
        self.proxies = _dedupe([proxy] + list(fallbacks)) or [""]
        self._index = 0
        self._openers: Dict[int, Any] = {}
        self._opener = self._opener_for(0)

    # ------------------------------------------------------------ 代理链
    def _opener_for(self, index: int) -> Any:
        cached = self._openers.get(index)
        if cached is not None:
            return cached
        proxy = self.proxies[index] if 0 <= index < len(self.proxies) else ""
        handlers = []
        if not proxy:
            # 不指定时沿用系统代理 / 环境变量，方便使用 Clash、v2rayN 等
            handlers.append(urllib.request.ProxyHandler())
        elif proxy == "none":
            # 强制直连：忽略系统代理与 HTTP(S)_PROXY
            handlers.append(urllib.request.ProxyHandler({}))
        else:
            handlers.append(urllib.request.ProxyHandler({"http": proxy,
                                                          "https": proxy}))
        if self.insecure_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=context))
        else:
            handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))
        opener = urllib.request.build_opener(*handlers)
        self._openers[index] = opener
        return opener

    @property
    def current_proxy(self) -> str:
        return self.proxies[self._index] if self.proxies else ""

    def _switch_to(self, index: int) -> None:
        self._index = index
        self._opener = self._opener_for(index)

    @staticmethod
    def _should_switch(exc: "HttpError") -> bool:
        """哪些错误值得换代理重试。

        404 / 400 这类是业务响应，换代理没用；网络不通、403（Cloudflare 或
        IDC IP 被拦）、407 代理要认证、429 / 5xx 才是链路问题。
        """
        status = getattr(exc, "status", 0) or 0
        if status == 0:
            return True
        return status in (403, 407, 408, 429, 502, 503, 504) or 500 <= status < 600

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

    def _try_order(self) -> List[int]:
        """先试当前生效的代理，再依次试链上其它代理。"""
        order = [self._index]
        order.extend(index for index in range(len(self.proxies))
                     if index != self._index)
        return order

    @staticmethod
    def _describe(proxy: str) -> str:
        if not proxy:
            return "系统代理"
        if proxy == "none":
            return "直连"
        return proxy

    def request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        method: str = "GET",
        body: Optional[bytes] = None,
    ) -> bytes:
        order = self._try_order()
        last_error: Optional[HttpError] = None
        for position, index in enumerate(order):
            try:
                raw = self._request_once(index, url, headers, method, body)
            except HttpError as exc:
                last_error = exc
                if (not self.auto_switch or position >= len(order) - 1
                        or not self._should_switch(exc)):
                    raise
                nxt = self._describe(self.proxies[order[position + 1]])
                self.log.warning("%s 请求失败（%s），切换代理 -> %s",
                                 self._describe(self.proxies[index]), exc, nxt)
                continue
            if index != self._index:
                self.log.info("代理已切换为 %s",
                              self._describe(self.proxies[index]))
                self._switch_to(index)
            return raw
        raise last_error or HttpError("请求失败", url=url)

    def _request_once(
        self,
        index: int,
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

        opener = self._opener_for(index)
        last_error: Optional[Exception] = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            req = urllib.request.Request(url, data=body, headers=request_headers,
                                         method=method)
            try:
                with opener.open(req, timeout=self.timeout) as response:
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

    def probe(self, url: str, max_bytes: int = 2048,
              headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """探活：只读少量字节就断开，用来判断这条网络能不能通。

        返回 `{"ok", "status", "ms", "blocked", "error", "snippet"}`；
        `blocked` 表示被对方拦了（403 / 407 / 429），换代理往往能解决。
        """
        started = time.time()
        merged = {"User-Agent": self.user_agent, "Accept": "*/*",
                  "Connection": "close"}
        if headers:
            merged.update({str(key): str(value) for key, value in headers.items()})
        req = urllib.request.Request(url, headers=merged)

        def _elapsed() -> int:
            return int((time.time() - started) * 1000)

        try:
            with self._opener.open(req, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 0) or 0)
                snippet = response.read(max_bytes)
                return {
                    # 探的是链路：拿到 HTTP 响应就说明这条路是通的
                    # （404 之类是地址问题，不是网络问题，交给业务层处理）
                    "ok": status > 0 and status not in (403, 407, 429, 500,
                                                        502, 503, 504),
                    "status": status,
                    "ms": _elapsed(),
                    "blocked": status in (403, 407, 429),
                    "snippet": snippet[:160].decode("utf-8", "replace"),
                }
        except urllib.error.HTTPError as exc:
            code = int(exc.code or 0)
            return {"ok": code > 0 and code not in (403, 407, 429, 500, 502,
                                                    503, 504),
                    "status": code, "ms": _elapsed(),
                    "blocked": code in (403, 407, 429),
                    "error": "HTTP %s %s" % (exc.code, exc.reason or "")}
        except Exception as exc:  # noqa: BLE001 - 探活不该抛
            return {"ok": False, "status": 0, "ms": _elapsed(), "blocked": False,
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}

    def build_url(self, base: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        url = base.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in query.items() if v is not None})
        return url
