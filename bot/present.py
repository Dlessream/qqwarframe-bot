"""把查询结果组装成 Reply（文字兜底 + 图片卡片）。"""

from __future__ import annotations

import time
from typing import List, Optional, Sequence

from . import autotrans, conquest, imagecard, render, terms
from . import names as names_db
from .alerts import Alert
from .market import Item, MarketStats, Order, PartQuote, SearchHit
from .replies import Reply
from .riven import RivenAuction, RivenWeapon
from .rivencalc import (RivenAnalysis, RollEvaluation, format_roll,
                        infer_config_label)
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
        text_value = format_roll(stat, name, value)
        rows.append([name, text_value])
        lines.append("%s %s" % (name, text_value))
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


def riven_ocr_reply(pairs, note: str = "") -> Reply:
    lines = ["【紫卡识别】"]
    if note:
        lines.append(note)
    for name, value in pairs:
        lines.append("  %s %s" % (name, format_roll(None, name, value)))
    lines.append("")
    lines.append("未识别到具体武器；发送完整紫卡截图，或带武器名可看范围/评分。")
    return Reply(text="\n".join(lines))


def rivencalc_rank(scale: float) -> int:
    """换算系数 → 紫卡等级；这里只用来显示，避免 present 直接依赖 rivenocr。"""
    return max(0, min(8, int(round(scale * 9.0)) - 1))


def riven_analysis_reply(analysis: RivenAnalysis,
                         evaluations: Sequence[RollEvaluation],
                         notes: Sequence[str] = (),
                         from_image: bool = False) -> Reply:
    scale = 1.0
    for item in evaluations or ():
        scale = getattr(item, "scale", 1.0) or 1.0
        break
    if scale < 1.0:
        level = "倾向 %.2f（%s）｜ 卡面约 Lv.%d，评分按 Lv.8" % (
            analysis.disposition, analysis.dots or "—",
            rivencalc_rank(scale))
    else:
        level = "倾向 %.2f（%s）｜ Lv.8 词条范围" % (
            analysis.disposition, analysis.dots or "—")
    lines = [
        "【%s】紫卡分析" % analysis.name,
        level,
    ]
    if analysis.name_en and analysis.name_en.lower() != analysis.name.lower():
        lines.append("英文：%s" % analysis.name_en)

    if evaluations:
        lines.append("")
        lines.append("你的词条（按 %s 配置比较）：" % infer_config_label(evaluations))
        for evaluation in evaluations:
            if evaluation.stat is None:
                lines.append("  %s %s：未识别（该武器没有这个词条）" % (
                    evaluation.name, format_roll(None, evaluation.name,
                                                 evaluation.value)))
                continue
            lines.append("  %s %s：范围 %s，%s" % (
                evaluation.name, evaluation.stat.format(evaluation.value),
                evaluation.stat.range_text(getattr(evaluation, "scale", 1.0)),
                evaluation.grade))
        if not from_image:
            lines.append("提示：有负面词条时请写成负值（例如 变焦=-30）。")

    if evaluations:
        config_label = infer_config_label(evaluations)
    else:
        config_label = "2+"
    if notes:
        lines.append("")
        lines.append("识别提示：")
        for note in notes:
            lines.append("  · %s" % note)
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
            analysis, evaluations,
            "数据 Warframe Wiki · 本地计算" +
            (" ｜ " + notes[0] if notes else "")))


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
        lines.append("商人已到访，但当前数据源没有库存清单。" if trader.active
                     else "当前没有库存数据（商人未到访）。")

    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "Baro Ki'Teer · 虚空商人", subtitle,
            ["物品", "杜卡德", "星币"], rows,
            "数据 世界状态聚合源 · 游戏内售价",
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
            "数据 世界状态聚合源 · 每日刷新",
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


def _arbi_mission(text) -> str:
    """arbi 节点表里偶尔带英文派系前缀（INFESTED 资源回收）。"""
    raw = str(text or "")
    if raw.upper().startswith("INFESTED "):
        return "感染者" + raw[len("INFESTED "):]
    return raw


def arbitration_reply(arbitration: Arbitration, rating: Optional[str] = None,
                      node=None, note: str = "",
                      rating_source: str = "") -> Reply:
    tier = rating or ""
    source_label = {"community": "社区梯队", "manual": "人工评级",
                    "local": "本地推算"}.get(rating_source, "")
    rating_text = tier
    if tier and source_label:
        rating_text = "%s（%s）" % (tier, source_label)
    subtitle = "剩余 %s" % countdown(arbitration.end)
    if rating_text:
        subtitle += " ｜ 评级 %s" % rating_text
    name = node.display_name if node is not None else (arbitration.node or "—")
    mission = (_arbi_mission(node.mission) if node is not None
               else (arbitration.mission or "—"))
    enemy = node.faction if node is not None else (arbitration.enemy or "—")
    enemy = terms.localize_faction(str(enemy))
    system = node.system if node is not None else "—"
    level = node.level_text if node is not None else "—"
    rows = [[name, system, mission, enemy, level, tier or "—"]]

    lines = ["【仲裁】", subtitle, "节点：%s" % name]
    if node is not None and node.system:
        lines.append("星球：%s" % node.system)
    lines.append("类型：%s" % mission)
    lines.append("敌人：%s" % enemy)
    if node is not None and node.max_level:
        lines.append("等级：%s" % node.level_text)
    if rating_text:
        lines.append("评级：%s" % rating_text)
    if rating_source == "local":
        lines.append("说明：该节点不在社区梯队里，评级由任务类型与敌人等级推算。")
    if note:
        lines.append(note)

    footer = "数据 世界状态聚合源"
    if rating or node is not None:
        footer += " + arbi.wf.wiki"
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "仲裁任务", subtitle,
            ["节点", "星球", "类型", "敌人", "等级", "评级"], rows,
            footer, accent=_rating_color(tier),
            chip="仲裁 %s" % tier if tier else "仲裁",
            note=note,
            ratios=[2.0, 0.8, 1.0, 1.0, 0.8, 0.6],
            highlights=[[_rating_color(tier), None, None, None, None,
                         _rating_color(tier)]]))


