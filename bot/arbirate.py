"""仲裁节点评级：社区梯队 → 人工评级 → 本地推算。

arbi.wf.wiki 的社区梯队只覆盖十几个热门节点，其余 70 多个一直是
「未评级」，所以这里给后端补一套自己能算的评级：

  1. 社区梯队（arbi.wf.wiki）——最权威，优先
  2. 人工评级（Web 控制台填写，存 data 目录的 arbi_ratings.json）
  3. 本地推算（任务类型 + 敌人等级，规则见 MISSION_SCORES）

本地推算不是瞎猜：仲裁的主要收益是 Vitus Essence（每 5 分钟一轮奖励），
所以「能不能稳定刷、怪够不够密、要不要来回跑」才是关键。可以挂机的
生存/防御天然优于要从头跑到尾的叛逃，这个排序和社区梯队基本一致。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

TIER_ORDER = ["S", "A+", "A", "A-", "B", "C"]

# 仲裁轮换里实际会出现任务类型（MT_ 开头，取自官方 worldState / arbi 节点表）
MISSION_SCORES: Dict[str, float] = {
    "MT_SURVIVAL": 5.0,        # 生存：挂机、怪密、稳定出 Vitus
    "MT_TERRITORY": 4.8,       # 拦截：四塔来回跑，但出怪不断，社区梯队里多是 S
    "MT_DEFENSE": 4.5,         # 防御：守一个点，怪够多
    "MT_EXCAVATE": 4.0,        # 挖掘：要跑图，但出怪稳定
    "MT_ARMAGEDDON": 3.5,      # 虚空决战：怪多，但要守结构体
    "MT_CORRUPTION": 3.5,      # 虚空洪流：跑圈，中等
    "MT_ARTIFACT": 3.0,        # 中断：机制繁琐，怪分散
    "MT_VOID_CASCADE": 3.0,    # 虚空覆涌
    "MT_ALCHEMY": 3.0,         # 元素转换：混合任务
    "MT_PURIFY": 2.0,          # 感染者资源回收：要反复搬运
    "MT_EVACUATION": 1.5,      # 叛逃：护送慢，最容易翻车
}
DEFAULT_SCORE = 2.5

# 敌人等级只是弱信号：仲裁本身会把等级拉高，这里用来区分「地图规模」
LEVEL_BONUS = ((40, 0.8), (25, 0.4), (15, 0.0))

TIER_THRESHOLDS = ((5.6, "S"), (5.0, "A+"), (4.4, "A"), (3.8, "A-"), (2.8, "B"))


def normalize_tier(text: str) -> str:
    """把用户输入（s、a+、a 减、清空）整理成梯队名。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw in ("清除", "删除", "无", "none", "-"):
        return ""
    upper = raw.upper().replace("－", "-").replace("−", "-")
    if upper in TIER_ORDER:
        return upper
    alias = {"A减": "A-", "A-1": "A-", "甲": "A", "乙": "B", "丙": "C"}
    return alias.get(upper, "")


def level_bonus(node) -> float:
    max_level = int(getattr(node, "max_level", 0) or 0)
    for threshold, bonus in LEVEL_BONUS:
        if max_level >= threshold:
            return bonus
    return 0.0


def score(node) -> float:
    mission = str(getattr(node, "mission_type", "") or "").upper()
    return MISSION_SCORES.get(mission, DEFAULT_SCORE) + level_bonus(node)


def local_rating(node) -> str:
    """按任务类型 + 等级推算梯队；节点缺失时返回空串。"""
    if node is None:
        return ""
    value = score(node)
    for threshold, tier in TIER_THRESHOLDS:
        if value >= threshold:
            return tier
    return "C"


def explain(node) -> str:
    """人话说明为什么给这个评级：生存 · 40-45 级。"""
    if node is None:
        return ""
    mission = str(getattr(node, "mission", "") or "")
    max_level = int(getattr(node, "max_level", 0) or 0)
    if max_level:
        return "%s · %d 级" % (mission or "未知任务", max_level)
    return mission or "未知任务"


def load_overrides(path: str) -> Dict[str, str]:
    """读人工评级：{nodeKey: 梯队}。文件损坏时当作空表，不影响主流程。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, str] = {}
    for key, value in raw.items():
        tier = normalize_tier(str(value))
        if tier:
            result[str(key)] = tier
    return result


def save_overrides(path: str, mapping: Dict[str, str]) -> None:
    """原子写回人工评级（先写临时文件再替换，避免半截文件）。"""
    cleaned = {}
    for key, value in (mapping or {}).items():
        tier = normalize_tier(str(value))
        if tier:
            cleaned[str(key)] = tier
    folder = os.path.dirname(os.path.abspath(path))
    if folder and not os.path.isdir(folder):
        os.makedirs(folder, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=folder or None,
        prefix=".arbi_ratings_", suffix=".json")
    try:
        json.dump(cleaned, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)


def summarize(nodes: List[Any], overrides: Dict[str, str],
              community: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """给控制台用的节点快照：社区 / 人工 / 本地 / 最终 四个值都给出。"""
    community = community or {}
    rows = []
    for node in nodes:
        key = str(getattr(node, "key", ""))
        local = local_rating(node)
        tier = community.get(key) or overrides.get(key) or local
        rows.append({
            "key": key,
            "name": str(getattr(node, "name", "") or ""),
            "system": str(getattr(node, "system", "") or ""),
            "mission": str(getattr(node, "mission", "") or ""),
            "faction": str(getattr(node, "faction", "") or ""),
            "levels": str(getattr(node, "level_text", "") or ""),
            "community": community.get(key, ""),
            "manual": overrides.get(key, ""),
            "local": local,
            "tier": tier,
            "source": ("community" if community.get(key)
                       else "manual" if overrides.get(key) else "local"),
        })
    rows.sort(key=lambda row: (TIER_ORDER.index(row["tier"])
                               if row["tier"] in TIER_ORDER else 99,
                               row["system"], row["name"]))
    return rows
