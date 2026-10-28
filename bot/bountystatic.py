"""官方接口没给的赏金据点：扎里曼 / 解剖圣所 / 霍瓦尼亚（data/bounty_static.json）。

官方 worldState 的 SyndicateMissions 里，这三处只有周期（Expiry/Activation），
`Jobs` 恒为空，所以 #赏金 以前只能显示「暂无可接赏金」。实际上这三处的赏金档位
是固定的（只有任务类型随批次轮换，档位与等级不变），按 Wiki 的 Mission Bounties
表补一份静态档位，让榜单能给出「几档、多少级、给什么」。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("qqwarframe.bountystatic")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(ROOT, "data", "bounty_static.json")

_lock = threading.Lock()
_cache: Dict[str, Any] = {}
_loaded_at = -1.0


def _table(path: str = DEFAULT_FILE) -> Dict[str, Any]:
    global _loaded_at
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = -1.0
    with _lock:
        if stamp == _loaded_at:
            return _cache
    data: Dict[str, Any] = {"locations": {}, "refresh_hours": 2.5,
                            "note": "", "source": ""}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        locations: Dict[str, Dict[str, Any]] = {}
        for key, row in (raw.get("locations") or {}).items():
            if not isinstance(row, dict):
                continue
            tiers = []
            for item in row.get("tiers") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    tier = int(item.get("tier") or 0)
                except (TypeError, ValueError):
                    continue
                if not tier:
                    continue
                tiers.append({
                    "tier": tier,
                    "level": str(item.get("level") or "—"),
                    "reward": str(item.get("reward") or "—"),
                })
            tiers.sort(key=lambda item: item["tier"])
            locations[str(key)] = {
                "label": str(row.get("label") or key),
                "npc": str(row.get("npc") or ""),
                "missions": [str(name) for name in (row.get("missions") or [])],
                "tiers": tiers,
            }
        data = {
            "locations": locations,
            "refresh_hours": float(raw.get("refresh_hours") or 2.5),
            "note": str(raw.get("note") or ""),
            "source": str(raw.get("source") or ""),
        }
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取固定赏金档位表失败（%s）：%s", path, exc)
    with _lock:
        _cache.clear()
        _cache.update(data)
        _loaded_at = stamp
    return _cache


def _entry(key: str) -> Optional[Dict[str, Any]]:
    return (_table().get("locations") or {}).get(str(key or ""))


def tiers(key: str) -> List[Dict[str, Any]]:
    """某据点的固定档位；没有静态数据返回空列表。"""
    entry = _entry(key)
    return list(entry["tiers"]) if entry else []


def missions(key: str) -> List[str]:
    entry = _entry(key)
    return list(entry["missions"]) if entry else []


def label(key: str) -> str:
    entry = _entry(key)
    return entry["label"] if entry else ""


def npc(key: str) -> str:
    entry = _entry(key)
    return entry["npc"] if entry else ""


def keys() -> List[str]:
    return list((_table().get("locations") or {}).keys())


def refresh_hours() -> float:
    return float(_table().get("refresh_hours") or 2.5)


def note() -> str:
    return str(_table().get("note") or "")