def arbitration_tiers_reply(data, current_key: str = "",
                            include_local: bool = False) -> Reply:
    """仲裁评级总表：社区梯队 + 人工评级；include_local 时附本地推算。"""
    rows = []
    highlights = []
    counts = data.counts()
    current = data.nodes.get(current_key) if current_key else None
    lines = ["【仲裁评级】"]

    header = "社区梯队 %d 个" % counts.get("community", 0)
    if counts.get("manual"):
        header += " ｜ 人工 %d 个" % counts["manual"]
    if data.use_local:
        header += " ｜ 本地推算 %d 个" % counts.get("local", 0)
    lines.append(header)
    lines.append("优先级：社区梯队 > 人工评级 > 本地推算")

    if current is not None:
        lines.insert(1, "当前：%s · %s · 评级 %s" % (
            current.display_name, _arbi_mission(current.mission),
            data.rating_label(current.key)))

    groups = data.groups()
    if not include_local and data.use_local:
        local_groups = data.local_groups()
        if local_groups:
            local_total = sum(len(nodes) for _tier, nodes in local_groups)
            lines.append("另有 %d 个节点只有本地推算评级，"
                         "输入「仲裁评级 本地」查看。" % local_total)
    if include_local:
        groups = groups + data.local_groups()

    limit = 40 if include_local else 200
    truncated = 0
    for tier, nodes in groups:
        for node in nodes:
            _tier, source = data.resolve(node.key)
            label = {"community": "社区", "manual": "人工",
                     "local": "本地"}.get(source, "—")
            if len(rows) >= limit:
                truncated += 1
                continue
            rows.append([tier, node.name, node.system,
                         _arbi_mission(node.mission), node.level_text, label])
            if current_key and node.key == current_key:
                highlights.append([imagecard.GOLD] * 6)
            else:
                highlights.append([_rating_color(tier), None, None, None,
                                   None, None])
        lines.append("%s：%s" % (tier, "；".join(
            "%s（%s）%s" % (node.name, node.system,
                            _arbi_mission(node.mission))
            for node in nodes)))

    if not rows:
        return Reply(text="还没有任何仲裁评级数据。")
    if truncated:
        lines.append("卡片只列前 %d 个节点，其余 %d 个在控制台查看。" % (
            limit, truncated))

    subtitle = "共 %d 个节点 · 社区 %d / 人工 %d / 本地 %d" % (
        len(data.nodes), counts.get("community", 0), counts.get("manual", 0),
        counts.get("local", 0))
    footer = "数据 arbi.wf.wiki · 本地推算取自任务类型与敌人等级"
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "仲裁评级", subtitle,
            ["梯队", "节点", "星球", "任务", "等级", "来源"], rows,
            footer, accent=imagecard.GOLD, chip="评级",
            ratios=[0.5, 1.6, 0.7, 1.0, 0.8, 0.6],
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
            parts.append(terms.localize_reward(
                str(item.get("type") or item.get("item") or "?")))
    for item in _as_list(reward.get("countedItems"))[:2]:
        if isinstance(item, dict):
            count = item.get("count") or 1
            parts.append("%s 个 %s" % (count, terms.localize_reward(
                str(item.get("type") or "?"))))
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
        return _world_reply("世界警报", "当前没有活跃警报", [], [], "数据 世界状态聚合源", "ALERT")
    return _world_reply(
        "世界警报", "共 %d 条" % len(rows),
        ["节点", "任务", "敌人", "奖励", "剩余"], rows,
        "数据 世界状态聚合源", "警报",
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
        return _world_reply("入侵", "当前没有入侵", [], [], "数据 世界状态聚合源", "入侵")
    return _world_reply(
        "入侵", "共 %d 条" % len(rows),
        ["节点", "进攻", "防守", "进度", "奖励"], rows,
        "数据 世界状态聚合源", "入侵",
        ratios=[2.0, 0.9, 0.9, 0.8, 1.6])


def steel_path_reply(raw) -> Reply:
    if not isinstance(raw, dict):
        raw = {}
    reward = raw.get("currentReward") or {}
    incursions = raw.get("incursions") or {}
    rows = [
        ["当前奖励", terms.localize_reward(str(reward.get("name") or "—")),
         "%s 精华" % reward.get("cost") if reward.get("cost") else "—"],
        ["侵袭周期", "本日", countdown(parse_timestamp(incursions.get("expiry")))],
        ["轮换", "%d 项" % len(_as_list(raw.get("rotation"))),
         countdown(parse_timestamp(raw.get("expiry")))],
    ]
    return _world_reply(
        "钢铁侵袭", "泰辛 · 钢铁之路", ["项目", "内容", "剩余"], rows,
        "数据 世界状态聚合源", "STEEL",
        ratios=[1.0, 2.0, 1.2], accent=imagecard.CYAN)


def _auto_translate(values: Sequence[str], budget: float = 3.0) -> List[str]:
    """把英文短句批量换成中文，整体限时，避免因接口慢拖住回复。"""
    deadline = time.time() + budget
    result = []
    for value in values:
        text = str(value or "")
        if time.time() > deadline:
            result.append(text)
            continue
        result.append(autotrans.to_chinese(text))
    return result


def nightwave_reply(raw) -> Reply:
    if not isinstance(raw, dict):
        raw = {}
    items = _as_list(raw.get("activeChallenges"))[:12]
    titles = _auto_translate([str(item.get("title") or "—") for item in items])
    rows = []
    for item, title in zip(items, titles):
        rows.append([
            "每日" if item.get("isDaily") else "每周",
            title,
            "%s 声望" % _format_int(item.get("reputation")),
            countdown(parse_timestamp(item.get("expiry"))),
        ])
    return _world_reply(
        "午夜电波", "第 %s 季 · %d 个挑战" % (
            raw.get("season") or "?", len(rows)),
        ["类型", "挑战", "声望", "剩余"], rows,
        "数据 世界状态聚合源", "电波",
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
        "数据 世界状态聚合源", "平原",
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
        return _world_reply("今日特惠", "当前没有特惠", [], [], "数据 世界状态聚合源", "特惠")
    return _world_reply(
        "今日特惠", "每日商店折扣", ["物品", "原价", "现价", "折扣", "剩余"], rows,
        "数据 世界状态聚合源", "特惠",
        ratios=[1.8, 0.8, 0.8, 0.7, 0.9], accent=imagecard.GOLD)


def news_reply(raw) -> Reply:
    items = _as_list(raw)[:10]
    messages = _auto_translate([
        terms.localize_phrase(str(item.get("message") or "—")) for item in items])
    rows = []
    text_lines = []
    for item, message in zip(items, messages):
        rows.append([message[:36], str(item.get("date") or "")[:10]])
        text_lines.append(message)
    return _world_reply(
        "新闻", "官方新闻与公告", ["标题", "日期"], rows,
        "数据 世界状态聚合源", "NEWS",
        text_lines=text_lines, ratios=[2.4, 0.8], accent=imagecard.CYAN)


def events_reply(raw) -> Reply:
    items = _as_list(raw)[:10]
    labels = _auto_translate([
        terms.localize_phrase(
            str(event.get("description") or event.get("tooltip") or "—"))
        for event in items])
    rows = []
    for event, label in zip(items, labels):
        score = "%s / %s" % (event.get("currentScore", 0), event.get("maximumScore", 0))
        rows.append([
            label[:32],
            terms.localize_text(terms.localize_node(
                str(event.get("node") or "—"))),
            score,
            countdown(parse_timestamp(event.get("expiry"))),
        ])
    if not rows:
        return _world_reply("活动", "当前没有活动", [], [], "数据 世界状态聚合源", "活动")
    return _world_reply(
        "活动", "当前进行中的活动", ["活动", "节点", "进度", "剩余"], rows,
        "数据 世界状态聚合源", "活动",
        ratios=[2.0, 1.2, 0.9, 0.9], accent=imagecard.PURPLE)


def circuit_reply(raw) -> Reply:
    """无尽回廊本周灵化：普通模式给战甲、钢铁模式给武器，名字一律翻成国服中文。"""
    if not isinstance(raw, dict):
        raw = {}
    rows = []
    lines = []
    for group in _as_list(raw.get("choices")):
        hard = str(group.get("category") or "").lower() != "normal"
        picked = [str(item) for item in group.get("choices") or []]
        kinds = {names_db.kind(name) for name in picked}
        kind = kinds.pop() if len(kinds) == 1 else ""
        mode = "钢铁" if hard else "普通"
        label = "%s · %s" % (mode, kind) if kind else mode
        # 卡面只放中文名，一行放得下；英文原名留给文字兜底，方便对照 wiki / 交易站
        rows.append([label, "、".join(names_db.chinese(name) for name in picked)])
        lines.append("%s：%s" % (
            label, "、".join(names_db.label(name) for name in picked)))
    return _world_reply(
        "无尽回廊", "本周灵化 · %s" % _state_label(raw.get("state")),
        ["模式", "可选"], rows,
        "数据 世界状态聚合源 · 国服译名", "回廊", text_lines=lines,
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
        "数据 世界状态聚合源", "执刑官",
        ratios=[2.0, 1.2, 0.8], accent=imagecard.GOLD)


def arbitration_schedule_reply(entries, title: str = "今日仲裁（未来 24 小时）",
                               empty: str = "") -> Reply:
    rows = []
    highlights = []
    lines = []
    for start, end, node, tier in entries[:24]:
        when = time.strftime("%m-%d %H:%M", time.localtime(start))
        left = countdown(end)
        rows.append([when, left, node.display_name,
                     _arbi_mission(node.mission), tier])
        lines.append("%s（%s）%s · %s · %s" % (
            when, left, node.display_name, _arbi_mission(node.mission), tier))
        color = _rating_color(tier)
        highlights.append([None, None, None, None, color])
    if not rows:
        return Reply(text=empty or "未来 24 小时没有仲裁。")
    return _world_reply(
        title, "按 arbi.wf.wiki 轮换表 · 已过时的不列出",
        ["时间", "剩余", "节点", "任务", "评级"], rows,
        "数据 arbi.wf.wiki · 轮换表", "仲裁",
        text_lines=lines, ratios=[1.0, 0.8, 1.6, 0.9, 0.5],
        accent=imagecard.GOLD, note="时间按本机时区显示。")


def _conquest_entry(group: str, key: str, kind: str, raw) -> dict:
    """一条科研词条：优先按内部代号查官方中文名与说明。"""
    payload = raw if isinstance(raw, dict) else {}
    english = str(payload.get("name") or "")
    info = conquest.describe(group, key, kind, english)
    name = info.get("name") or terms.localize_text(english) or "—"
    return {"name": name, "desc": info.get("desc") or ""}


def research_reply(items, label: str = "科研") -> Reply:
    """科研推送：任务编号 + 偏差/风险（名称+说明）+ 全局变量。"""
    sections = []
    text_parts: List[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        type_key = str(item.get("typeKey") or "")
        kind = str(item.get("kind") or conquest.kind_of(type_key))
        section_label = conquest.label_of(type_key) or label
        remaining = ""
        expiry = parse_timestamp(item.get("expiry"))
        if expiry:
            remaining = "剩余 " + countdown(expiry)
        lines = ["【%s】本周任务 · 偏差 / 风险 / 全局变量" % section_label]
        if remaining:
            lines.append(remaining)
        missions = []
        for index, mission in enumerate(_as_list(item.get("missions")), 1):
            if not isinstance(mission, dict):
                continue
            mission_name = (conquest.mission_type(
                str(mission.get("missionTypeKey") or ""))
                or terms.localize_mission(str(mission.get("missionType") or "—")))
            deviation = _conquest_entry(
                "variants", str(mission.get("variantKey") or ""), kind,
                mission.get("deviation"))
            risks = []
            keys = _as_list(mission.get("conditionKeys"))
            for position, raw in enumerate(_as_list(mission.get("risks"))):
                key = str(keys[position]) if position < len(keys) else ""
                risks.append(_conquest_entry("risks", key, "", raw))
            missions.append({"index": index, "name": mission_name,
                             "deviation": deviation, "risks": risks})
            lines.append("")
            lines.append("%d. %s" % (index, mission_name))
            lines.append(_entry_line("偏差", deviation))
            for risk in risks:
                lines.append(_entry_line("风险", risk))

        variables = []
        keys = _as_list(item.get("personalKeys"))
        for position, raw in enumerate(_as_list(item.get("personalModifiers"))):
            key = str(keys[position]) if position < len(keys) else ""
            variables.append(_conquest_entry("personal", key, "", raw))
        if variables:
            lines.append("")
            lines.append("全局变量")
            for variable in variables:
                lines.append(_entry_line("", variable, bullet="·"))
        text_parts.extend(lines)
        sections.append({"label": section_label, "remaining": remaining,
                         "missions": missions, "variables": variables})

    if not sections:
        return Reply(text="当前没有可用的%s数据。" % label)

    single = len(sections) == 1
    title = sections[0]["label"] if single else label
    subtitle = "本周任务 · 偏差 / 风险 / 全局变量"
    chip = sections[0]["remaining"] if single else ""
    footer = "数据 oracle.browse.wf · 官方中文字典"
    return Reply(
        text="\n".join(text_parts),
        image=lambda: imagecard.research_card(
            title, subtitle, sections, footer,
            accent=imagecard.PURPLE, chip=chip))


def _entry_line(tag: str, entry: dict, bullet: str = "") -> str:
    name = entry.get("name") or "—"
    desc = entry.get("desc") or ""
    prefix = ("%s " % bullet) if bullet else "  "
    head = ("%s%s · %s" % (prefix, tag, name)) if tag else ("%s%s" % (prefix, name))
    return "%s\n      %s" % (head, desc) if desc else head



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
    for item in items:
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
                            "数据 世界状态聚合源", "风暴")
    return _world_reply(
        "虚空风暴", "九重天风暴裂缝 · %d 条" % len(rows),
        ["遗物", "任务", "节点", "敌人", "剩余"], rows,
        "数据 世界状态聚合源 · 虚空风暴", "风暴",
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
    for row in items:
        rows.append([
            terms.localize_node(str(row.get("node") or "—")),
            terms.localize_mission(str(row.get("missionType") or "—")),
            terms.localize_relic_tier(str(row.get("tier") or "—")),
            terms.localize_faction(str(row.get("enemy") or "—")),
            countdown(parse_timestamp(row.get("expiry"))),
        ])
    title = "钢铁裂缝" if hard else "裂缝"
    source = ("数据 Warframe 官方 · 世界状态"
              if any(row.get("source") == "official" for row in items)
              else "数据 世界状态聚合源")
    return _world_reply(
        title, "当前 %d 条%s" % (
            len(items), "钢铁之路" if hard else ""),
        ["节点", "任务", "等级", "敌人", "剩余"], rows,
        source, title,
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
        "数据 世界状态聚合源 · 希图斯", "希图斯",
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
                             "暂无可接赏金", "—", "—", "—"])
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
                _bounty_level(job),
                "、".join(rewards) or "—",
                countdown(parse_timestamp(job.get("expiry"))),
            ])
    rows = rows[:30]
    if not rows:
        return _world_reply("赏金", "当前没有可接赏金", [], [],
                            "数据 世界状态聚合源", "赏金")
    return _world_reply(
        "赏金", "各据点可接的赏金",
        ["NPC / 地点", "任务", "等级", "奖励", "剩余"], rows,
        "数据 世界状态聚合源", "赏金",
        ratios=[1.5, 1.9, 0.6, 1.5, 0.7], accent=imagecard.GREEN)


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

    def missing(key: str) -> bool:
        """官方兜底源没有的模块，值为 None。"""
        return data.get(key, "absent") is None

    def count_row(label: str, key: str, value, text: str) -> list:
        if missing(key):
            return [label, "暂不可用", "—"]
        return [label, text, value]

    rows = [
        ["奸商", terms.localize_location(str(trader.get("location") or "—")),
         countdown(parse_timestamp(trader.get("expiry")))],
        ["突击", terms.localize_boss(str(sortie.get("boss") or "—")),
         countdown(parse_timestamp(sortie.get("expiry")))],
        ["仲裁", terms.localize_node(str(arbitration.get("node") or "—")),
         countdown(parse_timestamp(arbitration.get("expiry")))],
        ["警报", "%d 条" % len(alerts), "—"],
        ["入侵", "%d 条" % len(invasions), "—"],
        count_row("钢铁侵袭", "steelPath", "—", terms.localize_reward(
            str((steel.get("currentReward") or {}).get("name") or "—"))),
        count_row("电波", "nightwave", "—", "第 %s 季 · %d 个挑战" % (
            nightwave.get("season") or "?", len(nightwave.get("activeChallenges") or []))),
        ["平原", _state_label(cetus.get("state")),
         terms.localize_duration(str(cetus.get("timeLeft") or "—"))],
        count_row("特惠", "dailyDeals", "—", "%d 件" % len(deals)),
        count_row("回廊", "duviriCycle", "—", _state_label(circuit.get("state"))),
        count_row("执刑官", "archonHunt", "—",
                  terms.localize_boss(str(archon.get("boss") or "—"))),
        ["科研", "深层 / 时光" if len(archimedeas) >= 2 else "%d 项" % len(archimedeas),
         "—"],
        ["裂缝", "%d 条" % len(fissures), "—"],
        ["赏金", "%d 组" % len(bounties), "—"],
        count_row("新闻", "news", "—", "%d 条" % len(news)),
        count_row("活动", "events", "—", "%d 个" % len(events)),
    ]
    source = ("数据 Warframe 官方 · 世界状态" if data.get("source") == "official"
              else "数据 世界状态聚合源")
    return _world_reply(
        "世界状态", "星际战甲 · 全量状态总览", ["模块", "状态", "剩余"], rows,
        source, "WORLD",
        ratios=[1.1, 2.0, 1.0], accent=imagecard.GOLD,
        note=("标「暂不可用」的模块官方接口不提供，需要经第三方数据站访问"
              if data.get("source") == "official" else ""))


