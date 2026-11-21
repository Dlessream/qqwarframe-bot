"""希图斯赏金奖励池 A/B/C 轮换。

游戏内每 150 分钟换一次奖励池，A→B→C→A；三个平原据点（希图斯 /
福尔图娜 / 火卫二）共用同一个池字母。池字母不在 Wiki 的固定 UTC 时间表里
（Wiki 自己注明仅供参考，实际赏金到期时间有偏移），**唯一可靠来源是官方
世界状态 job 的奖励表名**：

    /Lotus/Types/Game/MissionDecks/EidolonJobMissionRewards/TierATableARewards
                                                              ^^^^^^^^
warframestat 把该路径放在 job.uniqueName；官方兜底源在 job.rewards，
由 worldfallback._bounties 统一映射成 uniqueName 后读取。

池内奖励来自 data/bounty_rotations.json（WFCD 官方掉落表整理）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from .datafiles import resolve as resolve_data_file

LOGGER = logging.getLogger("qqwarframe.bountyrotation")

DEFAULT_FILE = resolve_data_file(os.path.join("data", "bounty_rotations.json"))
POOLS = ("A", "B", "C")
PERIOD_SECONDS = 150 * 60

_TABLE_RE = re.compile(r"Table([ABC])Rewards", re.IGNORECASE)

# 各池在低档（5-15）的标志掉落，用于接口没给奖励表名时反推当前池
_SIGNATURES = {
    "A": ("redirection", "oxium", "iradite"),
    "B": ("pressure point", "cryotic", "grokdrul"),
    "C": ("vitality", "plastids", "nistlepod"),
}

_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_loaded_at = -1.0


def _table(path: str = DEFAULT_FILE) -> Dict[str, Any]:
    global _cache, _loaded_at
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = -1.0
    with _lock:
        if _cache is not None and stamp == _loaded_at:
            return _cache
    data: Dict[str, Any] = {"levels": {}, "order": list(POOLS)}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            data = {
                "levels": raw.get("levels") if isinstance(raw.get("levels"), dict) else {},
                "order": [str(item) for item in (raw.get("order") or POOLS)],
            }
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取赏金奖励池表失败（%s）：%s", path, exc)
    with _lock:
        _cache = data
        _loaded_at = stamp
    return data


def level_key(levels) -> str:
    """[5, 15] -> '5-15'；[100, 100] -> '100-100'。"""
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


def pool_rewards(level, pool: str) -> List[str]:
    """某档位某池的奖励列表（中文）。"""
    row = (_table().get("levels") or {}).get(str(level)) or {}
    return list(row.get(str(pool).upper()) or [])


def signature_rewards(pool: str, levels=None, limit: int = 6) -> List[str]:
    """跨档位挑该池的代表奖励：只保留本池有、另外两池没有的物品。"""
    pool = str(pool or "").upper()
    if pool not in POOLS:
        return []
    levels_table = _table().get("levels") or {}
    names = [str(level) for level in (levels or levels_table.keys())]
    target = set()
    others = set()
    for level in names:
        row = levels_table.get(level) or {}
        target.update(str(item) for item in (row.get(pool) or []))
        for other in POOLS:
            if other != pool:
                others.update(str(item) for item in (row.get(other) or []))
    unique = [item for item in (levels_table.get(names[0], {}).get(pool) or [])
              if item not in others] if names else []
    unique += [item for item in sorted(target - others) if item not in unique]
    return unique[:limit]


def next_pool(pool: str, steps: int = 1) -> str:
    """A→B→C→A；steps 为轮数。"""
    pool = str(pool or "").upper()
    if pool not in POOLS:
        return ""
    return POOLS[(POOLS.index(pool) + int(steps)) % len(POOLS)]


def pool_of_job(job: Any) -> str:
    """从单个 job 读池字母：优先 uniqueName，其次 rewards 路径。"""
    if not isinstance(job, dict):
        return ""
    for key in ("uniqueName", "rewards", "rewardPath"):
        match = _TABLE_RE.search(str(job.get(key) or ""))
        if match:
            return match.group(1).upper()
    return ""


def pool_of_drops(job: Any) -> str:
    """从当前掉落反推池字母（接口没给奖励表名时用）。"""
    if not isinstance(job, dict):
        return ""
    drops = job.get("rewardPoolDrops")
    if not isinstance(drops, list):
        drops = job.get("rewardPool") or []
    names = set()
    for drop in drops:
        if isinstance(drop, dict):
            names.add(str(drop.get("item") or "").lower())
        else:
            names.add(str(drop).lower())
    best = ""
    best_hits = 0
    for pool, marks in _SIGNATURES.items():
        hits = sum(1 for mark in marks if any(mark in name for name in names))
        if hits > best_hits:
            best, best_hits = pool, hits
    return best if best_hits >= 2 else ""


def current_pool(syndicate: Any) -> str:
    """从 Ostrons 等据点数据中读当前奖励池字母，读不到返回空串。"""
    if not isinstance(syndicate, dict):
        return ""
    for key in ("rewardPoolRotation", "rotation", "tableRotation"):
        value = str(syndicate.get(key) or "").strip().upper()
        if value in POOLS:
            return value
    jobs = syndicate.get("jobs") or []
    if isinstance(jobs, list):
        for job in jobs:
            pool = pool_of_job(job)
            if pool:
                return pool
        for job in jobs:
            pool = pool_of_drops(job)
            if pool:
                return pool
    return ""


def rotation_left(syndicate: Any, now: Optional[float] = None) -> str:
    """当前池剩余时间文案，如「剩 43 分」。"""
    expiry = str((syndicate or {}).get("expiry") or "") if isinstance(syndicate, dict) else ""
    if not expiry:
        return ""
    from .worldstate import countdown, parse_timestamp  # 避免循环导入
    end = parse_timestamp(expiry)
    if not end:
        return ""
    seconds = end - (time.time() if now is None else now)
    if seconds <= 0:
        return ""
    return countdown(end)
