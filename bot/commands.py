"""命令解析与业务路由（不涉及 QQ 连接，方便单测）。

所有查询结果都返回 Reply：图片卡片优先，同时保留文字版本做兜底。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import present
from . import ocr
from . import zh2en
from .alerts import AlertStore
from .arbi import ArbiClient, ArbiError
from .market import Item, MarketClient, MarketError, SearchHit
from .replies import Reply
from .riven import RivenClient
from .rivencalc import calculate, evaluate_roll, looks_like_stat
from .updater import DataUpdater
from .wiki import WikiClient, WikiError
from .worldstate import Arbitration, WorldStateClient, WorldStateError

LOGGER = logging.getLogger("qqwarframe.commands")

CQ_RE = re.compile(r"\[CQ:[^\]]*\]")

COMMANDS: Dict[str, str] = {
    "wm": "query",
    "查": "query", "查询": "query", "物品": "query", "装备": "query",
    "item": "query", "search": "query", "q": "query",
    "价格": "price", "行情": "price", "报价": "price", "price": "price", "p": "price",
    "战甲": "warframe", "warframe": "warframe", "wf": "warframe",
    "武器": "weapon", "weapon": "weapon", "w": "weapon",
    "mod": "mod", "mods": "mod", "卡片": "mod", "卡": "mod", "模组": "mod",
    "拆件": "set", "拆卖": "set", "拆": "set", "套装": "set", "parts": "set",
    "紫卡": "riven", "riven": "riven", "zk": "riven",
    "紫卡分析": "riven_analysis", "紫卡测评": "riven_analysis",
    "分析": "riven_analysis", "紫卡图片": "riven_analysis",
    "rivenanalysis": "riven_analysis", "zkfx": "riven_analysis",
    "奸商": "baro", "虚空商人": "baro", "baro": "baro",
    "突击": "sortie", "sortie": "sortie",
    "仲裁": "arbitration", "arbitration": "arbitration",
    "仲裁评级": "arbi_tiers", "仲裁梯队": "arbi_tiers",
    "arbitrationrating": "arbi_tiers",
    "世界状态": "world_summary", "worldstate": "world_summary",
    "警报": "world_alerts", "入侵": "world_invasions",
    "钢铁侵袭": "steel_path", "电波": "nightwave", "午夜电波": "nightwave",
    "平原": "cycles", "平原轮换": "cycles", "特惠": "deals",
    "今日特惠": "deals", "新闻": "news", "活动": "events",
    "回廊": "circuit", "无尽回廊": "circuit", "灵化": "circuit",
    "执刑官": "archon", "执刑官猎杀": "archon",
    "裂缝": "fissures", "钢铁裂缝": "steel_fissures", "钢铁裂隙": "steel_fissures",
    "虚空风暴": "storms", "九重天": "storms", "风暴": "storms",
    "赏金": "bounties", "平原赏金": "bounties",
    "希图斯": "cetus", "希图斯赏金": "cetus", "希图斯帐篷": "cetus",
    "今日仲裁": "today_arbitration", "高效仲裁": "effective_arbitration",
    "深层科研": "deep_research", "时光科研": "temporal_research",
    "科研": "research",
    "提醒": "alert", "订阅": "alert", "alert": "alert", "watch": "alert",
    "提醒列表": "alerts", "我的提醒": "alerts", "alerts": "alerts",
    "取消提醒": "unalert", "删除提醒": "unalert", "unalert": "unalert",
    "wiki": "wiki", "维基": "wiki",
    "翻译": "translate", "中英": "translate", "双语": "translate", "百科": "wiki", "wikipedia": "wiki",
    "更新": "update", "刷新": "update", "refresh": "update",
    "数据状态": "data_status", "缓存状态": "data_status", "状态": "data_status",
    "data": "data_status",
    "帮助": "help", "help": "help", "菜单": "help", "指令": "help",
    "命令": "help", "?": "help", "？": "help",
}

COMMAND_ORDER = sorted(COMMANDS.keys(), key=len, reverse=True)

PLATFORM_ALIASES = {
    "pc": "pc", "steam": "pc", "电脑": "pc",
    "ps": "ps4", "ps4": "ps4", "psn": "ps4", "playstation": "ps4",
    "xbox": "xbox", "xb": "xbox", "x1": "xbox",
    "switch": "switch", "ns": "switch", "sw": "switch",
    "mobile": "mobile", "手机": "mobile", "ios": "mobile", "安卓": "mobile",
}


class CommandError(Exception):
    """可以直接展示给用户的错误。"""


@dataclass
class Context:
    user_id: str = ""
    group_id: str = ""
    is_group: bool = False
    self_id: str = ""
    prefix: str = "#"
    image: Optional[bytes] = None
    allow_bare: bool = False

    @property
    def chat_type(self) -> str:
        return "group" if self.is_group else "private"

    @property
    def chat_id(self) -> str:
        return self.group_id if self.is_group else self.user_id


def split_roll_args(argument: str) -> Tuple[str, List[Tuple[str, float]]]:
    """拆分“武器名 + 词条数值”。

    支持：
        绝路 伤害=165 多重=90
        绝路 伤害 165 多重 90
    数值带负号表示负面词条。
    """
    tokens = [token for token in re.split(r"[\s,，]+", (argument or "").strip()) if token]
    weapon: List[str] = []
    pairs: List[Tuple[str, float]] = []
    for token in tokens:
        match = re.match(r"^(.+?)[=:：](-?\d+(?:\.\d+)?)$", token)
        if match:
            pairs.append((match.group(1).strip(), float(match.group(2))))
            continue
        if (re.match(r"^-?\d+(?:\.\d+)?$", token) and weapon
                and looks_like_stat(weapon[-1])):
            pairs.append((weapon.pop(), float(token)))
            continue
        weapon.append(token)
    return " ".join(weapon).strip(), pairs


def split_platform(argument: str) -> Tuple[str, Optional[str]]:
    """从参数末尾取出平台，例如 “绝路 ps”。"""
    parts = (argument or "").rsplit(None, 1)
    if len(parts) == 2:
        token = parts[1].strip().lower()
        if token in PLATFORM_ALIASES:
            return parts[0].strip(), PLATFORM_ALIASES[token]
    return (argument or "").strip(), None


class CommandRouter:
    def __init__(self, market: MarketClient, wiki: WikiClient,
                 config: Dict, logger: Optional[logging.Logger] = None,
                 riven: Optional[RivenClient] = None,
                 worldstate: Optional[WorldStateClient] = None,
                 alerts: Optional[AlertStore] = None,
                 arbi: Optional[ArbiClient] = None,
                 updater: Optional[DataUpdater] = None,
                 translator=None):
        self.market = market
        self.wiki = wiki
        self.cfg = config
        self.log = logger or LOGGER
        self.translator = translator
        self.prefixes = list(config.get("behavior", {}).get("prefixes") or ["#"])
        self.top_orders = int(config.get("warframe_market", {}).get("top_orders", 5))
        self.price_orders = int(config.get("warframe_market", {}).get("price_orders", 8))
        self.wm_realtime = bool(
            config.get("warframe_market", {}).get("realtime_quotes", True))
        self.default_platform = str(
            config.get("warframe_market", {}).get("platform", "pc"))
        self.riven = riven
        self.worldstate = worldstate
        self.alerts = alerts
        self.arbi = arbi
        self.updater = updater

    # ------------------------------------------------------------------ 解析
    def parse(self, text: str,
              allow_bare: bool = False) -> Optional[Tuple[str, str]]:
        text = (text or "").strip()
        if not text:
            return None

        prefix = ""
        for candidate in self.prefixes:
            if candidate and text.startswith(candidate):
                prefix = candidate
                text = text[len(candidate):].strip()
                break
        if not prefix:
            if not allow_bare:
                return None
            # 无前缀模式：只匹配已知命令，普通聊天不当作物品查询。
            parts = text.split(None, 1)
            head = parts[0]
            if head in ("唤醒", "唤醒词"):
                return None
            if head.lower() in COMMANDS:
                return (COMMANDS[head.lower()],
                        parts[1].strip() if len(parts) > 1 else "")
            for alias in COMMAND_ORDER:
                if (len(alias) >= 2 or not alias.isascii()) \
                        and text.startswith(alias) and len(text) > len(alias):
                    return (COMMANDS[alias], text[len(alias):].strip())
            return None
        if not text:
            return ("help", "")

        parts = text.split(None, 1)
        head = parts[0]
        tail = parts[1].strip() if len(parts) > 1 else ""
        if head in ("唤醒", "唤醒词"):
            # 明确废弃 #唤醒，不当作物品查询处理。
            return None
        if head.lower() in COMMANDS:
            return (COMMANDS[head.lower()], tail)

        # 兼容中文不加空格：#查绝路 / #战甲Mesa（单字母英文别名不参与，避免误判）
        for alias in COMMAND_ORDER:
            if (len(alias) >= 2 or not alias.isascii()) \
                    and text.startswith(alias) and len(text) > len(alias):
                return (COMMANDS[alias], text[len(alias):].strip())

        return ("query", text)

    # ------------------------------------------------------------------ 入口
    def handle(self, text: str, ctx: Context) -> List[Reply]:
        parsed = self.parse(text, allow_bare=ctx.allow_bare)
        if parsed is None:
            return []
        command, argument = parsed
        ctx.prefix = self._prefix_of(text)

        try:
            if command == "help":
                return [present.help_reply(ctx.prefix)]
            if command == "wiki":
                return [self._wiki(argument, ctx)]
            if command == "translate":
                return [self._translate(argument, ctx)]
            if command == "price":
                return [self._item(argument, ctx, category=None, price_only=True)]
            if command == "warframe":
                return [self._item(argument, ctx, category="warframe")]
            if command == "weapon":
                return [self._item(argument, ctx, category="weapon")]
            if command == "mod":
                return [self._item(argument, ctx, category="mod")]
            if command == "set":
                return [self._set(argument, ctx)]
            if command == "riven":
                return [self._riven(argument, ctx)]
            if command == "riven_analysis":
                return [self._riven_analysis(argument, ctx)]
            if command == "update":
                return [self._update(argument, ctx)]
            if command == "data_status":
                return [self._data_status(argument, ctx)]
            if command == "baro":
                return [self._baro(ctx)]
            if command == "sortie":
                return [self._sortie(ctx)]
            if command == "arbitration":
                return [self._arbitration(ctx)]
            if command == "arbi_tiers":
                return [self._arbi_tiers(argument, ctx)]
            if command == "world_summary":
                return [self._world_summary(ctx)]
            if command == "world_alerts":
                return [present.alerts_reply(self._ws("alerts"))]
            if command == "world_invasions":
                return [present.invasions_reply(self._ws("invasions"))]
            if command == "steel_path":
                return [present.steel_path_reply(self._ws("steelPath"))]
            if command == "nightwave":
                return [present.nightwave_reply(self._ws("nightwave"))]
            if command == "cycles":
                state = self._ws("")
                return [present.cycles_reply(state.get("cetusCycle"),
                                             state.get("vallisCycle"),
                                             state.get("cambionCycle"))]
            if command == "deals":
                return [present.deals_reply(self._ws("dailyDeals"))]
            if command == "news":
                return [present.news_reply(self._ws("news"))]
            if command == "events":
                return [present.events_reply(self._ws("events"))]
            if command == "circuit":
                return [present.circuit_reply(self._ws("duviriCycle"))]
            if command == "archon":
                return [present.archon_reply(self._ws("archonHunt"))]
            if command == "fissures":
                return [present.fissures_reply(self.worldstate.fissures())]
            if command == "steel_fissures":
                return [present.fissures_reply(self.worldstate.fissures(),
                                               hard=True)]
            if command == "storms":
                # 官方 VoidStorms 有节点与光体等级，任务类型取节点表；
                # 中转可用时优先用 warframestat 的完整风暴列表。
                if hasattr(self.worldstate, "void_storms"):
                    return [present.storms_reply(self.worldstate.void_storms())]
                return [present.storms_reply(self._ws("fissures"))]
            if command == "bounties":
                return [present.bounties_reply(self._ws("syndicateMissions"))]
            if command == "cetus":
                state = self._ws("")
                return [present.cetus_reply(
                    state.get("cetusCycle"),
                    state.get("syndicateMissions"))]
            if command == "today_arbitration":
                return [self._today_arbitration(argument, ctx)]
            if command == "effective_arbitration":
                return [self._effective_arbitration(argument, ctx)]
            if command == "deep_research":
                return [self._research("C T_ L A B", "深层科研")]
            if command == "temporal_research":
                return [self._research("C T_ H E X", "时光科研")]
            if command == "research":
                return [self._research(None, "科研")]
            if command == "alert":
                return [self._add_alert(argument, ctx)]
            if command == "alerts":
                return [self._list_alerts(ctx)]
            if command == "unalert":
                return [self._remove_alert(argument, ctx)]
            return [self._item(argument, ctx, category=None)]
        except CommandError as exc:
            return [present.error_reply(str(exc))]
        except MarketError as exc:
            return [present.error_reply(
                "查询失败：%s\n稍后再试；若持续失败，请运行 python selftest.py 自检。" % exc)]
        except WikiError as exc:
            return [present.error_reply(
                "维基查询失败：%s\n稍后再试，或直接访问 https://wiki.warframe.com/" % exc)]
        except WorldStateError as exc:
            return [present.error_reply("世界状态查询失败：%s" % exc)]

    def _prefix_of(self, text: str) -> str:
        for candidate in self.prefixes:
            if candidate and text.strip().startswith(candidate):
                return candidate
        return self.prefixes[0] if self.prefixes else "#"

    # ------------------------------------------------------------------ 物品
    def _item(self, argument: str, ctx: Context, category: Optional[str],
              price_only: bool = False) -> Reply:
        argument, platform = split_platform(argument)
        platform = platform or self.default_platform
        if not argument:
            example = "%s价格 牛甲" % ctx.prefix if price_only else "%swm 绝路" % ctx.prefix
            return present.error_reply("用法：%s%s <名称> [平台]\n例如：%s" % (
                ctx.prefix, "价格" if price_only else "wm", example))

        hits = self.market.lookup(argument, category=category, limit=5)
        if not hits:
            return present.not_found_reply(argument)

        best = hits[0]
        if len(hits) > 1 and best.score < 940:
            gap = best.score - hits[1].score
            strong_set = (best.score >= 850
                          and (best.item.set_root or "set" in best.item.tag_set))
            if gap < 60 and not strong_set:
                return present.candidates_reply(argument, hits)

        item = self._detail(best.item)
        try:
            sell, buy = self.market.top_orders(
                item.slug, limit=self.price_orders if price_only else self.top_orders,
                platform=platform, force=self.wm_realtime)
        except MarketError as exc:
            self.log.warning("订单查询失败 %s：%s", item.slug, exc)
            sell, buy = [], []
        except Exception as exc:  # pragma: no cover - 防御
            self.log.exception("订单查询异常 %s：%s", item.slug, exc)
            sell, buy = [], []

        stats = None
        try:
            stats = self.market.statistics(item.slug, platform=platform,
                                           force=self.wm_realtime)
        except MarketError as exc:
            self.log.warning("行情查询失败 %s：%s", item.slug, exc)
        except Exception as exc:  # pragma: no cover - 防御
            self.log.exception("行情查询异常 %s：%s", item.slug, exc)

        return present.item_reply(item, sell, buy, stats, platform,
                                  price_only=price_only)

    def _detail(self, item: Item) -> Item:
        try:
            detail = self.market.item_detail(item.slug)
        except MarketError as exc:
            self.log.warning("物品详情失败 %s：%s", item.slug, exc)
            return item
        return detail or item

    # ------------------------------------------------------------------ 拆件
    def _set(self, argument: str, ctx: Context) -> Reply:
        argument, platform = split_platform(argument)
        platform = platform or self.default_platform
        if not argument:
            return present.error_reply(
                "用法：%s拆件 <套装名> [平台]\n例如：%s拆件 Mesa Prime" % (
                    ctx.prefix, ctx.prefix))

        hits = self.market.lookup(argument, limit=3)
        if not hits:
            return present.not_found_reply(argument)
        item = self._detail(hits[0].item)
        if not item.set_parts and item.slug.endswith("_blueprint"):
            # 战甲别名现在指向总图；拆件时自动换成同套装的 _set。
            set_item = self.market.index.get(item.slug[:-len("_blueprint")] + "_set")
            if set_item is not None:
                item = self._detail(set_item)
        if not item.set_parts:
            return present.error_reply(
                "「%s」不是套装，或没有部件数据。试试完整套装名，例如 “Mesa Prime 一套”。"
                % item.name)

        try:
            sell, _ = self.market.top_orders(item.slug, limit=1, platform=platform)
        except MarketError as exc:
            self.log.warning("套装报价失败 %s：%s", item.slug, exc)
            sell = []

        quotes = self.market.set_breakdown(item, platform=platform)
        if not quotes:
            return present.error_reply("没有取到「%s」的部件列表。" % item.name)
        return present.set_reply(item, quotes, sell[0].platinum if sell else None,
                                 platform)

    # ------------------------------------------------------------------ 紫卡
    def _riven(self, argument: str, ctx: Context) -> Reply:
        stripped = (argument or "").strip()
        for word in ("分析", "测评"):
            if stripped.startswith(word):
                return self._riven_analysis(stripped[len(word):].strip(), ctx)
        argument, platform = split_platform(argument)
        platform = platform or self.default_platform
        if not argument:
            return present.error_reply(
                "用法：%s紫卡 <武器名> [平台]\n例如：%s紫卡 绝路" % (ctx.prefix, ctx.prefix))
        if self.riven is None:
            return present.error_reply("紫卡功能未启用。")

        try:
            hits = self.market.lookup(argument, limit=3)
        except MarketError:
            hits = []
        weapon = self.riven.find(hits[0].item) if hits else None
        if weapon is None:
            weapon = self.riven.resolve(argument)
        if weapon is None:
            return present.error_reply(
                "「%s」没有对应的紫卡（可能是战甲、守护或未开放紫卡的武器）。"
                % argument)

        auctions, note = self.riven.auctions(weapon.slug, limit=5)
        return present.riven_reply(weapon, auctions, note, platform)

    def _riven_analysis(self, argument: str, ctx: Context) -> Reply:
        if self.riven is None:
            return present.error_reply("紫卡功能未启用。")
        weapon_text, pairs = split_roll_args(argument)
        weapon_text, _platform = split_platform(weapon_text)
        ocr_text = ""
        if ctx.image:
            try:
                ocr_text = ocr.image_to_text(ctx.image)
            except ocr.OcrError as exc:
                return present.error_reply(str(exc))
            if not pairs:
                pairs = ocr.parse_rolls(ocr_text)
        weapon = None
        if weapon_text:
            try:
                hits = self.market.lookup(weapon_text, limit=3)
            except MarketError as exc:
                return present.error_reply("查询失败：%s" % exc)
            if hits:
                weapon = self.riven.find(hits[0].item)
            if weapon is None:
                weapon = self.riven.resolve(weapon_text)
        if weapon is None and ocr_text:
            for line in ocr_text.splitlines():
                line = line.strip()
                if not line or "%" in line or "％" in line or len(line) < 2:
                    continue
                weapon = self.riven.resolve(line)
                if weapon is not None:
                    break
        if weapon is None:
            if pairs:
                return present.riven_ocr_reply(
                    pairs, "未识别到武器，先按通用词条列出。")
            if not weapon_text and not ctx.image:
                return present.error_reply(
                    "用法：直接发送紫卡截图，或：\n"
                    "  %s紫卡分析 绝路\n"
                    "  %s紫卡分析 绝路 伤害=165 多重=90"                    % (ctx.prefix, ctx.prefix))
            return present.error_reply(
                "「%s」没有对应的紫卡（可能是战甲、守护或未开放紫卡的武器）。"
                % (weapon_text or "图片"))
        if ctx.image and not pairs:
            return present.error_reply(
                "图片里没有识别到有效紫卡词条，请发送完整、清晰的紫卡截图。")
        analysis = calculate(weapon)
        evaluations = evaluate_roll(analysis, pairs) if pairs else []
        return present.riven_analysis_reply(analysis, evaluations)

    # ------------------------------------------------------------------ 数据更新
    def _update(self, argument: str, ctx: Context) -> Reply:
        if self.updater is None:
            return present.error_reply("自动更新未启用。")
        return Reply(text=self.updater.trigger())

    def _data_status(self, argument: str, ctx: Context) -> Reply:
        if self.updater is None:
            return present.error_reply("自动更新未启用。")
        return Reply(text=self.updater.status_text(ctx.prefix))

    # ------------------------------------------------------------------ 世界状态
    def _baro(self, ctx: Context) -> Reply:
        if self.worldstate is None:
            return present.error_reply("世界状态功能未启用。")
        trader = self.worldstate.void_trader()
        try:
            index = self.market.load_items()
        except MarketError as exc:
            self.log.warning("奸商物品翻译失败（不影响显示）：%s", exc)
            index = None
        return present.baro_reply(trader, index=index)

    def _sortie(self, ctx: Context) -> Reply:
        if self.worldstate is None:
            return present.error_reply("世界状态功能未启用。")
        return present.sortie_reply(self.worldstate.sortie())

    def _ws(self, path: str):
        if self.worldstate is None:
            raise CommandError("世界状态功能未启用。")
        return self.worldstate.fetch(path)

    def _world_summary(self, ctx: Context) -> Reply:
        state = self._ws("")
        if not isinstance(state, dict):
            raise CommandError("世界状态数据格式异常。")
        arbitration = dict(state.get("arbitration") or {})
        if (arbitration.get("expired") or not arbitration.get("nodeKey")
                or arbitration.get("nodeKey") == "SolNode000"):
            # 官方兜底源没有仲裁字段，按 arbi.wf.wiki 轮换表补上
            arbitration = {"node": "暂无有效数据", "expiry": 0}
            if self.arbi is not None:
                try:
                    current = self.arbi.current_node()
                except Exception as exc:  # pragma: no cover - 防御
                    self.log.warning("仲裁轮换查询失败：%s", exc)
                    current = None
                if current:
                    node, expiry = current
                    arbitration = {"node": node.display_name, "expiry": expiry}
        state = dict(state)
        state["arbitration"] = arbitration
        return present.worldstate_reply(state)

    def _today_arbitration(self, argument: str, ctx: Context) -> Reply:
        if self.arbi is None:
            return present.error_reply("仲裁评级未启用。")
        entries = self.arbi.today()
        return present.arbitration_schedule_reply(entries, "今日仲裁")

    def _effective_arbitration(self, argument: str, ctx: Context) -> Reply:
        if self.arbi is None:
            return present.error_reply("仲裁评级未启用。")
        entries = self.arbi.effective(hours=24)
        return present.arbitration_schedule_reply(
            entries, "高效仲裁（未来 24 小时 S/A 级）",
            empty="未来 24 小时没有 S/A 级仲裁。")

    def _research(self, type_key, label: str) -> Reply:
        if self.worldstate is None:
            return present.error_reply("世界状态功能未启用。")
        state = self._ws("")
        if not isinstance(state, dict):
            return present.error_reply("科研数据格式异常。")
        items = [item for item in (state.get("archimedeas") or [])
                 if isinstance(item, dict)
                 and (type_key is None or item.get("typeKey") == type_key)]
        return present.research_reply(items, label)

    def _arbitration(self, ctx: Context) -> Reply:
        if self.worldstate is None:
            return present.error_reply("世界状态功能未启用。")
        try:
            arbitration = self.worldstate.arbitration()
        except WorldStateError as exc:
            return present.error_reply(str(exc))

        rating = None
        node = None
        note = ""
        if self.arbi is not None:
            try:
                if (arbitration.expired or not arbitration.node_key
                        or arbitration.node_key == "SolNode000"):
                    current = self.arbi.current_node()
                    if current:
                        site_node, expiry = current
                        arbitration = Arbitration(
                            node=site_node.display_name,
                            mission=site_node.mission,
                            enemy=site_node.faction,
                            end=expiry,
                            node_key=site_node.key,
                        )
                        node = site_node
                        rating = self.arbi.rating(site_node.key)
                        note = "Warframe 官方 API 暂无有效数据，按 arbi.wf.wiki 轮换推算"
                else:
                    node = self.arbi.node(arbitration.node_key)
                    rating = self.arbi.rating(arbitration.node_key)
            except ArbiError as exc:
                note = "评级数据暂不可用：%s" % exc

        if ((arbitration.expired or not arbitration.node
             or arbitration.node == "SolNode000") and node is None):
            return present.error_reply("当前没有有效的仲裁数据。")
        return present.arbitration_reply(arbitration, rating=rating,
                                         node=node, note=note)

    def _arbi_tiers(self, argument: str, ctx: Context) -> Reply:
        if self.arbi is None:
            return present.error_reply("仲裁评级未启用（arbi.enabled = false）。")
        try:
            data = self.arbi.data()
        except ArbiError as exc:
            return present.error_reply(str(exc))

        current_key = ""
        try:
            current = self.arbi.current_node()
            if current:
                current_key = current[0].key
        except Exception:
            pass
        return present.arbitration_tiers_reply(data, current_key)

    # ------------------------------------------------------------------ 提醒
    def _add_alert(self, argument: str, ctx: Context) -> Reply:
        if self.alerts is None:
            return present.error_reply("提醒功能未启用。")
        # 平台可以写在价格前（绝路 ps 50）或价格后（绝路 50 ps）
        argument, platform = split_platform(argument)
        parts = (argument or "").rsplit(None, 1)
        if len(parts) != 2:
            return present.error_reply(
                "用法：%s提醒 <名称> <目标价> [平台]\n例如：%s提醒 绝路 50" % (
                    ctx.prefix, ctx.prefix))
        name, target_text = parts[0].strip(), parts[1].strip()
        name, platform2 = split_platform(name)
        platform = platform2 or platform or self.default_platform
        target_text = target_text.rstrip("pP铂金 ")
        if not target_text.isdigit():
            return present.error_reply("目标价要写数字，例如：%s提醒 绝路 50" % ctx.prefix)
        target = int(target_text)
        if target <= 0 or target > 1000000:
            return present.error_reply("目标价需要在 1 ~ 1000000 之间。")

        hits = self.market.lookup(name, limit=3)
        if not hits:
            return present.not_found_reply(name)
        best = hits[0]
        if len(hits) > 1 and best.score < 850 and best.score - hits[1].score < 60:
            return present.candidates_reply(name, hits)

        item = self._detail(best.item)
        try:
            alert = self.alerts.add(ctx.chat_type, ctx.chat_id, ctx.user_id,
                                    item.slug, item.name, target, platform)
        except ValueError as exc:
            return present.error_reply(str(exc))

        return present.error_reply(
            "已添加提醒 #%s：%s ≤ %dp（%s）。\n达到目标价会在本会话通知你，"
            "发送 %s提醒列表 可查看。" % (
                alert.id, item.name, target, present.platform_label(platform),
                ctx.prefix))

    def _list_alerts(self, ctx: Context) -> Reply:
        if self.alerts is None:
            return present.error_reply("提醒功能未启用。")
        return present.alerts_list_reply(
            self.alerts.list_for(ctx.chat_type, ctx.chat_id))

    def _remove_alert(self, argument: str, ctx: Context) -> Reply:
        if self.alerts is None:
            return present.error_reply("提醒功能未启用。")
        ident = (argument or "").strip().lstrip("#")
        if not ident:
            return present.error_reply(
                "用法：%s取消提醒 <编号|all>\n先发 %s提醒列表 查看编号。" % (
                    ctx.prefix, ctx.prefix))
        removed = self.alerts.remove(ctx.chat_type, ctx.chat_id, ident)
        if not removed:
            return present.error_reply("没有找到编号为 #%s 的提醒。" % ident)
        return present.error_reply("已取消 %d 条提醒。" % removed)

    # ------------------------------------------------------------------ 维基
    def _translate(self, argument: str, ctx: Context) -> Reply:
        argument = argument.strip()
        if not argument:
            return present.error_reply(
                "用法：%s翻译 <中文/英文物品名>\n例如：%s翻译 Volt Prime Neuroptics Blueprint"
                % (ctx.prefix, ctx.prefix))
        item = None
        try:
            hits = self.market.lookup(argument, limit=3)
            if hits:
                item = hits[0].item
        except MarketError:
            item = None
        if item is not None:
            zh = item.name or item.name_en
            en = item.name_en or item.name
            if re.search(r"[\u4e00-\u9fff]", zh):
                return present.translation_reply(
                    argument, zh, en, "warframe market", item.url)
        try:
            page, _hits = self.wiki.lookup(argument, limit=1)
        except WikiError:
            page = None
        if page is not None:
            zh = page.title
            en = item.name_en if item is not None else argument
            source = ("英文维基" if "wiki.warframe.com" in page.url
                      else "中文维基")
            return present.translation_reply(
                argument, zh, en or argument, source, page.url)
        if item is not None:
            zh = item.name or item.name_en
            en = item.name_en or item.name
            return present.translation_reply(
                argument, zh, en, "warframe market", item.url)
        return present.error_reply(
            "没有找到“%s”的可靠中英译名。\n"
            "可以先用 wm 查询物品，或换一个更完整的英文名。" % argument)

    def _wiki(self, argument: str, ctx: Context) -> Reply:
        if not argument:
            return present.error_reply(
                "用法：%swiki <关键词>\n例如：%swiki Volt，也支持中文：%swiki 电男"
                % (ctx.prefix, ctx.prefix, ctx.prefix))
        # 英文维基只认英文词条：中文关键词先用术语表 + 物品索引换成英文名。
        mapped = self._english_titles(argument)
        titles = mapped or [argument]
        page = None
        hits: List = []
        used = titles[0]
        try:
            for title in titles:
                found, found_hits = self.wiki.lookup(title, limit=3)
                used = title
                if found_hits:
                    hits = found_hits
                # 中文映射出的候选要核对词条名，避免「Volt Set」命中
                # 「Volt Animation Set」这类搜索兜底结果。
                if found is not None and (not mapped or zh2en.title_compatible(
                        title, found.title)):
                    page = found
                    hits = found_hits or hits
                    break
        except WikiError as exc:
            from urllib.parse import quote
            keyword = quote(argument)
            return present.error_reply(
                "维基查询失败：%s\n"
                "如果浏览器能打开下面链接，说明是 Cloudflare 拦截了机器人请求：\n"
                "· https://wiki.warframe.com/w/%s\n"
                "· https://warframe.fandom.com/wiki/%s\n"
                "可换一个能通过验证的代理（wiki.proxy），或稍后再试。"
                % (exc, keyword, keyword))
        note = ""
        if page is not None:
            if used and used.strip().lower() != argument.strip().lower():
                note = "中文「%s」→ 英文词条「%s」" % (argument, used)
        else:
            used = titles[0]
        return present.wiki_reply(argument, page, hits,
                                  translated=self._translate_wiki(page),
                                  note=note, english="" if page is not None else used)

    def _english_titles(self, query: str) -> List[str]:
        """中文关键词 → 英文维基候选词条名；纯英文查询返回空列表。"""
        if not zh2en.has_cjk(query):
            return []
        try:
            titles = zh2en.english_titles(query, item_lookup=self._wiki_lookup_items)
        except Exception as exc:  # 映射失败不影响原始中文查询
            self.log.warning("中文 Wiki 关键词映射失败（%s）：%s", query, exc)
            return []
        if titles:
            self.log.info("中文 Wiki 关键词映射：%s → %s", query, " / ".join(titles[:3]))
        return titles[:3]

    def _wiki_lookup_items(self, query: str, limit: int = 3) -> List:
        try:
            return self.market.lookup(query, limit=limit)
        except MarketError:
            return []

    def _translate_wiki(self, page) -> str:
        if page is None or self.translator is None:
            return ""
        if not bool(self.cfg.get("wiki", {}).get("translate", True)):
            return ""
        try:
            return self.translator.translate(page.extract)
        except Exception as exc:
            self.log.warning("Wiki 中文翻译失败：%s", exc)
            return ""


def strip_cq(text: str) -> str:
    """去掉 CQ 码，只保留可读文本。"""
    return CQ_RE.sub("", text or "").strip()
