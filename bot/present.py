"""把查询结果组装成 Reply（文字兜底 + 图片卡片）。"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence

from . import imagecard, render, terms
from .alerts import Alert
from .market import Item, MarketStats, Order, PartQuote, SearchHit
from .replies import Reply
from .riven import RivenAuction, RivenWeapon
from .rivencalc import RivenAnalysis, RollEvaluation, infer_config_label
from .wiki import WikiHit, WikiPage
from .worldstate import (Arbitration, Sortie, VoidTrader, countdown,
                         parse_timestamp)

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
            "数据 warframe market · %s" % label,
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
            "数据 warframe market · %s" % label,
            accent=imagecard.PURPLE, chip="紫卡",
            note=note if not auctions else "",
            highlights=[[imagecard.PURPLE, None, None, None]] * len(rows),
            ratios=[0.8, 3.2, 1.4, 0.7],
            aligns=["right", "left", "left", "right"]))


# ---------------------------------------------------------------------- 紫卡分析
def riven_ocr_reply(analysis: RivenAnalysis,
                    pairs: Sequence) -> Reply:
    rows = []
    lines = []
    for name, value in pairs:
        stat = None
        for candidate in list(analysis.two) + list(analysis.two_neg) + \
                list(analysis.three) + list(analysis.three_neg):
            if candidate.name == name:
                stat = candidate
                break
        unit = stat.unit if stat else "%"
        rows.append([name, "%g%s" % (value, unit)])
        lines.append("%s %g%s" % (name, value, unit))
    text = "【%s】紫卡截图识别\n倾向 %.2f（%s）\n%s\n\n只做截图词条识别，不计算范围/评分。" % (
        analysis.name, analysis.disposition, analysis.dots or "—",
        "\n".join(lines))
    return Reply(
        text=text,
        image=lambda: imagecard.build_table(
            "紫卡截图识别", "%s · 倾向 %.2f" % (analysis.name, analysis.disposition),
            ["词条", "数值"], rows,
            "数据 OCR · 本地识别 · 不计算范围/评分",
            accent=imagecard.PURPLE, chip="OCR",
            ratios=[2.4, 0.8],
            note="只识别截图中的词条，不做范围与 roll 评分。"))


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
    enemy = terms.localize_faction(str(enemy))
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


# ---------------------------------------------------------------------- 世界状态
def _as_list(value) -> list:
    return value if isinstance(value, list) else []


def _reward_text(reward) -> str:
    if not isinstance(reward, dict):
        return str(reward or "—")
    parts = []
    for item in _as_list(reward.get("items"))[:2]:
        if isinstance(item, dict):
            parts.append(str(item.get("type") or item.get("item") or "?"))
    for item in _as_list(reward.get("countedItems"))[:2]:
        if isinstance(item, dict):
            count = item.get("count") or 1
            parts.append("%sx%s" % (count, item.get("type") or "?"))
    if reward.get("credits"):
        parts.append("%s 星币" % _format_int(reward.get("credits")))
    return " + ".join(parts) or "—"


def _format_int(value) -> str:
    try:
        return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return str(value or 0)


def _world_reply(title: str, subtitle: str, headers, rows,
                 footer: str, chip: str, text_lines=None,
                 ratios=None, accent=None, note: str = "") -> Reply:
    if text_lines is None:
        text_lines = [" / ".join(str(cell) for cell in row) for row in rows]
    text = "【%s】\n%s" % (title, subtitle)
    if text_lines:
        text += "\n" + "\n".join(text_lines)
    if note:
        text += "\n" + note
    return Reply(
        text=text,
        image=lambda: imagecard.build_table(
            title, subtitle, list(headers), rows, footer,
            accent=accent or imagecard.GREEN, chip=chip, note=note,
            ratios=ratios or [max(1.0, 6.0 / len(headers))] * len(headers)))


def alerts_reply(raw) -> Reply:
    rows = []
    for alert in _as_list(raw)[:12]:
        mission = alert.get("mission") or {}
        rows.append([
            terms.localize_node(str(mission.get("node") or "—")),
            terms.localize_mission(str(mission.get("type") or "—")),
            terms.localize_faction(str(mission.get("faction") or "—")),
            _reward_text(mission.get("reward")),
            countdown(parse_timestamp(alert.get("expiry"))),
        ])
    if not rows:
        return _world_reply("世界警报", "当前没有活跃警报", [], [], "数据 warframestat.us", "ALERT")
    return _world_reply(
        "世界警报", "共 %d 条" % len(rows),
        ["节点", "任务", "敌人", "奖励", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "警报",
        ratios=[1.8, 1.0, 0.9, 1.8, 0.9])


def invasions_reply(raw) -> Reply:
    rows = []
    for row in _as_list(raw)[:12]:
        attacker = row.get("attacker") or {}
        defender = row.get("defender") or {}
        rows.append([
            terms.localize_node(str(row.get("node") or "—")),
            terms.localize_faction(str(attacker.get("faction") or "—")),
            terms.localize_faction(str(defender.get("faction") or "—")),
            "%d%%" % round(float(row.get("completion") or 0)),
            _reward_text(attacker.get("reward")),
        ])
    if not rows:
        return _world_reply("入侵", "当前没有入侵", [], [], "数据 warframestat.us", "入侵")
    return _world_reply(
        "入侵", "共 %d 条" % len(rows),
        ["节点", "进攻", "防守", "进度", "奖励"], rows,
        "数据 warframestat.us · 世界状态", "入侵",
        ratios=[2.0, 0.9, 0.9, 0.8, 1.6])


def steel_path_reply(raw) -> Reply:
    if not isinstance(raw, dict):
        raw = {}
    reward = raw.get("currentReward") or {}
    incursions = raw.get("incursions") or {}
    rows = [
        ["当前奖励", str(reward.get("name") or "—"),
         "%s 精华" % reward.get("cost") if reward.get("cost") else "—"],
        ["侵袭周期", "本日", countdown(parse_timestamp(incursions.get("expiry")))],
        ["轮换", "%d 项" % len(_as_list(raw.get("rotation"))),
         countdown(parse_timestamp(raw.get("expiry")))],
    ]
    return _world_reply(
        "钢铁侵袭", "Teshin 钢铁之路", ["项目", "内容", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "STEEL",
        ratios=[1.0, 2.0, 1.2], accent=imagecard.CYAN)


def nightwave_reply(raw) -> Reply:
    if not isinstance(raw, dict):
        raw = {}
    rows = []
    for item in _as_list(raw.get("activeChallenges"))[:12]:
        rows.append([
            "每日" if item.get("isDaily") else "每周",
            str(item.get("title") or "—"),
            "%s 声望" % _format_int(item.get("reputation")),
            countdown(parse_timestamp(item.get("expiry"))),
        ])
    return _world_reply(
        "午夜电波", "第 %s 季 · %d 个挑战" % (
            raw.get("season") or "?", len(rows)),
        ["类型", "挑战", "声望", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "电波",
        ratios=[0.8, 2.2, 0.9, 0.9], accent=imagecard.PURPLE)


STATE_LABELS = {
    "day": "白天", "night": "夜晚",
    "warm": "温暖", "cold": "寒冷",
    "fass": "法斯", "vome": "沃姆",
    "fear": "恐惧", "joy": "喜悦", "anger": "愤怒",
    "envy": "嫉妒", "sorrow": "悲伤",
}


def _state_label(value) -> str:
    raw = str(value or "—")
    return STATE_LABELS.get(raw.lower(), terms.localize_text(raw))


def cycles_reply(cetus=None, vallis=None, cambion=None) -> Reply:
    def state(data, key="state"):
        if not isinstance(data, dict):
            return "—", "—"
        return (_state_label(data.get(key)),
                terms.localize_duration(str(data.get("timeLeft") or "—")))

    rows = []
    for name, data in (("地球 · 希图斯", cetus), ("金星 · 福尔图娜", vallis),
                       ("火卫二 · 英择谛", cambion)):
        state_text, left = state(data)
        rows.append([name, state_text, left])
    return _world_reply(
        "平原轮换", "希图斯 / 福尔图娜 / 英择谛",
        ["地点", "状态", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "平原",
        ratios=[1.6, 1.0, 1.0], accent=imagecard.CYAN)


def deals_reply(raw) -> Reply:
    rows = []
    for deal in _as_list(raw)[:10]:
        rows.append([
            str(deal.get("item") or "—"),
            "%sp" % _format_int(deal.get("originalPrice")),
            "%sp" % _format_int(deal.get("salePrice")),
            "%d%%" % int(deal.get("discount") or 0),
            countdown(parse_timestamp(deal.get("expiry"))),
        ])
    if not rows:
        return _world_reply("今日特惠", "当前没有特惠", [], [], "数据 warframestat.us", "特惠")
    return _world_reply(
        "今日特惠", "每日商店折扣", ["物品", "原价", "现价", "折扣", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "特惠",
        ratios=[1.8, 0.8, 0.8, 0.7, 0.9], accent=imagecard.GOLD)


def news_reply(raw) -> Reply:
    rows = []
    text_lines = []
    for item in _as_list(raw)[:10]:
        message = terms.localize_phrase(str(item.get("message") or "—"))
        rows.append([message[:36], str(item.get("date") or "")[:10]])
        text_lines.append(message)
    return _world_reply(
        "新闻", "官方新闻与公告", ["标题", "日期"], rows,
        "数据 warframestat.us · 世界状态", "NEWS",
        text_lines=text_lines, ratios=[2.4, 0.8], accent=imagecard.CYAN)


def events_reply(raw) -> Reply:
    rows = []
    for event in _as_list(raw)[:10]:
        score = "%s / %s" % (event.get("currentScore", 0), event.get("maximumScore", 0))
        rows.append([
            terms.localize_phrase(
                str(event.get("description") or event.get("tooltip") or "—"))[:32],
            terms.localize_text(terms.localize_node(
                str(event.get("node") or "—"))),
            score,
            countdown(parse_timestamp(event.get("expiry"))),
        ])
    if not rows:
        return _world_reply("活动", "当前没有活动", [], [], "数据 warframestat.us", "活动")
    return _world_reply(
        "活动", "当前进行中的活动", ["活动", "节点", "进度", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "活动",
        ratios=[2.0, 1.2, 0.9, 0.9], accent=imagecard.PURPLE)


def circuit_reply(raw) -> Reply:
    if not isinstance(raw, dict):
        raw = {}
    rows = []
    for group in _as_list(raw.get("choices")):
        category = "普通" if group.get("category") == "normal" else "钢铁"
        rows.append([category, "、".join(str(item) for item in group.get("choices") or [])])
    return _world_reply(
        "无尽回廊", "本周灵化 · %s" % _state_label(raw.get("state")),
        ["模式", "可选"], rows,
        "数据 warframestat.us · 世界状态", "回廊",
        ratios=[0.8, 2.8], accent=imagecard.PURPLE)


def archon_reply(raw) -> Reply:
    if not isinstance(raw, dict):
        raw = {}
    rows = []
    for mission in _as_list(raw.get("missions")):
        rows.append([
            terms.localize_node(str(mission.get("node") or "—")),
            terms.localize_mission(str(mission.get("type") or "—")),
            "执刑官",
        ])
    return _world_reply(
        "执刑官猎杀", "%s · %s" % (
            raw.get("boss") or "—", terms.localize_faction(str(raw.get("faction") or "—"))),
        ["节点", "任务", "类型"], rows,
        "数据 warframestat.us · 世界状态", "执刑官",
        ratios=[2.0, 1.2, 0.8], accent=imagecard.GOLD)


def arbitration_schedule_reply(entries, title: str = "今日仲裁",
                               empty: str = "") -> Reply:
    rows = []
    highlights = []
    lines = []
    for start, end, node, tier in entries[:20]:
        when = time.strftime("%m-%d %H:%M", time.localtime(start))
        rows.append([when, node.display_name, node.mission, tier])
        lines.append("%s %s · %s · %s" % (
            when, node.display_name, node.mission, tier))
        color = _rating_color(tier)
        highlights.append([None, None, None, color])
    if not rows:
        return Reply(text=empty or "今天没有更多仲裁。")
    return _world_reply(
        title, "按 arbi.wf.wiki 轮换表", ["时间", "节点", "任务", "评级"], rows,
        "数据 arbi.wf.wiki · 轮换表", "仲裁",
        text_lines=lines, ratios=[1.0, 1.8, 0.9, 0.6],
        accent=imagecard.GOLD, note="时间按本机时区显示。")


def research_reply(items, label: str = "科研") -> Reply:
    rows = []
    text_lines = []
    for item in items:
        research = terms.localize_research(str(item.get("typeKey") or ""))
        for mission in _as_list(item.get("missions")):
            deviation = terms.localize_text(
                str((mission.get("deviation") or {}).get("name") or "—"))
            risks = "、".join(
                terms.localize_text(str(risk.get("name") or ""))
                for risk in _as_list(mission.get("risks"))[:2])
            rows.append([
                research,
                terms.localize_mission(str(mission.get("missionType") or "—")),
                terms.localize_faction(str(mission.get("faction") or "—")),
                deviation,
                risks or "—",
            ])
            text_lines.append("%s / %s / %s / %s" % (
                research,
                terms.localize_mission(str(mission.get("missionType") or "—")),
                terms.localize_faction(str(mission.get("faction") or "—")),
                deviation))
    if not rows:
        return Reply(text="当前没有可用的%s数据。" % label)
    modifiers = []
    for item in items:
        for value in item.get("personalModifiers") or []:
            if isinstance(value, dict):
                modifiers.append(terms.localize_text(
                    str(value.get("name") or value.get("key") or "")))
            else:
                modifiers.append(str(value))
    return _world_reply(
        label, "本周科研任务与个人修正",
        ["类型", "任务", "敌人", "偏差", "风险"], rows,
        "数据 warframestat.us · 科研", "科研",
        text_lines=text_lines, ratios=[0.8, 0.9, 0.9, 1.2, 1.8],
        accent=imagecard.PURPLE,
        note=("个人修正：" + "、".join(modifiers[:8])) if modifiers else "")


def _json_table(raw, hard: bool = False) -> list:
    rows = []
    for item in _as_list(raw):
        if hard and not item.get("isHard"):
            continue
        rows.append(item)
    return rows


def storms_reply(raw) -> Reply:
    now = time.time()
    items = [item for item in _as_list(raw)
             if item.get("isStorm")
             and parse_timestamp(item.get("expiry")) > now]
    items.sort(key=lambda item: (
        int(item.get("tierNum") or 99),
        parse_timestamp(item.get("expiry"))))
    rows = []
    lines = []
    for item in items[:15]:
        tier = terms.localize_relic_tier(str(item.get("tier") or "—"))
        mission = terms.localize_storm_mission(
            str(item.get("missionType") or "—"))
        node = terms.localize_storm_node(str(item.get("node") or "—"))
        enemy = terms.localize_storm_enemy(str(item.get("enemy") or "—"))
        rows.append([tier, mission, node, enemy,
                     countdown(parse_timestamp(item.get("expiry")))])
        lines.append("%s / %s / %s / %s" % (tier, mission, node, enemy))
    if not rows:
        return _world_reply("虚空风暴", "当前没有活跃的风暴裂缝", [], [],
                            "数据 warframestat.us", "风暴")
    return _world_reply(
        "虚空风暴", "九重天 Railjack 风暴裂缝 · %d 条" % len(rows),
        ["遗物", "任务", "节点", "敌人", "剩余"], rows,
        "数据 warframestat.us · 虚空风暴", "风暴",
        text_lines=lines, ratios=[0.7, 0.8, 2.8, 0.6, 0.8],
        accent=imagecard.CYAN)


def fissures_reply(raw, hard: bool = False) -> Reply:
    rows = []
    now = time.time()
    if hard:
        items = [item for item in _as_list(raw)
                 if item.get("isHard") and not item.get("isStorm")
                 and parse_timestamp(item.get("expiry")) > now]
    else:
        items = [item for item in _as_list(raw)
                 if not item.get("isHard") and not item.get("isStorm")
                 and parse_timestamp(item.get("expiry")) > now]
    for row in items[:15]:
        rows.append([
            terms.localize_node(str(row.get("node") or "—")),
            terms.localize_mission(str(row.get("missionType") or "—")),
            terms.localize_relic_tier(str(row.get("tier") or "—")),
            terms.localize_faction(str(row.get("enemy") or "—")),
            countdown(parse_timestamp(row.get("expiry"))),
        ])
    title = "钢铁裂缝" if hard else "裂缝"
    return _world_reply(
        title, "当前 %d 条%s，显示前 15 条" % (
            len(items), "钢铁之路" if hard else ""),
        ["节点", "任务", "等级", "敌人", "剩余"], rows,
        "数据 warframestat.us · 世界状态", title,
        ratios=[1.7, 1.0, 0.7, 0.9, 0.9])


BOUNTY_LOCATIONS = {
    "Ostrons": "希图斯",
    "Solaris United": "福尔图娜",
    "Entrati": "火卫二",
    "Cavia": "解剖圣所",
    "The Hex": "霍瓦尼亚",
    "The Holdfasts": "扎里曼",
    "Necraloid": "火卫二",
}


BOUNTY_ORDER = ["Ostrons", "Solaris United", "Entrati", "Cavia",
                "The Hex", "The Holdfasts"]


def _bounty_level(job) -> str:
    levels = _as_list(job.get("enemyLevels"))
    if len(levels) >= 2:
        if str(levels[0]) == str(levels[1]):
            return str(levels[0])
        return "%s–%s" % (levels[0], levels[1])
    if levels:
        return str(levels[0])
    return "—"


def _bounty_key_rewards(job, limit: int = 2) -> str:
    rare = []
    blueprints = []
    fallback = []
    for drop in _as_list(job.get("rewardPoolDrops")):
        if not isinstance(drop, dict) or not drop.get("item"):
            continue
        raw = str(drop["item"])
        item = terms.localize_reward(raw)
        if item not in fallback:
            fallback.append(item)
        if str(drop.get("rarity") or "").lower() == "rare":
            if item not in rare:
                rare.append(item)
            if ("Blueprint" in raw or "蓝图" in item) and item not in blueprints:
                blueprints.append(item)
    values = blueprints or rare or fallback
    return "、".join(values[:limit]) or "—"


CETUS_TENTS = {
    "A": ["取回被盗器物", "间谍捕手", "破坏原型机"],
    "B": ["捕获新克隆尼指挥官", "宰杀敌人", "间谍捕手"],
    "C": ["削弱克隆尼据点", "捕获敌方领袖", "捕获克隆尼指挥官"],
}


def cetus_reply(cycle, raw) -> Reply:
    jobs = []
    seen = set()
    for syndicate in _as_list(raw):
        if syndicate.get("syndicate") != "Ostrons":
            continue
        for job in _as_list(syndicate.get("jobs")):
            title = terms.localize_bounty(
                str(job.get("type") or job.get("jobType") or "赏金"))
            key = (title, str(job.get("expiry") or ""))
            if key in seen:
                continue
            seen.add(key)
            jobs.append((job, title))
    if not jobs:
        return Reply(text="希图斯当前没有可接赏金。")

    numbers = ["一", "二", "三", "四", "五", "六", "七"]
    rows = []
    text_lines = []
    for index, (job, title) in enumerate(jobs[:7]):
        label = "赏金%s" % (numbers[index] if index < len(numbers)
                           else str(index + 1))
        rows.append([label, title, _bounty_level(job),
                     _bounty_key_rewards(job),
                     countdown(parse_timestamp(job.get("expiry")))])
        text_lines.append("%s / %s / %s" % (label, title, _bounty_level(job)))
    for key in ("A", "B", "C"):
        missions = "·".join(CETUS_TENTS[key])
        rows.append(["帐篷 %s" % key, missions, "—", "—", "—"])
        text_lines.append("帐篷 %s / %s" % (key, " / ".join(CETUS_TENTS[key])))

    state = _state_label((cycle or {}).get("state"))
    time_left = terms.localize_duration(str((cycle or {}).get("timeLeft") or "—"))
    return _world_reply(
        "希图斯赏金", "当前 %s · 剩余 %s ｜ 7 档赏金 + 帐篷 A/B/C" % (
            state, time_left),
        ["档位", "任务", "等级", "奖励", "剩余"], rows,
        "数据 warframestat.us · 希图斯", "希图斯",
        text_lines=text_lines, ratios=[0.7, 2.8, 0.6, 1.0, 0.8],
        accent=imagecard.GREEN,
        note="帐篷 A/B/C 各 3 个任务；赏金按当前 Ostrons 任务顺序显示。")


def translation_reply(query: str, zh: str, en: str,
                     source: str = "", url: str = "") -> Reply:
    zh = str(zh or "").strip() or "—"
    en = str(en or "").strip() or "—"
    lines = [
        "【中英对照】",
        "中文：%s" % zh,
        "英文：%s" % en,
    ]
    if source:
        lines.append("来源：%s" % source)
    if url:
        lines.append(url)
    if zh != "—" and en != "—" and zh.strip().lower() == en.strip().lower():
        lines.append("提示：该名称目前没有独立中文译名。")
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "中英对照", query,
            ["语言", "名称"],
            [["中文", zh], ["英文", en]],
            source or "warframe market", accent=imagecard.CYAN,
            chip="翻译", ratios=[0.9, 4.0]))


def bounties_reply(raw) -> Reply:
    rows = []
    seen = set()
    by_name = {}
    for syndicate in _as_list(raw):
        name = str(syndicate.get("syndicate") or "")
        if name and name not in by_name:
            by_name[name] = syndicate

    ordered = list(BOUNTY_ORDER) + [
        name for name in by_name if name not in BOUNTY_ORDER]
    for name in ordered:
        syndicate = by_name.get(name)
        if not syndicate:
            continue
        jobs = _as_list(syndicate.get("jobs"))
        location = BOUNTY_LOCATIONS.get(name, terms.localize_syndicate(name))
        if not jobs:
            if name in BOUNTY_ORDER:
                rows.append(["%s · %s" % (terms.localize_syndicate(name), location),
                             "暂无可接赏金", "—", "—"])
            continue
        for job in jobs:
            title = terms.localize_bounty(
                str(job.get("type") or job.get("jobType") or "赏金"))
            key = (name, title)
            if key in seen:
                continue
            seen.add(key)
            drops = _as_list(job.get("rewardPoolDrops"))
            rewards = []
            for drop in drops[:2]:
                if isinstance(drop, dict) and drop.get("item"):
                    rewards.append(terms.localize_reward(str(drop["item"])))
            if not rewards:
                rewards = [str(value) for value in _as_list(job.get("rewardPool"))[:2]]
            rows.append([
                "%s · %s" % (terms.localize_syndicate(name), location),
                title,
                "、".join(rewards) or "—",
                countdown(parse_timestamp(job.get("expiry"))),
            ])
    rows = rows[:30]
    if not rows:
        return _world_reply("赏金", "当前没有可接赏金", [], [],
                            "数据 warframestat.us", "赏金")
    return _world_reply(
        "赏金", "各大 NPC 可接赏金", ["NPC / 地点", "任务", "奖励", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "赏金",
        ratios=[1.5, 1.8, 1.7, 0.8], accent=imagecard.GREEN)


def worldstate_reply(data: dict) -> Reply:
    trader = data.get("voidTrader") or {}
    sortie = data.get("sortie") or {}
    arbitration = data.get("arbitration") or {}
    steel = data.get("steelPath") or {}
    nightwave = data.get("nightwave") or {}
    cetus = data.get("cetusCycle") or {}
    circuit = data.get("duviriCycle") or {}
    archon = data.get("archonHunt") or {}
    archimedeas = data.get("archimedeas") or []
    deals = data.get("dailyDeals") or []
    fissures = data.get("fissures") or []
    alerts = data.get("alerts") or []
    invasions = data.get("invasions") or []
    news = data.get("news") or []
    events = data.get("events") or []
    bounties = data.get("syndicateMissions") or []
    rows = [
        ["奸商", terms.localize_location(str(trader.get("location") or "—")),
         countdown(parse_timestamp(trader.get("expiry")))],
        ["突击", terms.localize_boss(str(sortie.get("boss") or "—")),
         countdown(parse_timestamp(sortie.get("expiry")))],
        ["仲裁", terms.localize_node(str(arbitration.get("node") or "—")),
         countdown(parse_timestamp(arbitration.get("expiry")))],
        ["警报", "%d 条" % len(alerts), "—"],
        ["入侵", "%d 条" % len(invasions), "—"],
        ["钢铁侵袭", terms.localize_text(str((steel.get("currentReward") or {}).get("name") or "—")),
         countdown(parse_timestamp((steel.get("incursions") or {}).get("expiry")))],
        ["电波", "第 %s 季 · %d 个挑战" % (
            nightwave.get("season") or "?", len(nightwave.get("activeChallenges") or [])), "—"],
        ["平原", _state_label(cetus.get("state")),
         terms.localize_duration(str(cetus.get("timeLeft") or "—"))],
        ["特惠", "%d 件" % len(deals), "—"],
        ["回廊", _state_label(circuit.get("state")),
         countdown(parse_timestamp(circuit.get("expiry")))],
        ["执刑官", terms.localize_boss(str(archon.get("boss") or "—")),
         countdown(parse_timestamp(archon.get("expiry")))],
        ["科研", "深层 / 时光" if len(archimedeas) >= 2 else "%d 项" % len(archimedeas),
         "—"],
        ["裂缝", "%d 条" % len(fissures), "—"],
        ["赏金", "%d 组" % len(bounties), "—"],
        ["新闻", "%d 条" % len(news), "—"],
        ["活动", "%d 个" % len(events), "—"],
    ]
    return _world_reply(
        "世界状态", "Warframe 全量状态总览", ["模块", "状态", "剩余"], rows,
        "数据 warframestat.us · 世界状态", "WORLD",
        ratios=[1.1, 2.0, 1.0], accent=imagecard.GOLD)


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
            "数据 warframe market · 每 5 分钟检查",
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
            "数据 warframe market · %s" % platform_label(alert.platform),
            accent=imagecard.GREEN, chip="已达成",
            note="提醒已自动关闭，需要的话可以重新添加。",
            ratios=[2, 1, 1]))


def help_reply(prefix: str) -> Reply:
    return Reply(text=render.render_help(prefix),
                 image=lambda: imagecard.help_card(prefix))


def error_reply(message: str) -> Reply:
    return Reply(text=message)
