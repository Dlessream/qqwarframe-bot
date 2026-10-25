"""把查询结果组装成 Reply（文字兜底 + 图片卡片）。"""

from __future__ import annotations

from typing import List, Optional, Sequence

from . import imagecard, render
from .alerts import Alert
from .market import Item, MarketStats, Order, PartQuote, SearchHit
from .replies import Reply
from .riven import RivenAuction, RivenWeapon
from .rivencalc import RivenAnalysis, RollEvaluation, infer_config_label
from .wiki import WikiHit, WikiPage
from .worldstate import Arbitration, Sortie, VoidTrader, countdown

PLATFORM_LABELS = {
    "pc": "PC",
    "ps4": "PlayStation",
    "xbox": "Xbox",
    "switch": "Switch",
    "mobile": "Mobile",
}


def platform_label(platform: Optional[str]) -> str:
    return PLATFORM_LABELS.get((platform or "pc").lower(), (platform or "PC").upper())


# ---------------------------------------------------------------------- 市场
def item_reply(item: Item, sell: Sequence[Order], buy: Sequence[Order],
               stats: Optional[MarketStats], platform: str,
               price_only: bool = False) -> Reply:
    label = platform_label(platform)
    text = (render.render_price(item, sell, buy, stats, order_limit=8)
            if price_only else render.render_item(item, sell, buy, stats, order_limit=5))
    return Reply(text=text, image=lambda: imagecard.item_card(
        item, sell, buy, stats, label, price_only=price_only))


def candidates_reply(query: str, hits: Sequence[SearchHit]) -> Reply:
    return Reply(text=render.render_candidates(query, hits))


def not_found_reply(query: str) -> Reply:
    return Reply(text=render.render_not_found(query))


def set_reply(item: Item, quotes: Sequence[PartQuote], set_sell: Optional[int],
              platform: str) -> Reply:
    label = platform_label(platform)
    total_sell = sum(quote.sell for quote in quotes if quote.sell)
    total_buy = sum(quote.buy for quote in quotes if quote.buy)
    known_sell = sum(1 for quote in quotes if quote.sell)

    lines = ["【%s】拆件比价" % item.name]
    for quote in quotes:
        lines.append("%s：%s / %s" % (
            quote.name,
            "%dp" % quote.sell if quote.sell else "无",
            "%dp" % quote.buy if quote.buy else "无"))
    lines.append("拆件合计：%dp（%d/%d 个部件有报价）" % (total_sell, known_sell, len(quotes)))
    if set_sell:
        lines.append("套装最低在售：%dp" % set_sell)
        if total_sell and total_sell < set_sell:
            lines.append("拆件更便宜：省 %dp" % (set_sell - total_sell))
        elif total_sell:
            lines.append("直接买套装更划算：省 %dp" % (total_sell - set_sell))

    rows = []
    for quote in quotes:
        rows.append([
            quote.name,
            "%dp" % quote.sell if quote.sell else "—",
            "%dp" % quote.buy if quote.buy else "—",
        ])
    rows.append(["合计", "%dp" % total_sell if total_sell else "—",
                 "%dp" % total_buy if total_buy else "—"])

    note = ""
    if set_sell and total_sell:
        if total_sell < set_sell:
            note = "结论：拆件更便宜，省 %dp，但需要分别交易 %d 次。" % (
                set_sell - total_sell, len(quotes))
        else:
            note = "结论：直接买套装更便宜，省 %dp。" % (total_sell - set_sell)
    elif not total_sell:
        note = "部件暂时没有在线报价，稍后再试。"

    subtitle = "套装 %s ｜ 拆件 %s" % (
        "%dp" % set_sell if set_sell else "无报价",
        "%dp" % total_sell if total_sell else "无报价")

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "%s · 拆件比价" % item.name, subtitle,
            ["部件", "最低在售", "最高收购"], rows,
            "数据 warframe.market · %s" % label,
            accent=imagecard.GOLD, chip="拆件", note=note,
            highlights=[[None, imagecard.GOLD, imagecard.CYAN]] * len(rows),
            bold_last_row=True, ratios=[2.4, 1, 1],
            aligns=["left", "right", "right"]))


