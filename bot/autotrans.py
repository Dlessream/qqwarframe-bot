"""英文短句 → 中文（自动翻译兜底）。

世界状态里有些文案只有英文：
- 官方接口的电波挑战只有内部路径（如 SeasonDailyTwoForOne）
- 中转数据不可用时，新闻/活动的标题也只剩英文原文

这里用 MyMemory 的免费接口做一层短句翻译，只处理“短且不含中文”的文本，
结果落盘缓存，避免重复请求。翻译失败时原样返回，不影响展示。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Dict, Optional

from .net import HttpClient

LOGGER = logging.getLogger("qqwarframe.autotrans")

ENDPOINT = "https://api.mymemory.translated.net/get"
DEFAULT_CACHE = os.path.join("data", "autotrans_cache.json")
MAX_LENGTH = 90            # 超长句子不翻（新闻正文），避免浪费额度
MIN_LETTERS = 4
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")

_lock = threading.Lock()
_memory: Dict[str, str] = {}
_loaded = False
_client: Optional[HttpClient] = None
_disabled = os.environ.get("QQWF_AUTOTRANS", "1").strip().lower() in (
    "0", "false", "off", "no")


def has_chinese(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def needs_translation(text: str) -> bool:
    value = str(text or "").strip()
    if not value or has_chinese(value):
        return False
    if len(value) > MAX_LENGTH:
        return False
    if len(_LATIN.findall(value)) < MIN_LETTERS:
        return False
    if value.lower().startswith("http"):
        return False
    return True


def _cache_path() -> str:
    return os.environ.get("QQWF_AUTOTRANS_CACHE", DEFAULT_CACHE)


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_cache_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            _memory.update({str(k): str(v) for k, v in data.items()})
    except Exception:
        pass


def _save() -> None:
    path = _cache_path()
    try:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        temp = path + ".tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(_memory, handle, ensure_ascii=False)
        os.replace(temp, path)
    except Exception:
        pass


def _http() -> HttpClient:
    global _client
    if _client is None:
        _client = HttpClient(user_agent="Mozilla/5.0", timeout=8,
                             proxy="none", retries=0, logger=LOGGER)
    return _client


def _fetch(text: str) -> str:
    url = "%s?q=%s&langpair=en|zh-CN" % (ENDPOINT, urllib.parse.quote(text))
    data = _http().get_json(url)
    if not isinstance(data, dict):
        return ""
    payload = data.get("responseData") or {}
    translated = str(payload.get("translatedText") or "").strip()
    if not translated or "QUERY LENGTH" in translated.upper():
        return ""
    if translated.lower() == text.lower():
        return ""
    return translated


def to_chinese(text: str, limit: Optional[int] = None) -> str:
    """英文短句转中文；不需要或失败时原样返回。"""
    global _disabled
    value = str(text or "").strip()
    if limit is not None and len(value) > limit:
        return value
    if _disabled or not needs_translation(value):
        return value
    with _lock:
        _load()
        cached = _memory.get(value)
    if cached:
        return cached
    try:
        translated = _fetch(value)
    except Exception as exc:  # 网络失败就放弃，别拖慢回复
        LOGGER.debug("自动翻译失败（%s）：%s", exc, value[:40])
        return value
    if not translated or not has_chinese(translated):
        return value
    with _lock:
        _memory[value] = translated
        if len(_memory) > 4000:
            for key in list(_memory)[:1000]:
                _memory.pop(key, None)
        if len(_memory) % 10 == 1:
            _save()
    return translated


def flush() -> None:
    """把内存里的翻译结果写盘（进程退出前调用）。"""
    with _lock:
        _save()


def clear() -> None:
    with _lock:
        _memory.clear()
        _loaded = False


def disable() -> None:
    global _disabled
    _disabled = True