# ---------------------------------------------------------------------- 维基
def wiki_reply(query: str, page: Optional[WikiPage],
               hits: Sequence[WikiHit], translated: str = "",
               note: str = "", english: str = "") -> Reply:
    text = render.render_wiki(query, page, hits, translated, note=note)
    if page is None:
        body = "英文维基没有找到这个关键词。\n"
        if english and english.strip().lower() != str(query or "").strip().lower():
            body += "已按英文词条「%s」检索，仍然没有结果。\n" % english
        body += "可以换英文名或更短的关键词，例如 Volt、Rubico、Riven Mod。"
        from urllib.parse import quote
        search_url = ("https://wiki.warframe.com/w/Special:Search?search="
                      + quote(str(english or query or "")))
        return Reply(
            text=text,
            image=lambda: imagecard.wiki_card(
                "未找到：%s" % query, body, search_url,
                "英文维基 · wiki.warframe.com"))
    label = render.wiki_label(page.url)
    footer = "%s · wiki.warframe.com" % label
    if translated:
        footer += " · 中文翻译"
    if note:
        footer += " · 中文检索"
    body = translated or page.extract
    if note:
        body = note + "\n\n" + body
    return Reply(text=text, image=lambda: imagecard.wiki_card(
        page.title, body, page.url, footer))


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