# ---------------------------------------------------------------------- 紫卡
def riven_reply(weapon: RivenWeapon, auctions: Sequence[RivenAuction],
                note: str, platform: str) -> Reply:
    label = platform_label(platform)
    lines = [
        "【%s】紫卡" % weapon.name,
        "倾向 %.2f（%s）｜ 类型 %s ｜ MR %d" % (
            weapon.disposition, weapon.disposition_label, weapon.riven_type,
            weapon.mastery_rank),
    ]
    if auctions:
        for auction in auctions:
            lines.append("%dp · %s · %s（R%d / %d 洗）" % (
                auction.price, " / ".join(auction.attributes[:3]) or "未洗",
                auction.seller, auction.rank, auction.re_rolls))
    else:
        lines.append(note or "暂无在售紫卡。")

    rows = []
    for auction in auctions:
        rows.append([
            "%dp" % auction.price,
            " / ".join(auction.attributes[:3]) or "未洗",
            auction.seller,
            "R%d" % auction.rank,
        ])
    subtitle = "倾向 %.2f（%s）｜ %s ｜ MR %d" % (
        weapon.disposition, weapon.disposition_label,
        weapon.riven_type or weapon.group, weapon.mastery_rank)

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "%s · 紫卡行情" % weapon.name, subtitle,
            ["价格", "词条", "卖家", "等级"], rows,
            "数据 warframe.market · %s" % label,
            accent=imagecard.PURPLE, chip="紫卡",
            note=note if not auctions else "",
            highlights=[[imagecard.PURPLE, None, None, None]] * len(rows),
            ratios=[0.8, 3.2, 1.4, 0.7],
            aligns=["right", "left", "left", "right"]))


# ---------------------------------------------------------------------- 紫卡分析
def riven_analysis_reply(analysis: RivenAnalysis,
                         evaluations: Sequence[RollEvaluation]) -> Reply:
    lines = [
        "【%s】紫卡分析" % analysis.name,
        "倾向 %.2f（%s）｜ Lv.8 词条范围" % (analysis.disposition, analysis.dots or "—"),
    ]
    if analysis.name_en and analysis.name_en.lower() != analysis.name.lower():
        lines.append("英文：%s" % analysis.name_en)

    if evaluations:
        lines.append("")
        lines.append("你的词条（按 %s 配置比较）：" % infer_config_label(evaluations))
        for evaluation in evaluations:
            if evaluation.stat is None:
                lines.append("  %s %g：未识别（该武器没有这个词条）" % (
                    evaluation.name, evaluation.value))
                continue
            lines.append("  %s %g%s：范围 %s，%s" % (
                evaluation.name, evaluation.value, evaluation.stat.unit,
                evaluation.stat.range_text(), evaluation.grade))
        lines.append("提示：有负面词条时请写成负值（例如 变焦=-30）。")

    if evaluations:
        config_label = infer_config_label(evaluations)
    else:
        config_label = "2+"
    if config_label.startswith("3"):
        group = analysis.three_neg if "+1" in config_label else analysis.three
    else:
        group = analysis.two_neg if "+1" in config_label else analysis.two
    lines.append("")
    lines.append("正向范围（%s，Lv.8）：" % config_label)
    for stat in (group or analysis.two_neg or analysis.two)[:6]:
        lines.append("  %s %s" % (stat.name, stat.range_text()))
    lines.append("完整 2/3 正向、2+1/3+1 与负面范围见图片。")

    if analysis.history:
        latest = analysis.history[-1]
        lines.append("最近一次倾向调整：%s（%s）" % (latest[1], latest[0]))

    lines.append("数据源：%s" % analysis.source_url)

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.riven_analysis_card(
            analysis, evaluations, "数据 Warframe Wiki · 本地计算"))


# ---------------------------------------------------------------------- 世界状态
def _translate_items(names: Sequence[str], index) -> List[str]:
    if index is None:
        return list(names)
    translated = []
    for name in names:
        hit = None
        try:
            hits = index.search(name, limit=1)
            hit = hits[0] if hits else None
        except Exception:
            hit = None
        if hit and hit.score >= 900 and hit.item.name:
            translated.append(hit.item.name)
        else:
            translated.append(name)
    return translated


