"""把 QQ 开放平台事件归一化成现有 OneBot 风格的事件字典。

这样 bot/app.py 的消息过滤、群策略、限流、命令分发、回复拼装全部不用改——
业务层继续只看到 post_type/message_type/user_id/group_id/message/raw_message。
额外保留 qq_* 字段，供回复时带 msg_id / msg_seq 做被动回复。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 官方事件名 -> 内部消息类型
MESSAGE_EVENTS = {
    "GROUP_AT_MESSAGE_CREATE": "group",
    "GROUP_MESSAGE_CREATE": "group",
    "C2C_MESSAGE_CREATE": "private",
    "DIRECT_MESSAGE_CREATE": "private",
    "AT_MESSAGE_CREATE": "group",  # 频道 @ 消息，channel_id 当作群 ID
}

_MENTION_RE = re.compile(r"<@([^>]+)>")


def _text_of(value: Any) -> str:
    return "" if value is None else str(value)


def parse_mentions(content: str, self_id: str, bot_id: str,
                   mention_list: Optional[List[Dict[str, Any]]] = None
                   ) -> Tuple[str, List[Dict[str, str]], bool]:
    """把 `<@openid>` 从正文里抽成 at 段，返回 (纯文本, at段列表, 是否@了本机器人)。"""
    ids = {str(self_id or ""), str(bot_id or "")}
    ids.discard("")
    for item in mention_list or []:
        if not isinstance(item, dict):
            continue
        if item.get("is_you") or item.get("bot"):
            for key in ("id", "member_openid", "user_openid"):
                value = _text_of(item.get(key))
                if value:
                    ids.add(value)
    ats: List[Dict[str, str]] = []
    at_me = False

    def replace(match: re.Match) -> str:
        nonlocal at_me
        target = match.group(1)
        if target == "all":
            return ""
        if target in ids:
            ats.append({"type": "at", "data": {"qq": self_id or bot_id}})
            at_me = True
            return ""
        return ""

    clean = _MENTION_RE.sub(replace, content or "")
    return clean, ats, at_me


def to_onebot_event(payload: Dict[str, Any], self_id: str = "",
                    bot_id: str = "") -> Optional[Dict[str, Any]]:
    """官方 payload -> 内部事件；非消息事件返回 None。"""
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("t") or "")
    kind = MESSAGE_EVENTS.get(name)
    if not kind:
        return None
    data = payload.get("d")
    if not isinstance(data, dict):
        return None

    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    bot_id = _text_of(bot_id or self_id)
    self_id = _text_of(self_id or bot_id)

    if kind == "group":
        group_id = (_text_of(data.get("group_openid"))
                    or _text_of(data.get("channel_id")))
    else:
        group_id = ""

    user_id = (_text_of(author.get("member_openid"))
               or _text_of(author.get("id"))
               or _text_of(data.get("user_openid"))
               or _text_of(data.get("author", {}).get("user_openid")
                           if isinstance(data.get("author"), dict) else ""))
    # 官方用户是 openid；内部仍放进 user_id 字段，_allow 的 key 只是字符串
    if not user_id:
        return None

    mentions = data.get("mentions")
    content = _text_of(data.get("content") or data.get("message"))
    text, ats, at_me = parse_mentions(
        content, self_id, bot_id, mentions if isinstance(mentions, list) else None)

    is_at_event = name == "GROUP_AT_MESSAGE_CREATE" or name == "AT_MESSAGE_CREATE"
    if is_at_event:
        # 官方已把 @机器人 前缀从 content 去掉：补一个 at 段让 respond_to_at 生效。
        ats.insert(0, {"type": "at", "data": {"qq": self_id or bot_id}})
    at_me = at_me or is_at_event

    segments: List[Dict[str, Any]] = list(ats)
    if text.strip():
        segments.append({"type": "text", "data": {"text": text}})
    for attachment in data.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        url = _text_of(attachment.get("url"))
        if url:
            segments.append({"type": "image", "data": {"url": url}})

    raw_parts = []
    for segment in segments:
        if segment["type"] == "text":
            raw_parts.append(_text_of(segment["data"].get("text")))
        elif segment["type"] == "at":
            raw_parts.append("[CQ:at,qq=%s]" % _text_of(
                segment["data"].get("qq")))
    raw_message = "".join(raw_parts)

    return {
        "post_type": "message",
        "message_type": kind,
        "sub_type": name.lower(),
        "self_id": self_id or bot_id,
        "user_id": user_id,
        "group_id": group_id,
        "message": segments,
        "raw_message": raw_message,
        # 官方特有字段：回复时用来带 msg_id / msg_seq
        "qq_event": name,
        "qq_msg_id": _text_of(data.get("id")),
        "qq_event_id": _text_of(payload.get("id")),
        "qq_msg_seq": data.get("msg_seq") or 1,
        "qq_at_me": bool(at_me),
        "qq_mentions": mentions if isinstance(mentions, list) else [],
    }
