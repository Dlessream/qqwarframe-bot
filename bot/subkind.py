"""可订阅事件的目录：群里的订阅图、控制台后端、命令解析共用这一份数据。

改这里等于同时改「#订阅 出图」和「控制台订阅面板」，不会再出现两处不一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# kind -> (中文名, 别名...)
KINDS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "fissure": ("裂缝", ("裂缝", "裂隙")),
    "storm": ("虚空风暴", ("风暴", "虚空风暴", "九重天", "storm")),
    "arbitration": ("仲裁", ("仲裁",)),
    "sortie": ("突击", ("突击",)),
    "circuit": ("回廊", ("回廊", "灵化", "无尽回廊")),
    "rotation": ("轮换", ("轮换", "商店", "言录使", "格拉斯", "埃莉诺")),
    "alert": ("警报", ("警报",)),
    "invasion": ("入侵", ("入侵",)),
    "deal": ("特惠", ("特惠", "折扣", "每日特惠")),
}

KIND_ORDER: Tuple[str, ...] = (
    "fissure", "storm", "arbitration", "sortie", "circuit",
    "rotation", "alert", "invasion", "deal",
)

# 每类推什么、能按什么筛、订阅命令的示例
DETAILS: Dict[str, Dict[str, str]] = {
    "fissure": {
        "desc": "新裂缝：任务 / 星球 / 等级 / 剩余时间",
        "filters": "任务、节点、遗物、敌人",
        "sample": "裂缝 防御",
    },
    "storm": {
        "desc": "新风暴：星域 / 节点 / 任务 / 剩余时间",
        "filters": "任务、节点、星域",
        "sample": "九重天 冥王",
    },
    "arbitration": {
        "desc": "仲裁节点 + 任务 + 评级（每小时换）",
        "filters": "不限",
        "sample": "仲裁",
    },
    "sortie": {
        "desc": "每日突击三关 + 奖励",
        "filters": "不限",
        "sample": "突击",
    },
    "circuit": {
        "desc": "本周回廊灵化战甲 / 武器",
        "filters": "不限",
        "sample": "回廊",
    },
    "rotation": {
        "desc": "言录使 / 格拉斯 / 埃莉诺 换批",
        "filters": "商店名",
        "sample": "轮换 言录使",
    },
    "alert": {
        "desc": "新警报 + 奖励 + 剩余时间",
        "filters": "奖励、节点",
        "sample": "警报 土豆",
    },
    "invasion": {
        "desc": "新入侵 + 阵营进度",
        "filters": "阵营",
        "sample": "入侵",
    },
    "deal": {
        "desc": "每日特惠折扣",
        "filters": "不限",
        "sample": "特惠",
    },
}

PUSH_FORM = "图片"


def kind_label(kind: str) -> str:
    return KINDS.get(kind, (kind, ()))[0]


def kind_names() -> str:
    return " / ".join(kind_label(kind) for kind in KIND_ORDER)


def kind_aliases(kind: str) -> Tuple[str, ...]:
    """中文名 + 所有别名，长的排前面（解析时优先匹配长的）。"""
    label, aliases = KINDS.get(kind, (kind, ()))
    return tuple(sorted({label, *aliases}, key=len, reverse=True))


def catalog() -> List[Dict[str, Any]]:
    """完整目录，给控制台接口和订阅图共用。"""
    rows: List[Dict[str, Any]] = []
    for kind in KIND_ORDER:
        info = DETAILS.get(kind, {})
        label = kind_label(kind)
        rows.append({
            "kind": kind,
            "label": label,
            "aliases": list(kind_aliases(kind)),
            "command": label,
            "desc": info.get("desc", ""),
            "filters": info.get("filters", "不限"),
            "sample": info.get("sample", label),
            "push": PUSH_FORM,
            "filterable": info.get("filters", "不限") != "不限",
        })
    return rows


def is_kind(value: str) -> bool:
    text = (value or "").strip().lower()
    if text in KINDS:
        return True
    return any(text in {alias.lower() for alias in kind_aliases(kind)}
               for kind in KIND_ORDER)


def resolve_kind(value: str) -> str:
    """把「裂缝 / 裂隙 / fissure」都归一成 kind；认不出返回空串。"""
    text = (value or "").strip().lower()
    if text in KINDS:
        return text
    for kind in KIND_ORDER:
        if text in {alias.lower() for alias in kind_aliases(kind)}:
            return kind
    return ""
