"""命令回复：文字 + 可选的图片生成器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass
class Reply:
    text: str = ""
    image: Optional[Callable[[], bytes]] = field(default=None)
    # QQ 官方消息按钮（InlineKeyboard）；None 表示不挂载按钮
    keyboard: Optional[Dict[str, Any]] = field(default=None)

    @property
    def has_image(self) -> bool:
        return self.image is not None


def text(content: str) -> Reply:
    return Reply(text=content)


def both(content: str, generator: Optional[Callable[[], bytes]]) -> Reply:
    return Reply(text=content, image=generator)
