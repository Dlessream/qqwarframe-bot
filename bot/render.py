"""把查询结果渲染成适合 QQ 阅读的纯文本。"""

from __future__ import annotations

from typing import List, Optional, Sequence

from .market import Item, Order, SearchHit, StatSummary, MarketStats
from .wiki import WikiHit, WikiPage

DIVIDER = "─" * 15


def fmt_int(value: float) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if abs(number - round(number)) < 0.05:
        return "{:,}".format(int(round(number)))
    return "{:,.1f}".format(number)


def fmt_price(value: float) -> str:
    if value is None:
        return "?"
    if value >= 10000:
        return "{:.1f}万".format(value / 10000.0)
    return fmt_int(value)


def _status_note(order: Order) -> str:
    return "（%s）" % order.status_label if order.status_label else ""


def _order_line(order: Order) -> str:
    extra = ""
    if order.rank is not None:
        extra += " · R%d" % order.rank
    if order.charges is not None and order.charges > 0:
        extra += " · %d 次" % order.charges
    return "%sp ×%d · %s%s%s" % (
        fmt_int(order.platinum), order.quantity, order.user, extra, _status_note(order))


def _stat_line(label: str, stat: Optional[StatSummary]) -> str:
    if not stat or stat.empty:
        return "%s：暂无成交数据" % label
    return "%s：均价 %sp · 中位 %sp · %s 笔 · 区间 %s~%sp" % (
        label, fmt_price(stat.avg), fmt_price(stat.median),
        fmt_int(stat.volume), fmt_price(stat.min_price), fmt_price(stat.max_price))


def _trend_lines(stats: Optional[MarketStats], days: int = 15) -> List[str]:
    points = list(getattr(stats, "history", None) or [])[-days:]
    if len(points) < 2:
        return []
    avg = sum(point.avg for point in points) / len(points)
    high = max(point.max_price or point.avg for point in points)
    low = min(point.min_price or point.avg for point in points)
    first, last = points[0], points[-1]
    change = ((last.avg - first.avg) / first.avg * 100.0) if first.avg else 0.0
    return [
        "",
        "▍近 %d 天走势（WM 每日成交均价）" % len(points),
        "  均价 %.1fp ｜ 高 %.0fp ｜ 低 %.0fp ｜ 变化 %+.1f%%" % (
            avg, high, low, change),
    ]


def render_item(item: Item, sell: Sequence[Order], buy: Sequence[Order],
                stats: Optional[MarketStats] = None, order_limit: int = 5,
                show_orders: bool = True) -> str:
    lines: List[str] = []
    lines.append("【%s】" % (item.name or item.slug))
    lines.append(item.kind_label)

    facts = []
    if item.ducats:
        facts.append("杜卡德 %s" % fmt_int(item.ducats))
    if item.mastery_rank:
        facts.append("MR %d" % item.mastery_rank)
    if item.trading_tax:
        facts.append("交易税 %s" % fmt_int(item.trading_tax))
    if facts:
        lines.append(" ｜ ".join(facts))

    if item.name_en and item.name_en != item.name:
        lines.append("英文：%s" % item.name_en)
    lines.append("市场：%s" % item.url)

    if sell or buy:
        low = min(order.platinum for order in sell) if sell else None
        high = max(order.platinum for order in buy) if buy else None
        parts = []
        if low is not None:
            parts.append("最低在售 %sp" % fmt_int(low))
        if high is not None:
            parts.append("最高收购 %sp" % fmt_int(high))
        if parts:
            lines.append("")
            lines.append(" ｜ ".join(parts))

    if show_orders:
        lines.append("")
        if sell:
            lines.append("▍在售（在线前 %d 条）" % min(len(sell), order_limit))
            for order in sell[:order_limit]:
                lines.append("  " + _order_line(order))
        else:
            lines.append("▍在售：暂无在线卖家")

        if buy:
            lines.append("")
            lines.append("▍收购（在线前 %d 条）" % min(len(buy), order_limit))
            for order in buy[:order_limit]:
                lines.append("  " + _order_line(order))
        else:
            lines.append("▍收购：暂无在线买家")

    if stats and not stats.empty:
        lines.append("")
        lines.append("▍行情")
        lines.append("  " + _stat_line("48小时", stats.h48))
        lines.append("  " + _stat_line("90天", stats.d90))
        lines.extend(_trend_lines(stats))

    if item.description:
        desc = item.description.strip().replace("\n", " ")
        if len(desc) > 120:
            desc = desc[:120].rstrip() + "…"
        lines.append("")
        lines.append("“%s”" % desc)

    return "\n".join(lines)


