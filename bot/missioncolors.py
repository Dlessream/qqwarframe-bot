"""任务类型显示色：仲裁卡片按「任务类型」上色，颜色可以随时改。

为什么要单独一个模块：
- 卡片里的任务类型（生存 / 防御 / 拦截 / 挖掘 …）以前只有一种颜色，
  看不出今天仲裁到底是哪种任务；
- 现在按任务类型上色，颜色表放在 `data/mission_colors.json`，
  控制台「任务配色」面板也能改，改完下一次出图立即生效（按文件 mtime 热加载）。

取值顺序：文件里改过的颜色 > 内置默认色 > 中性灰。
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

# 默认配色：同类任务同一个色系，尽量贴近数据源里任务标签的观感
DEFAULT_COLORS: Dict[str, str] = {
    # 仲裁轮换里会出现的任务类型（按出现频率排序）
    "防御": "#62BEDB",
    "生存": "#60CB8F",
    "拦截": "#A889E2",
    "挖掘": "#EBBC60",
    "中断": "#E89B5A",
    "叛逃": "#8B98AA",
    "镜像防御": "#8FD6EE",
    "感染者资源回收": "#B7C46A",
    "资源回收": "#B7C46A",
    "虚空洪流": "#5ECBB0",
    "虚空覆涌": "#7FD1C8",
    "虚空决战": "#E27070",
    "联结生存": "#8CE0AE",
    "元素转换": "#D9C86A",
    # 其他世界状态卡片里可能出现的任务
    "歼灭": "#E27070",
    "捕获": "#E89B5A",
    "破坏": "#EBBC60",
    "间谍": "#A889E2",
    "救援": "#60CB8F",
    "刺杀": "#E27070",
    "移动防御": "#6E9BF0",
    "劫持": "#8B98AA",
    "追击": "#62BEDB",
    "突袭": "#62BEDB",
    "强袭": "#EBBC60",
    "蜂巢": "#8B98AA",
    "圣殿突袭": "#A889E2",
    "精英圣殿突袭": "#A889E2",
    "奥菲克斯": "#6E9BF0",
    "遭遇战": "#62BEDB",
    "挥发": "#E89B5A",
    "开放地形": "#EBBC60",
    "赏金": "#EBBC60",
    "升天": "#5ECBB0",
    "虚空破坏": "#E27070",
    "虚空歼灭": "#E27070",
    "虚空生存": "#60CB8F",
    "虚空防御": "#62BEDB",
    "虚空拦截": "#A889E2",
    "虚空移动防御": "#6E9BF0",
}

# 显示顺序：仲裁里常见的排前面，控制台列表也按这个顺序
ORDER: Tuple[str, ...] = tuple(DEFAULT_COLORS)

FALLBACK = "#8B98AA"

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "data", "mission_colors.json")

_lock = threading.RLock()
_path = DEFAULT_PATH
_cache: Optional[Dict[str, str]] = None
_cache_stamp: float = -2.0


# ---------------------------------------------------------------------- 路径
def configure(path: str = "") -> None:
    """切换配色文件（默认 `data/mission_colors.json`）。"""
    global _path, _cache, _cache_stamp
    target = str(path or "").strip() or DEFAULT_PATH
    if not os.path.isabs(target):
        target = os.path.join(_ROOT, target)
    with _lock:
        if os.path.abspath(target) != os.path.abspath(_path):
            _path = os.path.abspath(target)
            _cache = None
            _cache_stamp = -2.0


def path() -> str:
    return _path


def normalize(color: str) -> str:
    """`60cb8f` / `#60CB8F` → `#60CB8F`；不合法返回空串。"""
    match = _HEX_RE.match(str(color or "").strip())
    if not match:
        return ""
    return "#" + match.group(1).upper()


def _strip_prefix(name: str) -> str:
    """`INFESTED 资源回收` → `资源回收`（老数据里的英文前缀）。"""
    text = str(name or "").strip()
    if text.upper().startswith("INFESTED "):
        text = text[len("INFESTED "):].strip()
    return text


# ---------------------------------------------------------------------- 读写
def _read_file() -> Dict[str, str]:
    try:
        with open(_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    colors = raw.get("colors", raw)
    if not isinstance(colors, dict):
        return {}
    result: Dict[str, str] = {}
    for name, value in colors.items():
        if str(name).startswith("_"):
            continue
        hex_color = normalize(str(value))
        if hex_color:
            result[str(name).strip()] = hex_color
    return result


def _load() -> Dict[str, str]:
    global _cache, _cache_stamp
    try:
        stamp = os.path.getmtime(_path)
    except OSError:
        stamp = -1.0
    with _lock:
        if stamp == _cache_stamp and _cache is not None:
            return _cache
    overrides = _read_file()
    merged: Dict[str, str] = dict(DEFAULT_COLORS)
    merged.update(overrides)
    with _lock:
        _cache = merged
        _cache_stamp = stamp
    return merged


def colors() -> Dict[str, str]:
    """生效中的完整配色表（默认 + 覆盖）。"""
    return dict(_load())


def mapping() -> Dict[str, str]:
    """`{任务名: "#RRGGBB"}`，给前端上色用。"""
    return colors()


def hex_of(mission: str) -> str:
    """任务类型 → 十六进制颜色；认不出返回中性灰。"""
    table = _load()
    name = str(mission or "").strip()
    if name in table:
        return table[name]
    bare = _strip_prefix(name)
    if bare in table:
        return table[bare]
    for key, value in table.items():
        if key and key in name:
            return value
    return FALLBACK


def rgb(mission: str) -> Tuple[int, int, int]:
    """任务类型 → RGB 三元组（卡片直接可用）。"""
    hex_color = hex_of(mission).lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16),
            int(hex_color[4:6], 16))


def is_custom(mission: str) -> bool:
    return str(mission or "").strip() in _read_file()


def entries() -> List[Dict[str, Any]]:
    """控制台列表：任务名、当前色、默认色、是否改过。"""
    current = _load()
    overrides = _read_file()
    names = list(ORDER)
    for name in list(current):
        if name not in names:
            names.append(name)
    rows = []
    for name in names:
        rows.append({
            "name": name,
            "color": current.get(name, FALLBACK),
            "default": DEFAULT_COLORS.get(name, FALLBACK),
            "custom": name in overrides,
        })
    return rows


def _write(mapping_colors: Dict[str, str]) -> None:
    payload = {
        "_说明": ("任务类型显示色（仲裁卡片按任务类型上色）。"
                  "可以直接改这个文件，也可以在本机控制台「任务配色」里改，"
                  "保存后下一次出图立即生效。"),
        "colors": {name: mapping_colors[name]
                   for name in sorted(mapping_colors)},
    }
    directory = os.path.dirname(_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    tmp = _path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, _path)
    global _cache_stamp
    with _lock:
        _cache_stamp = -2.0


def set_color(mission: str, color: str) -> Dict[str, Any]:
    """改一个任务的颜色；color 传空表示恢复默认。"""
    name = str(mission or "").strip()
    if not name:
        raise ValueError("缺少任务类型名称。")
    hex_color = normalize(color)
    table = dict(_load())
    if hex_color:
        table[name] = hex_color
    elif name in DEFAULT_COLORS:
        table[name] = DEFAULT_COLORS[name]     # 传空 = 恢复默认
    else:
        table.pop(name, None)
    _write(_overrides_of(table))
    return {
        "ok": True,
        "name": name,
        "color": hex_of(name),
        "text": "%s → %s" % (name, hex_of(name)),
    }


def _overrides_of(table: Dict[str, str]) -> Dict[str, str]:
    """只保留与默认色不同的项，落盘后仍与默认合并。"""
    return {key: value for key, value in table.items()
            if DEFAULT_COLORS.get(key, "").upper() != value.upper()}


def set_colors(items: Dict[str, str]) -> Dict[str, Any]:
    """批量改色：`{"生存": "#60CB8F", ...}`，值传空等于恢复默认。"""
    if not isinstance(items, dict) or not items:
        raise ValueError("没有要保存的颜色。")
    table = dict(_load())
    for name, color in items.items():
        key = str(name or "").strip()
        if not key:
            continue
        hex_color = normalize(color)
        if hex_color:
            table[key] = hex_color
        elif key in DEFAULT_COLORS:
            table[key] = DEFAULT_COLORS[key]
        else:
            table.pop(key, None)
    overrides = _overrides_of(table)
    _write(overrides)
    return {"ok": True, "count": len(overrides), "entries": entries()}


def reset() -> Dict[str, Any]:
    """全部恢复默认色。"""
    _write({})
    return {"ok": True, "count": 0, "entries": entries()}


def stats() -> Dict[str, Any]:
    overrides = _read_file()
    return {
        "file": _path,
        "custom": len(overrides),
        "total": len(DEFAULT_COLORS) + len([k for k in overrides
                                            if k not in DEFAULT_COLORS]),
    }
