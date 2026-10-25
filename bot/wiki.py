"""中文 Warframe 维基（huijiwiki）查询客户端。

使用 MediaWiki API：
  action=query&list=search        关键词搜索
  action=query&prop=extracts      词条摘要（TextExtracts 扩展）
  action=parse&prop=text          摘要缺失时回退解析首段 HTML
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

from .cache import TTLCache
from .net import HttpClient, HttpError

LOGGER = logging.getLogger("qqwarframe.wiki")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BWIKI_BASE = "https://wiki.biligame.com/warframe"

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200b\u202f\u205f\u3000]+")
_NEWLINE_RE = re.compile(r"\n{3,}")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.I | re.S)


class WikiError(Exception):
    """维基数据不可用。"""


@dataclass
class WikiHit:
    title: str
    snippet: str
    pageid: int = 0
    url: str = ""


@dataclass
class WikiPage:
    title: str
    extract: str
    url: str
    pageid: int = 0


def clean_wiki_text(text: str, limit: int = 0) -> str:
    """清理维基返回文本：去标签、还原实体、压缩空白。"""
    if not text:
        return ""
    value = _STYLE_RE.sub("", text)
    value = _TAG_RE.sub("", value)
    value = html.unescape(value)
    value = value.replace("\u2009", " ").replace("\u200b", "")
    value = _SPACE_RE.sub(" ", value)
    value = "\n".join(line.strip() for line in value.splitlines())
    value = _NEWLINE_RE.sub("\n\n", value).strip()
    if limit and len(value) > limit:
        value = _truncate(value, limit)
    return value


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    window = text[:limit]
    for mark in ("。", "！", "？", "\n", "；"):
        index = window.rfind(mark)
        if index >= limit * 0.55:
            return window[:index + 1].rstrip() + "…"
    return window.rstrip() + "…"


class WikiClient:
    def __init__(self, config: Dict[str, Any], http: Optional[HttpClient] = None,
                 logger: Optional[logging.Logger] = None,
                 cache_config: Optional[Dict[str, Any]] = None):
        self.cfg = config
        self.log = logger or LOGGER
        self.api_url = str(config["api_url"])
        self.site_url = str(config.get("site_url", "https://warframe.huijiwiki.com/wiki/"))
        self.summary_chars = int(config.get("summary_chars", 280))
        cache_cfg = cache_config or {}
        self.cache = TTLCache(cache_cfg, prefix="wiki")
        self.cache_seconds = int(cache_cfg.get("wiki_seconds", 600))
        self.cookie = str(config.get("cookie") or "").strip()
        self.http = http or HttpClient(
            user_agent=config.get("user_agent", BROWSER_UA),
            timeout=config.get("timeout_seconds", 15),
            proxy=config.get("proxy", ""),
            retries=config.get("retries", 2),
            logger=self.log,
        )
        self.bwiki = None
        if config.get("fallback_bwiki", True):
            self.bwiki = BWikiClient(config, logger=self.log)

    # ------------------------------------------------------------------ 基础
    def _call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = {"format": "json", "formatversion": "1"}
        query.update(params)
        url = self.api_url + ("&" if "?" in self.api_url else "?") + urllib.parse.urlencode(query)
        headers = {"Accept": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            data = self.http.get_json(url, headers=headers)
        except HttpError as exc:
            raise WikiError("维基接口请求失败（%s）。" % exc)
        if not isinstance(data, dict):
            raise WikiError("维基接口返回格式异常。")
        error = data.get("error")
        if error:
            raise WikiError("维基接口报错：%s" % (error.get("info") or error))
        return data

    def page_url(self, title: str) -> str:
        # 保留中文，只转义会破坏 URL 结构的字符，图片里显示更友好
        readable = (title.replace(" ", "_")
                    .replace("%", "%25")
                    .replace("#", "%23")
                    .replace("?", "%3F"))
        return self.site_url + readable

    # ------------------------------------------------------------------ 搜索
    def search(self, query: str, limit: int = 5) -> List[WikiHit]:
        data = self._call({
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max(1, min(int(limit), 10)),
            "srnamespace": "0",
            "srprop": "snippet",
        })
        rows = ((data.get("query") or {}).get("search") or [])
        hits = []
        for row in rows:
            title = clean_wiki_text(str(row.get("title") or ""))
            if not title:
                continue
            hits.append(WikiHit(
                title=title,
                snippet=clean_wiki_text(str(row.get("snippet") or ""), 90),
                pageid=int(row.get("pageid") or 0),
                url=self.page_url(title),
            ))
        return hits

    # ------------------------------------------------------------------ 摘要
    def page(self, title: str) -> Optional[WikiPage]:
        data = self._call({
            "action": "query",
            "prop": "extracts",
            "explaintext": "1",
            "exintro": "1",
            "exlimit": "1",
            "redirects": "1",
            "titles": title,
        })
        pages = ((data.get("query") or {}).get("pages") or {})
        row = _first_page(pages)
        if not row:
            return None
        if "missing" in row or "invalid" in row or str(row.get("pageid")) == "-1":
            return None
        real_title = clean_wiki_text(str(row.get("title") or title))
        extract = clean_wiki_text(str(row.get("extract") or ""), self.summary_chars)
        if not extract:
            extract = self._parse_intro(real_title)
        if not extract:
            return None
        return WikiPage(
            title=real_title,
            extract=extract,
            url=self.page_url(real_title),
            pageid=int(row.get("pageid") or 0),
        )

    def _parse_intro(self, title: str) -> str:
        """TextExtracts 不可用时的兜底：解析首段 HTML。"""
        try:
            data = self._call({
                "action": "parse",
                "page": title,
                "prop": "text",
                "section": "0",
                "redirects": "1",
                "disableeditsection": "1",
            })
        except WikiError:
            return ""
        text = ((data.get("parse") or {}).get("text") or "")
        if isinstance(text, dict):
            text = text.get("*") or ""
        cleaned = clean_wiki_text(str(text), self.summary_chars)
        return cleaned

    # ------------------------------------------------------------------ 组合
    def lookup(self, query: str, limit: int = 3) -> Tuple[Optional[WikiPage], List[WikiHit]]:
        key = ("lookup", (query or "").strip().lower(), int(limit))
        return self.cache.get_or_set(
            key, lambda: self._lookup_uncached(query, limit), self.cache_seconds)

    def _lookup_uncached(self, query: str,
                         limit: int = 3) -> Tuple[Optional[WikiPage], List[WikiHit]]:
        """先按标题精确查；失败再搜索并取第一条的摘要。

        huijiwiki 被 WAF 拦截或不可用时，自动尝试 B 站 WIKI（HTML 抓取）。
        """
        try:
            page = self.page(query)
            if page:
                return page, []
            hits = self.search(query, limit=limit)
            if hits:
                first = self.page(hits[0].title)
                return first, hits
        except WikiError as exc:
            self.log.warning("huijiwiki 查询失败，尝试 BWIKI 兜底：%s", exc)
            if self.bwiki is not None:
                try:
                    page, hits = self.bwiki.lookup(query, limit=limit)
                    if page or hits:
                        return page, hits
                except BWikiError as fallback_exc:
                    self.log.warning("BWIKI 兜底也失败：%s", fallback_exc)
            raise

        if self.bwiki is not None:
            try:
                page, hits = self.bwiki.lookup(query, limit=limit)
                if page or hits:
                    return page, hits
            except BWikiError as exc:
                self.log.warning("BWIKI 兜底失败：%s", exc)
        return None, []


def _first_page(pages: Any) -> Optional[Dict[str, Any]]:
    if isinstance(pages, list):
        for row in pages:
            if isinstance(row, dict):
                return row
        return None
    if isinstance(pages, dict):
        for row in pages.values():
            if isinstance(row, dict):
                return row
    return None

# ---------------------------------------------------------------------- BWIKI 兜底
def _titles_match(requested: str, actual: str) -> bool:
    left = re.sub(r"\s+", "", requested).lower()
    right = re.sub(r"\s+", "", actual).lower()
    if not left or not right:
        return False
    return left == right or left in right or right in left


class BWikiError(Exception):
    """B 站 WIKI 不可用。"""


def extract_bwiki_title(html_text: str) -> str:
    match = re.search(r'<h1[^>]*id="firstHeading"[^>]*>(.*?)</h1>', html_text, re.S)
    if match:
        return clean_wiki_text(match.group(1))
    match = re.search(r"<title>(.*?)</title>", html_text, re.S)
    if match:
        title = clean_wiki_text(match.group(1))
        for separator in (" - ", "_"):
            if separator in title:
                title = title.split(separator)[0]
        return title.strip()
    return ""


def extract_bwiki_intro(html_text: str, limit: int = 280) -> str:
    start = html_text.find("mw-parser-output")
    chunk = html_text[start:start + 80000] if start >= 0 else html_text
    chunk = re.sub(r"<(script|style|table|noscript|sup)[^>]*>.*?</\1>", " ",
                   chunk, flags=re.S | re.I)
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", chunk, re.S)
    parts: List[str] = []
    for paragraph in paragraphs:
        text = clean_wiki_text(paragraph)
        if len(text) >= 10:
            parts.append(text)
        if sum(len(part) for part in parts) > limit * 1.5:
            break
    if not parts:
        text = clean_wiki_text(chunk)
        if text:
            parts = [text]
    return clean_wiki_text("\n".join(parts), limit)


def parse_bwiki_search(html_text: str, base: str, limit: int = 5) -> List["WikiHit"]:
    hits: List["WikiHit"] = []
    seen = set()
    patterns = (
        r'class="mw-search-result-heading"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*title="([^"]+)"',
        r'<a[^>]*href="(/warframe/[^"#?]+)"[^>]*title="([^"]+)"',
    )
    for pattern in patterns:
        for href, title in re.findall(pattern, html_text, re.S):
            name = clean_wiki_text(title)
            if not name or name in seen:
                continue
            if ":" in href or "特殊:" in name:
                continue
            seen.add(name)
            hits.append(WikiHit(name, "", 0, urljoin(base, href)))
            if len(hits) >= limit:
                return hits
        if hits:
            return hits
    return hits


class BWikiClient:
    """B 站 WIKI 的 HTML 兜底客户端（没有可用的 MediaWiki API）。"""

    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None):
        self.base = str(config.get("bwiki_base", BWIKI_BASE)).rstrip("/")
        self.summary_chars = int(config.get("summary_chars", 280))
        self.log = logger or LOGGER
        self.cookie = str(config.get("cookie") or "").strip()
        self.http = HttpClient(
            user_agent=config.get("user_agent", BROWSER_UA),
            timeout=config.get("timeout_seconds", 15),
            proxy=config.get("proxy", ""),
            retries=config.get("retries", 1),
            logger=self.log,
        )

    def _get(self, url: str) -> str:
        headers = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            return self.http.get_text(url, headers=headers)
        except HttpError as exc:
            raise BWikiError("B 站 WIKI 请求失败（%s）" % exc)

    def page(self, title: str) -> Optional["WikiPage"]:
        url = self.base + "/" + urllib.parse.quote(title.replace(" ", "_"))
        html_text = self._get(url)
        if len(html_text) < 200:
            return None
        if "此页面目前没有内容" in html_text or "noarticletext" in html_text:
            return None
        real_title = extract_bwiki_title(html_text)
        # WAF 有时会把任意请求重定向到首页并返回 200，必须校验标题
        if real_title and not _titles_match(title, real_title):
            self.log.info("BWIKI 返回了其他页面（请求 %s，得到 %s），已丢弃",
                          title, real_title)
            return None
        real_title = real_title or title
        intro = extract_bwiki_intro(html_text, self.summary_chars)
        if not intro:
            return None
        return WikiPage(real_title, intro, url)

    def search(self, query: str, limit: int = 5) -> List["WikiHit"]:
        params = urllib.parse.urlencode({
            "search": query,
            "title": "特殊:搜索",
            "fulltext": "1",
        })
        html_text = self._get(self.base + "/index.php?" + params)
        return parse_bwiki_search(html_text, self.base, limit=limit)

    def lookup(self, query: str, limit: int = 3) -> Tuple[Optional["WikiPage"], List["WikiHit"]]:
        page = self.page(query)
        if page:
            return page, []
        hits = self.search(query, limit=limit)
        if not hits:
            return None, []
        return self.page(hits[0].title), hits