def baro_reply(trader: VoidTrader, index=None) -> Reply:
    if trader.active:
        subtitle = "%s ｜ 剩余 %s" % (trader.location or "未知位置", countdown(trader.end))
    else:
        subtitle = "尚未到访 ｜ 到达：%s" % (
            countdown(trader.start) if trader.start else "未知")

    names = _translate_items([item.name for item in trader.inventory], index)
    rows = []
    for name, item in zip(names, trader.inventory):
        rows.append([name, "{:,}".format(item.ducats) if item.ducats else "—",
                     "{:,}".format(item.credits) if item.credits else "—"])

    lines = ["【Baro Ki'Teer】虚空商人", subtitle]
    for row in rows:
        lines.append("%s ｜ 杜卡德 %s ｜ 星币 %s" % tuple(row))
    if not rows:
        lines.append("当前没有库存数据（商人未到访）。")

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "Baro Ki'Teer · 虚空商人", subtitle,
            ["物品", "杜卡德", "星币"], rows,
            "数据 warframestat.us · 游戏内售价",
            accent=imagecard.PURPLE, chip="奸商",
            note="价格为游戏内售价，实际以游戏内为准。",
            ratios=[2.6, 1, 1], aligns=["left", "right", "right"]))


def sortie_reply(sortie: Sortie) -> Reply:
    subtitle = "%s · %s ｜ 剩余 %s" % (
        sortie.boss or "未知 Boss", sortie.faction or "", countdown(sortie.end))
    rows = [[variant.mission or "—", variant.node or "—", variant.modifier or "—"]
            for variant in sortie.variants]
    lines = ["【每日突击】", subtitle]
    for index, row in enumerate(rows, 1):
        lines.append("%d. %s ｜ %s ｜ %s" % (index, row[0], row[1], row[2]))

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "每日突击", subtitle,
            ["任务", "节点", "修饰"], rows,
            "数据 warframestat.us · 每日刷新",
            accent=imagecard.CYAN, chip="突击",
            ratios=[1.2, 1.6, 2.2]))


def _rating_color(rating: str):
    if rating == "S":
        return imagecard.GOLD
    if rating in ("A+", "A"):
        return imagecard.GREEN
    if rating == "A-":
        return imagecard.CYAN
    if rating in ("B", "C"):
        return imagecard.RED
    return imagecard.MUTED


def arbitration_reply(arbitration: Arbitration, rating: Optional[str] = None,
                      node=None, note: str = "") -> Reply:
    subtitle = "剩余 %s" % countdown(arbitration.end)
    if rating:
        subtitle += " ｜ 评级 %s" % rating
    name = node.display_name if node is not None else (arbitration.node or "—")
    mission = node.mission if node is not None else (arbitration.mission or "—")
    enemy = node.faction if node is not None else (arbitration.enemy or "—")
    system = node.system if node is not None else "—"
    level = node.level_text if node is not None else "—"
    rows = [[name, system, mission, enemy, level, rating or "—"]]

    lines = ["【仲裁】", subtitle, "节点：%s" % name]
    if node is not None and node.system:
        lines.append("星球：%s" % node.system)
    lines.append("类型：%s" % mission)
    lines.append("敌人：%s" % enemy)
    if node is not None and node.max_level:
        lines.append("等级：%s" % node.level_text)
    if rating:
        lines.append("评级：%s" % rating)
    if note:
        lines.append(note)

    footer = "数据 warframestat.us"
    if rating or node is not None:
        footer += " + arbi.wf.wiki"
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "仲裁任务", subtitle,
            ["节点", "星球", "类型", "敌人", "等级", "评级"], rows,
            footer, accent=_rating_color(rating or ""),
            chip="仲裁 %s" % rating if rating else "仲裁",
            note=note,
            ratios=[2.0, 0.8, 1.0, 1.0, 0.8, 0.7],
            highlights=[[_rating_color(rating or ""), None, None, None, None,
                         _rating_color(rating or "")]]))


