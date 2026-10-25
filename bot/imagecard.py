"""图片卡片渲染（Pillow）。

设计目标：
- 深色 Warframe 风格，信息密度高，适合手机上看
- 兼容 Pillow 7.2（Windows 自带环境）到最新版
- 找不到中文字体或 Pillow 时自动降级为文字（由上层决定）

所有卡片宽度固定 860px，高度按内容自适应。
"""

from __future__ import annotations

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


def _scan_font_dirs(names: Sequence[str], limit: int = 8) -> List[str]:
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
            for name in names:
                if name in filenames:
                    found.append(os.path.join(dirpath, name))
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
        for family in ("Noto Sans CJK SC", "Source Han Sans SC",
                       "WenQuanYi Micro Hei", "Microsoft YaHei"):
            path = _fc_match(family)
            if path:
                bold = path.replace("Regular", "Bold").replace("-Regular", "-Bold")
                candidates.append((path, bold if os.path.isfile(bold) else path))

        names = [
            "NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc",
            "NotoSansCJKsc-Regular.otf", "NotoSansCJKsc-Bold.otf",
            "SourceHanSansSC-Regular.otf", "SourceHanSansSC-Bold.otf",
            "wqy-microhei.ttc", "wqy-zenhei.ttc", "DroidSansFallbackFull.ttf",
        ]
        scanned = _scan_font_dirs(names)
        regulars = [path for path in scanned if "bold" not in path.lower()]
        bolds = [path for path in scanned if "bold" in path.lower()]
        for path in regulars:
            bold = bolds[0] if bolds else path
            candidates.append((path, bold))

    candidates.extend([
        ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
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


def _chip(draw, x: int, y: int, text: str, color, font_size: int = 15) -> Tuple[int, int]:
    font = _font(font_size, bold=True)
    width, height = _tsize(draw, text, font)
    box = (x, y, x + width + 22, y + height + 14)
    _rrect(draw, box, (height + 14) // 2, fill=PANEL_LIGHT, outline=color)
    draw.text((x + 11, y + 7), text, font=font, fill=color)
    return box[2] - box[0], box[3] - box[1]


def _footer(draw, y: int, left: str, right: str = "QQ Warframe Bot") -> int:
    _divider(draw, y)
    font = _font(13)
    draw.text((PAD, y + 12), left, font=font, fill=DIM)
    width, _ = _tsize(draw, right, font)
    draw.text((WIDTH - PAD - width, y + 12), right, font=font, fill=DIM)
    return y + 40


def _header(draw, title: str, subtitle: str, chip: str, accent, top: int = 28) -> int:
    draw.rectangle([0, 0, WIDTH, 4], fill=accent)
    y = top
    title_font = _font(32, bold=True)
    max_width = WIDTH - 2 * PAD - 150
    lines = _wrap(draw, title, title_font, max_width, max_lines=2)
    for line in lines:
        draw.text((PAD, y), line, font=title_font, fill=TEXT)
        y += 42
    if chip:
        width, _ = _tsize(draw, chip, _font(15, bold=True))
        _chip(draw, WIDTH - PAD - width - 22, top + 2, chip, accent)
    if subtitle:
        draw.text((PAD, y + 2), _fit(draw, subtitle, _font(16), WIDTH - 2 * PAD),
                  font=_font(16), fill=MUTED)
        y += 26
    return y


def _pills(draw, y: int, items: Sequence[Tuple[str, str]], accent) -> int:
    x = PAD
    for label, value in items:
        text = "%s %s" % (label, value)
        font = _font(14, bold=True)
        width, height = _tsize(draw, text, font)
        if x + width + 26 > WIDTH - PAD:
            y += 40
            x = PAD
        _rrect(draw, (x, y, x + width + 24, y + 32), 16, fill=PANEL)
        draw.text((x + 12, y + 8), text, font=font, fill=accent if value else MUTED)
        x += width + 34
    return y + 48


# ---------------------------------------------------------------------- 物品卡
def _order_column(draw, x: int, width: int, y: int, title: str, color,
                  orders, empty_text: str) -> int:
    font_title = _font(17, bold=True)
    draw.ellipse([x, y + 6, x + 8, y + 14], fill=color)
    draw.text((x + 16, y), title, font=font_title, fill=TEXT)
    if orders:
        count_text = "%d 条" % len(orders)
        count_font = _font(13)
        count_width, _ = _tsize(draw, count_text, count_font)
        draw.text((x + width - count_width, y + 4), count_text, font=count_font, fill=DIM)
    y += 32

    if not orders:
        draw.text((x, y + 2), empty_text, font=_font(15), fill=DIM)
        return y + 30

    price_font = _font(23, bold=True)
    small_font = _font(13)
    for order in orders:
        price = "%sp" % _format_int(order.platinum)
        draw.text((x, y), price, font=price_font, fill=color)
        price_width, _ = _tsize(draw, price, price_font)
        draw.text((x + price_width + 8, y + 8), "×%d" % order.quantity,
                  font=_font(14), fill=MUTED)

        seller_x = x + max(76, price_width + 46)
        seller_width = x + width - seller_x - 58
        seller = _fit(draw, order.user, _font(15), max(30, seller_width))
        draw.text((seller_x, y + 7), seller, font=_font(15), fill=TEXT)

        status = order.status_label
        if status in ("游戏中", "在线"):
            status_color = GREEN if status == "游戏中" else CYAN
            status_text = status
        else:
            status_color = DIM
            status_text = status
        status_width, _ = _tsize(draw, status_text, small_font)
        draw.text((x + width - status_width, y + 8), status_text,
                  font=small_font, fill=status_color)

        mod = ""
        if order.rank is not None:
            mod = "R%d" % order.rank
        if order.charges:
            mod = (mod + " " if mod else "") + "%d次" % order.charges
        if mod:
            draw.text((x + price_width + 40, y + 8), mod, font=_font(12), fill=PURPLE)
        y += 34
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
    draw.text((x, y), label, font=_font(13), fill=DIM)
    draw.text((x, y + 20), value, font=_font(24, bold=True), fill=color)
    draw.text((x, y + 54), _fit(draw, sub, _font(13), width), font=_font(13), fill=MUTED)


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

    draw.text((PAD, y), "近 %d 天价格走势" % len(points),
              font=_font(17, bold=True), fill=TEXT)
    y += 28
    change_color = GREEN if change >= 0 else RED
    draw.text((PAD, y), "均价 %.1fp ｜ 高 %.0fp ｜ 低 %.0fp ｜ 变化 " % (
        avg, high, low), font=_font(13), fill=MUTED)
    prefix_width, _ = _tsize(draw, "均价 %.1fp ｜ 高 %.0fp ｜ 低 %.0fp ｜ 变化 " % (
        avg, high, low), _font(13))
    draw.text((PAD + prefix_width, y), "%+.1f%%" % change,
              font=_font(13, bold=True), fill=change_color)
    y += 24

    usable = WIDTH - 2 * PAD
    ratios = [0.8, 0.8, 0.7, 1.7]
    total = sum(ratios)
    widths = [int(usable * ratio / total) for ratio in ratios]
    headers = ["日期", "均价", "成交量", "区间"]
    header_font = _font(12, bold=True)
    x = PAD
    for index, header in enumerate(headers):
        draw.text((x + 6, y), header, font=header_font, fill=DIM)
        x += widths[index]
    y += 22
    _divider(draw, y)
    y += 4

    row_height = 23
    font = _font(13)
    for row_index, point in enumerate(ordered):
        if row_index % 2 == 0:
            _rrect(draw, (PAD - 4, y - 3, WIDTH - PAD + 4, y + row_height - 6),
                   6, fill=PANEL)
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
            draw.text((x + 6, y), _fit(draw, value, font, widths[index] - 12),
                      font=font, fill=color)
            x += widths[index]
        y += row_height
    return y + 14


def item_card(item, sell, buy, stats, platform_label: str = "PC",
              price_only: bool = False) -> bytes:
    """物品行情卡。"""
    height = 1800
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
    draw.text((PAD, y), "行情", font=_font(17, bold=True), fill=TEXT)
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
        draw.text((PAD, y), "“%s”" % _fit(draw, desc, _font(14), WIDTH - 2 * PAD),
                  font=_font(14), fill=DIM)
        y += 28

    left = "数据 warframe.market · %s · %s" % (
        platform_label, time.strftime("%m-%d %H:%M"))
    y = _footer(draw, y + 6, left)

    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


# ---------------------------------------------------------------------- 紫卡分析
def _analysis_section(draw, y: int, title: str, hint: str = "") -> int:
    draw.text((PAD, y), title, font=_font(18, bold=True), fill=TEXT)
    if hint:
        font = _font(13)
        width, _ = _tsize(draw, hint, font)
        draw.text((WIDTH - PAD - width, y + 6), hint, font=font, fill=DIM)
    y += 30
    _divider(draw, y)
    return y + 8


def _analysis_grid(draw, y: int, headers: Sequence[str], widths: Sequence[int],
                   rows: Sequence[Sequence[str]], aligns: Sequence[str],
                   colors: Optional[Dict[Tuple[int, int], tuple]] = None) -> int:
    header_font = _font(12, bold=True)
    x = PAD
    for index, header in enumerate(headers):
        text = _fit(draw, header, header_font, widths[index] - 12)
        text_width, _ = _tsize(draw, text, header_font)
        if aligns[index] == "right":
            draw.text((x + widths[index] - 8 - text_width, y), text,
                      font=header_font, fill=DIM)
        else:
            draw.text((x + 8, y), text, font=header_font, fill=DIM)
        x += widths[index]
    y += 24
    _divider(draw, y)
    y += 6

    font = _font(15)
    for row_index, row in enumerate(rows):
        if row_index % 2 == 0:
            _rrect(draw, (PAD - 6, y - 3, WIDTH - PAD + 6, y + 26), 7, fill=PANEL)
        x = PAD
        for index, cell in enumerate(row):
            text = _fit(draw, str(cell), font, widths[index] - 14)
            color = TEXT
            if colors:
                color = colors.get((row_index, index), TEXT)
            text_width, _ = _tsize(draw, text, font)
            if aligns[index] == "right":
                draw.text((x + widths[index] - 10 - text_width, y + 3),
                          text, font=font, fill=color)
            else:
                draw.text((x + 8, y + 3), text, font=font, fill=color)
            x += widths[index]
        y += 30
    return y


def riven_analysis_card(analysis, evaluations: Sequence = (),
                        footer: str = "数据 Warframe Wiki · 本地计算") -> bytes:
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

    height = 150 + len(names) * 30 + len(negative_names) * 30 + 290
    if evaluations:
        height += len(evaluations) * 30 + 80
    height = max(780, height)

    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)

    subtitle = "倾向 %.2f（%s）｜ Lv.8" % (analysis.disposition, analysis.dots or "—")
    if analysis.name_en and analysis.name_en.lower() != analysis.name.lower():
        subtitle += "｜ %s" % analysis.name_en
    y = _header(draw, "%s · 紫卡分析" % analysis.name, subtitle,
                "WARFRAME WIKI", PURPLE)
    config_label = "2 / 3 正向"
    if evaluations:
        positives = sum(1 for item in evaluations if not item.negative)
        has_negative = any(item.negative for item in evaluations)
        if positives >= 3:
            config_label = "3+1" if has_negative else "3+"
        elif positives == 2:
            config_label = "2+1" if has_negative else "2+"
        else:
            config_label = "参考"
    y = _pills(draw, y, [
        ("倾向", "%.2f" % analysis.disposition),
        ("配置", config_label),
        ("词条", "%d 条" % len(names)),
    ], PURPLE)

    if evaluations:
        y = _analysis_section(draw, y, "你的词条",
                              "按 %s 配置比较" % config_label)
        rows = []
        colors: Dict[Tuple[int, int], tuple] = {}
        for index, evaluation in enumerate(evaluations):
            unit = evaluation.stat.unit if evaluation.stat else ""
            rows.append([
                evaluation.name,
                "%g%s" % (evaluation.value, unit),
                evaluation.stat.range_text() if evaluation.stat else "—",
                evaluation.grade,
            ])
            colors[(index, 1)] = RED if evaluation.negative else GREEN
            if evaluation.grade in ("接近满值", "优秀", "轻微"):
                colors[(index, 3)] = GREEN
            elif evaluation.grade == "中等":
                colors[(index, 3)] = GOLD
            elif evaluation.grade == "未识别":
                colors[(index, 3)] = DIM
            else:
                colors[(index, 3)] = RED
        y = _analysis_grid(
            draw, y, ["你的词条", "数值", "可能范围", "评价"],
            [150, 90, 230, 320], rows, ["left", "right", "right", "left"], colors)
        y += 10

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
             "有负面词条时请写成负值（例如 变焦=-30）。",
             "后坐力负值表示降低后坐力，属于正面效果。"]
    if evaluations:
        notes.insert(0, "按输入的正向数量和负面推断配置。")
    if analysis.history:
        latest = analysis.history[-1]
        notes.append("最近一次倾向调整：%s（%s）" % (latest[1], latest[0]))
    y += 10
    for line in notes:
        draw.text((PAD, y), _fit(draw, line, _font(13), WIDTH - 2 * PAD),
                  font=_font(13), fill=MUTED)
        y += 22

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
               aligns: Optional[Sequence[str]] = None) -> bytes:
    """通用表格卡。

    columns: [(表头, 宽度比例), ...]
    rows: 每行与 columns 等长的 Cell 列表
    aligns: 每列 "left" / "right"
    """
    height = max(700, 260 + len(rows) * 42 + (40 if note else 0) + 120)
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, subtitle, chip, accent)
    y += 16

    total_ratio = sum(ratio for _, ratio in columns) or 1.0
    usable = WIDTH - 2 * PAD
    widths = [int(usable * ratio / total_ratio) for _, ratio in columns]
    aligns = list(aligns or ["left"] * len(columns))

    header_font = _font(13, bold=True)
    x = PAD
    for index, (name, _) in enumerate(columns):
        if index < len(aligns) and aligns[index] == "right":
            text_width, _ = _tsize(draw, name, header_font)
            draw.text((x + widths[index] - 12 - text_width, y), name,
                      font=header_font, fill=DIM)
        else:
            draw.text((x + 10, y), name, font=header_font, fill=DIM)
        x += widths[index]
    y += 26
    _divider(draw, y)
    y += 8

    row_height = 42
    text_font = _font(16)
    bold_font = _font(16, bold=True)
    for row_index, row in enumerate(rows):
        if row_index % 2 == 0:
            _rrect(draw, (PAD - 6, y - 4, WIDTH - PAD + 6, y + row_height - 10),
                   8, fill=PANEL)
        x = PAD
        for index, cell in enumerate(row):
            font = bold_font if cell.bold else text_font
            color = cell.color or TEXT
            text = _fit(draw, cell.text, font, widths[index] - 20)
            if index < len(aligns) and aligns[index] == "right":
                text_width, _ = _tsize(draw, text, font)
                draw.text((x + widths[index] - 12 - text_width, y + 9),
                          text, font=font, fill=color)
            else:
                draw.text((x + 10, y + 9), text, font=font, fill=color)
            x += widths[index]
        y += row_height

    if note:
        draw.text((PAD, y + 6), _fit(draw, note, _font(14), WIDTH - 2 * PAD),
                  font=_font(14), fill=MUTED)
        y += 30

    y = _footer(draw, y + 12, footer)
    return _encode(image.crop((0, 0, WIDTH, min(height, y + 10))))


