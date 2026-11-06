"""MOD 中文译名：读取中文 Wiki 的完整页面标题快照。

优先使用 `data/mod_names_zh_full.json`（MediaWiki API redirects，1336 条），
没有时退回 `data/mod_names_zh.json`（旧 Data:DictCY 快照）；运行时只读本地文件。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional

LOGGER = logging.getLogger("qqwarframe.mod_names_zh")

_LOCK = threading.Lock()
_DATA: Optional[Dict[str, Dict[str, str]]] = None


def _path() -> str:
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data")
    # 优先使用完整 Wiki 抓取结果；没有时退回内置快照。
    for name in ("mod_names_zh_full.json", "mod_names_zh.json"):
        path = os.path.join(root, name)
        if os.path.isfile(path):
            return path
    return os.path.join(root, "mod_names_zh.json")


def _normalize(text: str) -> str:
    return " ".join(str(text or "").strip().casefold().split())


def _load() -> Dict[str, Dict[str, str]]:
    global _DATA
    if _DATA is not None:
        return _DATA
    with _LOCK:
        if _DATA is not None:
            return _DATA
        try:
            with open(_path(), encoding="utf-8") as handle:
                raw = json.load(handle)
            items = raw.get("mods") if isinstance(raw, dict) else {}
        except (OSError, ValueError) as exc:
            LOGGER.warning("读取 MOD 中文译名失败：%s", exc)
            items = {}
        _DATA = {_normalize(key): value for key, value in (items or {}).items()
                 if isinstance(value, dict)}
        return _DATA


def lookup(name_en: str) -> Optional[Dict[str, str]]:
    """英文名 → {name: Wiki 中文名, legacy: 国服旧名}。"""
    key = _normalize(name_en)
    if not key:
        return None
    return _load().get(key)


def chinese(name_en: str) -> Optional[str]:
    entry = lookup(name_en)
    return entry.get("name") if entry else None
