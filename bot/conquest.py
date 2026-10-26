"""科研（Archimedea / 深层科研·时光科研）词条的中文名与官方说明。

数据文件 ``conquest_zh.json`` 由 ``tools/build_conquest_dict.py`` 从
oracle.browse.wf 的官方语言字典生成，内容是游戏内文案（名称 + 说明）。

官方 worldState 只给内部代号（例如 ``HostileSecurity``），而内部代号与
游戏里显示的名字是**错位**的：``HostileSecurity`` 实际是「侵蚀感官」，
``FortifiedFoes`` 实际是「密闭装甲」。所以这里一律按内部的 key 查表，
不要按字面翻译。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "conquest_zh.json")

# worldState 的 archimedeas[].typeKey → 数据文件里的分类
KIND_BY_TYPE = {"C T_ L A B": "LabConquest", "C T_ H E X": "HexConquest"}
# 命令里常用的短标签
LABEL_BY_KIND = {"LabConquest": "深层科研", "HexConquest": "时光科研"}

_CACHE: Optional[Dict[str, Any]] = None


def data() -> Dict[str, Any]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as handle:
            _CACHE = json.load(handle)
    except Exception:  # 数据文件缺失时保持可用，只是没有中文说明
        _CACHE = {"variants": {}, "risks": {}, "personal": {},
                  "missionTypes": {}, "englishIndex": {}}
    return _CACHE


def kind_of(type_key: str) -> str:
    return KIND_BY_TYPE.get(str(type_key or "").strip(), "LabConquest")


def label_of(type_key: str) -> str:
    return LABEL_BY_KIND.get(kind_of(type_key), "科研")


def _entry(group: str, key: str, kind: str = "") -> Optional[Dict[str, str]]:
    if not key:
        return None
    bucket = data().get(group) or {}
    if group == "variants":
        bucket = bucket.get(kind or "LabConquest") or {}
    entry = bucket.get(str(key).strip())
    if isinstance(entry, dict) and entry.get("name"):
        return entry
    return None


def variant(key: str, kind: str = "LabConquest") -> Optional[Dict[str, str]]:
    return _entry("variants", key, kind)


def risk(key: str) -> Optional[Dict[str, str]]:
    return _entry("risks", key)


def personal(key: str) -> Optional[Dict[str, str]]:
    return _entry("personal", key)


def by_english(name: str) -> str:
    """英文 UI 名 → 中文名（上游只给了名字时兜底）。"""
    if not name:
        return ""
    index = data().get("englishIndex") or {}
    return index.get(str(name).strip().lower(), "")


def mission_type(key: str) -> str:
    """DualDefense → 镜像防御。"""
    if not key:
        return ""
    types = data().get("missionTypes") or {}
    if key in types:
        return types[key]
    spaced = str(key).replace("_", " ")
    return types.get(spaced, "")


def describe(group: str, key: str = "", kind: str = "",
             english: str = "") -> Dict[str, str]:
    """按 key 取名称+说明；key 查不到时退回英文名反查。

    返回 {"name": 中文名, "desc": 说明}，查不到时 name 为空字符串。
    """
    lookup = {"variants": variant, "risks": risk, "personal": personal}.get(group)
    entry = None
    if lookup is not None and key:
        entry = lookup(key, kind) if group == "variants" else lookup(key)
    if entry is None and english:
        name = by_english(english)
        if name:
            entry = {"name": name, "desc": ""}
    return {"name": (entry or {}).get("name", ""),
            "desc": (entry or {}).get("desc", "")}