# ---------------------------------------------------------------------- 价格榜
def junk_label(ducats: int) -> str:
    labels = {100: "金垃圾", 45: "银垃圾", 15: "铜垃圾", 65: "假金垃圾", 25: "假银垃圾"}
    return labels.get(int(ducats), "垃圾")


def _fresh(updated: float) -> str:
    if not updated:
        return "未更新"
    seconds = max(0.0, time.time() - updated)
    if seconds < 90:
        return "刚刚更新"
    if seconds < 3600:
        return "%d 分钟前更新" % int(seconds // 60)
    return "%.1f 小时前更新" % (seconds / 3600)


def junk_reply(ducats: int, quotes: Sequence, stats: dict, name_of,
               limit: int = 10, platform: str = "PC") -> Reply:
    """金/银/铜垃圾行情卡。"""
    label = stats.get("label") or "垃圾"
    min_price = stats.get("min") or 0
    median = stats.get("median") or 0
    group_price = min_price * 6
    lines = [
        "【%s】杜卡特 %d" % (label, ducats),
        "已扫描 %d 件 ｜ 最低 %dp ｜ 中位 %dp ｜ 一组 6 个约 %dp" % (
            stats.get("ready", 0), min_price, median, group_price),
        "",
    ]
    rows = []
    for index, quote in enumerate(list(quotes)[:limit], 1):
        name = name_of(quote.slug)
        lines.append("%d. %s  %dp（%d 单）" % (index, name, quote.price, quote.orders))
        ratio = "%.1f" % (ducats / quote.price) if quote.price > 0 else "—"
        rows.append([
            "%d" % index,
            name,
            "%dp" % quote.price,
            ratio,
            "%d" % quote.orders,
        ])
    if not rows:
        lines.append("还没有拿到这一档的报价，稍后再试。")

    note = "%s=杜卡特 %d 的 Prime 部件；一组 6 个按最低价估算。" % (label, ducats)
    total = stats.get("total") or 0
    if total and stats.get("ready", 0) < total:
        note += " 已扫描 %d/%d 件。" % (stats.get("ready", 0), total)
    subtitle = "最低 %dp ｜ 中位 %dp ｜ 一组约 %dp ｜ %d 件样本" % (
        min_price, median, group_price, stats.get("ready", 0))
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "%s · %d 杜卡特" % (label, ducats), subtitle,
            ["#", "部件", "最低在售", "杜卡特/白金", "挂单"], rows,
            "数据 warframe market · %s ｜ %s" % (platform, _fresh(stats.get("updated", 0))),
            accent=imagecard.GOLD if ducats >= 100 else imagecard.CYAN,
            chip=label, note=note,
            highlights=[[None, None, imagecard.GOLD, imagecard.CYAN, None]] * len(rows),
            ratios=[0.4, 3.6, 1.1, 1.3, 0.8],
            aligns=["right", "left", "right", "right", "right"]))


