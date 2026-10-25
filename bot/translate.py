"""轻量英文 -> 中文翻译。

优先 Google 的公开 translate_a/single 端点（可走 Wiki 代理），
失败后用 MyMemory 免费接口（支持直连）。结果缓存 24 小时。
"""

from __future__ import annotations

import html
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

from .cache import TTLCache
from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.translate")
GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
MYMEMORY_URL = "https://api.mymemory.translated.net/get"


class TranslateError(Exception):
    pass


class Translator:
    def __init__(self, config: Dict[str, Any],
                 cache_config: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None,
                 http: Optional[HttpClient] = None):
        cfg = config or {}
        self.log = logger or LOGGER
        self.cache = TTLCache(cache_config or {}, prefix="translate")
        self.cache_seconds = int(cfg.get("translate_seconds", 86400))
        self.timeout = int(cfg.get("timeout_seconds", 15))
        self.proxy = str(cfg.get("proxy") or "")
        self.proxy_mode = str(cfg.get("proxy_mode") or "auto").lower()
        self._clients: List[HttpClient] = []
        if http is not None:
            self._clients = [http]
        else:
            proxies = [self.proxy]
            if self.proxy_mode == "auto" and "none" not in proxies:
                proxies.append("none")
            for proxy in proxies:
                if proxy in ("", "none"):
                    proxy = "none"
                self._clients.append(HttpClient(
                    user_agent=cfg.get("user_agent", "Mozilla/5.0"),
                    timeout=self.timeout, proxy=proxy, retries=0,
                    logger=self.log))
        self.http = self._clients[0]

    @staticmethod
    def _chunks(text: str, limit: int = 1200) -> List[str]:
        text = str(text or "").strip()
        if len(text) <= limit:
            return [text] if text else []
        chunks: List[str] = []
        current = ""
        for line in text.splitlines(True):
            if len(current) + len(line) > limit and current:
                chunks.append(current)
                current = ""
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current += line
        if current:
            chunks.append(current)
        return chunks

    def _google(self, text: str, client: HttpClient) -> str:
        query = {
            "client": "gtx", "sl": "en", "tl": "zh-CN",
            "dt": "t", "q": text,
        }
        url = GOOGLE_URL + "?" + urllib.parse.urlencode(query)
        data = client.get_json(url, headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        })
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            raise TranslateError("Google 翻译返回格式异常。")
        parts = [str(row[0]) for row in data[0]
                 if isinstance(row, list) and row and row[0]]
        if not parts:
            raise TranslateError("Google 翻译没有返回文本。")
        return "".join(parts).strip()

    def _mymemory(self, text: str) -> str:
        query = urllib.parse.urlencode({
            "q": text, "langpair": "en|zh-CN",
        })
        url = MYMEMORY_URL + "?" + query
        last_error = None
        for client in self._clients:
            try:
                data = client.get_json(url, headers={
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0",
                })
                if not isinstance(data, dict):
                    continue
                payload = data.get("responseData") or {}
                translated = str(payload.get("translatedText") or "").strip()
                if translated:
                    return html.unescape(translated)
            except Exception as exc:
                last_error = exc
        raise TranslateError("MyMemory 翻译失败：%s" % last_error)

    def translate(self, text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        key = ("en-zh", raw[:800])
        cached = self.cache.get(key)
        if cached is not None:
            return str(cached)
        translated_parts = []
        for chunk in self._chunks(raw):
            result = None
            last_error = None
            # Google 优先；不同代理线路各试一次。
            for client in self._clients:
                try:
                    result = self._google(chunk, client)
                    break
                except (HttpError, TranslateError, Exception) as exc:
                    last_error = exc
            if not result:
                try:
                    result = self._mymemory(chunk)
                except TranslateError as exc:
                    self.log.warning("翻译失败：%s（%s）", exc, last_error)
                    raise
            translated_parts.append(result)
        translated = "\n".join(translated_parts).strip()
        if translated:
            self.cache.set(key, translated, self.cache_seconds)
        return translated
