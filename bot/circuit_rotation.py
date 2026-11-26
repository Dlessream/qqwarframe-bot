"""无尽回廊轮换表：钢铁回廊灵化之源（9 组）与普通回廊战甲（11 周）。

官方 `EndlessXpSchedule` 只给「本周」这一条，完整轮换是固定顺序的循环表
（wiki: The Circuit § The Steel Path Circuit）。这里只存英文名，中文名交给
`names` 模块（战甲用国服译名，武器用市场 zh-hans 译名）。

本周是哪一组不写死，而是拿官方给的 `Choices` 去表里找命中的那一组 ——
DE 往池子里加武器时命中率仍然是 4/5 以上，照样对得上；真对不上就标
`degraded`，只显示本周，由上层提示轮换表需要更新。
"""

from __future__ import annotations

import calendar
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

WEEK = 7 * 86400.0

#: 钢铁回廊：9 组循环，每组 5 个灵化之源，玩家从里选 2 个（Tier 5 / Tier 10）
INCARNON_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("A", ("Braton", "Lato", "Skana", "Paris", "Kunai")),
    ("B", ("Boar", "Gammacor", "Angstrum", "Gorgon", "Anku")),
    ("C", ("Bo", "Latron", "Furis", "Furax", "Strun")),
    ("D", ("Lex", "Magistar", "Boltor", "Bronco", "Ceramic Dagger")),
    ("E", ("Torid", "Dual Toxocyst", "Dual Ichor", "Miter", "Atomos")),
    ("F", ("Ack & Brunt", "Soma", "Vasto", "Nami Solo", "Burston")),
    ("G", ("Zylok", "Sibear", "Dread", "Despair", "Hate")),
    ("H", ("Dera", "Sybaris", "Cestra", "Sicarus", "Okina")),
    ("I", ("Vectis", "Stug", "Ballistica", "Destreza", "Obex")),
)

#: 普通回廊：11 周循环，每周 3 个战甲，玩家从里选 1 个
WARFRAME_WEEKS: Tuple[Tuple[int, Tuple[str, ...]], ...] = (
    (1, ("Excalibur", "Trinity", "Ember")),
    (2, ("Loki", "Mag", "Rhino")),
    (3, ("Ash", "Frost", "Nyx")),
    (4, ("Saryn", "Vauban", "Nova")),
    (5, ("Nekros", "Valkyr", "Oberon")),
    (6, ("Hydroid", "Mirage", "Limbo")),
    (7, ("Mesa", "Chroma", "Atlas")),
    (8, ("Ivara", "Inaros", "Titania")),
    (9, ("Nidus", "Octavia", "Harrow")),
    (10, ("Gara", "Khora", "Revenant")),
    (11, ("Garuda", "Baruuk", "Hildryn")),
)

_CLEAN_RE = re.compile(r"[^a-z0-9&]")

#: 命中率下限：官方池子加一把新武器时 4/5 = 0.8 仍算命中，换成别的组就掉到 0
MATCH_FLOOR = 0.5


def _norm(value: Any) -> str:
    return _CLEAN_RE.sub("", str(value or "").lower())


def _match(choices: Optional[Sequence[str]],
           table: Sequence[Sequence[Any]]) -> Optional[int]:
    """拿本周官方给的名字反查是第几组；对不上返回 None。"""
    picked = {_norm(name) for name in (choices or []) if _norm(name)}
    if not picked:
        return None
    best: Optional[int] = None
    best_score = 0.0
    for index, entry in enumerate(table):
        pool = {_norm(name) for name in entry[-1]}
        common = len(picked & pool)
        if not common:
            continue
        score = common / max(len(picked), len(pool))
        if score > best_score:
            best, best_score = index, score
    return best if best_score >= MATCH_FLOOR else None


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("Z", "")
    if "." in text:
        text = text.split(".")[0]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return float(calendar.timegm(datetime.strptime(text, fmt).timetuple()))
        except ValueError:
            continue
    return 0.0


def week_start(now: Optional[float] = None) -> float:
    """本周刷新点：周一 00:00 UTC（与游戏内周常重置同一时刻）。"""
    if now is None:
        now = time.time()
    days = int(float(now) // 86400)
    # 1970-01-01 是周四，+3 天后 0 就是周一
    weekday = (days + 3) % 7
    return float((days - weekday) * 86400)


def _build(table: Sequence[Sequence[Any]], choices: Optional[Sequence[str]],
           start: float) -> Dict[str, Any]:
    """把「本周命中哪一组」展开成整张循环表，并标好每一组的起止时间。"""
    index = _match(choices, table)
    if index is None:
        cycles: List[Dict[str, Any]] = []
        if choices:
            # 对不上表就别硬猜第几组，只把本周官方给的名字给出去
            cycles.append({
                "key": "", "offset": 0, "start": start,
                "end": start + WEEK, "names": [str(x) for x in choices],
                "current": True,
            })
        return {"index": None, "key": "", "total": len(table),
                "degraded": True, "cycles": cycles}
    cycles = []
    for position, entry in enumerate(table):
        key, names = entry[0], entry[-1]
        offset = (position - index) % len(table)
        cycle_start = start + offset * WEEK
        cycles.append({
            "key": str(key),
            "offset": offset,
            "start": cycle_start,
            "end": cycle_start + WEEK,
            "names": list(names),
            "current": offset == 0,
        })
    return {"index": index, "key": str(table[index][0]), "total": len(table),
            "degraded": False, "cycles": cycles}


def rotation(raw: Any, now: Optional[float] = None) -> Dict[str, Any]:
    """回廊轮换总表。

    `raw` 是 worldstate 的 `duviriCycle`（官方 EndlessXpSchedule 转出来的
    choices 结构，只有本周）。返回：

        {"week_start": ..., "week_end": ...,
         "incarnon": {"key": "D", "total": 9, "degraded": False,
                      "cycles": [{"key", "offset", "start", "end",
                                  "names", "current"}, ...]},
         "warframe": {...}}

    `cycles` 按表内顺序（A→I / 第 1→11 周）排列，当前周那一条 `current=True`。
    """
    now = time.time() if now is None else float(now)
    raw = raw if isinstance(raw, dict) else {}
    start = _timestamp(raw.get("activation")) or week_start(now)
    end = _timestamp(raw.get("expiry"))
    if end <= start or end - start < 4 * 86400:
        end = start + WEEK

    choices: Dict[str, List[str]] = {}
    for group in raw.get("choices") or []:
        if not isinstance(group, dict):
            continue
        hard = str(group.get("category") or "").lower() != "normal"
        choices["hard" if hard else "normal"] = [
            str(name) for name in group.get("choices") or [] if str(name).strip()]

    return {
        "week_start": start,
        "week_end": end,
        "incarnon": _build(INCARNON_GROUPS, choices.get("hard"), start),
        "warframe": _build(WARFRAME_WEEKS, choices.get("normal"), start),
    }


def offset_label(offset: int) -> str:
    """相对本周的说法：本周 / 下周 / N 周后。"""
    if offset <= 0:
        return "本周"
    if offset == 1:
        return "下周"
    return "%d 周后" % offset
