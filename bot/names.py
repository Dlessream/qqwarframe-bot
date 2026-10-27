"""英文名 → 国服中文名（战甲 / 武器）。

世界状态里给的都是英文名（无尽回廊的可选战甲、武器等），直接显示对中文玩家
不友好。对照表放在 `data/names_zh.json`：

- 战甲：灰机 wiki「战甲对比/发布日期」里的国服名称列（含 Prime / Umbra、新战甲）
- 武器：warframe.market 紫卡武器接口的 `zh-hans` 译名（全部 420 把可上紫卡的武器）

查不到的名字一律原样返回英文——宁可留着英文，也不瞎猜一个错的中文名。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Dict, Optional, Tuple

LOGGER = logging.getLogger("qqwarframe.names")

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "names_zh.json")

# 常见后缀：套装 / 蓝图 / 部件，拆掉再查一次基础名
_STRIP_SUFFIXES = (" set", " blueprint")
_SPACE_RE = re.compile(r"\s+")

_lock = threading.Lock()
_tables: Optional[Tuple[Dict[str, str], Dict[str, str]]] = None


def _normalize(text: str) -> str:
    """统一大小写与空白，方便做键。"""
    return _SPACE_RE.sub(" ", str(text or "").strip()).lower()


def _load() -> Tuple[Dict[str, str], Dict[str, str]]:
    global _tables
    if _tables is not None:
        return _tables
    with _lock:
        if _tables is not None:
            return _tables
        warframes: Dict[str, str] = {}
        weapons: Dict[str, str] = {}
        try:
            with open(_PATH, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取名称对照表失败：%s", exc)
            raw = {}
        for key, table in (("warframes", warframes), ("weapons", weapons)):
            for english, chinese in (raw.get(key) or {}).items():
                name = _normalize(english)
                value = str(chinese or "").strip()
                if name and value:
                    table[name] = value
        _tables = (warframes, weapons)
    return _tables


def _lookup(name: str, table: Dict[str, str]) -> Optional[str]:
    key = _normalize(name)
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
    # `Vectis Prime` 在武器表里没有，但国服写法就是「圣装 + 基础名」
    if key.endswith(" prime"):
        base = _lookup(key[:-len(" prime")].strip(), table)
        if base:
            return "圣装" + base
    return None


def warframe(name: str) -> Optional[str]:
    """战甲中文名；不是战甲就返回 None。"""
    return _lookup(name, _load()[0])


def weapon(name: str) -> Optional[str]:
    """武器中文名；不是武器就返回 None。"""
    return _lookup(name, _load()[1])


def kind(name: str) -> str:
    """`战甲` / `武器` / 空字符串（认不出来）。"""
    if warframe(name):
        return "战甲"
    if weapon(name):
        return "武器"
    return ""


def known(name: str) -> bool:
    return bool(warframe(name) or weapon(name))


def chinese(name: str) -> str:
    """只要中文名；查不到就原样返回。"""
    text = str(name or "").strip()
    return warframe(text) or weapon(text) or text


def label(name: str, with_english: bool = True) -> str:
    """`圣枪弥撒（Mesa）`；查不到中文时返回英文原名。"""
    text = str(name or "").strip()
    if not text:
        return "—"
    chinese_name = warframe(text) or weapon(text)
    if not chinese_name:
        return text
    if not with_english or _normalize(chinese_name) == _normalize(text):
        return chinese_name
    return "%s（%s）" % (chinese_name, text)
