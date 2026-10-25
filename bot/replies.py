"""命令回复：文字 + 可选的图片生成器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Reply:
    text: str = ""
    image: Optional[Callable[[], bytes]] = field(default=None)

    @property
    def has_image(self) -> bool:
        return self.image is not None


def text(content: str) -> Reply:
    return Reply(text=content)


def both(content: str, generator: Optional[Callable[[], bytes]]) -> Reply:
    return Reply(text=content, image=generator)
