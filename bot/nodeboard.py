"""节点背景板风格的推送卡片。

每个节点一条「背景板」：游戏内任务场景铺底 + 该节点所属派系的透明徽记
（居中）+ 任务类型 / 遗物等级 / 节点名 / 派系+等级 / 剩余时间，全部居中对齐。

设计来源：用户提供的游戏内节点界面截图。徽记严格使用游戏原图/等比例重绘，
不做艺术改动；奥罗金徽记由游戏截图抠出（bot/assets/factions/orokin.png）。

Pillow 不可用时返回 None，由上层降级为原表格卡。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence

try:
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    PIL_AVAILABLE = False

from . import scenebg

WIDTH = 860
BOARD_H = 400          # 单节点背景板高度
MAX_BOARDS = 4         # 一次推送最多堆叠的节点数（避免图片过高）
CX = WIDTH // 2

_FAC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "assets", "factions")
_EMBLEM_CACHE: Dict[str, object] = {}

# 派系名（中文/英文/缩写）→ 徽记文件名
_FACTION_MAP = {
    "orokin": "orokin", "奥罗金": "orokin", "corrupted": "orokin",
    "堕落者": "orokin", "o系": "orokin",
    "grineer": "grineer", "克隆尼": "grineer", "g系": "grineer",
    "corpus": "corpus", "科普斯": "corpus", "科波斯": "corpus", "c系": "corpus",
    "infested": "infested", "infection": "infested", "感染者": "infested", "i系": "infested",
    "sentient": "sentient", "s系": "sentient",
}


def _font(size: int, bold: bool = False):
    """复用 imagecard 的字体解析（含 fc-match 与缓存）。"""
    from . import imagecard
    return imagecard._font(size, bold=bold)


def emblem_key(faction: str) -> str:
    """把派系文本归一化为徽记 key；未知派系回退到奥罗金。"""
    raw = str(faction or "").strip().lower()
    return _FACTION_MAP.get(raw, "orokin")


def _emblem(key: str):
    key = emblem_key(key)
    if key in _EMBLEM_CACHE:
        return _EMBLEM_CACHE[key]  # type: ignore[return-value]
    if not PIL_AVAILABLE:
        return None
    path = os.path.join(_FAC_ROOT, key + ".png")
    if not os.path.isfile(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
        img.thumbnail((240, 240), Image.LANCZOS)
    except Exception:
        return None
    _EMBLEM_CACHE[key] = img
    return img


def _center_text(draw, text, cy, fnt, fill) -> None:
    """按 textbbox 左 bearing 修正后，居中绘制到 (WIDTH/2, cy)。"""
    b = draw.textbbox((0, 0), text, font=fnt)
    w, h = b[2] - b[0], b[3] - b[1]
    x = CX - w // 2 - b[0]
    y = cy - h // 2 - b[1]
    draw.text((x, y), text, font=fnt, fill=fill)


def _board_canvas(scene: str, height: int):
    """场景铺底 + 上浅下深蒙版；无场景时降级为深色渐变。"""
    from . import imagecard
    bg = scenebg.scene_image(scene) if scene else None
    if bg is None:
        return imagecard._gradient(WIDTH, height, imagecard.BG_TOP,
                                   imagecard.BG_BOTTOM).convert("RGBA")
    bw, bh = bg.size
    scale = max(WIDTH / float(bw), height / float(bh))
    nw, nh = max(WIDTH, int(bw * scale)), max(height, int(bh * scale))
    scaled = bg.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    left = (nw - WIDTH) // 2
    top = (nh - height) // 2
    image = scaled.crop((left, top, left + WIDTH, top + height))
    veil = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil, "RGBA")
    for y in range(height):
        a = int(112 + 72 * (y / max(1, height - 1)))
        vd.line([(0, y), (WIDTH, y)], fill=(6, 10, 17, a))
    return Image.alpha_composite(image, veil)


def _draw_board(canvas, top: int, entry: dict) -> None:
    """在 canvas 的 [top, top+BOARD_H) 区域画一块节点背景板。"""
    h = BOARD_H
    # 徽记居中偏上，半透明融入场景
    em = _emblem(entry.get("faction", ""))
    if em is not None:
        size = 168
        e = em.resize((size, size), Image.LANCZOS) if em.size != (size, size) else em
        canvas.alpha_composite(e, (CX - size // 2, top + 8))

    d = ImageDraw.Draw(canvas)
    steel = bool(entry.get("steel"))
    node = str(entry.get("node") or "—")
    mission = str(entry.get("mission") or "—")
    tier = str(entry.get("tier") or "").strip()
    level = str(entry.get("level") or "").strip()
    faction_cn = str(entry.get("faction_cn") or entry.get("faction") or "").strip()
    remaining = str(entry.get("remaining") or "").strip()

    header = ("钢铁裂缝 · " if steel else "") + node
    _center_text(d, header, top + 196, _font(24, bold=True), (235, 188, 96))
    _center_text(d, mission, top + 230, _font(29, bold=True), (242, 245, 250))

    if tier:
        tf = _font(19, bold=True)
        b = d.textbbox((0, 0), tier, font=tf)
        tw, th = b[2] - b[0], b[3] - b[1]
        ty = top + 262
        px, py = 18, 7
        d.rounded_rectangle((CX - tw // 2 - px, ty - py,
                             CX + tw // 2 + px, ty + th + py),
                            radius=13, fill=(14, 56, 66),
                            outline=(92, 206, 214), width=1)
        d.text((CX - tw // 2 - b[0], ty - b[1]), tier, font=tf,
               fill=(158, 236, 240))

    meta_parts = [p for p in (("敌人：" + faction_cn if faction_cn else ""),
                              ("等级 " + level if level else "")) if p]
    if meta_parts:
        _center_text(d, " · ".join(meta_parts), top + 316, _font(21),
                     (214, 223, 236))
    if remaining:
        _center_text(d, "剩余 " + remaining, top + h - 30, _font(22, bold=True),
                     (235, 188, 96))


def render(entries: Sequence[Dict], scene: str = "fissure") -> Optional[bytes]:
    """把节点列表渲染为纵向堆叠的背景板卡片 PNG。失败返回 None。"""
    if not PIL_AVAILABLE or not entries:
        return None
    import io

    items = list(entries)[:MAX_BOARDS]
    height = BOARD_H * len(items)
    canvas = _board_canvas(scene, height).convert("RGBA")
    for i, entry in enumerate(items):
        if i:
            d = ImageDraw.Draw(canvas)
            d.line([(0, i * BOARD_H), (WIDTH, i * BOARD_H)],
                   fill=(210, 220, 235, 70), width=2)
        _draw_board(canvas, i * BOARD_H, entry)
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()