def junk_overview_reply(groups: Sequence[dict], platform: str = "PC") -> Reply:
    """金/银/铜垃圾一览。"""
    lines = ["【Prime 垃圾行情】按杜卡特档位"]
    rows = []
    highlights = []
    for group in groups:
        min_price = group.get("min") or 0
        median = group.get("median") or 0
        lines.append("%s（%d）：最低 %dp ｜ 中位 %dp ｜ 一组约 %dp ｜ %d 件" % (
            group.get("label"), group.get("ducats", 0), min_price, median,
            min_price * 6, group.get("ready", 0)))
        rows.append([
            "%s" % group.get("label"),
            "%d" % group.get("ducats", 0),
            "%dp" % min_price if min_price else "—",
            "%dp" % (min_price * 6) if min_price else "—",
            "%dp" % median if median else "—",
            "%d" % group.get("ready", 0),
        ])
        highlights.append([
            imagecard.GOLD if group.get("ducats", 0) >= 100 else None,
            None, imagecard.GOLD, imagecard.CYAN, None, None,
        ])
    updated = max((group.get("updated") or 0 for group in groups), default=0.0)
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "Prime 垃圾行情", "金 / 银 / 铜垃圾的当前地板价",
            ["档位", "杜卡特", "最低在售", "一组 6 个", "中位", "样本"], rows,
            "数据 warframe market · %s ｜ %s" % (platform, _fresh(updated)),
            accent=imagecard.GOLD, chip="垃圾",
            note="金垃圾=100 杜卡特、银垃圾=45、铜垃圾=15；假金 65 / 假银 25 也一并统计。",
            highlights=highlights,
            ratios=[1.4, 0.8, 1.2, 1.2, 1.0, 0.8],
            aligns=["left", "right", "right", "right", "right", "right"]))


