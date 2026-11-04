"""命令解析与业务路由（不涉及 QQ 连接，方便单测）。

所有查询结果都返回 Reply：图片卡片优先，同时保留文字版本做兜底。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import imagecard
from . import names
from . import present
from . import zh2en
from .alerts import AlertStore
from .command_store import CommandStore
from .arbi import ArbiClient, ArbiError
from .market import (Item, MarketClient, MarketError, SearchHit, is_arcane,
                     wants_arcane)
from .replies import Reply
from .relics import RelicClient
from .riven import (CATEGORY_LABELS, GROUP_CATEGORY_ORDER, GROUP_LABELS,
                    RivenClient, parse_group_detail, parse_riven_filters)
from .rivencalc import RivenAnalyzer
from .subscriptions import (SubscriptionService, kind_label, kind_names,
                            parse_kind)
from .updater import DataUpdater
from .wiki import WikiClient, WikiError
from .worldstate import Arbitration, WorldStateClient, WorldStateError

LOGGER = logging.getLogger("qqwarframe.commands")

# 已经下线的功能前缀：这些词后面跟任何东西都不再当命令。
# 「紫卡分析」已恢复成按武器组看数值范围（riven_group），不再属于下线写法。
RETIRED_PREFIXES: Dict[str, Tuple[str, ...]] = {}


def is_retired(text: str) -> bool:
    """是否属于已经下线的功能写法。"""
    value = str(text or "").strip().lower()
    for alias, command in COMMANDS.items():
        for word in RETIRED_PREFIXES.get(command, ()):
            for sep in ("", " "):
                if value.startswith(alias + sep + word):
                    return True
    return False

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
    # wr = 紫卡市场（warframe.market 在线紫卡拍卖）
    "wr": "riven_market", "wriven": "riven_market", "紫卡市场": "riven_market",
    "紫卡拍卖": "riven_market", "紫卡行情": "riven_market", "收紫卡": "riven_market",
    "zkmarket": "riven_market", "wm紫卡": "riven_market",
    "遗物": "relic", "圣遗物": "relic", "relic": "relic",
    "紫卡分析": "riven_group", "紫卡分组": "riven_group",
    "紫卡类别": "riven_group", "紫卡类型": "riven_group",
    "紫卡数值": "riven_group", "zkfx": "riven_group",
    "紫卡榜": "riven_rank", "紫卡排行": "riven_rank", "紫卡排名": "riven_rank",
    "零洗紫卡": "riven_rank", "最贵紫卡": "riven_rank", "紫卡价格榜": "riven_rank",
    "金垃圾": "junk_gold", "金垃圾行情": "junk_gold", "goldjunk": "junk_gold",
    "银垃圾": "junk_silver", "银垃圾行情": "junk_silver", "silverjunk": "junk_silver",
    "铜垃圾": "junk_bronze", "铜垃圾行情": "junk_bronze",
    "假金垃圾": "junk_fake_gold", "假银垃圾": "junk_fake_silver",
    "垃圾": "junk_overview", "垃圾行情": "junk_overview",
    "prime垃圾": "junk_overview", "junk": "junk_overview",
    "战甲榜": "frame_rank", "战甲排行": "frame_rank", "战甲排名": "frame_rank",
    "战甲价格榜": "frame_rank", "最贵战甲": "frame_rank", "framrank": "frame_rank",
    "紫卡范围": "riven", "词条范围": "riven", "紫卡词条": "riven",
    "奸商": "baro", "虚空商人": "baro", "baro": "baro",
    "突击": "sortie", "sortie": "sortie",
    "仲裁": "arbitration", "arbitration": "arbitration",
    "仲裁评级": "arbi_tiers", "仲裁梯队": "arbi_tiers",
    "arbitrationrating": "arbi_tiers",
    "仲裁表": "arbi_schedule", "仲裁排期": "arbi_schedule",
    "未来仲裁": "arbi_schedule", "七天仲裁": "arbi_schedule",
    "arbischedule": "arbi_schedule", "arbitable": "arbi_schedule",
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
    # 官方接口不给这三处的任务明细，按固定档位单独出卡（别名按群里习惯）
    "解剖圣所": "bounty_cavia", "圣所": "bounty_cavia",
    "霍瓦尼亚": "bounty_hex", "1999": "bounty_hex",
    "扎里曼": "bounty_zariman",
    "希图斯": "cetus", "希图斯赏金": "cetus", "希图斯帐篷": "cetus",
    "今日仲裁": "today_arbitration", "高效仲裁": "effective_arbitration",
    "深层科研": "deep_research", "时光科研": "temporal_research",
    "科研": "research",
    "轮换": "rotation", "周轮换": "rotation", "每周轮换": "rotation",
    "轮换商店": "rotation", "rotation": "rotation", "weekly": "rotation",
    "言录使": "rotation_acrithis", "acrithis": "rotation_acrithis",
    "格拉斯": "rotation_glast", "埃尔戈·格拉斯": "rotation_glast",
    "格拉斯特": "rotation_glast", "ergo": "rotation_glast",
    "glast": "rotation_glast",
    "埃莉诺": "rotation_eleanor", "eleanor": "rotation_eleanor",
    "终幕": "rotation_eleanor", "终幕武器": "rotation_eleanor",
    "提醒": "alert", "alert": "alert", "watch": "alert",
    "提醒列表": "alerts", "我的提醒": "alerts", "alerts": "alerts",
    "取消提醒": "unalert", "删除提醒": "unalert", "unalert": "unalert",
    # 事件订阅（世界状态推送）：和「提醒」（价格）分开
    "订阅": "subscribe", "subscribe": "subscribe",
    "订阅列表": "subs", "我的订阅": "subs", "subs": "subs",
    "取消订阅": "unsubscribe", "退订": "unsubscribe", "unsubscribe": "unsubscribe",
    "wiki": "wiki", "维基": "wiki",
    "翻译": "translate", "中英": "translate", "双语": "translate", "百科": "wiki", "wikipedia": "wiki",
    "更新": "update", "刷新": "update", "refresh": "update",
    "数据状态": "data_status", "缓存状态": "data_status", "状态": "data_status",
    "data": "data_status",
    # 帮助只认「帮助」这一个词：问号、菜单、?、指令等一律不触发，
    # 避免群里随手打一个 ? 就被机器人刷屏。
    "帮助": "help",
}

COMMAND_ORDER = sorted(COMMANDS.keys(), key=len, reverse=True)

# 单据点赏金：命令 → 官方 SyndicateMissions 里的 syndicate 名
BOUNTY_VIEW = {
    "bounty_cavia": "Cavia",
    "bounty_hex": "The Hex",
    "bounty_zariman": "The Holdfasts",
}

# 曾经当作「帮助」的别名，现在一律忽略（既不回帮助，也不当成物品去查）
IGNORED_WORDS = {"菜单", "指令", "命令", "help", "?", "？", "helpme"}

# 在线翻译接口连不上时的冷却时间：别让每条查询都把所有代理试一遍
TRANSLATE_BLOCK_SECONDS = 900.0

# 不吃参数的命令：裸词唤醒时必须「整条消息就是这个词」。
# 以前群里聊「仲裁什么时候刷新」「回廊怎么进」都会被当成查询，机器人乱插话。
WHOLE_WORD_COMMANDS = frozenset({
    "baro", "sortie", "arbitration", "world_summary", "world_alerts",
    "world_invasions", "steel_path", "nightwave", "cycles", "deals", "news",
    "events", "circuit", "archon", "fissures", "steel_fissures", "storms",
    "bounties", "cetus", "bounty_cavia", "bounty_hex", "bounty_zariman",
    "deep_research", "temporal_research", "research",
    "riven_rank", "frame_rank", "junk_gold", "junk_silver", "junk_bronze",
    "junk_fake_gold", "junk_fake_silver", "junk_overview", "alerts", "help",
})

# 「查绝路」这种连写没问题，但「紫卡这个怎么样」是在聊天，别接。
_SENTENCE_RE = re.compile(
    r"[？?！!。，,；;~～]|怎么|怎样|什么|怎么着|为啥|为什么|如何|多少|"
    r"什么时候|啥时候|哪里|哪个|能不能|可不可以|吗|呢|呀|啊|吧")

# 看起来是在聊天，而不是在查物品的短句。
# 例：「一次等30秒」「还有多久」「该功能暂未实现」不该触发"没找到物品"。
_CHAT_NOISE_RE = re.compile(
    r"(?:一次|等\s*\d|秒|分钟|小时|多久|几点|什么时候|有人|请问|"
    r"暂未|未实现|功能暂)")


def _looks_like_noise(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return bool(_CHAT_NOISE_RE.search(value) or _SENTENCE_RE.search(value))


def is_ignored(text: str) -> bool:
    head = str(text or "").strip().split(None, 1)[0] if str(text or "").strip() else ""
    return head.lower() in IGNORED_WORDS


PLATFORM_ALIASES = {
    "pc": "pc", "steam": "pc", "电脑": "pc",
    "ps": "ps4", "ps4": "ps4", "psn": "ps4", "playstation": "ps4",
    "xbox": "xbox", "xb": "xbox", "x1": "xbox",
    "switch": "switch", "ns": "switch", "sw": "switch",
    "mobile": "mobile", "手机": "mobile", "ios": "mobile", "安卓": "mobile",
}

# 轮换商店：#轮换 后面可以跟的关键词
ROTATION_SHOPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("acrithis", ("言录使", "acrithis", "双衍", "王境", "苦栓")),
    ("glast", ("格拉斯", "格拉斯特", "埃尔戈", "ergo", "glast", "信条", "近战")),
    ("eleanor", ("埃莉诺", "eleanor", "终幕", "1999", "心肌")),
)
ROTATION_ALL = ("", "全部", "总览", "all", "overview", "?")


def rotation_shop(argument: str) -> Optional[str]:
    """把 `#轮换 xxx` 的参数翻译成商店 key；看不懂返回 None。"""
    text = (argument or "").strip().lower()
    if text in ROTATION_ALL:
        return "all"
    for key, aliases in ROTATION_SHOPS:
        if any(alias in text for alias in aliases):
            return key
    return None


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
                 translator=None,
                 board=None,
                 rotation=None,
                 subscriptions: Optional[SubscriptionService] = None,
                 database=None,
                 relics: Optional[RelicClient] = None):
        self.market = market
        self.wiki = wiki
        self.cfg = config
        self.log = logger or LOGGER
        self.translator = translator
        self.database = database
        # 翻译接口失败后的冷却时间戳（见 _translate_wiki）
        self._translate_blocked_until = 0.0
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
        self.board = board
        self.rotation = rotation
        self.subscriptions = subscriptions
        self.relics = relics
        command_config = config.get("behavior", {}) or {}
        self.command_store = CommandStore(
            str(command_config.get("commands_file") or "data/commands.json"),
            sorted(set(COMMANDS.values())), logger=self.log)
        self.command_store.set_builtin_lookup(lambda key: key in COMMANDS)
        self.commands: Dict[str, str] = dict(COMMANDS)
        self.command_order: List[str] = []
        self.disabled_commands: set = set()
        self._reload_commands()
        # 紫卡排行/卡面要武器小图标：给图片模块接一个带磁盘缓存的下载器
        self.icon_dir = str(config.get("warframe_market", {}).get(
            "icon_dir") or "data/cache/icons")
        imagecard.set_icon_loader(self._load_icon)

    def _reload_commands(self) -> None:
        aliases = self.command_store.aliases()
        self.commands = dict(COMMANDS)
        self.commands.update(aliases)
        self.command_order = sorted(self.commands, key=len, reverse=True)
        self.disabled_commands = self.command_store.disabled()

    def command_snapshot(self) -> Dict:
        self._reload_commands()
        data = self.command_store.snapshot()
        data["builtin_aliases"] = {
            command: sorted(alias for alias, value in COMMANDS.items()
                            if value == command)
            for command in sorted(set(COMMANDS.values()))
        }
        return data

    def set_command_enabled(self, command: str, enabled: bool) -> Dict:
        self.command_store.set_enabled(command, enabled)
        self._reload_commands()
        return self.command_snapshot()

    def add_command_alias(self, alias: str, command: str) -> Dict:
        self.command_store.add_alias(alias, command)
        self._reload_commands()
        return self.command_snapshot()

    def remove_command_alias(self, alias: str) -> Dict:
        self.command_store.remove_alias(alias)
        self._reload_commands()
        return self.command_snapshot()

    def _command_enabled(self, command: str) -> bool:
        return command not in self.disabled_commands

    def _load_icon(self, url: str) -> Optional[bytes]:
        """武器图标下载（磁盘缓存，失败返回 None，不影响出图）。"""
        url = str(url or "").strip()
        if not url:
            return None
        try:
            os.makedirs(self.icon_dir, exist_ok=True)
        except OSError:
            return None
        name = hashlib.md5(url.encode("utf-8")).hexdigest() + ".img"
        path = os.path.join(self.icon_dir, name)
        if os.path.isfile(path):
            try:
                with open(path, "rb") as handle:
                    return handle.read()
            except OSError:
                pass
        try:
            raw = self.market.http.request(
                url, headers={"User-Agent": "qqwarframe-bot/1.0"})
        except Exception as exc:  # 图标是装饰，网络失败不报错
            self.log.info("图标下载失败（%s）：%s", url, exc)
            return None
        if raw:
            try:
                with open(path, "wb") as handle:
                    handle.write(raw)
            except OSError:
                pass
        return raw

    # ------------------------------------------------------------------ 解析
    def parse(self, text: str,
              allow_bare: bool = False) -> Optional[Tuple[str, str]]:
        text = (text or "").strip()
        if not text:
            return None
        if not re.search(r"[\w\u4e00-\u9fff]", text):
            # 纯标点（? ？ 。 ！ 等）不当作命令或查询，群里打问号不该被刷屏。
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
            # 无前缀模式：只认「独立的词」，句子里夹带的词不算唤醒。
            parts = text.split(None, 1)
            head = parts[0]
            tail = parts[1].strip() if len(parts) > 1 else ""
            if head in ("唤醒", "唤醒词") or is_ignored(text):
                return None
            command = self.commands.get(head.lower())
            if command and self._command_enabled(command):
                if command in WHOLE_WORD_COMMANDS and tail:
                    # 「仲裁 什么时候刷新」不再当作查询
                    return None
                if command in RETIRED_PREFIXES and tail.startswith(RETIRED_PREFIXES[command]):
                    # 「紫卡分析」已下线，不再当作紫卡查询
                    return None
                return (command, tail)
            for alias in self.command_order:
                command = self.commands[alias]
                if command in WHOLE_WORD_COMMANDS or not self._command_enabled(command):
                    continue  # 这个词必须单独出现，连写的一律不算
                if (len(alias) >= 2 or not alias.isascii()) \
                        and text.startswith(alias) and len(text) > len(alias):
                    rest = text[len(alias):].strip()
                    if _SENTENCE_RE.search(rest):
                        continue  # 「紫卡这个怎么样」是在聊天
                    if command in RETIRED_PREFIXES and rest.startswith(RETIRED_PREFIXES[command]):
                        continue  # 「紫卡分析…」已下线
                    return (command, rest)
            return None
        if not text:
            # 只发一个前缀（例如单独的 #）不再当作求助，直接忽略。
            return None

        parts = text.split(None, 1)
        head = parts[0]
        tail = parts[1].strip() if len(parts) > 1 else ""
        if head in ("唤醒", "唤醒词"):
            # 明确废弃 #唤醒，不当作物品查询处理。
            return None
        if is_ignored(text):
            return None
        if head.lower() in self.commands:
            command = self.commands[head.lower()]
            if not self._command_enabled(command):
                return None
            if command in RETIRED_PREFIXES and tail.startswith(RETIRED_PREFIXES[command]):
                # 「紫卡分析」已下线，不再当作紫卡查询
                return None
            return (command, tail)

        # 兼容中文不加空格：#查绝路 / #战甲Mesa（单字母英文别名不参与，避免误判）
        for alias in self.command_order:
            if (len(alias) >= 2 or not alias.isascii()) \
                    and text.startswith(alias) and len(text) > len(alias):
                command = self.commands[alias]
                if not self._command_enabled(command):
                    continue
                rest = text[len(alias):].strip()
                if command in RETIRED_PREFIXES and rest.startswith(RETIRED_PREFIXES[command]):
                    continue  # 「紫卡分析…」已下线
                return (command, rest)

        if is_retired(text):
            return None
        if _looks_like_noise(text):
            return None
        return ("query", text)

    # ------------------------------------------------------------------ 入口
    def handle(self, text: str, ctx: Context) -> List[Reply]:
        parsed = self.parse(text, allow_bare=ctx.allow_bare)
        if parsed is None:
            return []
        command, argument = parsed
        if command in ("query", "price") and _looks_like_noise(argument):
            return []
        ctx.prefix = self._prefix_of(text)

        try:
            if command == "help":
                return [present.help_reply(ctx.prefix, self.disabled_commands)]
            if command == "wiki":
                return [self._wiki(argument, ctx)]
            if command == "translate":
                return [self._translate(argument, ctx)]
            if command == "price":
                return [self._item(argument, ctx, category=None, price_only=True)]
            if command == "warframe":
                # 不带名称时给 Prime 战甲价格（高→低），带名称才是分类搜索
                if not str(argument or "").strip():
                    return [self._frame_rank(ctx)]
                return [self._item(argument, ctx, category="warframe")]
            if command == "weapon":
                return [self._item(argument, ctx, category="weapon")]
            if command == "mod":
                return [self._item(argument, ctx, category="mod")]
            if command == "set":
                return [self._set(argument, ctx)]
            if command == "riven":
                return [self._riven(argument, ctx)]
            if command == "relic":
                return [self._relic(argument, ctx)]
            if command == "riven_market":
                return [self._riven_market(argument, ctx)]
            if command == "riven_group":
                return [self._riven_group(argument, ctx)]
            if command == "riven_rank":
                return [self._riven_rank(ctx)]
            if command == "frame_rank":
                return [self._frame_rank(ctx)]
            if command == "junk_gold":
                return [self._junk(100, ctx)]
            if command == "junk_silver":
                return [self._junk(45, ctx)]
            if command == "junk_bronze":
                return [self._junk(15, ctx)]
            if command == "junk_fake_gold":
                return [self._junk(65, ctx)]
            if command == "junk_fake_silver":
                return [self._junk(25, ctx)]
            if command == "junk_overview":
                return [self._junk_overview(ctx)]
            if command == "rotation":
                return [self._rotation(argument, ctx)]
            if command == "rotation_acrithis":
                return [self._rotation("言录使", ctx)]
            if command == "rotation_glast":
                return [self._rotation("格拉斯", ctx)]
            if command == "rotation_eleanor":
                return [self._rotation("埃莉诺", ctx)]
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
            if command == "arbi_schedule":
                return [self._arbi_schedule(argument, ctx)]
            if command == "world_summary":
                return [self._world_summary(ctx)]
            if command == "world_alerts":
                return [present.alerts_reply(self._ws("alerts", force=True))]
            if command == "world_invasions":
                return [present.invasions_reply(self._ws("invasions", force=True))]
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
            if command in BOUNTY_VIEW:
                return [present.bounties_reply(self._ws("syndicateMissions"),
                                               location=BOUNTY_VIEW[command])]
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
            if command == "subscribe":
                return [self._subscribe(argument, ctx)]
            if command == "subs":
                return [self._subscriptions_list(ctx)]
            if command == "unsubscribe":
                return [self._unsubscribe(argument, ctx)]
            if command == "query":
                # 兼容 wm mod <名称> / wm 模组 <名称>
                mod_match = re.match(r"^(?:mod|模组)[\s:：/]+(.+)$",
                                     str(argument or "").strip(), re.I)
                if mod_match:
                    return [self._item(mod_match.group(1), ctx,
                                       category="mod")]
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

        # 俗名和市场物品重名时（如「双子」= 战甲 Orion & Sirius，市场里是武器 双子恶行），
        # 以玩家习惯为准，先按战甲解释。
        priority = names.priority_frame_nickname(argument)
        if priority:
            alias_name, english = priority
            return present.untradable_frame_reply(
                alias_name, english, names.warframe(english) or "")

        hits = self.market.lookup(argument, category=category, limit=5)
        if not hits:
            # 赋能被「必须带赋能前缀」挡掉了：提示加上前缀再查
            arcane_hint = ""
            if not wants_arcane(argument):
                only_arcane = self.market.lookup(
                    argument, category=category, limit=1, allow_arcane=True)
                if only_arcane and is_arcane(only_arcane[0].item):
                    arcane_hint = "想查赋能请加「赋能」前缀：%s%s 赋能%s" % (
                        ctx.prefix, "价格" if price_only else "wm", argument)
            english = names.frame_nickname(argument)
            if english:
                reply = present.untradable_frame_reply(
                    argument, english, names.warframe(english) or "")
            else:
                reply = present.not_found_reply(argument)
            if arcane_hint:
                reply = present.with_hint(reply, arcane_hint)
            return reply

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
        argument, platform = split_platform(argument)
        platform = platform or self.default_platform
        if not argument:
            # 不带武器名时给「最贵零洗紫卡榜」，比原来的用法提示更有用。
            return self._riven_rank(ctx)
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
        analysis = RivenAnalyzer().analyze(weapon)
        return present.riven_reply(weapon, auctions, note, platform,
                                   analysis=analysis)

    # 紫卡市场一次给几张（3×3 刚好一张图）
    RIVEN_MARKET_LIMIT = 9

    def _resolve_riven_weapon(self, text: str):
        """武器名 → RivenWeapon（先按紫卡武器表，再借市场物品索引）。"""
        if self.riven is None or not text:
            return None
        weapon = self.riven.resolve(text)
        if weapon is not None:
            return weapon
        try:
            hits = self.market.lookup(text, limit=3)
        except MarketError:
            hits = []
        if not hits:
            return None
        return self.riven.find(hits[0].item)

    def _relic(self, argument: str, ctx: Context) -> Reply:
        """#遗物 后纪s18：遗物内容、杜卡德、出入库与 WM 行情。"""
        if self.relics is None:
            return present.error_reply("遗物功能未启用。")
        argument, platform = split_platform(argument)
        platform = platform or self.default_platform
        text = str(argument or "").strip()
        if not text:
            return present.error_reply(
                "用法：%s遗物 <遗物编号>\n"
                "例如：%s遗物 后纪s18　%s遗物 后s18　%s遗物 Axi S18"
                % (ctx.prefix, ctx.prefix, ctx.prefix, ctx.prefix))
        matches = self.relics.search(text)
        if not matches:
            return present.error_reply(
                "没有找到遗物「%s」。\n"
                "可写：后纪s18 / 后s18 / Axi S18；"
                "后=后纪，前=前纪，中=中纪，古=古纪。" % text)
        if len(matches) > 1:
            codes = "、".join("%s %s" % (item.tier_label, item.code)
                              for item in matches[:12])
            return present.error_reply(
                "找到多个遗物：%s。\n请补完整编号，例如 后纪s18。" % codes)
        relic = self.relics.enrich(matches[0], platform=platform)
        return present.relic_reply(relic, platform)

    def _riven_market(self, argument: str, ctx: Context) -> Reply:
        """#wr <武器> [词条…] [负任意]：warframe.market 在线紫卡，最便宜 9 张。"""
        if self.riven is None:
            return present.error_reply("紫卡功能未启用。")
        argument, platform = split_platform(argument)
        platform = platform or self.default_platform
        text = str(argument or "").strip()
        if not text:
            return present.error_reply(
                "用法：%swr <武器> [词条…] [负任意]\n"
                "例如：%swr 翁\n　　　%swr 翁 攻击速度 范围 暴击伤害 负任意"
                % (ctx.prefix, ctx.prefix, ctx.prefix))

        # 武器名可能带空格（阿利乌双枪 / Dex 侍刃），从长到短试前缀
        words = text.split()
        weapon = None
        rest: List[str] = []
        for cut in range(min(4, len(words)), 0, -1):
            weapon = self._resolve_riven_weapon(" ".join(words[:cut]))
            if weapon is not None:
                rest = words[cut:]
                break
        if weapon is None:
            return present.error_reply(
                "「%s」没有对应的紫卡（可能是战甲、守护或未开放紫卡的武器）。"
                % text)

        query = parse_riven_filters(rest, self.riven.resolve_attribute)
        auctions, note = self.riven.auctions(
            weapon.slug, limit=self.RIVEN_MARKET_LIMIT, query=query)
        for auction in auctions:
            auction.weapon_name = weapon.name
            auction.weapon_icon = weapon.icon_url
        return present.riven_market_reply(weapon, auctions, note, platform, query)

    def _riven_group(self, argument: str, ctx: Context) -> Reply:
        """#紫卡分析 <组名>：按武器组看紫卡数值范围（主武器/副武器/近战/空战/同伴）。"""
        if self.riven is None:
            return present.error_reply("紫卡功能未启用。")
        text = str(argument or "").strip()
        key, category_filter = parse_group_detail(text) if text else (None, None)
        counts = self.riven.group_counts()
        if not sum(counts.values()):
            return present.error_reply(
                "紫卡武器表还没拉到（市场接口不通），稍后再试。")
        if not key:
            return present.riven_group_menu(counts)

        buckets = self.riven.group_weapons(key)
        if not buckets:
            return present.error_reply("「%s」这一组暂时没有紫卡武器。" % text)
        disposition = self.riven.disposition_override
        if disposition is None:
            disposition = 1.2
        label = GROUP_LABELS.get(key, key)
        if category_filter:
            label = "%s·%s" % (label, CATEGORY_LABELS.get(category_filter,
                                                        category_filter))
        # 常用类别排在前面；市场里副武器有「霰弹手枪」、同伴里有近战/手枪/霰弹，
        # 这些没在预设顺序里，按实际数据追加，别漏掉。
        order = list(GROUP_CATEGORY_ORDER.get(key) or ())
        for category in sorted(buckets):
            if category not in order:
                order.append(category)
        sections = []
        for category in order:
            if category_filter and category != category_filter:
                continue
            rows = buckets.get(category) or []
            if not rows:
                continue
            sections.append((category, RivenAnalyzer(disposition).analyze_group(
                GROUP_LABELS.get(key, key), category, disposition)))
        if not sections:
            return present.error_reply("「%s」暂时没有紫卡武器。" % text)
        return present.riven_group_reply(
            label, sections,
            {category: len(rows) for category, rows in buckets.items()},
            float(disposition))

    # ------------------------------------------------------------------ 价格榜（后台快照）
    def _scanning_reply(self, label: str, ready: int, total: int) -> Reply:
        if not total:
            return present.error_reply(
                "%s 数据还没就绪：物品索引尚未加载完成，稍后几秒再试。" % label)
        interval = float(getattr(self.board, "interval", 1.0) or 1.0)
        pending = max(0, total - ready)
        # 三张榜共用一条扫描队列，报出全量积压的耗时更接近真实等待。
        try:
            status = self.board.status() or {}
        except Exception:  # noqa: BLE001 - 状态取不到就按本档估算
            status = {}
        backlog = 0
        for kind in ("junk", "frames", "riven"):
            block = status.get(kind) or {}
            backlog += max(0, int(block.get("total") or 0)
                           - int(block.get("ready") or 0))
        pending = max(pending, backlog)
        minutes = max(1, int(pending * interval / 60) + 1)
        return present.error_reply(
            "%s 数据正在后台扫描（%d/%d），三张榜共用一条队列，全部扫完约 %d 分钟；"
            "到时候再发一次就行，之后都是秒回。" % (label, ready, total, minutes))

    def _junk(self, ducats: int, ctx: Context) -> Reply:
        if self.board is None:
            return present.error_reply("垃圾行情未启用（board.enabled = false）。")
        stats = self.board.junk_stats(ducats)
        if not stats.get("ready"):
            rarities = list(getattr(self.board, "rarities", (100, 45)))
            if ducats not in rarities:
                return present.error_reply(
                    "%s 目前不在扫描范围（board.junk_rarities = %s）。\n"
                    "需要的话把这个档位的杜卡特数加进配置即可。"
                    % (present.junk_label(ducats), rarities))
            ready, total = self.board.junk_coverage(ducats)
            return self._scanning_reply(stats.get("label") or "垃圾", ready, total)
        quotes = self.board.junk_quotes(ducats)
        return present.junk_reply(
            ducats, quotes, stats, self.board.item_name,
            platform=present.platform_label(self.default_platform))

    def _junk_overview(self, ctx: Context) -> Reply:
        if self.board is None:
            return present.error_reply("垃圾行情未启用（board.enabled = false）。")
        rarities = list(getattr(self.board, "rarities", (100, 45)))
        groups = [self.board.junk_stats(ducats) for ducats in rarities]
        if not any(group.get("ready") for group in groups):
            ready, total = self.board.coverage("junk")
            return self._scanning_reply("垃圾行情", ready, total)
        return present.junk_overview_reply(
            groups, present.platform_label(self.default_platform))

    def _rotation(self, argument: str, ctx: Context) -> Reply:
        """#轮换 / #轮换 言录使 / 格拉斯 / 埃莉诺。"""
        if self.rotation is None:
            return present.error_reply("轮换商店未启用（rotation.enabled = false）。")
        shop = rotation_shop(argument)
        if shop is None:
            return present.error_reply(
                "轮换商店怎么查：\n"
                "#轮换 —— 三个商店总览（含倒计时）\n"
                "#轮换 言录使 —— 本周 5 件（双衍王境 · 苦栓）\n"
                "#轮换 格拉斯 —— 信条近战 5 把的元素与加成\n"
                "#轮换 埃莉诺 —— 当前批次的终幕武器")
        state = self.rotation.snapshot()
        error = str(state.get("error") or "")
        acrithis = self.rotation.acrithis_view()
        glast = self.rotation.glast_view()
        eleanor = self.rotation.eleanor_view()
        if shop == "acrithis":
            reply = present.acrithis_reply(acrithis)
        elif shop == "glast":
            reply = present.glast_reply(glast)
        elif shop == "eleanor":
            reply = present.eleanor_reply(eleanor)
        else:
            reply = present.rotation_overview_reply(acrithis, glast, eleanor,
                                                    error=error)
        return reply

    def _frame_rank(self, ctx: Context) -> Reply:
        if self.board is None:
            return present.error_reply("战甲价格未启用（board.enabled = false）。")
        quotes = self.board.frame_quotes()
        ready, total = self.board.frame_coverage()
        if not quotes:
            return self._scanning_reply("Prime 战甲", ready, total)
        return present.frame_rank_reply(
            quotes, self.board.item_name, (ready, total),
            platform=present.platform_label(self.default_platform))

    def _riven_rank(self, ctx: Context) -> Reply:
        if self.board is None:
            return present.error_reply("紫卡排行未启用（board.enabled = false）。")
        quotes = self.board.riven_quotes()
        ready, total = self.board.riven_coverage()
        if not quotes:
            return self._scanning_reply("紫卡排行", ready, total)

        def weapon_of(slug: str):
            if self.riven is None:
                return None
            try:
                return self.riven.weapons().get(slug)
            except Exception:  # 武器表拉取失败不影响榜单
                return None

        def name_of(slug: str) -> str:
            weapon = weapon_of(slug)
            if weapon is not None and weapon.name:
                return weapon.name
            return str(slug).replace("_", " ")

        def icon_of(slug: str) -> str:
            weapon = weapon_of(slug)
            return weapon.icon_url if weapon is not None else ""

        return present.riven_rank_reply(quotes, name_of, (ready, total),
                                        icon_of=icon_of,
                                        calibrated=self.board.riven_calibrated())

    # ------------------------------------------------------------------ 数据更新
    def _update(self, argument: str, ctx: Context) -> Reply:
        if self.updater is None:
            return present.error_reply("自动更新未启用。")
        return Reply(text=self.updater.trigger())

    def _data_status(self, argument: str, ctx: Context) -> Reply:
        if self.updater is None:
            return present.error_reply("自动更新未启用。")
        text = self.updater.status_text(ctx.prefix)
        if self.database is not None:
            try:
                snapshot = self.database.snapshot()
            except Exception as exc:  # noqa: BLE001 - 状态页不该被数据库拖挂
                self.log.warning("读取数据库状态失败：%s", exc)
                snapshot = {"available": False, "message": str(exc)}
            if snapshot.get("available"):
                text += "\n数据库：%s 行 / 物品 %s / 构建 %s" % (
                    snapshot.get("total_rows", 0),
                    snapshot.get("items", 0),
                    snapshot.get("built_at") or "?")
            else:
                text += "\n数据库：未生成（%s）" % (
                    snapshot.get("message") or "python db/build.py")
        return Reply(text=text)

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

    def _ws(self, path: str, force: bool = False):
        if self.worldstate is None:
            raise CommandError("世界状态功能未启用。")
        if force:
            return self.worldstate.fetch(path, force=True)
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
        return present.arbitration_schedule_reply(entries)

    def _effective_arbitration(self, argument: str, ctx: Context) -> Reply:
        if self.arbi is None:
            return present.error_reply("仲裁评级未启用。")
        entries = self.arbi.effective(hours=24)
        return present.arbitration_schedule_reply(
            entries, "高效仲裁（未来 24 小时 S/A 级）",
            empty="未来 24 小时没有 S/A 级仲裁。")

    def _arbi_schedule(self, argument: str, ctx: Context) -> Reply:
        """仲裁表：未来 N 天（默认 7 天）的完整排期。"""
        if self.arbi is None:
            return present.error_reply("仲裁评级未启用（arbi.enabled = false）。")
        days = 7
        matched = re.search(r"\d+", str(argument or ""))
        if matched:
            days = max(1, min(int(matched.group()), 7))
        try:
            entries = self.arbi.upcoming(days)
        except ArbiError as exc:
            return present.error_reply(str(exc))
        return present.arbitration_week_reply(entries, days)

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
        rating_source = ""
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
                        rating_source = self.arbi.rating_source(site_node.key)
                        note = "Warframe 官方 API 暂无有效数据，按 arbi.wf.wiki 轮换推算"
                else:
                    node = self.arbi.node(arbitration.node_key)
                    rating = self.arbi.rating(arbitration.node_key)
                    rating_source = self.arbi.rating_source(arbitration.node_key)
            except ArbiError as exc:
                note = "评级数据暂不可用：%s" % exc

        if ((arbitration.expired or not arbitration.node
             or arbitration.node == "SolNode000") and node is None):
            return present.error_reply("当前没有有效的仲裁数据。")
        return present.arbitration_reply(arbitration, rating=rating,
                                         node=node, note=note,
                                         rating_source=rating_source)

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
        include_local = str(argument or "").strip() in ("本地", "local", "全部", "推演")
        return present.arbitration_tiers_reply(data, current_key,
                                               include_local=include_local)

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

    # ------------------------------------------------------------------ 订阅
    def _subscribe_help(self, ctx: Context) -> str:
        """订阅说明：类型一行给全，下面再逐条说清每种推什么、能筛什么。"""
        return (
            "【订阅】有新内容就推送到本群\n"
            "%s订阅 <类型> [筛选词]\n"
            "类型：%s\n"
            "　裂缝＝新裂缝出现（可筛 任务 / 节点 / 遗物等级 / 敌人）\n"
            "　虚空风暴＝新风暴出现（可筛 任务 / 节点 / 星域）\n"
            "　仲裁＝仲裁任务刷新（每小时一次）\n"
            "　突击＝每日突击刷新\n"
            "　回廊＝无尽回廊每周灵化刷新\n"
            "　轮换＝商店 / 言录使 / 格拉斯轮换\n"
            "　警报＝新警报出现　入侵＝新入侵出现　特惠＝每日特惠上架\n"
            "例如：%s订阅 裂缝 生存\n"
            "　　　%s订阅 仲裁\n"
            "%s订阅列表 查看　%s取消订阅 <内容>（例如 防御裂缝）取消" % (
                ctx.prefix, kind_names(), ctx.prefix, ctx.prefix,
                ctx.prefix, ctx.prefix))

    def _subscribe_lines(self, ctx: Context) -> str:
        if self.subscriptions is None:
            return "订阅功能未启用。"
        subs = self.subscriptions.store.list_for(ctx.chat_type, ctx.chat_id)
        if not subs:
            return "本群目前没有订阅。"
        lines = ["本群订阅（%d 条）：" % len(subs)]
        for index, sub in enumerate(subs, 1):
            lines.append("%d. %s　#%s" % (index, sub.title, sub.id))
        lines.append("取消：%s取消订阅 <内容>，例如 %s取消订阅 防御裂缝"
                     % (ctx.prefix, ctx.prefix))
        return "\n".join(lines)

    def _subscribe_panel(self, ctx: Context, headline: str = "") -> Reply:
        """订阅说明 + 本群订阅，直接出图（9 类事件一次列全）。"""
        text = "%s\n\n%s" % (self._subscribe_help(ctx), self._subscribe_lines(ctx))
        if headline:
            text = "%s\n\n%s" % (headline, text)
        subs = []
        if self.subscriptions is not None:
            for index, sub in enumerate(
                    self.subscriptions.store.list_for(ctx.chat_type, ctx.chat_id), 1):
                subs.append({"index": index, "title": sub.title,
                             "keyword": sub.keyword, "user": sub.user_id,
                             "created": sub.created_at})
        return present.subscribe_panel_reply(ctx.prefix, subs, text=text)

    def _subscribe(self, argument: str, ctx: Context) -> Reply:
        if self.subscriptions is None:
            return present.error_reply("订阅功能未启用。")
        argument = (argument or "").strip()
        if not argument:
            return self._subscribe_panel(ctx)
        kind, keyword = parse_kind(argument)
        if kind is None:
            hint = ""
            if any(token.isdigit() for token in argument.split()):
                hint = "\n价格提醒请用 %s提醒 <名称> <目标价>。" % ctx.prefix
            return self._subscribe_panel(
                ctx, "看不懂订阅类型「%s」。%s" % (argument, hint))
        try:
            sub, created = self.subscriptions.store.add(
                ctx.chat_type, ctx.chat_id, ctx.user_id, kind, keyword)
        except ValueError as exc:
            return present.error_reply(str(exc))
        if not created:
            return self._subscribe_panel(ctx, "本群已经订阅过「%s」了。" % sub.title)
        # 新订阅先记基线：当前已经存在的内容不补推，只推之后的
        self.subscriptions.seed(sub)
        return self._subscribe_panel(
            ctx, "已订阅「%s」，有新内容会推图到本群。" % sub.title)

    def _subscriptions_list(self, ctx: Context) -> Reply:
        if self.subscriptions is None:
            return present.error_reply("订阅功能未启用。")
        return self._subscribe_panel(ctx)

    def _unsubscribe(self, argument: str, ctx: Context) -> Reply:
        if self.subscriptions is None:
            return present.error_reply("订阅功能未启用。")
        ident = (argument or "").strip()
        if not ident:
            return self._subscribe_panel(
                ctx, "写法：%s取消订阅 <内容>，例如「%s取消订阅 防御裂缝」"
                     "「%s取消订阅 裂缝」「%s取消订阅 生存」；\n"
                     "清空本群全部订阅用 %s取消订阅 all。"
                     % (ctx.prefix, ctx.prefix, ctx.prefix, ctx.prefix,
                        ctx.prefix))
        removed = self.subscriptions.store.remove(ctx.chat_type, ctx.chat_id, ident)
        if not removed:
            return self._subscribe_panel(
                ctx, "本群没有和「%s」对得上的订阅，看看下面这张表里的类型和筛选词。"
                     % ident)
        names = "、".join(sub.title for sub in removed)
        return self._subscribe_panel(ctx, "已取消：%s" % names)

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
        # 市场里没有可交易物品的战甲（老九 / 蘑菇 / 冰女…），术语表查不到，
        # 用俗名表直接换成官方英文名。
        nickname = names.frame_nickname(query)
        if nickname:
            return [nickname]
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
        # 翻译接口不通时（例如这台机器连不上 MyMemory）会一路超时到所有代理
        # 都试完，一次要等几十秒。失败后冷却一段时间，别每条查询都白等。
        if time.time() < self._translate_blocked_until:
            return ""
        try:
            return self.translator.translate(page.extract)
        except Exception as exc:
            self.log.warning("Wiki 中文翻译失败：%s", exc)
            self._translate_blocked_until = time.time() + TRANSLATE_BLOCK_SECONDS
            return ""


def strip_cq(text: str) -> str:
    """去掉 CQ 码，只保留可读文本。"""
    return CQ_RE.sub("", text or "").strip()
