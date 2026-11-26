"""QQ 官方机器人消息按钮（InlineKeyboard）构造工具。

按钮点击后平台推 INTERACTION_CREATE，button_data 由 app 层解析执行。
所有 data 都以短前缀开头：a:命令 / wm:trend:token / wm:desc:token。
"""

from __future__ import annotations

from typing import Any, Dict, Sequence


def button(button_id: str, label: str, data: str,
           style: int = 1) -> Dict[str, Any]:
    return {
        "id": button_id,
        # visited_label 在旧版文档里是必填项，留空则点击后文字不变。
        "render_data": {"label": label, "visited_label": label, "style": style},
        "action": {
            "type": 1,  # 回调按钮：data 回传到机器人后台
            "permission": {"type": 2},  # 所有人可点
            "data": data,
            "unsupport_tips": "请升级 QQ 后重试",
        },
    }


def keyboard(rows: Sequence[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    return {"content": {"rows": [{"buttons": list(row)} for row in rows]}}


def arbitration_keyboard() -> Dict[str, Any]:
    """仲裁卡片底部按钮：其余仲裁功能一键切换并反馈。"""
    return keyboard([
        [
            button("arb-ceres", "谷拦仲裁", "a:谷拦仲裁", 1),
            button("arb-mirror", "镜防仲裁", "a:镜防仲裁", 1),
            button("arb-effective", "高效仲裁", "a:高效仲裁", 1),
        ],
        [
            button("arb-today", "今日仲裁", "a:今日仲裁", 0),
            button("arb-week", "仲裁表", "a:仲裁表", 0),
            button("arb-tiers", "仲裁评级", "a:仲裁评级", 0),
        ],
    ])


def wm_keyboard(token: str) -> Dict[str, Any]:
    """wm 卡片底部按钮：行情（近 15 天天梯图）与对话文本（私聊话术）。"""
    return keyboard([
        [
            button("wm-trend", "行情", "wm:trend:%s" % token, 1),
            button("wm-chat", "对话文本", "wm:chat:%s" % token, 1),
        ],
    ])


def circuit_keyboard(back: bool = False) -> Dict[str, Any]:
    """回廊卡片底部按钮：主卡给「灵化列表」，轮换表上给「本周回廊」回退。"""
    if back:
        return keyboard([[button("circuit-now", "本周回廊", "a:回廊", 0)]])
    return keyboard([[button("circuit-list", "灵化列表", "a:灵化列表", 1)]])
