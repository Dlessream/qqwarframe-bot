"""赏金奖励池：data/bounty_rewards.json（官方掉落表整理，见 tools/build_bounty_rewards.py）。

官方 worldState 的 Jobs 只给「任务 / 等级 / 到期」，没有掉落明细；
warframestat 的 rewardPoolDrops 又只有英文物品名且线上常被 403。
所以把官方掉落表按「据点 × 档位」整理成静态文件：档位不变，奖励池也就不会变。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

LOGGER = logging.getLogger("qqwarframe.bountyrewards")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(ROOT, "data", "bounty_rewards.json")

_lock = threading.Lock()
_cache: Optional[Dict[str, Dict[str, List[str]]]] = None
_loaded_at = -1.0


def _table(path: str = DEFAULT_FILE) -> Dict[str, Dict[str, List[str]]]:
    global _cache, _loaded_at
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = -1.0
    with _lock:
        if _cache is not None and stamp == _loaded_at:
            return _cache
    data: Dict[str, Dict[str, List[str]]] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            for syndicate, table in raw.items():
                if not isinstance(table, dict):
                    continue
                rows: Dict[str, List[str]] = {}
                for key, items in table.items():
                    if isinstance(items, list):
                        rows[str(key)] = [str(item) for item in items if str(item)]
                data[str(syndicate)] = rows
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取赏金奖励表失败（%s）：%s", path, exc)
    with _lock:
        _cache = data
        _loaded_at = stamp
    return data


def level_key(levels) -> str:
    """[5, 15] -> "5-15"；[100, 100] -> "100-100"。"""
    values = []
    for value in levels or []:
        try:
            values.append(int(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return ""
    if len(values) == 1:
        return "%d-%d" % (values[0], values[0])
    return "%d-%d" % (values[0], values[-1])


def for_level(syndicate: str, levels, vault: bool = False) -> List[str]:
    """官方 Job 的 enemyLevels → 该档奖励列表；vault=True 查隔离宝库那一套。"""
    table = _table().get(str(syndicate or "")) or {}
    key = level_key(levels)
    if vault:
        return list(table.get("vault-" + key) or table.get(key) or [])
    return list(table.get(key) or [])


def for_tier(syndicate: str, tier: int) -> List[str]:
    """固定档位据点（扎里曼 / 圣所 / 霍瓦尼亚）的第 tier 档奖励。"""
    return list((_table().get(str(syndicate or "")) or {}).get(str(int(tier))) or [])


def keys() -> List[str]:
    return list(_table().keys())