def arbitration_tiers_reply(data, current_key: str = "") -> Reply:
    rows = []
    highlights = []
    lines = ["【仲裁评级】"]
    current = data.nodes.get(current_key) if current_key else None
    for tier, nodes in data.groups():
        lines.append("%s：%s" % (tier, "；".join(
            "%s（%s）%s" % (node.name, node.system, node.mission)
            for node in nodes)))
        for node in nodes:
            rows.append([tier, node.name, node.system, node.mission,
                         node.level_text])
            if current_key and node.key == current_key:
                highlights.append([imagecard.GOLD] * 5)
            else:
                highlights.append([_rating_color(tier), None, None, None, None])

    subtitle = "共 %d 个已评级节点" % data.rated_count
    if current is not None:
        subtitle += " ｜ 当前 %s" % current.display_name
        lines.insert(1, "当前：%s · %s · 评级 %s" % (
            current.display_name, current.mission,
            data.rating(current.key)))
    lines.append("未评级节点：%d 个（未列出）" % (
        len(data.nodes) - data.rated_count))
    lines.append("数据源：https://arbi.wf.wiki/")

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "仲裁评级", subtitle,
            ["梯队", "节点", "星球", "任务", "等级"], rows,
            "数据 arbi.wf.wiki · 未评级节点未列出",
            accent=imagecard.GOLD, chip="评级",
            ratios=[0.6, 1.7, 0.8, 1.2, 0.8],
            highlights=highlights))


# ---------------------------------------------------------------------- 维基
def wiki_reply(query: str, page: Optional[WikiPage],
               hits: Sequence[WikiHit]) -> Reply:
    text = render.render_wiki(query, page, hits)
    if page is None:
        return Reply(text=text)
    return Reply(text=text, image=lambda: imagecard.wiki_card(
        page.title, page.extract, page.url,
        "中文维基 · warframe.huijiwiki.com"))


# ---------------------------------------------------------------------- 提醒
def alerts_list_reply(alerts: Sequence[Alert]) -> Reply:
    if not alerts:
        return Reply(text="当前没有生效中的降价提醒。\n用 #提醒 绝路 50 添加一条。")

    rows = []
    lines = ["【我的降价提醒】"]
    for alert in alerts:
        current = "%dp" % alert.last_price if alert.last_price else "—"
        rows.append(["#%s" % alert.id, alert.name, "%dp" % alert.target,
                     current, platform_label(alert.platform)])
        lines.append("#%s %s：目标 %dp，当前 %s（%s）" % (
            alert.id, alert.name, alert.target, current,
            platform_label(alert.platform)))

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "我的降价提醒", "共 %d 条" % len(alerts),
            ["编号", "物品", "目标", "当前", "平台"], rows,
            "数据 warframe.market · 每 5 分钟检查",
            accent=imagecard.GOLD, chip="提醒",
            ratios=[0.8, 2.4, 1, 1, 1], aligns=["left", "left", "right", "right", "left"]))


def alert_fired_reply(item: Item, order: Order, alert: Alert) -> Reply:
    title = "%s 降价提醒" % (item.name or alert.name)
    subtitle = "目标 %dp ｜ 当前 %dp" % (alert.target, order.platinum)
    rows = [[order.user, "×%d" % order.quantity, order.status_label]]
    text = "【%s】\n%s\n卖家：%s ×%d（%s）\n%s" % (
        title, subtitle, order.user, order.quantity, order.status_label, item.url)
    return Reply(
        text=text,
        image=lambda: imagecard.build_table(
            title, subtitle, ["卖家", "数量", "状态"], rows,
            "数据 warframe.market · %s" % platform_label(alert.platform),
            accent=imagecard.GREEN, chip="已达成",
            note="提醒已自动关闭，需要的话可以重新添加。",
            ratios=[2, 1, 1]))


def help_reply(prefix: str) -> Reply:
    return Reply(text=render.render_help(prefix))


def error_reply(message: str) -> Reply:
    return Reply(text=message)
