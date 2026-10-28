"""灰机 wiki 的国服译名表（`data/wiki_zh.json`）。

数据来自灰机 wiki「国服名称对照」页（正文数据在 `Data:DictCY.tabx`，由
`Module:TranslateCY` 按章节渲染成表格）。国服栏写「保留」的条目沿用国际服
名称，写「暂无」的不收录。

用法：
    wiki_zh.lookup("Rubico")          -> "绝路"
    wiki_zh.lookup("Rubico Prime")    -> "圣装绝路"（表里没有就按「圣装 + 基础名」）
    wiki_zh.localize("Rubico Prime")  -> 查不到时原样返回

查不到一律返回 None / 原字符串——宁可留英文，也不瞎猜。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Dict, Optional

LOGGER = logging.getLogger("qqwarframe.wiki_zh")

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "wiki_zh.json")

_SPACE_RE = re.compile(r"\s+")
_AMP_RE = re.compile(r"\s*&\s*")
_STRIP_SUFFIXES = (" set", " blueprint", " 蓝图", " 套装")

_lock = threading.Lock()
_index: Optional[Dict[str, str]] = None

# 分类名，方便按类取表（战甲表在 data/names_zh.json 里，这里不重复）
CATEGORIES = ("weapons", "mods", "zaw", "arcane", "resources", "fish")


def _normalize(text: str) -> str:
    """统一大小写 / 空白 / & 两侧空格，方便做键。"""
    value = str(text or "").strip().lower()
    value = _AMP_RE.sub(" & ", value)
    value = value.replace("mk1-", "mk1 ").replace("mk-", "mk ")
    return _SPACE_RE.sub(" ", value).strip()


def _load() -> Dict[str, str]:
    """英文名（归一化）→ 国服中文名。"""
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is not None:
            return _index
        table: Dict[str, str] = {}
        try:
            with open(_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取 wiki 译名表失败：%s", exc)
            raw = {}
        for key, block in (raw or {}).items():
            if key.startswith("_") or not isinstance(block, dict):
                continue
            for english, chinese in block.items():
                name = _normalize(english)
                value = str(chinese or "").strip()
                if name and value:
                    table.setdefault(name, value)
        _index = table
    return _index


def lookup(text: str) -> Optional[str]:
    """英文名 → 国服中文名；查不到返回 None。"""
    table = _load()
    key = _normalize(text)
    if not key:
        return None
    hit = table.get(key)
    if hit:
        return hit
    for suffix in _STRIP_SUFFIXES:
        if key.endswith(suffix):
            hit = table.get(key[:-len(suffix)].strip())
            if hit:
                return hit
    # 表里没单列的「X Prime」：国服写法是「圣装 + 基础名」
    if key.endswith(" prime"):
        base = lookup(key[:-len(" prime")].strip())
        if base:
            return "圣装" + base
    return None


def localize(text: str) -> str:
    """查到就返回国服译名，查不到原样返回。

    `X Blueprint` / `X Set` 这类后缀会被拆掉再查，查到后把中文后缀补回去
    （圣装绝路蓝图 / 圣装绝路套装），免得调用方拿到少了半截的名字。
    """
    value = str(text or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    for suffix, chinese in ((" blueprint", "蓝图"), (" set", "套装")):
        if lowered.endswith(suffix):
            hit = lookup(value[:-len(suffix)])
            if hit:
                return hit + chinese
            break
    return lookup(value) or value


def count() -> int:
    return len(_load())


def categories() -> Dict[str, int]:
    """各分类收录条数，供自检/控制台查看。"""
    try:
        with open(_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {key: len(value) for key, value in (raw or {}).items()
            if not key.startswith("_") and isinstance(value, dict)}
