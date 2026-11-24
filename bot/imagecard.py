"""图片卡片渲染（Pillow）。

设计目标：
- 深色 Warframe 风格，信息密度高，适合手机上看
- 兼容 Pillow 7.2（Windows 自带环境）到最新版
- 找不到中文字体或 Pillow 时自动降级为文字（由上层决定）

所有卡片宽度固定 860px，高度按内容自适应。
"""

from __future__ import annotations

import fnmatch
import io
import os
import subprocess
import time
from typing import Dict, List, Optional, Sequence, Tuple

try:  # Pillow 是可选依赖
    from PIL import Image, ImageDraw, ImageFont

    PIL_AVAILABLE = True
except Exception:  # pragma: no cover - 环境缺 Pillow
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    PIL_AVAILABLE = False

# ---------------------------------------------------------------------- 配色
BG_TOP = (17, 22, 31)
BG_BOTTOM = (8, 10, 15)
PANEL = (24, 30, 41)
PANEL_LIGHT = (31, 38, 51)
LINE = (44, 53, 69)
TEXT = (233, 238, 246)
MUTED = (139, 152, 170)
DIM = (100, 112, 128)
GOLD = (235, 188, 96)
GOLD_DARK = (150, 112, 44)
CYAN = (98, 190, 219)
GREEN = (96, 203, 143)
RED = (226, 112, 112)
PURPLE = (168, 137, 226)

WIDTH = 860
PAD = 34
RADIUS = 14

# ---- 统一「舒适版」排版尺寸：所有推送卡共享，避免拥挤、保证对齐 ----
CELL_FONT = 18          # 表格正文（原 16）
CELL_HEADER_FONT = 15   # 表头（原 13）
CELL_PAD_X = 14         # 单元格左右内边距（原 10/12）
ROW_H = 52              # 表格行高（原 42）
ROW_TEXT_DY = 14        # 行内文字垂直偏移（原 9）
GROUP_ROW_H = 50        # 分组表行高（原 38）
ZEBRA_PAD = 8           # 斑马纹左右外扩

_FONT_CACHE: Dict[Tuple[str, int], object] = {}
_FONT_PATHS: Optional[Tuple[str, str]] = None


def _fc_match(family: str) -> str:
    """用 fontconfig 查找字体（Linux 上最可靠）。"""
    try:
        output = subprocess.check_output(
            ["fc-match", "-f", "%{file}", family],
            stderr=subprocess.DEVNULL, timeout=3)
        path = output.decode("utf-8", "replace").strip()
        lowered = os.path.basename(path).lower()
        if path and any(key in lowered for key in
                        ("cjk", "han", "wqy", "noto", "sourcehan", "droidsansfallback")):
            return path
    except Exception:
        pass
    return ""


