"""极简 HTTP 客户端（仅标准库）。

- gzip / deflate 解压
- 4xx/5xx 明确抛错，429 与 5xx 自动重试
- 可配置代理、超时、UA
- 返回 JSON / 文本
"""

from __future__ import annotations

import errno
import gzip
import http.client
import json
import logging
import os
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


class _NeedsFallback(Exception):
    """连接复用这条路处理不了（重定向 / 非 GET），交给 urllib 兜底。"""


class HttpError(Exception):
    """HTTP 请求失败。"""

    def __init__(self, message: str, status: int = 0, url: str = "", body: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url
        self.body = body
        # 链路根本不通（网络不可达 / 代理没监听），重试没有意义
        self.unreachable = False

    def is_retryable(self) -> bool:
        return self.status == 429 or 500 <= self.status < 600 or self.status == 0


# 这些 errno 表示「这条路根本走不通」：网段不可达、主机不可达、代理端口没监听。
# 跟超时不一样——重试多少次都不会通，只会白等好几个完整超时。
def _unreachable_errnos() -> frozenset:
    names = ("ENETUNREACH", "ENETDOWN", "EHOSTUNREACH", "ECONNREFUSED",
             "EADDRNOTAVAIL", "ENONET", "EHOSTDOWN")
    return frozenset(getattr(errno, name)
                     for name in names if hasattr(errno, name))


UNREACHABLE_ERRNOS = _unreachable_errnos()


def is_unreachable(reason: Any) -> bool:
    """判断一个网络错误是不是「链路不通」（而不是慢 / 临时抖动）。"""
    code = getattr(reason, "errno", None)
    if isinstance(code, int) and code in UNREACHABLE_ERRNOS:
        return True
    text = str(reason or "").lower()
    return any(word in text for word in ("unreachable", "refused",
                                         "network is down", "no route to host"))


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
        # 直连时复用 TCP/TLS 连接：每个新连接要付一次完整握手（实测 0.6~1.6s），
        # 一条命令往往要打 3~9 个请求，握手开销比传输本身还大。
        # 按线程各自持有连接（http.client 的连接不能跨线程共用）。
        self._conn_local = threading.local()
        self._ssl_cache: Optional[ssl.SSLContext] = None
        self._ssl_lock = threading.Lock()
        # 代理被目标站按 IP 拦（403/407）后别再每次都先撞一遍：
        # 记一段时间，下次直接从下一个候选开始，省掉半个超时的白等。
        self._banned: Dict[int, float] = {}
        self._ban_seconds = 900.0

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
        """先试当前生效的代理，再依次试链上其它代理；被 403 过的排最后。"""
        order = [self._index]
        order.extend(index for index in range(len(self.proxies))
                     if index != self._index)
        now = time.time()
        fresh = [index for index in order
                 if now >= self._banned.get(index, 0.0)]
        banned = [index for index in order
                  if now < self._banned.get(index, 0.0)]
        return fresh + banned

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
                if exc.status in (403, 407):
                    self._banned[index] = time.time() + self._ban_seconds
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
            self._banned.pop(index, None)
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

        # 强制直连时走连接复用；配了代理的情况交给 urllib（代理隧道没法定制）。
        if self.proxies[index] == "none" and body is None \
                and method.upper() in ("GET", "HEAD"):
            try:
                return self._request_pooled(url, request_headers, method)
            except _NeedsFallback:
                pass

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
                if is_unreachable(reason):
                    # 链路不通：不再重试，直接换代理，省掉整轮超时等待
                    last_error.unreachable = True
                    raise last_error
                if attempt < attempts - 1:
                    delay = 0.8 * (2 ** attempt)
                    self.log.warning("请求 %s 失败（%s），%.1fs 后重试", url, reason, delay)
                    time.sleep(delay)
                    continue
                raise last_error

        raise last_error or HttpError("请求失败", url=url)

    # ------------------------------------------------------------ 连接复用
    def _ssl_for_pool(self) -> ssl.SSLContext:
        """连接池用的 TLS 上下文，只建一次（每次新建要读证书，几十毫秒）。"""
        cached = self._ssl_cache
        if cached is not None:
            return cached
        with self._ssl_lock:
            if self._ssl_cache is None:
                if self.insecure_tls:
                    context = ssl.create_default_context()
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    self._ssl_cache = context
                else:
                    self._ssl_cache = _ssl_context()
            return self._ssl_cache

    def _new_conn(self, scheme: str, host: str, port: int):
        if scheme == "https":
            return http.client.HTTPSConnection(
                host, port, timeout=self.timeout, context=self._ssl_for_pool())
        return http.client.HTTPConnection(host, port, timeout=self.timeout)

    def _request_pooled(self, url: str, request_headers: Dict[str, str],
                        method: str) -> bytes:
        """直连时复用 keep-alive 连接，省掉每请求一次的 TCP+TLS 握手。

        - 连接按线程隔离（http.client 不保证跨线程安全）
        - 服务端已经关掉的空闲连接（RemoteDisconnected / BrokenPipe）
          自动新建一条重发，不算失败
        - 3xx 重定向不在这里处理，交给 urllib 兜底，避免自己实现跳转
        """
        parts = urllib.parse.urlsplit(url)
        scheme = (parts.scheme or "").lower()
        host = parts.hostname or ""
        if scheme not in ("http", "https") or not host:
            raise _NeedsFallback()
        port = parts.port or (443 if scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path = "%s?%s" % (path, parts.query)

        pool = getattr(self._conn_local, "pool", None)
        if pool is None:
            pool = {}
            self._conn_local.pool = pool
        key = (scheme, host, port)

        headers = dict(request_headers)
        headers.pop("Connection", None)  # 保留长连接
        attempts = self.retries + 1
        last_error: Optional[HttpError] = None

        for attempt in range(attempts):
            conn = pool.pop(key, None)
            reused = conn is not None
            try:
                if conn is None:
                    conn = self._new_conn(scheme, host, port)
                else:
                    conn.timeout = self.timeout
                conn.request(method, path, body=None, headers=headers)
                response = conn.getresponse()
                raw = response.read()
                status = int(getattr(response, "status", 0) or 0)
                if 300 <= status < 400:
                    conn.close()
                    raise _NeedsFallback()
                if status >= 400:
                    conn.close()
                    last_error = HttpError(
                        "HTTP %d %s" % (status, getattr(response, "reason", "")),
                        status=status, url=url)
                    if (status == 429 or 500 <= status < 600) and attempt < attempts - 1:
                        delay = 0.8 * (2 ** attempt)
                        self.log.warning("请求 %s 返回 %d，%.1fs 后重试",
                                         url, status, delay)
                        time.sleep(delay)
                        continue
                    raise last_error
                pool[key] = conn
                return self._decode_body(
                    raw, response.getheader("Content-Encoding", "") or "")
            except _NeedsFallback:
                raise
            except HttpError:
                raise
            except (http.client.HTTPException, socket.timeout, ssl.SSLError,
                    OSError) as exc:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 - 关闭失败无所谓
                    pass
                if reused:
                    # 复用的连接可能被服务端关了：换新连接原样重发一次
                    last_error = HttpError("网络错误：%s" % exc, status=0, url=url)
                    continue
                last_error = HttpError("网络错误：%s" % exc, status=0, url=url)
                if is_unreachable(exc):
                    last_error.unreachable = True
                    raise last_error
                if attempt < attempts - 1:
                    delay = 0.8 * (2 ** attempt)
                    self.log.warning("请求 %s 失败（%s），%.1fs 后重试",
                                     url, exc, delay)
                    time.sleep(delay)
                    continue
                raise last_error

        raise last_error or HttpError("请求失败", url=url)

    def download(self, url: str, dest: str,
                 headers: Optional[Dict[str, str]] = None,
                 progress=None,
                 max_bytes: int = 0,
                 chunk_size: int = 262144) -> int:
        """流式下载到文件，返回字节数。

        与 ``request`` 的区别：不把整个响应读进内存（源码包十几 MB），
        并且每写一块回调 ``progress(got, total)``，便于前端显示进度；
        ``total`` 为 0 表示服务端没给 Content-Length。
        超过 ``max_bytes`` 直接中止，避免被异常大文件拖死。
        """
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/octet-stream",
            "Accept-Encoding": "identity",
            "Connection": "close",
        }
        if headers:
            request_headers.update({str(k): str(v) for k, v in headers.items()})

        order = self._try_order()
        last_error: Optional[HttpError] = None
        for position, index in enumerate(order):
            opener = self._opener_for(index)
            got = 0
            done = False
            try:
                req = urllib.request.Request(url, headers=request_headers)
                with opener.open(req, timeout=self.timeout) as response:
                    try:
                        total = int(response.headers.get("Content-Length") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    with open(dest, "wb") as handle:
                        while True:
                            chunk = response.read(chunk_size)
                            if not chunk:
                                break
                            handle.write(chunk)
                            got += len(chunk)
                            if max_bytes and got > max_bytes:
                                raise HttpError(
                                    "文件超过 %d 字节上限" % max_bytes, url=url)
                            if progress is not None:
                                try:
                                    progress(got, total)
                                except Exception:  # 进度回调不该影响下载
                                    pass
                done = True
                if index != self._index:
                    self.log.info("代理已切换为 %s",
                                  self._describe(self.proxies[index]))
                    self._switch_to(index)
                return got
            except urllib.error.HTTPError as exc:
                last_error = HttpError("HTTP %d %s" % (exc.code, exc.reason),
                                       status=exc.code, url=url)
                if (not self.auto_switch or position >= len(order) - 1
                        or not self._should_switch(last_error)):
                    raise last_error
                self.log.warning("%s 下载失败（%s），切换代理 -> %s",
                                 self._describe(self.proxies[index]), last_error,
                                 self._describe(self.proxies[order[position + 1]]))
            except (urllib.error.URLError, socket.timeout, ssl.SSLError,
                    OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last_error = HttpError("网络错误：%s" % reason, status=0, url=url)
                if is_unreachable(reason) or not self.auto_switch \
                        or position >= len(order) - 1:
                    raise last_error
                self.log.warning("%s 下载失败（%s），切换代理 -> %s",
                                 self._describe(self.proxies[index]), last_error,
                                 self._describe(self.proxies[order[position + 1]]))
            finally:
                if not done and os.path.exists(dest):
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
        raise last_error or HttpError("下载失败", url=url)

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
