"""节点背景板风格的推送卡片。

每个节点一行：左侧是该节点所属星球+派系的游戏内任务场景缩略图，右侧是
节点名 / 任务类型 / 遗物等级 / 派系+等级 / 剩余时间。行高固定，条目多时
只增加图片高度、绝不截断——裂缝、钢铁裂缝、九重天、仲裁排期都要出全量。

徽记严格使用游戏原图/等比例重绘，不做艺术改动；奥罗金徽记由游戏截图
抠出（bot/assets/factions/orokin.png）。

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

from . import tilesets
from .imagecard import encode_card

WIDTH = 860
ROW_H = 140            # 单行高度（紧凑行，保证一屏能看全十条以上）
MAX_BOARDS = 40        # 安全上限：正常实时数据远小于此，超出才会截断
HEAD_H = 112           # 有标题时的头部高度
THUMB_W, THUMB_H = 168, 96
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


# 派系 → 游戏内英文原名（卡片"敌人"项按用户要求显示英文）
# 基础手工项 + 由 terms.FACTIONS 反向生成，覆盖所有本地化派系名
_FACTION_EN = {
    "奥罗金": "Orokin", "堕落者": "Corrupted",
    "克隆尼": "Grineer", "科普斯": "Corpus", "科波斯": "Corpus",
    "感染者": "Infested", "感染体": "Infested", "infection": "Infested",
    "感知者": "Sentient", "灵煞": "Sentient", "天诺": "Tenno",
}
try:
    from . import terms as _terms
    for _en, _cn in _terms.FACTIONS.items():
        _FACTION_EN.setdefault(_cn, _en)
except Exception:  # pragma: no cover
    pass


def faction_en(entry: dict) -> str:
    """返回派系英文原名：优先用原始英文字段，中文则映射，未知原样返回。"""
    raw = str(entry.get("faction") or "").strip()
    if not raw:
        raw = str(entry.get("faction_cn") or "").strip()
    if not raw:
        return ""
    return _FACTION_EN.get(raw, _FACTION_EN.get(raw.lower(), raw))


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


def _fit(draw, text, fnt, max_w: int) -> str:
    """超宽文字截断加省略号。"""
    text = str(text or "")
    if max_w <= 0:
        return ""
    try:
        if draw.textlength(text, font=fnt) <= max_w:
            return text
        while text and draw.textlength(text + "…", font=fnt) > max_w:
            text = text[:-1]
    except Exception:  # pragma: no cover - 老版本 Pillow
        return text
    return (text + "…") if text else "…"


def _left_text(draw, x, cy, text, fnt, fill) -> int:
    """左对齐、垂直居中绘制，返回文字宽度。"""
    b = draw.textbbox((0, 0), text, font=fnt)
    draw.text((x - b[0], cy - (b[3] - b[1]) // 2 - b[1]),
              text, font=fnt, fill=fill)
    return b[2] - b[0]


def _right_text(draw, right, cy, text, fnt, fill) -> int:
    """右对齐、垂直居中绘制，返回文字宽度。"""
    b = draw.textbbox((0, 0), text, font=fnt)
    w = b[2] - b[0]
    draw.text((right - w - b[0], cy - (b[3] - b[1]) // 2 - b[1]),
              text, font=fnt, fill=fill)
    return w


def _tier_pill(draw, x, cy, text, max_w: int = 0) -> int:
    """遗物等级胶囊，返回宽度；超宽时先缩字号再截断，保证任务列能对齐。"""
    fnt = _font(17, bold=True)
    if max_w:
        b = draw.textbbox((0, 0), text, font=fnt)
        if b[2] - b[0] + 24 > max_w:
            fnt = _font(15, bold=True)
        b = draw.textbbox((0, 0), text, font=fnt)
        if b[2] - b[0] + 20 > max_w:
            text = _fit(draw, text, fnt, max_w - 22)
    b = draw.textbbox((0, 0), text, font=fnt)
    tw, th = b[2] - b[0], b[3] - b[1]
    px, py = 12, 6
    box = (x, cy - (th + 2 * py) // 2,
           x + tw + 2 * px, cy + (th + 2 * py) // 2)
    draw.rounded_rectangle(box, radius=(th + 2 * py) // 2,
                           fill=(14, 56, 66), outline=(92, 206, 214), width=1)
    draw.text((x + px - b[0], box[1] + py - b[1]), text, font=fnt,
              fill=(158, 236, 240))
    return tw + 2 * px


def _thumb(bg, w: int = THUMB_W, h: int = THUMB_H):
    """把场景图裁成圆角缩略图（cover + 暗色蒙版）。"""
    bw, bh = bg.size
    scale = max(w / float(bw), h / float(bh))
    nw, nh = max(w, int(bw * scale)), max(h, int(bh * scale))
    img = bg.resize((nw, nh), Image.LANCZOS).convert("RGBA")
    left, top = (nw - w) // 2, (nh - h) // 2
    img = img.crop((left, top, left + w, top + h))
    img = Image.alpha_composite(img, Image.new("RGBA", (w, h), (8, 12, 20, 44)))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1),
                                           radius=12, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _scene_of(entry: dict):
    """按节点原始名 + 节点原生阵营选场景图。

    裂缝里敌人虽统一显示堕落者，场景贴图仍应是该星球原本的 tile set
    （赤毒要塞不能套成奥罗金金厅）。
    """
    name = str(entry.get("node_raw") or "") or str(entry.get("node") or "")
    faction_key = str(entry.get("native_faction") or entry.get("faction") or "")
    return tilesets.scene_image(tilesets.scene_key(name, faction_key))


def _draw_row(canvas, top: int, entry: dict) -> None:
    """在 canvas 的 [top, top+ROW_H) 区域画一行节点信息。"""
    # 半透明底板必须画在独立的透明图层再合成：Pillow 的 rounded_rectangle
    # 遇到四元组颜色会直接覆盖像素 alpha，转 RGB 后会变成一块白板。
    panel = Image.new("RGBA", (WIDTH, ROW_H), (0, 0, 0, 0))
    ImageDraw.Draw(panel, "RGBA").rounded_rectangle(
        (16, 5, WIDTH - 16, ROW_H - 7), radius=16,
        fill=(255, 255, 255, 16), outline=(255, 255, 255, 30), width=1)
    canvas.alpha_composite(panel, (0, top))

    bg = _scene_of(entry)
    if bg is not None and PIL_AVAILABLE:
        thumb = _thumb(bg)
        canvas.alpha_composite(thumb, (26, top + (ROW_H - THUMB_H) // 2))

    d = ImageDraw.Draw(canvas)

    x = 26 + THUMB_W + 18
    right = WIDTH - 34
    cy1 = top + 46
    cy2 = top + 96

    # 第一行：节点名（左） + 剩余时间（右）
    remaining = str(entry.get("remaining") or "").strip()
    rem_w = 0
    if remaining:
        rem_w = _right_text(d, right, cy1, "剩余 " + remaining,
                            _font(20, bold=True), (235, 188, 96))
    node_font = _font(24, bold=True)
    node = _fit(d, str(entry.get("node") or "—"), node_font,
                right - x - rem_w - 24)
    _left_text(d, x, cy1, node, node_font, (242, 245, 250))

    # 第二行右侧：徽记 + 派系英文 + 等级
    enemy = faction_en(entry)
    level = str(entry.get("level") or "").strip()
    meta = " · ".join(part for part in (
        enemy, ("等级 " + level) if level else "") if part)
    meta_font = _font(16)
    meta_w = int(d.textlength(meta, font=meta_font)) if meta else 0
    em = _emblem(str(entry.get("native_faction") or entry.get("faction") or ""))
    em_size = 26
    group_w = meta_w + ((em_size + 8) if (em is not None and meta) else 0)
    gx = right - group_w
    if em is not None and meta:
        canvas.alpha_composite(em.resize((em_size, em_size), Image.LANCZOS),
                               (gx, cy2 - em_size // 2))
        gx += em_size + 8
    if meta:
        _left_text(d, gx, cy2, meta, meta_font, (198, 210, 228))

    # 第二行左侧：遗物等级胶囊 + 任务类型
    # 胶囊占固定宽度（190px），任务列从固定位置开始，各行才能对齐成一条线。
    tier = str(entry.get("tier") or "").strip()
    mission_x = x + 190
    if tier:
        _tier_pill(d, x, cy2, tier, max_w=mission_x - x - 6)
    mission = str(entry.get("mission") or "—")
    mission_font = _font(21, bold=True)
    _left_text(d, mission_x, cy2,
               _fit(d, mission, mission_font, right - group_w - 18 - mission_x),
               mission_font, (228, 234, 246))


def render(entries: Sequence[Dict], scene: str = "fissure",
           title: str = "", subtitle: str = "") -> Optional[bytes]:
    """把节点列表渲染为 PNG 卡片；条目全量出图，不截断。失败返回 None。"""
    if not PIL_AVAILABLE or not entries:
        return None
    import io

    from . import imagecard

    items = list(entries)[:MAX_BOARDS]
    head = HEAD_H if title else 0
    height = head + ROW_H * len(items) + 12
    canvas = imagecard._gradient(WIDTH, height, imagecard.BG_TOP,
                                 imagecard.BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(canvas)
    if title:
        title_font = _font(38, bold=True)
        _center_text(draw, _fit(draw, title, title_font, WIDTH - 80), 46,
                     title_font, (242, 245, 250))
        if subtitle:
            sub_font = _font(17)
            _center_text(draw, _fit(draw, subtitle, sub_font, WIDTH - 100), 88,
                         sub_font, (172, 184, 208))
    for index, entry in enumerate(items):
        _draw_row(canvas, head + index * ROW_H, entry)
    return encode_card(canvas)