def render_price(item: Item, sell: Sequence[Order], buy: Sequence[Order],
                 stats: Optional[MarketStats] = None, order_limit: int = 8) -> str:
    lines: List[str] = ["【%s】价格" % (item.name or item.slug), item.kind_label]
    if item.name_en and item.name_en != item.name:
        lines.append("英文：%s" % item.name_en)
    lines.append(item.url)

    if sell or buy:
        parts = []
        if sell:
            parts.append("最低在售 %sp" % fmt_int(min(order.platinum for order in sell)))
        if buy:
            parts.append("最高收购 %sp" % fmt_int(max(order.platinum for order in buy)))
        lines.append("")
        lines.append(" ｜ ".join(parts))

    lines.append("")
    if sell:
        lines.append("▍在售")
        for order in sell[:order_limit]:
            lines.append("  " + _order_line(order))
    else:
        lines.append("▍在售：暂无在线卖家")
    if buy:
        lines.append("▍收购")
        for order in buy[:order_limit]:
            lines.append("  " + _order_line(order))
    else:
        lines.append("▍收购：暂无在线买家")

    lines.append("")
    lines.append("▍行情")
    if stats and not stats.empty:
        lines.append("  " + _stat_line("48小时", stats.h48))
        lines.append("  " + _stat_line("90天", stats.d90))
        lines.extend(_trend_lines(stats))
    else:
        lines.append("  暂无成交数据")
    return "\n".join(lines)


def render_candidates(query: str, hits: Sequence[SearchHit]) -> str:
    lines = ["没有找到唯一匹配：“%s”" % query, "你可能想找："]
    for index, hit in enumerate(hits, 1):
        item = hit.item
        lines.append("%d. %s（%s）" % (index, item.name, item.kind_label))
        if item.name_en and item.name_en != item.name:
            lines.append("   %s" % item.name_en)
    lines.append("")
    lines.append("直接复制完整名称再查一次即可，例如：#查 绝路 Prime")
    return "\n".join(lines)


def render_not_found(query: str) -> str:
    return ("没有在 warframe.market 找到“%s”。\n"
            "· 检查名称是否写错（可用中文、英文或常用外号）\n"
            "· 试试更短的关键词，例如只输入“绝路”\n"
            "· 该物品可能不可交易（如普通战甲本体、任务钥匙）" % query)


def wiki_label(url: str = "") -> str:
    value = str(url or "")
    if "huijiwiki" in value or "biligame" in value:
        return "中文维基"
    return "英文维基"


def render_wiki(query: str, page: Optional[WikiPage],
                hits: Sequence[WikiHit] = (), translated: str = "",
                note: str = "") -> str:
    if page is None:
        from urllib.parse import quote
        search = ("https://wiki.warframe.com/w/Special:Search?search="
                  + quote(str(query or "")))
        lines = ["英文维基里没有找到“%s”。" % query]
        if note:
            lines.append("· " + note)
        lines.extend(["· 试试英文关键词，例如 Volt、Rubico、Riven Mod",
                      "· 也可以直接搜索：%s" % search])
        return "\n".join(lines)

    label = wiki_label(page.url)
    lines = ["【%s】%s" % (page.title, label), ""]
    if note:
        lines.extend([note, ""])
    if translated:
        lines.extend(["中文翻译：", translated, "", "英文原文：", page.extract])
    else:
        lines.extend([page.extract])
    lines.extend(["", page.url])
    others = [hit for hit in hits if hit.title != page.title][:3]
    if others:
        lines.append("")
        lines.append("其他结果：" + "、".join(hit.title for hit in others))
    return "\n".join(lines)