def _encode(image: "Image.Image") -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def wiki_card(title: str, body: str, url: str, footer: str = "",
              chip: str = "WIKI") -> bytes:
    """维基词条卡：标题 + 自动换行正文 + 链接。"""
    height = 1600
    image = _gradient(WIDTH, height, BG_TOP, BG_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(image)
    y = _header(draw, title, "", chip, CYAN)
    y += 14

    body_font = _font(17)
    line_height = 32
    max_lines = 18
    lines: List[str] = []
    for paragraph in str(body or "").splitlines():
        paragraph = paragraph.strip()
        if not paragraph:
            if lines:
                lines.append("")
            continue
        # 逐字换行（中文没有空格）
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
        draw.text((PAD, y), line, font=body_font, fill=TEXT)
        y += line_height

    y += 12
    _divider(draw, y)
    y += 14
    url_font = _font(14)
    draw.text((PAD, y), _fit(draw, url, url_font, WIDTH - 2 * PAD),
              font=url_font, fill=CYAN)
    y += 30

    return _encode(image.crop((0, 0, WIDTH, min(height, _footer(draw, y, footer) + 10))))


def build_table(title: str, subtitle: str, headers: Sequence[str],
                rows: Sequence[Sequence[str]], footer: str, accent=GOLD,
                chip: str = "", note: str = "",
                highlights: Optional[Sequence[Sequence[Optional[tuple]]]] = None,
                bold_last_row: bool = False,
                ratios: Optional[Sequence[float]] = None,
                aligns: Optional[Sequence[str]] = None) -> bytes:
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
                      footer, accent=accent, chip=chip, note=note, aligns=aligns)


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
