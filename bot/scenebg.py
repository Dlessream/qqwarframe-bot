"""推送卡片的游戏内任务场景背景。

图片渲染工具生成的 Warframe 风格任务场景，已去除生成水印，
随包放在 bot/assets/scenes/。任何读取失败都返回 None，上层降级为渐变底。
"""

from __future__ import annotations

import os
from typing import Dict, Optional

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    PIL_AVAILABLE = False

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "scenes")
_CACHE: Dict[str, object] = {}

# 场景名 -> 文件。名称与 present 层调用保持一致。
SCENES = ("fissure", "railjack", "arbitration", "cetus", "eidolon")


def scene_image(name: str) -> Optional[object]:
    """返回指定场景的 PIL.Image（已缩放），没有则 None。"""
    if not PIL_AVAILABLE or not name:
        return None
    name = str(name)
    if name in _CACHE:
        return _CACHE[name]  # type: ignore[return-value]
    path = os.path.join(ROOT, name + ".jpg")
    if not os.path.isfile(path):
        return None
    try:
        image = Image.open(path).convert("RGB")
        image.thumbnail((1000, 700), Image.LANCZOS)
    except Exception:
        return None
    _CACHE[name] = image
    return image