def frame_rank_reply(quotes: Sequence, name_of, coverage: tuple,
                     limit: int = 15, platform: str = "PC") -> Reply:
    """Prime 战甲价格榜（高 → 低）。"""
    rows = []
    lines = ["【Prime 战甲价格榜】按最低在售从高到低"]
    for index, quote in enumerate(list(quotes)[:limit], 1):
        name = name_of(quote.slug)
        lines.append("%d. %s  %dp（收 %dp）" % (
            index, name, quote.price, quote.buy))
        rows.append([
            "%d" % index,
            name,
            "%dp" % quote.price,
            "%dp" % quote.buy if quote.buy else "—",
        ])
    if not rows:
        lines.append("还没有拿到战甲报价，稍后再试。")
    ready, total = coverage if coverage else (0, 0)
    updated = max((quote.updated for quote in quotes), default=0.0)
    note = "战甲榜按 Prime 整套最低在售排序；已扫描 %d/%d 套，排名随扫描继续更新。" % (
        ready, total)
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "Prime 战甲价格榜", "最低在售从高到低 ｜ %d/%d 套已扫描" % (ready, total),
            ["#", "战甲", "最低在售", "最高收价"], rows,
            "数据 warframe market · %s ｜ %s" % (platform, _fresh(updated)),
            accent=imagecard.CYAN, chip="战甲",
            note=note,
            highlights=[[None, None, imagecard.GOLD, imagecard.GREEN]] * len(rows),
            ratios=[0.5, 3.6, 1.2, 1.2],
            aligns=["right", "left", "right", "right"]))


def riven_rank_reply(quotes: Sequence, name_of, coverage: tuple,
                     limit: int = 12) -> Reply:
    """最贵零洗紫卡榜（高 → 低）。"""
    rows = []
    lines = ["【零洗紫卡价格榜】按当前最高价从高到低"]
    for index, quote in enumerate(list(quotes)[:limit], 1):
        attrs = " / ".join(quote.attributes[:3]) or "未洗词条"
        lines.append("%d. %s  %dp（%s）· %s · %s" % (
            index, name_of(quote.slug), quote.price,
            "一口价" if quote.buyout else "起拍价", attrs, quote.seller))
        rows.append([
            "%d" % index,
            name_of(quote.slug),
            "%dp" % quote.price,
            " / ".join(quote.attributes[:2]) or "未洗词条",
        ])
    if not rows:
        lines.append("还没有拿到零洗紫卡报价，稍后再试。")
    ready, total = coverage if coverage else (0, 0)
    updated = max((quote.updated for quote in quotes), default=0.0)
    note = ("零洗 = re_rolls 0；已扫描 %d/%d 把武器，取每把武器最贵的零洗拍卖再全局排序。"
            % (ready, total))
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "零洗紫卡价格榜", "每把武器取最贵零洗，再全局从高到低 ｜ %d/%d 把" % (ready, total),
            ["#", "武器", "价格", "词条"], rows,
            "数据 warframe market · %s" % _fresh(updated),
            accent=imagecard.PURPLE, chip="紫卡",
            note=note,
            highlights=[[None, None, imagecard.PURPLE, None]] * len(rows),
            ratios=[0.4, 1.7, 1.0, 3.9],
            aligns=["right", "left", "right", "left"]))