def render_help(prefix: str = "#") -> str:
    lines = [
        "Warframe 查询机器人",
        DIVIDER,
        "%swm <名称> [平台]   物品详情 + 实时报价" % prefix,
        "%s价格 <名称> [平台] 只看价格与行情" % prefix,
        "%s战甲 / %s武器 / %smod <名称>  分类搜索" % (prefix, prefix, prefix),
        "%s拆件 <套装>        套装拆件比价" % prefix,
        "%s金垃圾 / %s银垃圾 / %s铜垃圾  当前地板价 + 一组价" % (prefix, prefix, prefix),
        "%s垃圾               金/银/铜垃圾行情总览" % prefix,
        "%s战甲榜             Prime 战甲价格排行（高 → 低）" % prefix,
        "%s紫卡 <武器>        紫卡倾向与拍卖" % prefix,
        "%s紫卡                最贵零洗紫卡榜（高 → 低）" % prefix,
        "%s紫卡分析 + 截图                  自动识别武器/词条/负面" % prefix,
        "%s紫卡分析 <武器> [词条=数值 ...]  范围 + roll 评分" % prefix,
        "%s轮换               言录使 / 格拉斯 / 埃莉诺 轮换商店" % prefix,
        "%s轮换 <言录使|格拉斯|埃莉诺>  单个商店明细" % prefix,
        "%s奸商 / %s突击 / %s仲裁  世界状态" % (prefix, prefix, prefix),
        "%s世界状态           全量世界状态总览" % prefix,
        "%s警报 / %s入侵 / %s裂缝" % (prefix, prefix, prefix),
        "%s钢铁侵袭 / %s电波 / %s平原" % (prefix, prefix, prefix),
        "%s特惠 / %s新闻 / %s活动" % (prefix, prefix, prefix),
        "%s回廊 / %s执刑官 / %s赏金" % (prefix, prefix, prefix),
        "%s钢铁裂缝 / %s平原赏金" % (prefix, prefix),
        "%s虚空风暴 / %s九重天   Railjack 风暴裂缝" % (prefix, prefix),
        "%s希图斯               希图斯时间 + 小帐篷赏金" % prefix,
        "%s深层科研 / %s时光科研 / %s科研" % (prefix, prefix, prefix),
        "%s仲裁评级           仲裁梯队（加「本地」看推算结果）" % prefix,
        "%s今日仲裁 / %s高效仲裁  未来 24 小时轮换、高价值仲裁" % (prefix, prefix),
        "%s仲裁表 [天数]     未来 7 天完整仲裁排期（1 - 7 天）" % prefix,
        "%s提醒 <名称> <价>   降价提醒" % prefix,
        "%s提醒列表 / %s取消提醒 <编号|all>" % (prefix, prefix),
        "%s订阅 <类型> [筛选]  新内容出现就推送到群" % prefix,
        "%s订阅列表 / %s取消订阅 <编号|类型>" % (prefix, prefix),
        "%s更新               立即刷新缓存数据" % prefix,
        "%s数据状态           各数据源更新时间" % prefix,
        "%swiki <关键词>      英文维基（支持中文，如 电男）" % prefix,
        "%s翻译 <物品名>      中英双语对照" % prefix,
        "%s帮助               显示本菜单" % prefix,
        DIVIDER,
        "平台可写 pc / ps / xbox / switch / mobile，默认 PC。",
        "命令可直接发送，也可带 # / ＃；不再需要先唤醒。",
        "例：wm 绝路 ｜ 拆件 Mesa ｜ 紫卡分析 绝路 + 截图 ｜ 提醒 牛甲 50",
        "名称支持中文、英文和常用外号；结果以图片卡片发送。",
    ]
    return "\n".join(lines)


def render_market_error(message: str) -> str:
    return "查询失败：%s\n稍后再试；若持续失败，请运行 python selftest.py 自检。" % message


def chunk_text(text: str, limit: int = 1200) -> List[str]:
    """按行切分长文本，保证单条消息不超过 limit 个字符。"""
    limit = max(100, int(limit))
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]
