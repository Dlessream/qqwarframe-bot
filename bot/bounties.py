"""赏金目标名：官方 jobType 路径 → 中文目标（data/bounty_objectives.json）。

官方 worldState 的 SyndicateMissions[].Jobs[].jobType 只给资源路径，
例如 /Lotus/Types/Gameplay/Eidolon/Jobs/AttritionBountyExt，
以前只能靠关键字猜出「歼灭」这种任务类型，目标名（宰杀敌人）就丢了。
这里按完整路径查表，把「歼灭 · 宰杀敌人」补齐。

表由 tools/build_bounty_dict.py 从 WFCD 的 languages.json 生成，
键是去掉 /Lotus/Types/Gameplay/ 前缀的小写路径。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict

LOGGER = logging.getLogger("qqwarframe.bounties")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_FILE = os.path.join(ROOT, "data", "bounty_objectives.json")

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, str]] = {}
_loaded_at = -1.0


def key_of(job_type: Any) -> str:
    """/Lotus/Types/Gameplay/Eidolon/Jobs/Xxx → eidolon/jobs/xxx"""
    text = str(job_type or "").strip().lower()
    marker = "/types/gameplay/"
    if marker in text:
        text = text.split(marker, 1)[1]
    return text.lstrip("/")


def _table(path: str = DEFAULT_FILE) -> Dict[str, Dict[str, str]]:
    global _loaded_at
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        stamp = -1.0
    with _lock:
        if stamp == _loaded_at:
            return _jobs
    table: Dict[str, Dict[str, str]] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        for name, row in (raw.get("jobs") or {}).items():
            if isinstance(row, dict):
                table[str(name).lower()] = {
                    "en": str(row.get("en") or ""),
                    "zh": str(row.get("zh") or row.get("en") or ""),
                }
    except (OSError, ValueError) as exc:
        LOGGER.warning("读取赏金目标表失败（%s）：%s", path, exc)
    with _lock:
        _jobs.clear()
        _jobs.update(table)
        _loaded_at = stamp
    return _jobs


def lookup(job_type: Any, path: str = DEFAULT_FILE) -> Dict[str, str]:
    row = _table(path).get(key_of(job_type))
    return row or {}


def _norm(text: Any) -> str:
    """英文目标名归一：小写、去多余空格、全角括号转半角。"""
    value = str(text or "").strip().lower()
    value = value.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", value)


def objective_zh(job_type: Any, path: str = DEFAULT_FILE) -> str:
    return lookup(job_type, path).get("zh", "")


def objective_by_en(text: Any, path: str = DEFAULT_FILE) -> str:
    """官方 job 只给英文目标名（如 Capture the Grineer Agent）时反查中文。"""
    wanted = _norm(text)
    if not wanted:
        return ""
    for row in _table(path).values():
        if _norm(row.get("en")) == wanted:
            return str(row.get("zh") or "")
    return ""


def objective_en(job_type: Any, path: str = DEFAULT_FILE) -> str:
    return lookup(job_type, path).get("en", "")


def label(job_type: Any, kind: str = "", path: str = DEFAULT_FILE) -> str:
    """「歼灭 · 宰杀敌人」；查不到目标名时只给任务类型。"""
    original = str(kind or "").strip()
    objective = objective_zh(job_type, path)
    if not objective:
        return original
    kind = "" if original in ("赏金", "任务") else original
    if not kind or objective == kind or kind in objective:
        # 类型没识别出来、或目标名里本来就带着类型时，别重复
        return objective
    return "%s · %s" % (kind, objective)