# ---------------------------------------------------------------------- 轮换
def _weapon_cell_rows(rows: Sequence[dict], accent) -> List[List[object]]:
    """武器行：名字 / 元素 / 加成，缺数据时写「未上报」。"""
    result = []
    for row in rows:
        percent = row.get("percent") or 0.0
        result.append([
            row.get("name") or "？",
            imagecard.Cell(row.get("element") or "未上报", color=accent),
            imagecard.Cell("%.1f%%" % percent if percent else "—",
                           color=imagecard.GOLD, bold=True),
        ])
    return result


def rotation_overview_reply(acrithis: dict, glast: dict, eleanor: dict,
                            error: str = "") -> Reply:
    """三店轮换总览：三个货架全部列全，武器逐把给数值。"""
    acrithis_rows = acrithis.get("rows") or []
    glast_rows = glast.get("rows") or []
    eleanor_rows = eleanor.get("rows") or []
    batch = eleanor.get("batch", "A")

    acrithis_rest = countdown(acrithis.get("next", 0))
    glast_rest = countdown(glast.get("next", 0))
    eleanor_rest = countdown(eleanor.get("next", 0))

    lines = ["【轮换商店】言录使 · 埃尔戈·格拉斯 · 埃莉诺"]
    lines.append("")
    lines.append("言录使（双衍王境 · 剩 %s）" % acrithis_rest)
    if acrithis_rows:
        for index, row in enumerate(acrithis_rows, 1):
            cost = row.get("cost") or 0
            lines.append("%d. %s  %s" % (index, row.get("name"),
                                        "%d 苦栓" % cost if cost else "价格未上报"))
    else:
        lines.append("本周清单还没抓到")
    if acrithis.get("observed"):
        lines.append("（wiki 上报日期：%s）" % acrithis.get("observed"))

    lines.append("")
    lines.append("格拉斯（佩兰数列 · 剩 %s）" % glast_rest)
    if glast_rows:
        for row in glast_rows:
            if row.get("element"):
                lines.append("%s：%s %.1f%%" % (row.get("name"), row.get("element"),
                                                row.get("percent", 0.0)))
            else:
                lines.append("%s：加成未上报" % row.get("name"))
    else:
        lines.append("5 把信条近战还没抓到")

    lines.append("")
    lines.append("埃莉诺（1999 · %s 批 · 剩 %s）" % (batch, eleanor_rest))
    if eleanor_rows:
        for row in eleanor_rows:
            if row.get("element"):
                lines.append("%s：%s %.1f%%" % (row.get("name"), row.get("element"),
                                                row.get("percent", 0.0)))
            else:
                lines.append("%s：加成未上报" % row.get("name"))
    else:
        lines.append("当前批次的终幕武器还没抓到")

    lines.append("")
    lines.append("明细发 #轮换 言录使 / #轮换 格拉斯 / #轮换 埃莉诺")

    acrithis_section = {
        "label": "言录使 · 双衍王境",
        "badge": "剩 " + acrithis_rest,
        "columns": [("商品", 3.1), ("价格", 1.0)],
        "rows": [[row.get("name") or "？",
                  imagecard.Cell("%d 苦栓" % (row.get("cost") or 0),
                                 color=imagecard.GOLD, bold=True)
                  if row.get("cost") else imagecard.Cell("价格未上报",
                                                         color=imagecard.MUTED)]
                 for row in acrithis_rows] or [["本周清单还没抓到", "—"]],
    }
    weapon_columns = [("武器", 2.9), ("元素", 1.0), ("加成", 1.0)]
    glast_section = {
        "label": "格拉斯 · 佩兰数列",
        "badge": "剩 " + glast_rest,
        "columns": weapon_columns,
        "rows": _weapon_cell_rows(glast_rows, imagecard.CYAN)
                or [["5 把信条近战还没抓到", "—", "—"]],
    }
    eleanor_section = {
        "label": "埃莉诺 · 1999 · %s 批" % batch,
        "badge": "剩 " + eleanor_rest,
        "columns": weapon_columns,
        "rows": _weapon_cell_rows(eleanor_rows, imagecard.PURPLE)
                or [["当前批次的终幕武器还没抓到", "—", "—"]],
    }

    updated = max(acrithis.get("fetched", 0), glast.get("fetched", 0),
                  eleanor.get("fetched", 0))
    note = "言录使每周一、格拉斯与埃莉诺每 4 天的 08:00（北京时间）刷新；数值由玩家上报。"
    if error:
        note = "wiki 抓取失败，下面是上次快照：%s" % error[:60]
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.sections_table(
            "轮换商店", "三个商店的当期货架与倒计时",
            [acrithis_section, glast_section, eleanor_section],
            "数据 wiki.warframe.com ｜ 玩家上报 ｜ %s" % _fresh(updated),
            accent=imagecard.GOLD, chip="轮换", note=note))


