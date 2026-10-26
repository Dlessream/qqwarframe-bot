"""把 API 返回的繁体中文文案转成简体。

warframestat 的 `zh` 语系是繁体（例如「防禦」「殲滅」），群里看简体更顺眼。
数据表 `t2s.json` 来自 OpenCC 的 TSCharacters（单字映射），只做逐字替换，
对已经是简体的文本是空操作。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "t2s.json")

_TABLE: Optional[Dict[int, str]] = None


def _table() -> Dict[int, str]:
    global _TABLE
    if _TABLE is None:
        try:
            with open(_PATH, "r", encoding="utf-8") as handle:
                pairs = json.load(handle)
            _TABLE = {ord(key): value for key, value in pairs.items()}
        except Exception:  # 缺表就原样返回
            _TABLE = {}
    return _TABLE


def convert(text: Any) -> str:
    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else ""
    return text.translate(_table())


def convert_obj(value: Any) -> Any:
    """递归转换 dict / list 里的字符串。"""
    if isinstance(value, str):
        return convert(value)
    if isinstance(value, list):
        return [convert_obj(item) for item in value]
    if isinstance(value, dict):
        return {key: convert_obj(item) for key, item in value.items()}
    return value