def _scan_font_dirs(names: Sequence[str], patterns: Sequence[str] = (),
                    limit: int = 12) -> List[str]:
    roots = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
    ]
    found: List[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                lowered = filename.lower()
                matched = filename in names or any(
                    fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns)
                if not matched:
                    continue
                path = os.path.join(dirpath, filename)
                if path not in found:
                    found.append(path)
                if len(found) >= limit:
                    return found
    return found


def _candidate_fonts() -> List[Tuple[str, str]]:
    win = "C:/Windows/Fonts"
    candidates: List[Tuple[str, str]] = [
        (os.path.join(win, "msyh.ttc"), os.path.join(win, "msyhbd.ttc")),
        (os.path.join(win, "msyh.ttf"), os.path.join(win, "msyhbd.ttf")),
        (os.path.join(win, "simhei.ttf"), os.path.join(win, "simhei.ttf")),
    ]

    # Linux：先问 fontconfig，再扫常见目录
    if os.name != "nt":
        for family in ("Noto Sans CJK SC", "Noto Sans CJK", "Noto Sans SC",
                       "Source Han Sans SC", "WenQuanYi Micro Hei",
                       "WenQuanYi Zen Hei", "Microsoft YaHei"):
            path = _fc_match(family)
            if path:
                bold = path.replace("Regular", "Bold").replace("-Regular", "-Bold")
                candidates.append((path, bold if os.path.isfile(bold) else path))

        names = [
            "NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc",
            "NotoSansCJK-VF.otf.ttc", "NotoSansCJKsc-Regular.otf",
            "NotoSansCJKsc-Bold.otf", "NotoSansSC-Regular.otf",
            "SourceHanSansSC-Regular.otf", "SourceHanSansSC-Bold.otf",
            "wqy-microhei.ttc", "wqy-zenhei.ttc", "DroidSansFallbackFull.ttf",
        ]
        patterns = ["notosanscjk*", "notosanssc*", "sourcehansans*",
                    "wqy-*", "droidsansfallback*", "msyh*", "simhei*"]
        scanned = _scan_font_dirs(names, patterns)
        regulars = [path for path in scanned if "bold" not in path.lower()]
        bolds = [path for path in scanned if "bold" in path.lower()]
        for path in regulars:
            bold = bolds[0] if bolds else path
            candidates.append((path, bold))

    candidates.extend([
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-VF.otf.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-VF.otf.ttc"),
        ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
         "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf"),
        ("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc"),
        ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
         "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ])
    return candidates


def configure(font_regular: str = "", font_bold: str = "") -> None:
    """允许配置文件覆盖字体路径。"""
    global _FONT_PATHS
    if font_regular and os.path.isfile(font_regular):
        bold = font_bold if font_bold and os.path.isfile(font_bold) else font_regular
        _FONT_PATHS = (font_regular, bold)
        _FONT_CACHE.clear()


def _resolve_fonts() -> Optional[Tuple[str, str]]:
    global _FONT_PATHS
    if _FONT_PATHS is not None:
        return _FONT_PATHS
    for regular, bold in _candidate_fonts():
        if os.path.isfile(regular):
            if not os.path.isfile(bold):
                bold = regular
            _FONT_PATHS = (regular, bold)
            return _FONT_PATHS
    return None


def available() -> bool:
    return bool(PIL_AVAILABLE and _resolve_fonts())


def _font(size: int, bold: bool = False):
    paths = _resolve_fonts()
    if not paths:
        raise RuntimeError("找不到中文字体")
    path = paths[1] if bold else paths[0]
    key = (path, size)
    cached = _FONT_CACHE.get(key)
    if cached is None:
        cached = ImageFont.truetype(path, size)
        _FONT_CACHE[key] = cached
    return cached


# ---------------------------------------------------------------------- 基础绘制
def _tsize(draw, text: str, font) -> Tuple[int, int]:
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    except AttributeError:  # Pillow < 8
        return draw.textsize(text, font=font)


def _vcenter(draw, text: str, font, box_h: float, box_top: float = 0.0) -> int:
    """返回 draw.text 的 y，使文字墨迹在 [box_top, box_top + box_h] 内垂直居中。

    Pillow 默认以字体 ascent 顶部为 y 基准，墨迹实际从 y + bbox.top 开始，
    因此需减去 bbox.top 才能精确居中（避免固定偏移在不同字号下偏下）。
    """
    try:
        box = draw.textbbox((0, 0), text, font=font)
        top, bottom = box[1], box[3]
    except AttributeError:  # Pillow < 8
        top, bottom = 0, _tsize(draw, text, font)[1]
    glyph_h = bottom - top
    return int(round(box_top + (box_h - glyph_h) / 2.0 - top))


def _ctext(draw, y: int, text: str, font, fill, cx: float = None,
           width: int = WIDTH) -> None:
    """水平居中绘制文字；cx 默认取 width 的水平中线。"""
    if cx is None:
        cx = width / 2.0
    tw, _ = _tsize(draw, text, font)
    draw.text((int(round(cx - tw / 2.0)), y), text, font=font, fill=fill)


def _cvtext(draw, text: str, font, fill, box_h: float, box_top: float = 0.0,
            cx: float = None, width: int = WIDTH) -> None:
    """在 [box_top, box_top + box_h] 的行框内水平 + 垂直居中绘制文字。"""
    if cx is None:
        cx = width / 2.0
    tw, _ = _tsize(draw, text, font)
    draw.text((int(round(cx - tw / 2.0)),
               _vcenter(draw, text, font, box_h, box_top)),
              text, font=font, fill=fill)


def _fit(draw, text: str, font, max_width: int) -> str:
    text = str(text or "")
    if not text:
        return ""
    if _tsize(draw, text, font)[0] <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if _tsize(draw, text[:mid] + "…", font)[0] <= max_width:
            low = mid
        else:
            high = mid - 1
    return text[:low] + "…"


def _wrap(draw, text: str, font, max_width: int, max_lines: int = 2) -> List[str]:
    text = str(text or "").strip()
    if not text:
        return []
    lines: List[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if _tsize(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = char
        if len(lines) >= max_lines - 1:
            break
    if current and len(lines) < max_lines:
        # 剩余内容合并进最后一行并截断
        consumed = sum(len(line) for line in lines)
        rest = text[consumed:]
        lines.append(_fit(draw, rest, font, max_width))
    return lines


def _wrap_items(draw, items, font, max_width: int, max_lines: int = 12,
                prefix: str = "奖励：") -> List[str]:
    """按整项换行：不把「奥罗金定向矩阵装置 x3」这种切断，超宽才退化成字符切。"""
    values = [str(item) for item in (items or []) if str(item)]
    if not values:
        return []
    lines: List[str] = []
    current = prefix
    has_item = False
    for item in values:
        piece = item if not has_item else " · " + item
        if _tsize(draw, current + piece, font)[0] <= max_width:
            current += piece
            has_item = True
            continue
        if has_item:
            lines.append(current)
            if len(lines) >= max_lines:
                return lines
            current = item
            has_item = True
        else:
            # 单个物品就超宽：退回字符切
            for line in _wrap(draw, prefix + item, font, max_width, max_lines=max_lines):
                lines.append(line)
            current = ""
            has_item = False
    if has_item and len(lines) < max_lines:
        lines.append(current)
    return lines


def _rrect(draw, box, radius: int, fill=None, outline=None, width: int = 1) -> None:
    x1, y1, x2, y2 = [int(value) for value in box]
    radius = max(0, min(radius, (x2 - x1) // 2, (y2 - y1) // 2))
    if fill is not None:
        draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
        draw.rectangle([x1, y1 + radius, x1 + radius, y2 - radius], fill=fill)
        draw.rectangle([x2 - radius, y1 + radius, x2, y2 - radius], fill=fill)
        for cx, cy, start in ((x1, y1, 180), (x2 - 2 * radius, y1, 270),
                              (x1, y2 - 2 * radius, 90), (x2 - 2 * radius, y2 - 2 * radius, 0)):
            draw.pieslice([cx, cy, cx + 2 * radius, cy + 2 * radius], start, start + 90, fill=fill)
    if outline is not None:
        draw.rectangle([x1 + radius, y1, x2 - radius, y1 + width - 1], fill=outline)
        draw.rectangle([x1 + radius, y2 - width + 1, x2 - radius, y2], fill=outline)
        draw.rectangle([x1, y1 + radius, x1 + width - 1, y2 - radius], fill=outline)
        draw.rectangle([x2 - width + 1, y1 + radius, x2, y2 - radius], fill=outline)
        for cx, cy, start in ((x1, y1, 180), (x2 - 2 * radius, y1, 270),
                              (x1, y2 - 2 * radius, 90), (x2 - 2 * radius, y2 - 2 * radius, 0)):
            draw.arc([cx, cy, cx + 2 * radius, cy + 2 * radius], start, start + 90,
                     fill=outline, width=width)


def _gradient(width: int, height: int, top, bottom) -> "Image.Image":
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return image


def _divider(draw, y: int, x1: int = PAD, x2: int = WIDTH - PAD) -> None:
    draw.line([(x1, y), (x2, y)], fill=LINE, width=1)


def _chip(draw, x: int, y: int, text: str, color, font_size: int = 16) -> Tuple[int, int]:
    font = _font(font_size, bold=True)
    width, height = _tsize(draw, text, font)
    box = (x, y, x + width + 26, y + height + 16)
    _rrect(draw, box, (height + 16) // 2, fill=PANEL_LIGHT, outline=color)
    draw.text((x + 13, y + 8), text, font=font, fill=color)
    return box[2] - box[0], box[3] - box[1]


def _footer(draw, y: int, left: str, right: str = "QQ Warframe Bot",
            width: int = WIDTH) -> int:
    _divider(draw, y, PAD, width - PAD)
    font = _font(14)
    text = "  ·  ".join(part for part in (left, right) if part)
    text = _fit(draw, text, font, width - 2 * PAD)
    tw, _ = _tsize(draw, text, font)
    draw.text(((width - tw) // 2, y + 12), text, font=font, fill=DIM)
    return y + 42


def _header(draw, title: str, subtitle: str, chip: str, accent, top: int = 30,
            width: int = WIDTH) -> int:
    draw.rectangle([0, 0, width, 4], fill=accent)
    y = top
    title_font = _font(34, bold=True)
    max_width = width - 2 * PAD - 150
    lines = _wrap(draw, title, title_font, max_width, max_lines=2)
    for line in lines:
        lw, _ = _tsize(draw, line, title_font)
        draw.text(((width - lw) // 2, y), line, font=title_font, fill=TEXT)
        y += 46
    if chip:
        cw, _ = _tsize(draw, chip, _font(16, bold=True))
        _chip(draw, width - PAD - cw - 26, top + 2, chip, accent)
    if subtitle:
        sub = _fit(draw, subtitle, _font(17), width - 2 * PAD)
        sw, _ = _tsize(draw, sub, _font(17))
        draw.text(((width - sw) // 2, y + 3), sub, font=_font(17), fill=MUTED)
        y += 28
    return y


def _pills(draw, y: int, items: Sequence[Tuple[str, str]], accent,
           width: int = WIDTH) -> int:
    x = PAD
    for label, value in items:
        text = "%s %s" % (label, value)
        font = _font(14, bold=True)
        tw, _height = _tsize(draw, text, font)
        if x + tw + 26 > width - PAD:
            y += 40
            x = PAD
        _rrect(draw, (x, y, x + tw + 24, y + 32), 16, fill=PANEL)
        draw.text((x + 12, y + 8), text, font=font, fill=accent if value else MUTED)
        x += tw + 34
    return y + 48


# ---------------------------------------------------------------------- 物品卡
def _order_column(draw, x: int, width: int, y: int, title: str, color,
                  orders, empty_text: str) -> int:
    font_title = _font(19, bold=True)
    draw.ellipse([x + 6, y + 8, x + 16, y + 18], fill=color)
    _ctext(draw, y, title, font_title, TEXT, cx=x + width / 2.0)
    if orders:
        count_text = "%d 条" % len(orders)
        count_font = _font(15)
        count_width, _ = _tsize(draw, count_text, count_font)
        draw.text((x + width - count_width, y + 4), count_text, font=count_font, fill=DIM)
    y += 38

    if not orders:
        _ctext(draw, y + 2, empty_text, _font(17), DIM, cx=x + width / 2.0)
        return y + 34

    price_font = _font(25, bold=True)
    small_font = _font(15)
    for order in orders:
        price = "%sp" % _format_int(order.platinum)
        draw.text((x, y), price, font=price_font, fill=color)
        price_width, _ = _tsize(draw, price, price_font)
        draw.text((x + price_width + 10, y + 10), "×%d" % order.quantity,
                  font=_font(16), fill=MUTED)

        seller_x = x + max(84, price_width + 52)
        seller_width = x + width - seller_x - 60
        seller = _fit(draw, order.user, _font(17), max(30, seller_width))
        draw.text((seller_x, y + 9), seller, font=_font(17), fill=TEXT)

        status = order.status_label
        if status in ("游戏中", "在线"):
            status_color = GREEN if status == "游戏中" else CYAN
            status_text = status
        else:
            status_color = DIM
            status_text = status
        status_width, _ = _tsize(draw, status_text, small_font)
        draw.text((x + width - status_width, y + 10), status_text,
                  font=small_font, fill=status_color)

        mod = ""
        if order.rank is not None:
            mod = "R%d" % order.rank
        if order.charges:
            mod = (mod + " " if mod else "") + "%d次" % order.charges
        if mod:
            draw.text((x + price_width + 46, y + 10), mod, font=_font(14), fill=PURPLE)
        y += 40
    return y


def _format_int(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if abs(number - round(number)) < 0.05:
        return "{:,}".format(int(round(number)))
    return "{:,.1f}".format(number)


def _format_price(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if number >= 10000:
        return "%.1f万" % (number / 10000.0)
    return _format_int(number)


def _stat_block(draw, x: int, width: int, y: int, label: str, value: str,
                sub: str, color) -> None:
    cx = x + width / 2.0
    _ctext(draw, y, label, _font(15), DIM, cx=cx)
    _ctext(draw, y + 24, value, _font(26, bold=True), color, cx=cx)
    _ctext(draw, y + 62, _fit(draw, sub, _font(15), width), _font(15), MUTED, cx=cx)


def _trend_table(draw, y, stats, days: int = 15) -> int:
    points = list(getattr(stats, "history", None) or [])[-days:]
    if len(points) < 2:
        return y
    ordered = list(reversed(points))
    avg = sum(point.avg for point in points) / len(points)
    high = max(point.max_price or point.avg for point in points)
    low = min(point.min_price or point.avg for point in points)
    first, last = points[0], points[-1]
    change = ((last.avg - first.avg) / first.avg * 100.0) if first.avg else 0.0

    trend_title = "近 %d 天价格走势" % len(points)
    ttw, _ = _tsize(draw, trend_title, _font(19, bold=True))
    draw.text(((WIDTH - ttw) // 2, y), trend_title,
              font=_font(19, bold=True), fill=TEXT)
    y += 32
    change_color = GREEN if change >= 0 else RED
    prefix = "均价 %.1fp ｜ 高 %.0fp ｜ 低 %.0fp ｜ 变化 " % (avg, high, low)
    summary = prefix + "%+.1f%%" % change
    sw, _ = _tsize(draw, summary, _font(15))
    draw.text(((WIDTH - sw) // 2, y), summary, font=_font(15), fill=MUTED)
    y += 30

    usable = WIDTH - 2 * PAD
    ratios = [0.8, 0.8, 0.7, 1.7]
    total = sum(ratios)
    widths = [int(usable * ratio / total) for ratio in ratios]
    headers = ["日期", "均价", "成交量", "区间"]
    header_font = _font(14, bold=True)
    x = PAD
    for index, header in enumerate(headers):
        hw, _ = _tsize(draw, header, header_font)
        draw.text((x + (widths[index] - hw) // 2, y), header,
                  font=header_font, fill=DIM)
        x += widths[index]
    y += 28
    _divider(draw, y)
    y += 8

    row_height = 32
    font = _font(16)
    for row_index, point in enumerate(ordered):
        if row_index % 2 == 0:
            _rrect(draw, (PAD - 5, y - 4, WIDTH - PAD + 5, y + row_height - 8),
                   7, fill=PANEL)
        latest = row_index == 0
        color = GOLD if latest else TEXT
        values = [
            point.date[5:],
            "%.1fp" % point.avg,
            _format_int(point.volume),
            "%s~%s" % (_format_price(point.min_price),
                       _format_price(point.max_price)),
        ]
        x = PAD
        for index, value in enumerate(values):
            text = _fit(draw, value, font, widths[index] - 16)
            vw, _ = _tsize(draw, text, font)
            ty = y + _vcenter(draw, text, font, row_height - 4, -4)
            draw.text((x + (widths[index] - vw) // 2, ty), text,
                      font=font, fill=color)
            x += widths[index]
        y += row_height
    return y + 18


def item_card(item, sell, buy, stats, platform_label: str = "PC",
              price_only: bool = False) -> bytes:
    """物品行情卡。"""
    height = 2100
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    accent = GOLD
    y = _header(draw, item.name or item.slug, "英文：%s" % item.name_en
                if item.name_en and item.name_en != item.name else "",
                item.kind_label, accent)

    facts = []
    if item.ducats:
        facts.append(("杜卡德", _format_int(item.ducats)))
    if item.mastery_rank:
        facts.append(("MR", str(item.mastery_rank)))
    if item.trading_tax:
        facts.append(("交易税", _format_int(item.trading_tax)))
    facts.append(("平台", platform_label))
    y = _pills(draw, y + 4, facts, accent)

    _divider(draw, y)
    y += 22

    column_gap = 26
    column_width = (WIDTH - 2 * PAD - column_gap) // 2
    left_y = _order_column(draw, PAD, column_width, y, "在售 · 在线", GOLD,
                           sell, "暂无在线卖家")
    right_x = PAD + column_width + column_gap
    right_y = _order_column(draw, right_x, column_width, y, "收购 · 在线", CYAN,
                            buy, "暂无在线买家")
    divider_x = PAD + column_width + column_gap // 2
    draw.line([(divider_x, y - 4), (divider_x, max(left_y, right_y) - 8)], fill=LINE, width=1)
    y = max(left_y, right_y) + 10

    _divider(draw, y)
    y += 20
    _ctext(draw, y, "行情", _font(17, bold=True), TEXT)
    y += 30

    third = (WIDTH - 2 * PAD) // 3
    if stats and stats.h48 and not stats.h48.empty:
        _stat_block(draw, PAD, third, y, "48 小时均价",
                    "%.1fp" % stats.h48.avg,
                    "中位 %sp · %s 笔" % (_format_price(stats.h48.median),
                                          _format_int(stats.h48.volume)), GOLD)
    else:
        _stat_block(draw, PAD, third, y, "48 小时", "—", "暂无成交", MUTED)

    if stats and stats.d90 and not stats.d90.empty:
        _stat_block(draw, PAD + third, third, y, "90 天中位",
                    "%sp" % _format_price(stats.d90.median),
                    "均价 %sp" % _format_price(stats.d90.avg), CYAN)
        _stat_block(draw, PAD + 2 * third, third, y, "90 天区间",
                    "%sp ~ %sp" % (_format_price(stats.d90.min_price),
                                   _format_price(stats.d90.max_price)),
                    "%s 笔成交" % _format_int(stats.d90.volume), GREEN)
    else:
        _stat_block(draw, PAD + third, third, y, "90 天", "—", "暂无成交", MUTED)

    y += 92
    y = _trend_table(draw, y, stats)
    if item.description and not price_only:
        desc = item.description.replace("\n", " ").strip()
        y += 4
        _ctext(draw, y, "“%s”" % _fit(draw, desc, _font(14), WIDTH - 2 * PAD),
               _font(14), DIM)
        y += 28

    left = "数据 warframe market · %s · %s" % (
        platform_label, time.strftime("%m-%d %H:%M"))
    y = _footer(draw, y + 6, left)

    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def price_trend_card(item, stats, platform_label: str = "PC") -> bytes:
    """按钮触发：单独的近 15 天价格走势卡。"""
    points = list(getattr(stats, "history", None) or [])[-15:]
    height = max(720, 300 + len(points) * 32 + 170)
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, item.name or item.slug, "价格走势 · %s" % platform_label,
                "TREND", GOLD)
    y += 18
    if len(points) < 2:
        _ctext(draw, y + 10, "暂无足够的价格历史数据。", _font(16), MUTED)
        y += 50
    else:
        y = _trend_table(draw, y, stats)
    left = "数据 warframe market · %s · %s" % (
        platform_label, time.strftime("%m-%d %H:%M"))
    y = _footer(draw, y + 10, left)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def item_desc_card(item, platform_label: str = "PC") -> bytes:
    """按钮触发：参考 warframe.market 物品页的对话式说明卡。"""
    desc = str(getattr(item, "description", "") or "").replace("\n", " ").strip()
    if not desc:
        desc = "该物品在 warframe.market 暂无说明文本。"
    height = 900
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, item.name or item.slug,
                "物品说明 · warframe.market 风格", "DESC", CYAN)
    y += 30
    _ctext(draw, y, "TENNO", _font(13, bold=True), GOLD)
    y += 26
    font = _font(18)
    lines = _wrap(draw, desc, font, WIDTH - 2 * PAD - 60, max_lines=10)
    box = (PAD, y - 8, WIDTH - PAD, y + len(lines) * 32 + 22)
    _rrect(draw, box, 13, fill=PANEL_LIGHT, outline=(70, 84, 106), width=1)
    for index, line in enumerate(lines):
        _ctext(draw, y + index * 32, line, font, TEXT)
    y = box[3] + 30
    _ctext(draw, y, "来源：warframe.market 物品页说明文本", _font(15), DIM)
    y = _footer(draw, y + 44, "平台：%s" % platform_label)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


# ---------------------------------------------------------------------- 紫卡范围
def _analysis_section(draw, y: int, title: str, hint: str = "") -> int:
    font = _font(20, bold=True)
    heading = title + ("  ·  " + hint if hint else "")
    _ctext(draw, y, _fit(draw, heading, font, WIDTH - 2 * PAD), font, TEXT)
    y += 34
    _divider(draw, y)
    return y + 10


def _analysis_grid(draw, y: int, headers: Sequence[str], widths: Sequence[int],
                   rows: Sequence[Sequence[str]], aligns: Sequence[str],
                   colors: Optional[Dict[Tuple[int, int], tuple]] = None) -> int:
    header_font = _font(14, bold=True)
    x = PAD
    for index, header in enumerate(headers):
        text = _fit(draw, header, header_font, widths[index] - 16)
        text_width, _ = _tsize(draw, text, header_font)
        draw.text((x + (widths[index] - text_width) // 2, y), text,
                  font=header_font, fill=DIM)
        x += widths[index]
    y += 28
    _divider(draw, y)
    y += 8

    font = _font(17)
    for row_index, row in enumerate(rows):
        if row_index % 2 == 0:
            _rrect(draw, (PAD - 6, y - 4, WIDTH - PAD + 6, y + 32), 8, fill=PANEL)
        x = PAD
        for index, cell in enumerate(row):
            text = _fit(draw, str(cell), font, widths[index] - 18)
            color = TEXT
            if colors:
                color = colors.get((row_index, index), TEXT)
            text_width, _ = _tsize(draw, text, font)
            ty = y + _vcenter(draw, text, font, 36, -4)
            draw.text((x + (widths[index] - text_width) // 2, ty),
                      text, font=font, fill=color)
            x += widths[index]
        y += 36
    return y


def riven_range_card(analysis, footer: str = "数据 Warframe Wiki · 本地计算") -> bytes:
    """紫卡词条范围表：2 / 2+1 / 3 / 3+1 与负面词条（Lv.8）。"""
    names: List[str] = []
    seen = set()
    for group in (analysis.three_neg, analysis.two_neg,
                  analysis.three, analysis.two):
        for stat in group:
            if stat.name not in seen:
                seen.add(stat.name)
                names.append(stat.name)

    negative_names: List[str] = []
    for stat in (analysis.negatives_three or analysis.negatives_two):
        if stat.name not in negative_names:
            negative_names.append(stat.name)

    height = 200 + len(names) * 36 + len(negative_names) * 36 + 240
    height = max(820, height)

    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    subtitle = "倾向 %.2f（%s）｜ Lv.8" % (analysis.disposition, analysis.dots or "—")
    if analysis.name_en and analysis.name_en.lower() != analysis.name.lower():
        subtitle += "｜ %s" % analysis.name_en
    y = _header(draw, "%s · 词条范围" % analysis.name, subtitle,
                "WARFRAME WIKI", PURPLE)
    y = _pills(draw, y, [
        ("倾向", "%.2f" % analysis.disposition),
        ("正向", "%d 条" % len(names)),
        ("负面", "%d 条" % len(negative_names)),
    ], PURPLE)

    maps = {
        "two": {stat.name: stat for stat in analysis.two},
        "two_neg": {stat.name: stat for stat in analysis.two_neg},
        "three": {stat.name: stat for stat in analysis.three},
        "three_neg": {stat.name: stat for stat in analysis.three_neg},
    }
    y = _analysis_section(draw, y, "正向词条范围 · Lv.8",
                          "2+1 / 3+1 数值更高")
    rows = []
    for name in names:
        rows.append([
            name,
            maps["two"].get(name).range_text() if name in maps["two"] else "—",
            maps["two_neg"].get(name).range_text() if name in maps["two_neg"] else "—",
            maps["three"].get(name).range_text() if name in maps["three"] else "—",
            maps["three_neg"].get(name).range_text() if name in maps["three_neg"] else "—",
        ])
    y = _analysis_grid(
        draw, y, ["词条", "双属性", "双属性+负面", "三属性", "三属性+负面"],
        [164, 150, 160, 150, 160], rows,
        ["left", "right", "right", "right", "right"],
        {(i, j): GREEN for i in range(len(rows)) for j in (1, 2, 3, 4)})
    y += 12

    neg_maps = {
        "two": {stat.name: stat for stat in analysis.negatives_two},
        "three": {stat.name: stat for stat in analysis.negatives_three},
    }
    if negative_names:
        y = _analysis_section(draw, y, "负面词条范围 · Lv.8", "数值越小越好")
        rows = []
        for name in negative_names:
            rows.append([
                name,
                neg_maps["two"].get(name).range_text() if name in neg_maps["two"] else "—",
                neg_maps["three"].get(name).range_text() if name in neg_maps["three"] else "—",
            ])
        y = _analysis_grid(
            draw, y, ["词条", "双属性+负面", "三属性+负面"],
            [240, 270, 270], rows, ["left", "right", "right"],
            {(i, j): RED for i in range(len(rows)) for j in (1, 2)})

    notes = ["2+1 / 3+1 的正向数值更高，负面从下表随机抽取。",
             "元素伤害 = 火焰 / 冰冻 / 电击 / 毒素；复合元素 = 毒气 / 腐蚀 / "
             "病毒 / 辐射 / 爆炸 / 磁力，同族数值相同。",
             "后坐力负值表示降低后坐力，属于正面效果。"]
    if analysis.history:
        latest = analysis.history[-1]
        notes.append("最近一次倾向调整：%s（%s）" % (latest[1], latest[0]))
    y += 10
    for line in notes:
        _ctext(draw, y, _fit(draw, line, _font(14), WIDTH - 2 * PAD),
               _font(14), MUTED)
        y += 25

    y = _footer(draw, y + 8, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


# ---------------------------------------------------------------------- 表格卡
class Cell:
    __slots__ = ("text", "color", "bold")

    def __init__(self, text, color=None, bold=False):
        self.text = str(text)
        self.color = color
        self.bold = bold


def table_card(title: str, subtitle: str, columns: Sequence[Tuple[str, float]],
               rows: Sequence[Sequence[Cell]], footer: str,
               accent=GOLD, chip: str = "", note: str = "",
               aligns: Optional[Sequence[str]] = None,
               background=None) -> bytes:
    """通用表格卡。

    columns: [(表头, 宽度比例), ...]
    rows: 每行与 columns 等长的 Cell 列表
    aligns: 每列 "left" / "right"
    """
    height = max(760, 300 + len(rows) * (ROW_H + 4) + (48 if note else 0) + 150)
    image = _scene_canvas(background, height)
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, subtitle, chip, accent)
    y += 20

    total_ratio = sum(ratio for _, ratio in columns) or 1.0
    usable = WIDTH - 2 * PAD
    widths = [int(usable * ratio / total_ratio) for _, ratio in columns]
    aligns = list(aligns or ["left"] * len(columns))

    header_font = _font(CELL_HEADER_FONT, bold=True)
    x = PAD
    for index, (name, _) in enumerate(columns):
        if name:
            text_width, _ = _tsize(draw, name, header_font)
            draw.text((x + (widths[index] - text_width) // 2, y), name,
                      font=header_font, fill=DIM)
        x += widths[index]
    y += 32
    _divider(draw, y)
    y += 12

    row_height = ROW_H
    text_font = _font(CELL_FONT)
    bold_font = _font(CELL_FONT, bold=True)
    for row_index, row in enumerate(rows):
        if row_index % 2 == 0:
            _rrect(draw, (PAD - ZEBRA_PAD, y - 5,
                          WIDTH - PAD + ZEBRA_PAD, y + row_height - 12),
                   10, fill=PANEL)
        x = PAD
        for index, cell in enumerate(row):
            font = bold_font if cell.bold else text_font
            color = cell.color or TEXT
            text = _fit(draw, cell.text, font, widths[index] - 2 * CELL_PAD_X)
            text_width, _ = _tsize(draw, text, font)
            ty = y + _vcenter(draw, text, font, row_height - 7, -5)
            draw.text((x + (widths[index] - text_width) // 2, ty),
                      text, font=font, fill=color)
            x += widths[index]
        y += row_height

    if note:
        note_font = _font(16)
        note_text = _fit(draw, note, note_font, WIDTH - 2 * PAD)
        nw, _ = _tsize(draw, note_text, note_font)
        draw.text(((WIDTH - nw) // 2, y + 8), note_text,
                  font=note_font, fill=MUTED)
        y += 38

    y = _footer(draw, y + 12, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def _cell_parts(cell) -> Tuple[Optional[tuple], bool, str]:
    """接受 str 或 Cell，返回 (颜色, 是否加粗, 文本)。"""
    if isinstance(cell, Cell):
        return cell.color, cell.bold, cell.text
    return None, False, str(cell)


def sections_table(title: str, subtitle: str, sections: Sequence[Dict],
                   footer: str, accent=GOLD, chip: str = "",
                   note: str = "", center: bool = True) -> bytes:
    """分组表格卡：每组一个小标题（右侧可带倒计时）＋ 若干行。

    sections: [{"label": 小标题, "badge": 右侧胶囊, "columns": [(表头, 比例), ...],
                "rows": [[str | Cell, ...], ...]}, ...]
    默认表头与所有单元格居中；center=False 时首列左对齐、末列右对齐。
    """
    sections = list(sections or [])
    total_rows = sum(len(item.get("rows") or []) for item in sections)
    height = max(760, 360 + total_rows * (GROUP_ROW_H + 4)
                 + len(sections) * 100 + 150)
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, subtitle, chip, accent)
    y += 16

    usable = WIDTH - 2 * PAD
    label_font = _font(22, bold=True)
    head_font = _font(CELL_HEADER_FONT, bold=True)
    text_font = _font(CELL_FONT)
    bold_font = _font(CELL_FONT, bold=True)
    row_height = GROUP_ROW_H
    right = WIDTH - PAD

    for item in sections:
        label = str(item.get("label") or "")
        badge = str(item.get("badge") or "")
        if label:
            label_text = _fit(draw, label, label_font, usable - 250)
            lw, _ = _tsize(draw, label_text, label_font)
            lx = (WIDTH - lw) // 2
            draw.rectangle([lx - 14, y + 4, lx - 10, y + 24], fill=accent)
            draw.text((lx, y), label_text, font=label_font, fill=TEXT)
            if badge:
                badge_width, _ = _tsize(draw, badge, _font(16, bold=True))
                _chip(draw, right - badge_width - 20, y - 2, badge, accent)
            y += 42

        columns = list(item.get("columns") or [])
        rows = list(item.get("rows") or [])
        if not columns:
            columns = [("", 1.0)] * max(1, len(rows[0]) if rows else 1)
        total_ratio = sum(ratio for _, ratio in columns) or 1.0
        widths = [int(usable * ratio / total_ratio) for _, ratio in columns]

        x = PAD
        for index, (name, _) in enumerate(columns):
            if name:
                name_width, _ = _tsize(draw, name, head_font)
                if center:
                    draw.text((x + (widths[index] - name_width) // 2, y),
                              name, font=head_font, fill=DIM)
                elif index == len(columns) - 1:
                    draw.text((x + widths[index] - CELL_PAD_X - name_width, y),
                              name, font=head_font, fill=DIM)
                else:
                    draw.text((x + CELL_PAD_X, y), name, font=head_font, fill=DIM)
            x += widths[index]
        y += 28
        _divider(draw, y)
        y += 10

        for row_index, row in enumerate(rows):
            if row_index % 2 == 0:
                _rrect(draw, (PAD - ZEBRA_PAD, y - 5,
                              WIDTH - PAD + ZEBRA_PAD, y + row_height - 12),
                       10, fill=PANEL)
            x = PAD
            for index, cell in enumerate(list(row)[:len(widths)]):
                color, bold, value = _cell_parts(cell)
                font = bold_font if bold else text_font
                value = _fit(draw, value, font, widths[index] - 2 * CELL_PAD_X)
                value_width, _ = _tsize(draw, value, font)
                ty = y + _vcenter(draw, value, font, row_height - 7, -5)
                if center:
                    draw.text((x + (widths[index] - value_width) // 2, ty),
                              value, font=font, fill=color or TEXT)
                elif index == len(widths) - 1:
                    draw.text((x + widths[index] - CELL_PAD_X - value_width, ty),
                              value, font=font, fill=color or TEXT)
                else:
                    draw.text((x + CELL_PAD_X, ty), value,
                              font=font, fill=color or TEXT)
                x += widths[index]
            y += row_height
        y += 20

    if note:
        note_font = _font(16)
        note_text = _fit(draw, note, note_font, usable)
        nw, _ = _tsize(draw, note_text, note_font)
        draw.text(((WIDTH - nw) // 2, y + 6), note_text,
                  font=note_font, fill=MUTED)
        y += 36

    y = _footer(draw, y + 10, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


# 仲裁表底部的快捷筛选按钮（个人号无法点击，画出样式提示用户发对应命令）
ARBITRATION_BUTTONS = [
    {"label": "谷拦仲裁", "hint": "谷神星 · 拦截", "color": (86, 200, 190)},
    {"label": "镜防仲裁", "hint": "火星 / 火卫二 · 镜像防御", "color": (110, 160, 235)},
    {"label": "高效仲裁", "hint": "评级 S / A+", "color": GOLD},
]


def _draw_buttons(draw, y: int, buttons: Sequence[Dict]) -> int:
    """在图片底部画一排按钮样式区块，返回绘制后的 y 坐标。"""
    if not buttons:
        return y
    gap = 16
    col_w = (WIDTH - 2 * PAD - gap * (len(buttons) - 1)) // len(buttons)
    btn_h = 64
    top = y + 10
    label_font = _font(18, bold=True)
    hint_font = _font(13)
    for index, spec in enumerate(buttons):
        x1 = PAD + index * (col_w + gap)
        x2 = x1 + col_w
        color = spec.get("color") or GOLD
        _rrect(draw, (x1, top, x2, top + btn_h), 11,
               fill=PANEL_LIGHT, outline=color, width=2)
        label = _fit(draw, str(spec.get("label", "")), label_font, col_w - 28)
        hint = _fit(draw, str(spec.get("hint", "")), hint_font, col_w - 28)
        draw.text((x1 + 14, top + 9), label, font=label_font, fill=color)
        draw.text((x1 + 14, top + 38), hint, font=hint_font, fill=MUTED)
    return top + btn_h


def schedule_table(title: str, subtitle: str, days: Sequence[Dict],
                   footer: str, accent=GOLD, chip: str = "",
                   note: str = "", buttons: Optional[Sequence[Dict]] = None,
                   background=None) -> bytes:
    """按天分组的排期表：每天一节，条目分左右两列铺开。

    days: [{"label": "10-28 周三", "badge": "24 场 · S×1",
            "rows": [[时间, 节点, 任务, 评级], ...]}, ...]
    每行四格，接受 str 或 Cell。条目多时自动折成两列，避免长图过高。
    """
    days = list(days or [])
    per_col = 1
    for item in days:
        per_col = max(per_col, (len(list(item.get("rows") or [])) + 1) // 2)
    buttons = list(buttons or [])
    height = max(760, 320 + len(days) * (78 + per_col * 42) + 150)
    if buttons:
        height += 96
    image = _scene_canvas(background, height)
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, subtitle, chip, accent)
    y += 16

    usable = WIDTH - 2 * PAD
    gap = 26
    col_w = (usable - gap) // 2
    right = WIDTH - PAD
    label_font = _font(21, bold=True)
    badge_font = _font(14, bold=True)
    head_font = _font(13, bold=True)
    time_font = _font(15)
    node_font = _font(16)
    mission_font = _font(15)
    rating_font = _font(15, bold=True)
    row_h = 37

    # 每列内部的列坐标：时间 / 节点 / 任务 / 评级
    time_x, node_x = 8, 60
    mission_x = col_w - 120
    node_w = mission_x - node_x - 6
    headers = [("时间", time_x), ("节点", node_x),
               ("任务", mission_x), ("评级", col_w - 8)]

    for item in days:
        label = str(item.get("label") or "")
        badge = str(item.get("badge") or "")
        rows = list(item.get("rows") or [])
        if label:
            label_text = _fit(draw, label, label_font, usable - 300)
            lw, _ = _tsize(draw, label_text, label_font)
            lx = (WIDTH - lw) // 2
            draw.rectangle([lx - 14, y + 4, lx - 9, y + 26], fill=accent)
            draw.text((lx, y), label_text, font=label_font, fill=TEXT)
            if badge:
                badge_width, badge_height = _tsize(draw, badge, badge_font)
                _chip(draw, right - badge_width - 24, y - 2, badge, accent,
                      font_size=14)
            y += 40
        if not rows:
            empty = "这一天的排期还没算出来。"
            ew, _ = _tsize(draw, empty, _font(15))
            draw.text(((WIDTH - ew) // 2, y + 2), empty,
                      font=_font(15), fill=DIM)
            y += 40
            continue

        # 列头：左右两列各画一次，四格标题居中于各自列位
        head_slots = [(time_x, 48), (node_x, node_w),
                      (mission_x, 66), (col_w - 54, 46)]
        for col in range(2):
            cx = PAD + col * (col_w + gap)
            for (name, _dx), (slot_x, slot_w) in zip(headers, head_slots):
                text_width, _ = _tsize(draw, name, head_font)
                draw.text((cx + slot_x + (slot_w - text_width) // 2, y),
                          name, font=head_font, fill=DIM)
        y += 24
        _divider(draw, y)
        y += 8

        for index in range(per_col):
            for col in range(2):
                pos = index + col * per_col
                if pos >= len(rows):
                    continue
                cx = PAD + col * (col_w + gap)
                if index % 2 == 0:
                    _rrect(draw, (cx - 6, y - 4, cx + col_w + 6, y + row_h - 10),
                           9, fill=PANEL)
                cells = list(rows[pos])[:4]
                while len(cells) < 4:
                    cells.append("")
                cx = PAD + col * (col_w + gap)
                time_color, _tb, time_text = _cell_parts(cells[0])
                node_color, node_bold, node_text = _cell_parts(cells[1])
                mission_color, mission_bold, mission_text = _cell_parts(cells[2])
                rating_color, _rb, rating_text_raw = _cell_parts(cells[3])
                if mission_color:
                    draw.rectangle([cx + 4, y + 4, cx + 7, y + row_h - 14],
                                   fill=mission_color)
                time_text = _fit(draw, time_text, time_font, 48)
                tw, _ = _tsize(draw, time_text, time_font)
                ty_time = y + _vcenter(draw, time_text, time_font, row_h - 6, -4)
                draw.text((cx + time_x + (48 - tw) // 2, ty_time), time_text,
                          font=time_font, fill=time_color or MUTED)
                node_text = _fit(draw, node_text, node_font, node_w)
                nw, _ = _tsize(draw, node_text, node_font)
                ty_node = y + _vcenter(draw, node_text, node_font, row_h - 6, -4)
                draw.text((cx + node_x + (node_w - nw) // 2, ty_node), node_text,
                          font=node_font, fill=node_color or TEXT)
                mission_text = _fit(draw, mission_text, mission_font, 66)
                mw, _ = _tsize(draw, mission_text, mission_font)
                ty_mission = y + _vcenter(draw, mission_text, mission_font, row_h - 6, -4)
                draw.text((cx + mission_x + (66 - mw) // 2, ty_mission), mission_text,
                          font=mission_font, fill=mission_color or TEXT)
                rating_text = _fit(draw, rating_text_raw, rating_font, 46)
                rw, _ = _tsize(draw, rating_text, rating_font)
                ty_rating = y + _vcenter(draw, rating_text, rating_font, row_h - 6, -4)
                draw.text((cx + col_w - 54 + (46 - rw) // 2, ty_rating), rating_text,
                          font=rating_font, fill=rating_color or MUTED)
            y += row_h
        y += 24

    if note:
        note_font = _font(16)
        note_text = _fit(draw, note, note_font, usable)
        nw, _ = _tsize(draw, note_text, note_font)
        draw.text(((WIDTH - nw) // 2, y + 4), note_text,
                  font=note_font, fill=MUTED)
        y += 34

    if buttons:
        _divider(draw, y + 8)
        y = _draw_buttons(draw, y, buttons)
        y += 14

    y = _footer(draw, y + 10, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def _encode(image: "Image.Image") -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------- 紫卡卡面
RIVEN_BG_TOP = (30, 16, 46)
RIVEN_BG_BOTTOM = (64, 31, 76)
RIVEN_GOLD = (206, 172, 96)
RIVEN_TEXT = (238, 232, 248)
RIVEN_NEG = (255, 126, 126)

# 极性图标：取自灰机 wiki「文本图标 · 极性」的官方卡面图标（白色透明底 PNG），
# 本地随包存放（bot/assets/polarity）。卡面只画图标不画文字，和游戏里一致。
POLARITY_FILES = {
    "madurai": "madurai.png",
    "naramon": "naramon.png",
    "vazarin": "vazarin.png",
    "zenurik": "zenurik.png",
    "unairu": "unairu.png",
    "penjaga": "penjaga.png",
    "precept": "penjaga.png",
    "umbra": "umbra.png",
    "universal": "universal.png",
    "any": "universal.png",
    "koneksi": "koneksi.png",
}
POLARITY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "assets", "polarity")
_POLARITY_CACHE: Dict[Tuple[str, int], object] = {}

_ICON_LOADER = None


def set_icon_loader(loader) -> None:
    """注入武器图标加载器（url -> bytes）；命令层负责网络与磁盘缓存。"""
    global _ICON_LOADER
    _ICON_LOADER = loader


def _icon_image(source: str, size: int = 64):
    if not source or _ICON_LOADER is None or Image is None:
        return None
    try:
        raw = _ICON_LOADER(source)
    except Exception:  # 图标是装饰，失败就当没有
        return None
    if not raw:
        return None
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        return None
    return image.resize((size, size), Image.LANCZOS)


def _platinum_glyph(draw, cx: float, cy: float, r: float,
                    color=(216, 202, 242)) -> None:
    """白金符号：菱形外边 + 中心圆点。"""
    points = [(cx, cy - r), (cx + r * 0.78, cy), (cx, cy + r), (cx - r * 0.78, cy)]
    try:
        draw.polygon(points, outline=color, width=2)
    except TypeError:
        # Pillow < 8 不支持 polygon(width=)，退回 polygon + line 描边。
        draw.polygon(points, outline=color)
        draw.line(points + [points[0]], fill=color, width=2)
    rr = max(1.0, r * 0.26)
    draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=color)


def _polarity_image(polarity: str, size: int,
                    color=(228, 214, 252)):
    """极性图标（白色透明底 PNG，按需着色 + 缩放），拿不到就返回 None。"""
    key = str(polarity or "").strip().lower()
    name = POLARITY_FILES.get(key)
    if not name or Image is None:
        return None
    cached = _POLARITY_CACHE.get((key, size))
    if cached is not None:
        return cached or None      # 缓存里存 False 表示「这个图标没有」
    try:
        icon = Image.open(os.path.join(POLARITY_DIR, name)).convert("RGBA")
    except Exception:  # 资源缺失就当没有，卡片走矢量兜底
        _POLARITY_CACHE[(key, size)] = False
        return None
    icon = icon.resize((size, size), Image.LANCZOS)
    tinted = Image.new("RGBA", icon.size, tuple(color) + (0,))
    tinted.putalpha(icon.getchannel("A"))
    _POLARITY_CACHE[(key, size)] = tinted
    return tinted


def _polarity_glyph(draw, cx: float, cy: float, r: float, polarity: str,
                    color=(222, 206, 250)) -> None:
    """极性徽章：外环 + 内部符号（按游戏里的五种极性简化绘制）。"""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=2)
    key = str(polarity or "").lower()
    if key == "madurai":
        draw.polygon([(cx - r * 0.44, cy - r * 0.48), (cx + r * 0.44, cy - r * 0.48),
                      (cx, cy)], fill=color)
        draw.polygon([(cx - r * 0.44, cy + r * 0.48), (cx + r * 0.44, cy + r * 0.48),
                      (cx, cy)], fill=color)
    elif key == "vazarin":
        draw.polygon([(cx, cy - r * 0.52), (cx + r * 0.46, cy),
                      (cx, cy + r * 0.52), (cx - r * 0.46, cy)], fill=color)
    elif key == "naramon":
        draw.polygon([(cx - r * 0.46, cy + r * 0.36), (cx, cy - r * 0.48),
                      (cx + r * 0.46, cy + r * 0.36)], outline=color, width=2)
    elif key == "zenurik":
        draw.arc([cx - r * 0.52, cy - r * 0.52, cx + r * 0.16, cy + r * 0.16],
                 10, 330, fill=color, width=2)
        draw.arc([cx - r * 0.16, cy - r * 0.16, cx + r * 0.52, cy + r * 0.52],
                 190, 150, fill=color, width=2)
    elif key == "unairu":
        draw.polygon([(cx - r * 0.46, cy - r * 0.38), (cx + r * 0.46, cy - r * 0.38),
                      (cx + r * 0.36, cy + r * 0.26), (cx, cy + r * 0.52),
                      (cx - r * 0.36, cy + r * 0.26)], outline=color, width=2)
    elif key == "penjaga":
        draw.arc([cx - r * 0.52, cy - r * 0.52, cx + r * 0.08, cy + r * 0.52],
                 300, 60, fill=color, width=2)
        draw.arc([cx - r * 0.08, cy - r * 0.52, cx + r * 0.52, cy + r * 0.52],
                 120, 240, fill=color, width=2)
    else:
        draw.line([(cx, cy - r * 0.46), (cx, cy + r * 0.46)], fill=color, width=2)
        draw.line([(cx - r * 0.46, cy), (cx + r * 0.46, cy)], fill=color, width=2)


def _riven_card(auction, width: int = 340, height: int = 470,
                weapon_name: str = "") -> "Image.Image":
    """一张游戏风紫卡：卡名 / 词条 / 极性 / 段位 / 等级点 / 洗数 + 卡下报价。"""
    info_h = 88
    canvas = Image.new("RGBA", (width, height + info_h), (0, 0, 0, 0))
    card = _gradient(width, height, RIVEN_BG_TOP, RIVEN_BG_BOTTOM).convert("RGBA")

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(0, height, 3):
        od.line([(0, y), (width, y)], fill=(0, 0, 0, 22))
    for i in range(16, 0, -1):
        od.ellipse([width / 2 - i * 15, 78 - i * 10, width / 2 + i * 15, 78 + i * 10],
                   fill=(170, 130, 226, 4))
    card = Image.alpha_composite(card, overlay)
    cd = ImageDraw.Draw(card)

    _rrect(cd, (0, 0, width - 1, height - 1), 16, outline=RIVEN_GOLD, width=3)
    _rrect(cd, (7, 7, width - 8, height - 8), 12, outline=(124, 92, 164), width=1)
    for cx, cy in ((18, 18), (width - 18, 18), (18, height - 18),
                   (width - 18, height - 18)):
        cd.polygon([(cx, cy - 7), (cx + 7, cy), (cx, cy + 7), (cx - 7, cy)],
                   fill=RIVEN_GOLD)

    name = str(getattr(auction, "name", "") or "").strip()
    # 游戏里紫卡名只首字母大写（laci-acricron → Laci-acricron）
    name = (name[0].upper() + name[1:]) if name else "Riven"
    title_font = _font(29, bold=True)
    name = _fit(cd, name, title_font, width - 56)
    tw, _ = _tsize(cd, name, title_font)
    cd.text(((width - tw) / 2, 30), name, font=title_font, fill=RIVEN_TEXT)
    small = _font(12, bold=True)
    lw, _ = _tsize(cd, "RIVEN MOD", small)
    cd.text(((width - lw) / 2, 70), "RIVEN MOD", font=small, fill=RIVEN_GOLD)

    cd.line([(34, 100), (width - 34, 100)], fill=(150, 122, 190), width=1)
    cd.polygon([(width / 2, 96), (width / 2 + 5, 100), (width / 2, 104),
                (width / 2 - 5, 100)], fill=RIVEN_GOLD)

    attrs = list(getattr(auction, "detail", None) or [])
    y = 120
    value_font = _font(23, bold=True)
    label_font = _font(18)
    for attr in attrs[:4]:
        color = RIVEN_TEXT if attr.positive else RIVEN_NEG
        cd.text((34, y), attr.text, font=value_font, fill=color)
        label = _fit(cd, attr.label, label_font, width - 160)
        cd.text((152, y + 4), label, font=label_font, fill=color)
        y += 35

    cd.line([(34, 296), (width - 34, 296)], fill=(104, 78, 138), width=1)

    # 极性槽：只画游戏里的极性图标（马杜莱/纳拉蒙…），不再写中文名
    py = 332
    icon = _polarity_image(getattr(auction, "polarity", ""), 52)
    if icon is not None:
        card.alpha_composite(icon, (30, py - 26))
    else:
        _polarity_glyph(cd, 56, py, 24, getattr(auction, "polarity", ""))

    mr = "MR %d" % (int(getattr(auction, "mastery_level", 0) or 0))
    mf = _font(18, bold=True)
    mw, _ = _tsize(cd, mr, mf)
    cd.text((width - 34 - mw, py - 18), mr, font=mf, fill=RIVEN_GOLD)
    rolls = "%d 洗" % (int(getattr(auction, "re_rolls", 0) or 0))
    rf = _font(13)
    rw, _ = _tsize(cd, rolls, rf)
    cd.text((width - 34 - rw, py + 8), rolls, font=rf, fill=(158, 142, 184))

    dots_y = height - 72
    rank = max(0, min(8, int(getattr(auction, "rank", 0) or 0)))
    total_w = 8 * 15 + 7 * 9
    x = (width - total_w) / 2
    for i in range(8):
        box = (x, dots_y, x + 15, dots_y + 15)
        if i < rank:
            _rrect(cd, box, 3, fill=(196, 168, 246), outline=(242, 230, 255))
        else:
            _rrect(cd, box, 3, fill=(46, 27, 66), outline=(112, 86, 152))
        x += 24
    rk = "RANK %d" % rank
    kf = _font(12, bold=True)
    kw, _ = _tsize(cd, rk, kf)
    cd.text(((width - kw) / 2, dots_y + 22), rk, font=kf, fill=RIVEN_GOLD)

    info = Image.new("RGBA", (width, info_h), (0, 0, 0, 0))
    idraw = ImageDraw.Draw(info)
    _rrect(idraw, (0, 8, width - 1, info_h - 1), 12, fill=(20, 13, 32, 240))
    label = weapon_name or getattr(auction, "weapon_name", "") or ""
    idraw.text((16, 16), _fit(idraw, label, _font(18, bold=True), width - 96),
               font=_font(18, bold=True), fill=RIVEN_GOLD)
    price = "%dp" % int(getattr(auction, "price", 0) or 0)
    pf = _font(25, bold=True)
    pw, _ = _tsize(idraw, price, pf)
    idraw.text((width - 44 - pw, 11), price, font=pf, fill=RIVEN_TEXT)
    _platinum_glyph(idraw, width - 26, 26, 8)
    status = str(getattr(auction, "status", "") or "")
    tag = {"ingame": "游戏中", "online": "在线"}.get(status, "离线")
    idraw.text((16, 48), "%s · %s" % (getattr(auction, "seller", "?") or "?", tag),
               font=_font(12), fill=(168, 154, 196))
    buys = "一口价" if getattr(auction, "buyout", True) else "起拍"
    bw, _ = _tsize(idraw, buys, _font(12))
    idraw.text((width - 18 - bw, 48), buys, font=_font(12), fill=(168, 154, 196))

    canvas.alpha_composite(card, (0, 0))
    canvas.alpha_composite(info, (0, height))
    return canvas


def riven_market_card(weapon, auctions, filters=(), note: str = "",
                      footer: str = "", status_counts=None) -> Optional[bytes]:
    """#wr：武器头部 + 最便宜几张紫卡（3 列网格，9 张刚好 3×3）。"""
    if Image is None or not auctions:
        return None
    cards = [_riven_card(item, weapon_name=getattr(weapon, "name", ""))
             for item in auctions]
    if not cards:
        return None
    cw, ch = cards[0].size
    count = len(cards)
    cols = 3 if count > 4 else (2 if count > 1 else 1)
    rows = (count + cols - 1) // cols
    gap_x, gap_y = 20, 18
    content_w = cols * cw + (cols - 1) * gap_x
    width = content_w + 2 * PAD
    left = (width - content_w) // 2

    filters = [str(item) for item in (filters or ()) if str(item)]
    header_h = 168 + (44 if filters else 0) + (34 if note else 0)
    height = header_h + rows * ch + (rows - 1) * gap_y + 76

    image = _gradient(width, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    inclination = float(getattr(weapon, "disposition", 0) or 0)
    if status_counts is None:
        online = sum(1 for item in auctions if getattr(item, "status", "") == "online")
        ingame = sum(1 for item in auctions if getattr(item, "status", "") == "ingame")
    else:
        online, ingame = status_counts
    subtitle = "倾向 %.2f（%s）｜ %s ｜ MR %d ｜ 在线 %d · 游戏中 %d ｜ 最便宜 %d 张" % (
        inclination, getattr(weapon, "disposition_label", ""),
        getattr(weapon, "type_label", "") or "—",
        int(getattr(weapon, "mastery_rank", 0) or 0), online, ingame, count)
    y = _header(draw, "%s · 紫卡市场" % getattr(weapon, "name", ""), subtitle,
                "WARFRAME MARKET", PURPLE, width=width)
    if filters:
        y = _pills(draw, y, [("词条", " + ".join(filters))], PURPLE, width=width)
    if note:
        draw.text((PAD, y), _fit(draw, note, _font(15), width - 2 * PAD),
                  font=_font(15), fill=GOLD)
        y += 34

    for index, card in enumerate(cards):
        row, col = divmod(index, cols)
        x = left + col * (cw + gap_x)
        top = header_h + row * (ch + gap_y)
        image.alpha_composite(card, (x, top))

    _footer(draw, height - 62, footer or "数据 warframe.market · 实时紫卡拍卖",
            width=width)
    return _encode(image)


def riven_rank_card(rows, subtitle: str = "在线/游戏中卖家 · 第 3~8 低价均价",
                    footer: str = "") -> Optional[bytes]:
    """紫卡排行：序号 + 武器图标 + 名称 + 价格（参考游戏内榜单样式）。"""
    if Image is None or not rows:
        return None
    width = 620
    row_h = 46
    top = 138
    bottom = 70
    height = top + len(rows) * row_h + bottom
    image = _gradient(width, height, (52, 38, 82), (96, 76, 138)).convert("RGBA")

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(18, 0, -1):
        od.ellipse([width / 2 - i * 26, 70 - i * 14, width / 2 + i * 26, 70 + i * 14],
                   fill=(190, 170, 235, 3))
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    title_font = _font(40, bold=True)
    draw.text((36, 34), "紫卡排行", font=title_font, fill=(250, 246, 255))
    sub_font = _font(14)
    sw, _ = _tsize(draw, subtitle, sub_font)
    draw.text((width - 36 - sw, 46), subtitle, font=sub_font, fill=(206, 194, 224))
    draw.line([(36, 106), (width - 36, 106)], fill=(178, 160, 214), width=1)

    for index, row in enumerate(rows, 1):
        rank, name, price, icon_url = row
        y = top + (index - 1) * row_h
        if index % 2 == 0:
            bar = Image.new("RGBA", (width - 56, row_h - 6), (255, 255, 255, 10))
            image.alpha_composite(bar, (28, y - 1))
        color = (255, 214, 130) if index <= 3 else (236, 228, 248)
        num_font = _font(23, bold=True)
        draw.text((44, y + 6), "%02d" % rank, font=num_font, fill=color)

        icon = _icon_image(icon_url, 34)
        if icon is not None:
            mask = Image.new("L", (34, 34), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, 33, 33], fill=255)
            image.paste(icon, (96, y + 3), mask)
        else:
            draw.ellipse([96, y + 3, 130, y + 37], fill=(70, 54, 100))

        name_font = _font(21, bold=index <= 3)
        draw.text((146, y + 9), _fit(draw, name, name_font, 330),
                  font=name_font, fill=(250, 246, 255))

        price_text = "%.1f" % price if isinstance(price, float) else "%d" % price
        pf = _font(21, bold=True)
        pw, _ = _tsize(draw, price_text, pf)
        draw.text((width - 62 - pw, y + 10), price_text, font=pf, fill=(252, 248, 255))
        _platinum_glyph(draw, width - 46, y + 21, 9, (222, 208, 246))

    draw.line([(36, height - 58), (width - 36, height - 58)],
              fill=(178, 160, 214), width=1)
    foot = footer or "数据 warframe.market · 在线卖家第 3~8 低均价"
    draw.text((36, height - 44), foot, font=_font(12), fill=(206, 194, 224))
    return _encode(image)


def riven_group_card(sections: Sequence[Tuple[str, object]],
                     footer: str = "数据 Warframe Wiki · 本地计算") -> Optional[bytes]:
    """紫卡分组范围卡：把「步枪 / 霰弹枪」等类别的范围表纵向拼成一张图。

    sections: [(类别中文名, RivenAnalysis)]
    """
    if Image is None or not sections:
        return None
    cards = []
    for label, analysis in sections:
        if analysis is None:
            continue
        analysis.name = label
        try:
            raw = riven_range_card(analysis, footer)
        except Exception:  # pragma: no cover - 渲染失败就跳过这张
            continue
        if raw:
            cards.append(raw)
    if not cards:
        return None
    if len(cards) == 1:
        return cards[0]

    images = []
    for raw in cards:
        try:
            images.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        except Exception:  # pragma: no cover
            pass
    if not images:
        return None
    if len(images) == 1:
        return cards[0]

    width = max(image.width for image in images)
    height = sum(image.height for image in images) + 12 * (len(images) - 1)
    canvas = Image.new("RGB", (width, height), BG_TOP)
    y = 0
    for image in images:
        canvas.paste(image, (0, y))
        y += image.height + 12
    return _encode(canvas)


def wiki_card(title: str, body: str, url: str, footer: str = "",
              chip: str = "WIKI") -> bytes:
    """维基词条卡：标题 + 自动换行正文 + 链接。"""
    height = 1900
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, "", chip, CYAN)
    y += 18

    body_font = _font(19)
    line_height = 38
    max_lines = 20
    lines: List[str] = []
    for paragraph in str(body or "").splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            if lines:
                lines.append("")
            continue
        # 中文逐字换行；英文按单词换行，避免把 abilities 拆成 abilit/ies。
        if " " in paragraph:
            current = ""
            for word in paragraph.split(" "):
                candidate = (current + " " + word).strip()
                if _tsize(draw, candidate, body_font)[0] <= WIDTH - 2 * PAD:
                    current = candidate
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)
        else:
            current = ""
            for char in paragraph:
                candidate = current + char
                if _tsize(draw, candidate, body_font)[0] <= WIDTH - 2 * PAD:
                    current = candidate
                else:
                    lines.append(current)
                    current = char
            if current:
                lines.append(current)
        lines.append("")

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _fit(draw, lines[-1] + "……", body_font, WIDTH - 2 * PAD)

    for line in lines:
        if not line:
            y += 12
            continue
        _ctext(draw, y, line, body_font, TEXT)
        y += line_height

    y += 16
    _divider(draw, y)
    y += 18
    url_font = _font(16)
    _ctext(draw, y, _fit(draw, "链接 " + url, url_font, WIDTH - 2 * PAD),
           url_font, CYAN)
    y += 36

    return _encode(image.crop((0, 0, WIDTH, min(height, _footer(draw, y, footer) + 10))))


def _help_groups(prefix: str):
    """帮助菜单分组结构：[(分组名, 强调色, [(命令, 说明), ...]), ...]。"""
    return [
        ("市场行情", GOLD, [
            ("%swm <名称> [平台]" % prefix, "物品详情 + 实时报价 + 15 天走势"),
            ("%swm mod <名称>" % prefix, "只查 MOD（等价于 %smod <名称>）" % prefix),
            ("%s价格 <名称> [平台]" % prefix, "只看价格、买卖单与行情"),
            ("%s战甲 / %s武器 / %smod" % (prefix, prefix, prefix), "按分类搜索"),
            ("%s拆件 <套装>" % prefix, "套装 vs 部件比价"),
            ("%s金/银/铜垃圾" % prefix, "垃圾地板价 + 一组价"),
            ("%s垃圾" % prefix, "金/银/铜垃圾行情总览"),
            ("%s战甲榜" % prefix, "Prime 战甲价格排行"),
        ]),
        ("紫卡与遗物", PURPLE, [
            ("%s紫卡 <武器>" % prefix, "倾向 + 词条范围（2 / 2+1 / 3 / 3+1）"),
            ("%swr <武器> [词条…]" % prefix,
             "在线/游戏中卖家；不写「负任意」= 只看无负面"),
            ("%s遗物 <编号|奖励名>" % prefix,
             "编号→奖励明细；奖励名→反查全部遗物（支持别名）"),
            ("%s紫卡" % prefix, "最贵零洗紫卡榜（高 → 低）"),
            ("%s紫卡分析 <组>" % prefix, "主武器 / 副武器 / 近战 / 空战 / 同伴"),
        ]),
        ("世界状态", CYAN, [
            ("%s轮换" % prefix, "言录使 / 格拉斯 / 埃莉诺 轮换商店"),
            ("%s轮换 言录使" % prefix, "本周 5 件（苦栓）"),
            ("%s轮换 格拉斯 / 埃莉诺" % prefix, "信条近战 / 终幕武器加成"),
            ("%s奸商 / %s突击 / %s仲裁" % (prefix, prefix, prefix), "奸商、突击、当前仲裁"),
            ("%s世界状态" % prefix, "完整世界状态总览"),
            ("%s警报 / %s入侵" % (prefix, prefix), "当前警报、入侵进度与双方奖励"),
            ("%s钢铁侵袭 / %s电波" % (prefix, prefix), "钢铁侵袭、午夜电波"),
            ("%s平原 / %s特惠" % (prefix, prefix), "平原时间、每日特惠"),
            ("%s新闻 / %s活动" % (prefix, prefix), "新闻、活动"),
            ("%s回廊 / %s执刑官" % (prefix, prefix), "本周灵化、执刑官猎杀"),
            ("%s裂缝 / %s钢铁裂缝" % (prefix, prefix), "普通裂缝、钢铁之路裂缝"),
            ("%s虚空风暴 / %s九重天" % (prefix, prefix), "航道星舰 风暴裂缝"),
            ("%s赏金 / %s平原赏金" % (prefix, prefix), "各大 NPC 可接赏金"),
            ("%s希图斯" % prefix, "希图斯时间 + 7档赏金 + 帐篷A/B/C"),
            ("%s圣所 / %s1999 / %s扎里曼" % (prefix, prefix, prefix),
             "三处固定档位赏金（几档 / 几级 / 给什么）"),
            ("%s深层科研 / %s时光科研" % (prefix, prefix), "深层科研、时光科研"),
            ("%s科研" % prefix, "两种科研总览"),
        ]),
        ("仲裁排期", GREEN, [
            ("%s仲裁评级" % prefix, "仲裁梯队（加「本地」看推算）"),
            ("%s今日仲裁 / %s高效仲裁" % (prefix, prefix), "未来 24 小时轮换、S/A+ 高价值仲裁"),
            ("%s仲裁表 [天数]" % prefix, "未来 7 天完整仲裁排期（底部含筛选按钮）"),
            ("%s谷拦仲裁 / %s镜防仲裁" % (prefix, prefix), "谷神星拦截、火星/火卫二镜像防御仲裁"),
        ]),
        ("订阅与工具", GOLD, [
            ("%s提醒 <名称> <价>" % prefix, "降价提醒"),
            ("%s提醒列表 / %s取消提醒" % (prefix, prefix), "管理降价提醒"),
            ("%s订阅 <类型> [筛选]" % prefix, "新内容出现就推送到群"),
            ("%s订阅列表 / %s取消订阅" % (prefix, prefix), "管理事件订阅"),
            ("%s更新 / %s数据状态" % (prefix, prefix), "刷新缓存、查看数据源"),
            ("%swiki <关键词>" % prefix, "英文维基"),
            ("%s翻译 <物品名>" % prefix, "中英双语对照"),
            ("%s帮助" % prefix, "显示本菜单"),
        ]),
    ]


# 帮助卡为横板，整卡尺寸固定；背景铺满整卡并压低透明度
HELP_W = 1680
HELP_H = 1160
HELP_BG_OPACITY = 0.40          # key art 不透明度，越低原图自带元素越淡
HELP_BG_BLUR = 7                # 轻微模糊，进一步弱化 Logo / 按钮文字
HELP_ROW_H = 50                 # 每行高度，留足两行文字的呼吸空间
HELP_SECTION_H = 54             # 分组标题区高度
HELP_GROUP_GAP = 30             # 分组之间的间距


def _help_canvas(background, width: int, height: int) -> "Image.Image":
    """横板帮助卡整卡背景：官方 key art 铺满 + 低透明度 + 轻微模糊 + 深色蒙版。

    原图自带的 WARFRAME 标题 / 按钮 / 副标题通过「降透明度 + 模糊 + 蒙版」
    三重处理退化为纯氛围底图，只保留画面色彩和构图。
    """
    canvas = _gradient(width, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    if background is not None:
        try:
            bw, bh = background.size
            scale = max(width / float(bw), height / float(bh))
            nw = max(width, int(bw * scale))
            nh = max(height, int(bh * scale))
            scaled = background.resize((nw, nh), Image.LANCZOS).convert("RGBA")
            left = (nw - width) // 2
            top = (nh - height) // 2
            hero = scaled.crop((left, top, left + width, top + height))
            if HELP_BG_BLUR > 0:
                try:
                    from PIL import ImageFilter
                    hero = hero.filter(ImageFilter.GaussianBlur(HELP_BG_BLUR))
                except Exception:
                    pass
            hero.putalpha(int(max(0.0, min(1.0, HELP_BG_OPACITY)) * 255))
            canvas = Image.alpha_composite(canvas, hero)
        except Exception:
            pass

    veil = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil, "RGBA")
    for y in range(height):
        ratio = y / max(1, height - 1)
        alpha = int(154 - 30 * ratio)
        vd.line([(0, y), (width, y)], fill=(8, 11, 18, alpha))
    return Image.alpha_composite(canvas, veil)


_HELP_COLUMN_OF = {
    "市场行情": 0,
    "紫卡与遗物": 0,
    "世界状态": 1,
    "仲裁排期": 2,
    "订阅与工具": 2,
}


def _help_columns(groups, ncols: int = 3):
    """把命令分组均衡分配到横板的三列，保证阅读顺序自然。"""
    columns = [[] for _ in range(ncols)]
    loads = [0] * ncols
    for group in groups:
        preferred = _HELP_COLUMN_OF.get(group[0])
        if preferred is None or preferred >= ncols:
            preferred = min(range(ncols), key=lambda i: (loads[i], i))
        columns[preferred].append(group)
        loads[preferred] += (HELP_SECTION_H + len(group[2]) * HELP_ROW_H
                             + HELP_GROUP_GAP)
    return columns


def _scene_canvas(background, height: int) -> "Image.Image":
    """游戏任务场景铺底 + 深色蒙版，保证表格文字清晰。"""
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    if background is None:
        return image
    try:
        bw, bh = background.size
        scale = max(WIDTH / float(bw), height / float(bh))
        nw, nh = max(WIDTH, int(bw * scale)), max(height, int(bh * scale))
        scaled = background.resize((nw, nh), Image.LANCZOS).convert("RGBA")
        left = (nw - WIDTH) // 2
        top = (nh - height) // 2
        image = scaled.crop((left, top, left + WIDTH, top + height))
    except Exception:
        return _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    veil = Image.new("RGBA", (WIDTH, height), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil, "RGBA")
    for y in range(height):
        alpha = int(168 - 28 * (y / max(1, height - 1)))
        vd.line([(0, y), (WIDTH, y)], fill=(8, 11, 18, alpha))
    return Image.alpha_composite(image, veil)


def _help_emblem(draw, cx: int, cy: int, color) -> None:
    """启动器中央的简化 Tenno 徽记（三层折线）。"""
    for index, (half, rise) in enumerate(((44, 34), (30, 24), (17, 14))):
        points = [(cx - half, cy - rise // 2), (cx, cy + rise),
                  (cx + half, cy - rise // 2), (cx + half - 9, cy - rise // 2 - 8),
                  (cx, cy + rise - 15), (cx - half + 9, cy - rise // 2 - 8)]
        shade = tuple(int(c * (0.9 - index * 0.08)) for c in color) + (230,)
        draw.polygon(points, fill=shade)


def help_card(prefix: str = "#", disabled=(), background=None,
              hero_title: str = "") -> bytes:
    """横板帮助菜单：官方 key art 低透明度铺满整卡，命令三列排布在其上。

    background: 官方 key art（可选），铺满整张卡片并降低不透明度；
    hero_title: 保留参数兼容调用方，画面上不再单独渲染。
    """
    groups = _help_groups(prefix)
    disabled = set(disabled or ())
    hidden = []
    if "wiki" in disabled:
        hidden.append(prefix + "wiki")
    if "riven_market" in disabled:
        hidden.append(prefix + "wr")
    if "relic" in disabled:
        hidden.append(prefix + "遗物")
    if "help" in disabled:
        hidden.append(prefix + "帮助")

    filtered = []
    for name, color, rows in groups:
        kept = [(cmd, desc) for cmd, desc in rows
                if not any(cmd.startswith(h) for h in hidden)]
        if kept:
            filtered.append((name, color, kept))

    columns = _help_columns(filtered, 3)
    canvas = _help_canvas(background, HELP_W, HELP_H)
    overlay = Image.new("RGBA", (HELP_W, HELP_H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    text_ops = []

    M = 56
    # ---- 顶部标题条 ----
    _rrect(od, (M, 30, HELP_W - M, 124), 14,
           fill=(9, 12, 19, 158), outline=GOLD + (110,), width=1)
    probe = ImageDraw.Draw(canvas)
    title_font = _font(31, bold=True)
    tw, _ = _tsize(probe, "命令一览", title_font)
    text_ops.append((((HELP_W - tw) // 2 - 18, 44), "命令一览", title_font, TEXT))
    sub_font = _font(17)
    sub_text = "命令可直接发送，不需要 # 前缀或 @ 机器人"
    sw, _ = _tsize(probe, sub_text, sub_font)
    text_ops.append((((HELP_W - sw) // 2, 88), sub_text, sub_font, MUTED))
    chip_text = "HELP"
    chip_font = _font(18, bold=True)
    probe = ImageDraw.Draw(canvas)
    cw, ch = _tsize(probe, chip_text, chip_font)
    _rrect(od, (HELP_W - M - cw - 32, 50, HELP_W - M, 104), 9,
           fill=(235, 188, 96, 42), outline=GOLD + (200,), width=1)
    text_ops.append(((HELP_W - M - cw - 16, 57), chip_text, chip_font, GOLD))

    # ---- 三列命令面板 ----
    GAP = 28
    col_w = (HELP_W - 2 * M - 2 * GAP) // 3
    BODY_Y = 152
    SECTION_H = HELP_SECTION_H
    ROW_H = HELP_ROW_H
    GROUP_GAP = HELP_GROUP_GAP
    cmd_font = _font(21, bold=True)
    desc_font = _font(17)
    section_font = _font(24, bold=True)

    for ci, column in enumerate(columns):
        x = M + ci * (col_w + GAP)
        y = BODY_Y
        for name, color, rows in column:
            panel_bottom = y + SECTION_H + len(rows) * ROW_H + 16
            _rrect(od, (x - 20, y - 16, x + col_w + 20, panel_bottom), 14,
                   fill=(9, 12, 19, 148), outline=LINE + (125,), width=1)
            col_cx = x + col_w / 2.0
            od.rectangle([x + 10, y + 4, x + 16, y + 36], fill=color)
            nw, _ = _tsize(probe, name, section_font)
            text_ops.append(((int(round(col_cx - nw / 2.0)), y - 2),
                             name, section_font, color))
            left_line = int(round(col_cx - nw / 2.0)) + nw + 12
            od.line([(left_line, y + 19), (x + col_w - 6, y + 19)],
                    fill=tuple(int(c * 0.45) for c in color) + (180,), width=1)
            y += SECTION_H
            for index, (cmd, desc) in enumerate(rows):
                if index % 2 == 0:
                    _rrect(od, (x + 2, y - 4, x + col_w - 2, y + ROW_H - 8), 8,
                           fill=(28, 36, 50, 128))
                cmd_text = _fit(probe, cmd, cmd_font, col_w - 28)
                cw2, _ = _tsize(probe, cmd_text, cmd_font)
                text_ops.append(((int(round(col_cx - cw2 / 2.0)), y - 2),
                                 cmd_text, cmd_font, GOLD))
                desc_text = _fit(probe, desc, desc_font, col_w - 28)
                dw, _ = _tsize(probe, desc_text, desc_font)
                text_ops.append(((int(round(col_cx - dw / 2.0)), y + 25),
                                 desc_text, desc_font, TEXT))
                y += ROW_H
            y += GROUP_GAP

    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    for (x, y), text, font, fill in text_ops:
        draw.text((x, y), text, font=font, fill=fill)

    footer_font = _font(16)
    draw.line([(M, HELP_H - 54), (HELP_W - M, HELP_H - 54)],
              fill=(120, 128, 140, 170), width=1)
    footer_text = "平台：pc / ps / xbox / switch / mobile，默认 PC  ·  QQ Warframe Bot"
    footer_text = _fit(draw, footer_text, footer_font, HELP_W - 2 * M)
    _ctext(draw, HELP_H - 44, footer_text, footer_font, DIM, width=HELP_W)

    return _encode(canvas)

# ---------------------------------------------------------------------- 科研卡
def _tag_chip(draw, x: int, y: int, text: str, color) -> int:
    font = _font(13, bold=True)
    width, height = _tsize(draw, text, font)
    _rrect(draw, (x, y, x + width + 16, y + height + 10), 6,
           fill=PANEL_LIGHT, outline=color)
    draw.text((x + 8, y + 4), text, font=font, fill=color)
    return width + 16


def _research_rows(draw, mission, right: int):
    """把一个任务块排版成 (标签, 名称, 说明行, 名称起点x, 标签宽度)。"""
    name_font = _font(19, bold=True)
    desc_font = _font(17)
    chip_font = _font(15, bold=True)
    label_x = PAD + 74
    rows = []
    deviation = mission.get("deviation") or {}
    raw = [("偏差", GOLD, deviation.get("name") or "—",
            deviation.get("desc") or "")]
    for risk in mission.get("risks") or []:
        raw.append(("风险", RED, risk.get("name") or "—",
                    risk.get("desc") or ""))
    for label, color, name, desc in raw:
        chip_width = _tsize(draw, label, chip_font)[0] + 16
        name_x = label_x + chip_width + 12
        name_text = _fit(draw, name, name_font, right - name_x)
        lines = (_wrap(draw, desc, desc_font, right - name_x, max_lines=3)
                 if desc else [])
        rows.append((label, color, name_text, lines, chip_width))
    return rows


def research_card(title: str, subtitle: str, sections: Sequence[Dict],
                  footer: str, accent=PURPLE, chip: str = "",
                  note: str = "") -> bytes:
    """科研卡：任务编号 + 偏差/风险名称与说明 + 全局变量。"""
    height = 3400
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, subtitle, chip, accent)
    y += 6

    right = WIDTH - PAD - 18
    name_font = _font(22, bold=True)
    desc_font = _font(17)
    num_font = _font(19, bold=True)

    for section in sections or []:
        label = str(section.get("label") or "")
        remaining = str(section.get("remaining") or "")
        if label and label != title:
            lf = _font(22, bold=True)
            lw, _ = _tsize(draw, label, lf)
            lx = (WIDTH - lw) // 2
            draw.rectangle([lx - 14, y + 5, lx - 9, y + 29], fill=accent)
            draw.text((lx, y), label, font=lf, fill=TEXT)
            if remaining:
                chip_width, _ = _tsize(draw, remaining, _font(16, bold=True))
                _chip(draw, WIDTH - PAD - chip_width - 26, y - 2, remaining, accent)
            y += 44
            _divider(draw, y - 10)

        for mission in section.get("missions") or []:
            rows = _research_rows(draw, mission, right)
            block_height = 40 + 10
            for _label, _color, _name, lines, _width in rows:
                block_height += 28 + len(lines) * 26 + 8
            block_height += 14
            _rrect(draw, (PAD, y, WIDTH - PAD, y + block_height), 12,
                   fill=PANEL)
            draw.rectangle([PAD, y + 12, PAD + 5, y + block_height - 12],
                           fill=accent)
            # 编号圆 + 任务名
            cx = PAD + 34
            draw.ellipse([cx - 15, y + 12, cx + 15, y + 42], outline=accent, width=2)
            number = str(mission.get("index") or "")
            number_w, number_h = _tsize(draw, number, num_font)
            draw.text((cx - number_w / 2, y + 27 - number_h / 2 - 2), number,
                      font=num_font, fill=accent)
            mission_name = _fit(draw, str(mission.get("name") or "—"), name_font,
                                right - (PAD + 58))
            draw.text((PAD + 58, y + 15), mission_name, font=name_font, fill=TEXT)

            row_y = y + 62
            for label_text, color, name_text, lines, chip_width in rows:
                _tag_chip(draw, PAD + 74, row_y, label_text, color)
                name_x = PAD + 74 + chip_width + 14
                draw.text((name_x, row_y + 3), name_text,
                          font=_font(19, bold=True), fill=TEXT)
                line_y = row_y + 32
                for line in lines:
                    draw.text((name_x, line_y), line, font=desc_font, fill=MUTED)
                    line_y += 26
                row_y += 28 + len(lines) * 26 + 8
            y += block_height + 12

        variables = section.get("variables") or []
        if variables:
            vf = _font(21, bold=True)
            vw, _ = _tsize(draw, "全局变量", vf)
            draw.text(((WIDTH - vw) // 2, y + 4), "全局变量",
                      font=vf, fill=TEXT)
            y += 38
            for item in variables:
                name_text = str(item.get("name") or "—")
                desc = str(item.get("desc") or "")
                draw.ellipse([PAD + 6, y + 10, PAD + 13, y + 17], fill=accent)
                draw.text((PAD + 24, y), name_text, font=_font(18, bold=True),
                          fill=TEXT)
                name_width, _ = _tsize(draw, name_text, _font(18, bold=True))
                text_x = PAD + 24 + name_width + 12
                lines = _wrap(draw, desc, desc_font, right - text_x,
                              max_lines=2) if desc else []
                if not lines:
                    y += 26
                    continue
                draw.text((text_x, y + 2), lines[0], font=desc_font, fill=MUTED)
                for extra in lines[1:]:
                    y += 26
                    draw.text((text_x, y + 2), extra, font=desc_font, fill=MUTED)
                y += 30
            y += 8

    if note:
        nf = _font(16)
        nt = _fit(draw, note, nf, WIDTH - 2 * PAD)
        nw, _ = _tsize(draw, nt, nf)
        draw.text(((WIDTH - nw) // 2, y + 4), nt, font=nf, fill=MUTED)
        y += 34
    y = _footer(draw, y + 10, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def build_table(title: str, subtitle: str, headers: Sequence[str],
                rows: Sequence[Sequence[str]], footer: str, accent=GOLD,
                chip: str = "", note: str = "",
                highlights: Optional[Sequence[Sequence[Optional[tuple]]]] = None,
                bold_last_row: bool = False,
                ratios: Optional[Sequence[float]] = None,
                aligns: Optional[Sequence[str]] = None,
                background=None) -> bytes:
    """简化接口：rows 为纯字符串，highlights 可指定每格颜色。"""
    if ratios:
        column_ratios = list(ratios)
    else:
        column_ratios = [max(1.0, 6.0 / len(headers))] * len(headers)
    cells_rows = []
    for row_index, row in enumerate(rows):
        cells = []
        for index, text in enumerate(row):
            color = None
            bold = False
            if highlights and row_index < len(highlights):
                color = highlights[row_index][index]
            if bold_last_row and row_index == len(rows) - 1:
                bold = True
            cells.append(Cell(text, color=color, bold=bold))
        cells_rows.append(cells)
    return table_card(title, subtitle, list(zip(headers, column_ratios)), cells_rows,
                      footer, accent=accent, chip=chip, note=note, aligns=aligns,
                      background=background)


def bounty_board(title: str, subtitle: str, groups: Sequence[Dict],
                 footer: str, accent=GREEN, chip: str = "",
                 note: str = "") -> bytes:
    """赏金一览：按据点分组，每档一行任务 + 一行奖励（奖励可换两行）。

    groups: [{"label": 据点标题, "note": 右侧小字,
              "rows": [{"badge": "赏金三", "task": "歼灭", "meta": "L50–55 · 剩 1 小时",
                        "rewards": ["...", "..."]}]}]
    """
    groups = list(groups or [])
    probe = Image.new("RGB", (WIDTH, 8))
    pdraw = ImageDraw.Draw(probe)
    task_font = _font(18, bold=True)
    small_font = _font(16)
    meta_font = _font(15)
    badge_font = _font(15, bold=True)

    inner_left = PAD + 10
    inner_right = WIDTH - PAD

    layout = []
    for group in groups:
        rows = []
        for row in group.get("rows") or []:
            badge = str(row.get("badge") or "")
            task = str(row.get("task") or "")
            meta = str(row.get("meta") or "")
            rewards = [str(item) for item in (row.get("rewards") or []) if str(item)]
            badge_w = (_tsize(pdraw, badge, badge_font)[0] + 20) if badge else 0
            task_x = inner_left + badge_w + (10 if badge_w else 0)
            meta_w = _tsize(pdraw, meta, meta_font)[0] if meta else 0
            task_max = max(90, inner_right - meta_w - 16 - task_x)
            tasks = _wrap(pdraw, task, task_font, task_max, max_lines=1) or [""]
            lines = _wrap_items(pdraw, rewards, small_font,
                                inner_right - inner_left, max_lines=12)
            rows.append({
                "badge": badge, "badge_w": badge_w, "task_x": task_x,
                "tasks": tasks, "meta": meta, "meta_w": meta_w, "lines": lines,
            })
        layout.append((group, rows))

    # 先按「最常见的两行奖励」估高，画完再裁
    estimate = 280 + 6 * 48
    for group, rows in layout:
        estimate += 46 if group.get("label") else 0
        estimate += sum(36 + len(row["lines"]) * 25 + 16 for row in rows)
    estimate += 60 if note else 0
    image = _gradient(WIDTH, max(760, estimate), BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, subtitle, chip, accent) + 12

    reward_color = (141, 196, 224)
    for group, rows in layout:
        label = str(group.get("label") or "")
        if label:
            lf = _font(21, bold=True)
            lw, _ = _tsize(draw, label, lf)
            lx = (WIDTH - lw) // 2
            draw.rectangle([lx - 14, y + 6, lx - 9, y + 30], fill=accent)
            draw.text((lx, y + 2), label, font=lf, fill=TEXT)
            right_note = str(group.get("note") or "")
            if right_note:
                width, _ = _tsize(draw, right_note, _font(15))
                draw.text((WIDTH - PAD - width, y + 10), right_note,
                          font=_font(15), fill=DIM)
            y += 42
        for index, row in enumerate(rows):
            row_top = y
            badge = row["badge"]
            if badge:
                box = (inner_left, y + 2, inner_left + row["badge_w"], y + 32)
                _rrect(draw, box, 14, fill=PANEL_LIGHT, outline=accent)
                text_w, text_h = _tsize(draw, badge, badge_font)
                draw.text((inner_left + (row["badge_w"] - text_w) / 2,
                           y + 2 + (30 - text_h) / 2), badge,
                          font=badge_font, fill=accent)
            for line in row["tasks"]:
                draw.text((row["task_x"], y + 4), line, font=task_font, fill=TEXT)
            if row["meta"]:
                draw.text((WIDTH - PAD - row["meta_w"], y + 9), row["meta"],
                          font=meta_font, fill=MUTED)
            y += 36
            for line in row["lines"]:
                draw.text((inner_left, y), line, font=small_font, fill=reward_color)
                y += 25
            y += 16
            if index < len(rows) - 1:
                draw.line([(inner_left, y - 8), (WIDTH - PAD, y - 8)], fill=LINE, width=1)
        y += 10

    if note:
        nf = _font(16)
        nt = _fit(draw, note, nf, WIDTH - 2 * PAD)
        nw, _ = _tsize(draw, nt, nf)
        draw.text(((WIDTH - nw) // 2, y + 4), nt, font=nf, fill=MUTED)
        y += 34

    y = _footer(draw, y + 10, footer)
    return _encode(image.crop((0, 0, WIDTH, min(estimate, y + 10))))


# ---------------------------------------------------------------------- 警报 / 入侵
PURPLE_TOP = (39, 25, 61)
PURPLE_BOTTOM = (117, 75, 161)
PURPLE_CARD = (78, 52, 108)
PURPLE_BORDER = (157, 124, 196)
PURPLE_TEXT = (244, 238, 252)
PURPLE_MUTED = (203, 184, 224)
PURPLE_GOLD = (244, 202, 117)


def _purple_pill(draw, x: int, y: int, text: str, bg, fg,
                 font_size: int = 14) -> Tuple[int, int]:
    font = _font(font_size, bold=True)
    width, height = _tsize(draw, text, font)
    box = (x, y, x + width + 18, y + height + 10)
    _rrect(draw, box, (height + 10) // 2, fill=bg, outline=None)
    draw.text((x + 9, y + 5), text, font=font, fill=fg)
    return box[2] - box[0], box[3] - box[1]


def _purple_right(draw, text: str, right: int, y: int, font, fill) -> None:
    width, _ = _tsize(draw, text, font)
    draw.text((right - width, y), text, font=font, fill=fill)


def _purple_footer(draw, y: int, left: str,
                   right: str = "QQ Warframe Bot", width: int = WIDTH) -> int:
    """紫色卡的页脚：先加深色底衬，保证页脚文字在亮紫渐变上清晰。"""
    band_top = y - 6
    draw.rectangle([0, band_top, width, y + 50], fill=(24, 16, 38))
    draw.line([(PAD, y), (width - PAD, y)], fill=(96, 74, 122), width=1)
    font = _font(14)
    text = "  ·  ".join(part for part in (left, right) if part)
    text = _fit(draw, text, font, width - 2 * PAD)
    _ctext(draw, y + 14, text, font, (196, 182, 214), width=width)
    return y + 52


def _purple_percent(value: float) -> str:
    text = "%.1f" % float(value or 0)
    if text.endswith(".0"):
        text = text[:-2]
    return text + "%"


def alerts_card(items: Sequence[Dict],
                footer: str = "数据 Warframe 世界状态 · 实时") -> bytes:
    """警报卡片：任务、等级、派系、节点、奖励、剩余时间。"""
    count = max(1, len(items))
    row_h = 138
    height = 140 + count * row_h + 80
    image = _gradient(WIDTH, height, PURPLE_TOP, PURPLE_BOTTOM)
    draw = ImageDraw.Draw(image)
    _ctext(draw, 30, "警报信息", _font(40, bold=True), PURPLE_TEXT)
    _ctext(draw, 88, "世界状态实时警报", _font(18), PURPLE_MUTED)
    y = 128
    if not items:
        _ctext(draw, y + 28, "当前没有活跃警报", _font(22), PURPLE_MUTED)
    for item in items:
        box = (26, y, WIDTH - 26, y + row_h - 12)
        _rrect(draw, box, 16, fill=PURPLE_CARD, outline=PURPLE_BORDER, width=1)
        mission = str(item.get("mission") or "未知任务")
        draw.text((48, y + 16), mission, font=_font(26, bold=True),
                  fill=PURPLE_GOLD)
        mission_w, _ = _tsize(draw, mission, _font(26, bold=True))
        chip_x = 48 + mission_w + 14
        level = str(item.get("level") or "")
        if level:
            w, _ = _purple_pill(draw, chip_x, y + 20, level,
                                (104, 83, 133), PURPLE_TEXT, font_size=15)
            chip_x += w + 10
        faction = str(item.get("faction") or "")
        if faction:
            _purple_pill(draw, chip_x, y + 20, faction,
                         (112, 82, 119), PURPLE_TEXT, font_size=15)
        node = _fit(draw, str(item.get("node") or "—"), _font(19), 330)
        _purple_right(draw, node, WIDTH - 48, y + 24, _font(19), PURPLE_TEXT)
        reward = _fit(draw, str(item.get("reward") or "—"),
                      _font(18), WIDTH - 96)
        _ctext(draw, y + 66, reward, _font(18), PURPLE_MUTED)
        _ctext(draw, y + 100,
               "剩余时间：" + str(item.get("remaining") or "—"),
               _font(17), PURPLE_GOLD)
        y += row_h
    y = _purple_footer(draw, y + 8, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def invasions_card(items: Sequence[Dict],
                   footer: str = "数据 Warframe 世界状态 · 实时") -> bytes:
    """入侵卡片：星球/节点、进度条、攻防双方奖励。"""
    count = max(1, len(items))
    row_h = 156
    height = 144 + count * row_h + 80
    image = _gradient(WIDTH, height, PURPLE_TOP, PURPLE_BOTTOM)
    draw = ImageDraw.Draw(image)
    _ctext(draw, 30, "入侵任务", _font(40, bold=True), PURPLE_TEXT)
    _ctext(draw, 88, "使用命令 #订阅 可蹲守物品 · 部分信息会先于游戏出现",
           _font(17), PURPLE_MUTED)
    y = 132
    if not items:
        _ctext(draw, y + 28, "当前没有入侵", _font(22), PURPLE_MUTED)
    for item in items:
        box = (26, y, WIDTH - 26, y + row_h - 12)
        _rrect(draw, box, 16, fill=PURPLE_CARD, outline=PURPLE_BORDER, width=1)
        label = _fit(draw, str(item.get("label") or "—"),
                     _font(24, bold=True), 540)
        draw.text((48, y + 14), label, font=_font(24, bold=True),
                  fill=PURPLE_TEXT)
        completion = float(item.get("completion") or 0)
        _purple_right(draw, _purple_percent(completion), WIDTH - 48, y + 16,
                      _font(23, bold=True), PURPLE_GOLD)
        bar_x, bar_y, bar_w, bar_h = 48, y + 56, WIDTH - 96, 8
        _rrect(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), 4,
               fill=(62, 43, 87))
        filled = max(0, min(bar_w, int(bar_w * completion / 100.0)))
        if filled:
            _rrect(draw, (bar_x, bar_y, bar_x + filled, bar_y + bar_h), 4,
                   fill=PURPLE_GOLD)
        left_faction = str(item.get("left_faction") or "")
        right_faction = str(item.get("right_faction") or "")
        left_x = 48
        if left_faction:
            w, _ = _purple_pill(draw, left_x, y + 82, left_faction,
                                (104, 83, 133), PURPLE_TEXT, font_size=16)
            left_x += w + 12
        left_reward = _fit(draw, str(item.get("left_reward") or "—"),
                           _font(17), 300)
        draw.text((left_x, y + 88), left_reward, font=_font(17),
                  fill=PURPLE_MUTED)
        right_x = WIDTH - 48
        if right_faction:
            fw, _ = _tsize(draw, right_faction, _font(16, bold=True))
            right_x -= fw + 20
            _purple_pill(draw, right_x, y + 82, right_faction,
                         (112, 82, 119), PURPLE_TEXT, font_size=16)
            right_x -= 12
        right_reward = _fit(draw, str(item.get("right_reward") or "—"),
                            _font(17), max(120, right_x - 360))
        rw, _ = _tsize(draw, right_reward, _font(17))
        draw.text((right_x - rw, y + 88), right_reward, font=_font(17),
                  fill=PURPLE_MUTED)
        y += row_h
    y = _purple_footer(draw, y + 8, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def relic_card(relic, footer: str = "数据 warframestat · warframe.market") -> bytes:
    """遗物：名称 + 出入库 + 精炼 / 奖励 / 概率 / 杜卡德 / WM 均价。"""
    rewards = list(getattr(relic, "rewards", ()) or ())
    count = max(1, len(rewards))
    row_h = 116
    locations = list(getattr(relic, "locations", ()) or ())
    height = 170 + count * row_h + 80 + (28 if locations else 0)
    image = _gradient(WIDTH, height, PURPLE_TOP, PURPLE_BOTTOM)
    draw = ImageDraw.Draw(image)
    title = getattr(relic, "title", "遗物")
    _ctext(draw, 30, title, _font(42, bold=True), PURPLE_TEXT)
    badge = str(getattr(relic, "vault_label", ""))
    if badge:
        bw, _ = _tsize(draw, badge, _font(16, bold=True))
        _purple_pill(draw, WIDTH - PAD - bw - 20, 42, badge,
                     (112, 82, 119), PURPLE_GOLD, font_size=16)
    refinement = str(getattr(relic, "refinement_label", "完整"))
    code = str(getattr(relic, "refinement", "Intact"))
    _ctext(draw, 94, "精炼：%s（%s） · WM 在线中/游戏中卖单最低 / 最高价" % (
        refinement, code), _font(17), PURPLE_MUTED)
    if locations:
        _ctext(draw, 122, "获取：%s" % _fit(
            draw, "、".join(locations[:8]), _font(14), WIDTH - 2 * PAD),
            _font(14), PURPLE_MUTED)
    y = 150 if locations else 144
    for reward in rewards:
        name = str(getattr(reward, "market_name", "")
                   or getattr(reward, "name", "") or "—")
        name = _fit(draw, name, _font(21, bold=True), 470)
        draw.text((48, y + 12), name, font=_font(21, bold=True),
                  fill=PURPLE_TEXT)
        rarity = str(getattr(reward, "rarity_label", "") or "")
        chance = float(getattr(reward, "chance", 0) or 0)
        chip_x = 48
        if rarity:
            chip_w, _ = _purple_pill(draw, chip_x, y + 48, rarity,
                                     (104, 83, 133), PURPLE_TEXT,
                                     font_size=14)
            chip_x += chip_w + 12
        if chance:
            draw.text((chip_x, y + 52), "概率 %.2f%%" % chance,
                      font=_font(14), fill=PURPLE_MUTED)
        ducats = int(getattr(reward, "ducats", 0) or 0)
        draw.text((WIDTH - 360, y + 18), "%d 杜卡德" % ducats,
                  font=_font(18, bold=True), fill=PURPLE_GOLD)
        price_low = float(getattr(reward, "price_low", 0) or 0)
        price_high = float(getattr(reward, "price_high", 0) or 0)
        draw.text((WIDTH - 246, y + 12), "最低 %.2fp" % price_low,
                  font=_font(17), fill=PURPLE_MUTED)
        draw.text((WIDTH - 246, y + 44), "最高 %.2fp" % price_high,
                  font=_font(17), fill=PURPLE_MUTED)
        _divider(draw, y + row_h - 14, 34, WIDTH - 34)
        y += row_h
    if not rewards:
        _ctext(draw, y + 18, "这个遗物没有可读取的奖励数据。",
               _font(18), PURPLE_MUTED)
    y = _purple_footer(draw, y + 8, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def relic_reverse_card(query: str, target: str, rows,
                       footer: str = "本地遗物库 · 别名表自动同步") -> bytes:
    """奖励反查清单：输入奖励/部件名 → 列出可获得它的全部遗物。

    ``rows`` 为 [(relic, [命中的 RelicReward, ...]), ...]。
    """
    rows = list(rows or ())
    row_h = 104
    head_h = 148
    height = head_h + len(rows) * row_h + 80
    image = _gradient(WIDTH, height, PURPLE_TOP, PURPLE_BOTTOM)
    draw = ImageDraw.Draw(image)
    _ctext(draw, 28, "奖励反查", _font(40, bold=True), PURPLE_TEXT)
    sub = _fit(draw, "%s  →  %s" % (query, target), _font(20), WIDTH - 2 * PAD)
    _ctext(draw, 86, sub, _font(20), PURPLE_GOLD)
    _ctext(draw, 118, "共 %d 个遗物可获得" % len(rows),
           _font(17), PURPLE_MUTED)
    y = head_h
    for index, (relic, rewards) in enumerate(rows, 1):
        title = str(getattr(relic, "title", "遗物"))
        vault = str(getattr(relic, "vault_label", ""))
        draw.text((PAD, y + 8), "%d. %s" % (index, title),
                  font=_font(22, bold=True), fill=PURPLE_TEXT)
        if vault:
            _purple_right(draw, vault, WIDTH - PAD, y + 12,
                          _font(15, bold=True), PURPLE_GOLD)
        parts = []
        for reward in rewards:
            name = str(getattr(reward, "market_name", "")
                       or getattr(reward, "name", "") or "—")
            rarity = str(getattr(reward, "rarity_label", "") or "")
            chance = float(getattr(reward, "chance", 0) or 0)
            parts.append("%s (%s %.2f%%)" % (name, rarity, chance) if rarity
                         else "%s (%.2f%%)" % (name, chance))
        detail = _fit(draw, "、".join(parts), _font(15), WIDTH - 2 * PAD - 20)
        draw.text((PAD + 18, y + 48), detail, font=_font(15), fill=PURPLE_MUTED)
        _divider(draw, y + row_h - 12, PAD, WIDTH - PAD)
        y += row_h
    y = _purple_footer(draw, y + 8, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


if __name__ == "__main__":  # 本地预览：python -m bot.imagecard
    import json
    import sys

    from .market import MarketClient, MarketStats, Order, StatSummary

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "tests", "fixtures", "item_mesa.json"), encoding="utf-8") as fh:
        raw = json.load(fh)["data"]
    client = MarketClient({"base_url": "http://x", "language": "zh-hans",
                           "platform": "pc", "cache_file": "", "alias_file": ""})
    item = client._parse_short(raw)
    item.description = raw["i18n"]["zh-hans"]["description"]
    sell = [Order("sell", 60, 12, "adadaAS", "ingame"),
            Order("sell", 63, 11, "Novaky_Nov", "ingame"),
            Order("sell", 65, 1, "Ga9er", "online")]
    buy = [Order("buy", 50, 25, "Tayfun0606", "ingame"),
           Order("buy", 45, 1, "cobaka00000", "ingame")]
    stats = MarketStats(h48=StatSummary(412, 61.8, 62, 55, 70),
                        d90=StatSummary(13404, 71.5, 70, 55, 89))
    png = item_card(item, sell, buy, stats, "PC")
    out = os.path.join(root, "preview_item.png")
    with open(out, "wb") as fh:
        fh.write(png)
    print("wrote", out, len(png), "bytes")