def acrithis_reply(view: dict) -> Reply:
    """言录使本周 5 件。"""
    rows_data = view.get("rows") or []
    rest = countdown(view.get("next", 0))
    lines = ["【言录使】本周 5 件兑换 ｜ 剩 %s" % rest]
    rows = []
    for index, row in enumerate(rows_data, 1):
        cost = row.get("cost") or 0
        lines.append("%d. %s  %d 苦栓" % (index, row.get("name"), cost))
        rows.append([str(index), row.get("name") or "？",
                     "%d 苦栓" % cost if cost else "—"])
    if not rows:
        lines.append("本周清单还没抓到：wiki 上由玩家上报，国内服务器偶尔抓不到。")
    if view.get("observed"):
        lines.append("（wiki 上报日期：%s）" % view.get("observed"))
    note = "本周 5 件由玩家在 wiki 上报（截至 %s）；每日赋能与 Captura 没有上报。" % (
        view.get("observed") or "未知日期")
    subtitle = "双衍王境居住舱 ｜ 苦栓兑换 ｜ 周一 08:00 换"
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "言录使 · 本周 5 件", subtitle, ["#", "商品", "价格"], rows,
            "数据 wiki.warframe.com ｜ %s" % _fresh(view.get("fetched", 0)),
            accent=imagecard.GOLD, chip="言录使", note=note,
            highlights=[[None, None, imagecard.CYAN]] * len(rows),
            ratios=[0.4, 4.2, 1.2],
            aligns=["right", "left", "right"]))


def _rotation_rows(rows: Sequence[dict]) -> List[List[str]]:
    result = []
    for row in rows:
        percent = row.get("percent") or 0.0
        result.append([row.get("name") or "？",
                       row.get("element") or "未上报",
                       "%.1f%%" % percent if percent else "—"])
    return result


def glast_reply(view: dict) -> Reply:
    """埃尔戈·格拉斯：信条近战 5 把。"""
    rows_data = view.get("rows") or []
    rest = countdown(view.get("next", 0))
    lines = ["【埃尔戈·格拉斯】信条近战 5 把 ｜ 剩 %s" % rest]
    for row in rows_data:
        if row.get("element"):
            lines.append("%s：%s %.1f%%" % (row.get("name"), row.get("element"),
                                            row.get("percent", 0.0)))
        else:
            lines.append("%s：加成未上报" % row.get("name"))
    lines.append("40 堕落全息密钥一把，任意中继站找佩兰数列的格拉斯。")
    if not view.get("complete"):
        lines.append("（部分数值还没抓到，wiki 抓取失败时会沿用上次快照）")
    note = "数值由玩家上报，实际以游戏内为准；每 4 天 08:00 换一轮元素与数值。"
    if not view.get("complete"):
        note = "部分数值还没抓到（wiki 抓取失败），稍后再试。"
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "埃尔戈·格拉斯 · 信条近战",
            "中继站 佩兰数列 ｜ 40 堕落全息密钥 ｜ 每 4 天 08:00 换加成",
            ["武器", "元素", "加成"], _rotation_rows(rows_data),
            "数据 wiki.warframe.com ｜ %s" % _fresh(view.get("fetched", 0)),
            accent=imagecard.CYAN, chip="格拉斯", note=note,
            highlights=[[None, imagecard.CYAN, imagecard.GOLD]] * len(rows_data),
            ratios=[3.4, 1.2, 1.2],
            aligns=["left", "left", "right"]))


def eleanor_reply(view: dict) -> Reply:
    """埃莉诺：当前批次的终幕武器。"""
    rows_data = view.get("rows") or []
    batch = view.get("batch", "A")
    rest = countdown(view.get("next", 0))
    lines = ["【埃莉诺】终幕武器 %s 批 ｜ 剩 %s" % (batch, rest)]
    for row in rows_data:
        if row.get("element"):
            lines.append("%s：%s %.1f%%" % (row.get("name"), row.get("element"),
                                            row.get("percent", 0.0)))
        else:
            lines.append("%s：加成未上报" % row.get("name"))
    if not view.get("complete"):
        lines.append("（部分数值还没抓到，wiki 抓取失败时会沿用上次快照）")
    note = "A / B 两批各 7 把交替，每 4 天 08:00 换批并重掷元素与数值。"
    if not view.get("complete"):
        note = "部分数值还没抓到（wiki 抓取失败），稍后再试。"
    return Reply(
        text="\n".join(lines),
        image=lambda: imagecard.build_table(
            "埃莉诺 · 终幕武器 %s 批" % batch,
            "霍瓦尼亚中央商场 ｜ 10 活体心肌细胞 ｜ 每 4 天换批",
            ["武器", "元素", "加成"], _rotation_rows(rows_data),
            "数据 wiki.warframe.com ｜ %s" % _fresh(view.get("fetched", 0)),
            accent=imagecard.PURPLE, chip="埃莉诺", note=note,
            highlights=[[None, imagecard.PURPLE, imagecard.GOLD]] * len(rows_data),
            ratios=[3.4, 1.2, 1.2],
            aligns=["left", "left", "right"]))


def help_reply(prefix: str) -> Reply:
    return Reply(text=render.render_help(prefix),
                 image=lambda: imagecard.help_card(prefix))

def error_reply(message: str) -> Reply:
    return Reply(text=message)
