import json
import os
import tempfile
import time
import unittest
from unittest import mock

from bot import rivenocr
from bot.arbi import ArbiData, ArbiNode
from bot.alerts import AlertStore
from bot.commands import (CommandRouter, Context, split_platform, split_roll_args,
                          strip_cq)
from bot.market import ItemIndex, MarketClient, MarketError, MarketStats, Order, StatSummary
from bot.riven import RivenWeapon
from bot.wiki import WikiPage
from bot.worldstate import Arbitration

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_index():
    client = MarketClient({
        "base_url": "http://example.invalid",
        "language": "zh-hans",
        "platform": "pc",
        "crossplay": True,
        "cache_file": "",
        "alias_file": os.path.join(BASE, "data", "aliases.json"),
    })
    raw = load_fixture("items.json")
    items = [client._parse_short(row) for row in raw["data"]]
    aliases = client._load_aliases()
    # 测试夹具没有 Rhino Prime 总图蓝图。
    aliases["牛甲"] = "rhino_prime_set"
    aliases["女枪"] = "mesa_prime_set"
    return ItemIndex([item for item in items if item], aliases)


class FakeMarket:
    def __init__(self, index):
        self.index = index
        self.detail_calls = []
        self.platforms = []
        self.force = []
        self.fail = False

    def lookup(self, query, category=None, limit=5):
        if self.fail:
            raise MarketError("测试故障")
        return self.index.search(query, category=category, limit=limit)

    def item_detail(self, slug):
        self.detail_calls.append(slug)
        return self.index.get(slug)

    def top_orders(self, slug, limit=5, platform=None, force=False):
        self.force.append(force)
        self.platforms.append(platform)
        return ([Order("sell", 60, 12, "adadaAS", "ingame"),
                 Order("sell", 63, 11, "Novaky_Nov", "ingame")],
                [Order("buy", 50, 25, "Tayfun0606", "ingame")])

    def statistics(self, slug, platform=None, force=False):
        return MarketStats(
            h48=StatSummary(412, 61.8, 62, 55, 70),
            d90=StatSummary(13404, 71.5, 70, 55, 89),
        )

    def set_breakdown(self, item, platform=None, ttl=300):
        from bot.market import PartQuote
        return [PartQuote("a", "Mesa Prime 蓝图", 12, 9),
                PartQuote("b", "Mesa Prime 机体 蓝图", 18, 14)]


class FakeWiki:
    def __init__(self):
        self.calls = []
        self.missing = []
        # 模拟搜索兜底返回了不相干的词条
        self.wrong_titles = {}

    def lookup(self, query, limit=3):
        self.calls.append(query)
        if query == "空" or query in self.missing:
            return None, []
        title = self.wrong_titles.get(query, query)
        return WikiPage(title=title, extract="高斯是一款以速度为主题的战甲。",
                        url="https://warframe.huijiwiki.com/wiki/高斯"), []


def make_router(fail_market=False, alerts=None, riven=None, updater=None,
                arbi=None, worldstate=None, subscriptions=None):
    market = FakeMarket(build_index())
    market.fail = fail_market
    wiki = FakeWiki()
    config = {
        "behavior": {"prefixes": ["#", "＃"]},
        "warframe_market": {"top_orders": 3, "price_orders": 5, "platform": "pc"},
    }
    return CommandRouter(market, wiki, config, alerts=alerts, riven=riven,
                         updater=updater, arbi=arbi,
                         worldstate=worldstate,
                         subscriptions=subscriptions), market, wiki


class FakeImageRiven:
    def resolve(self, text):
        if "视使之触" in str(text):
            return RivenWeapon(
                slug="ocucor", name="视使之触", name_en="Ocucor",
                riven_type="secondary", group="secondary",
                disposition=1.0, mastery_rank=8)
        return None

    def find(self, item):
        return None


class FakeUpdater:
    def __init__(self):
        self.triggered = 0
        self.prefix = ""

    def trigger(self):
        self.triggered += 1
        return "已开始后台更新，完成后自动生效。"

    def status_text(self, prefix="#"):
        self.prefix = prefix
        return "【数据更新状态】\n市场物品：100 条（1.0 小时前）"


class FakeRiven:
    def __init__(self):
        self.queries = []

    def weapons(self):
        return {"soma": RivenWeapon(
            slug="soma", name="索玛", name_en="Soma", riven_type="rifle",
            group="primary", disposition=1.2, mastery_rank=6)}

    def find(self, item):
        self.queries.append(item.slug)
        return RivenWeapon(
            slug="rubico", name="绝路", name_en="Rubico", riven_type="rifle",
            group="primary", disposition=0.95, mastery_rank=12)


def texts(replies):
    return [reply.text for reply in replies]


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.router, _, _ = make_router()

    def test_basic(self):
        self.assertEqual(self.router.parse("#查 绝路"), ("query", "绝路"))

    def test_no_space_chinese(self):
        self.assertEqual(self.router.parse("#查绝路"), ("query", "绝路"))

    def test_prefix_no_space_warframe(self):
        self.assertEqual(self.router.parse("#战甲Mesa"), ("warframe", "Mesa"))

    def test_price(self):
        self.assertEqual(self.router.parse("#价格 牛甲"), ("price", "牛甲"))

    def test_case_insensitive_english(self):
        self.assertEqual(self.router.parse("#MOD 分裂膛室"), ("mod", "分裂膛室"))

    def test_wf_alias(self):
        self.assertEqual(self.router.parse("#wf Mesa"), ("warframe", "Mesa"))

    def test_help(self):
        self.assertEqual(self.router.parse("#帮助"), ("help", ""))
        # 帮助只认「帮助」：问号、菜单之类的词不再触发
        self.assertIsNone(self.router.parse("#?"))
        self.assertIsNone(self.router.parse("#？"))
        self.assertIsNone(self.router.parse("#菜单"))
        self.assertEqual(self.router.parse("#帮助我看看"), ("help", "我看看"))

    def test_fullwidth_prefix(self):
        self.assertEqual(self.router.parse("＃查 绝路"), ("query", "绝路"))

    def test_bare_prefix_is_ignored_now(self):
        # 只发一个 # 不再回帮助卡片
        self.assertIsNone(self.router.parse("#"))

    def test_unknown_command_is_query(self):
        self.assertEqual(self.router.parse("#绝路"), ("query", "绝路"))

    def test_not_a_command(self):
        self.assertIsNone(self.router.parse("查 绝路"))
        self.assertIsNone(self.router.parse(""))

    def test_strip_cq(self):
        self.assertEqual(strip_cq("[CQ:at,qq=1] 查 绝路"), "查 绝路")

    def test_new_commands(self):
        self.assertEqual(self.router.parse("#拆件 Mesa")[0], "set")
        self.assertEqual(self.router.parse("#紫卡 绝路")[0], "riven")
        self.assertEqual(self.router.parse("#奸商")[0], "baro")
        self.assertEqual(self.router.parse("#突击")[0], "sortie")
        self.assertEqual(self.router.parse("#仲裁")[0], "arbitration")
        self.assertEqual(self.router.parse("#提醒 绝路 50")[0], "alert")
        self.assertEqual(self.router.parse("#提醒列表")[0], "alerts")
        self.assertEqual(self.router.parse("#取消提醒 ab12")[0], "unalert")

    def test_alert_list_not_parsed_as_alert(self):
        self.assertEqual(self.router.parse("#提醒列表")[0], "alerts")

    def test_split_platform(self):
        self.assertEqual(split_platform("绝路 ps"), ("绝路", "ps4"))
        self.assertEqual(split_platform("绝路 xbox"), ("绝路", "xbox"))
        self.assertEqual(split_platform("绝路"), ("绝路", None))
        self.assertEqual(split_platform("Mesa Prime"), ("Mesa Prime", None))


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.router, self.market, self.wiki = make_router()
        self.ctx = Context(user_id="1", group_id="2", is_group=True, prefix="#")

    def test_help(self):
        replies = texts(self.router.handle("#帮助", self.ctx))
        self.assertEqual(len(replies), 1)
        self.assertIn("#wm", replies[0])
        self.assertIn("#紫卡", replies[0])

    def test_query_card(self):
        replies = texts(self.router.handle("#查 mesa", self.ctx))
        self.assertIn("Mesa Prime 一套", replies[0])
        self.assertIn("60p ×12", replies[0])
        self.assertIn("adadaAS", replies[0])
        self.assertIn("48小时", replies[0])

    def test_query_has_image(self):
        replies = self.router.handle("#查 mesa", self.ctx)
        self.assertTrue(replies[0].has_image)
        png = replies[0].image()
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_query_without_argument(self):
        replies = texts(self.router.handle("#查", self.ctx))
        self.assertIn("用法", replies[0])

    def test_price_command(self):
        replies = texts(self.router.handle("#价格 绝路", self.ctx))
        self.assertIn("价格", replies[0])
        self.assertIn("60p", replies[0])

    def test_platform_passed_through(self):
        self.router.handle("#价格 绝路 ps", self.ctx)
        self.assertIn("ps4", self.market.platforms)

    def test_not_found(self):
        replies = texts(self.router.handle("#查 zzzzz", self.ctx))
        self.assertIn("没有在 warframe.market 找到", replies[0])

    def test_ambiguous(self):
        replies = texts(self.router.handle("#查 一套", self.ctx))
        self.assertIn("没有找到唯一匹配", replies[0])

    def test_set_wins_over_component(self):
        replies = texts(self.router.handle("#查 绝路", self.ctx))
        self.assertIn("绝路 Prime 一套", replies[0])
        self.assertNotIn("没有找到唯一匹配", replies[0])

    def test_category_weapon_no_mesa(self):
        replies = texts(self.router.handle("#武器 mesa", self.ctx))
        self.assertIn("没有在 warframe.market 找到", replies[0])

    def test_category_mod(self):
        replies = texts(self.router.handle("#mod 分裂", self.ctx))
        self.assertIn("分裂膛室", replies[0])
        self.assertIn("MOD", replies[0])

    def test_wiki(self):
        replies = texts(self.router.handle("#wiki 高斯", self.ctx))
        self.assertIn("中文维基", replies[0])
        self.assertIn("速度为主题", replies[0])
        self.assertEqual(self.wiki.calls, ["高斯"])

    def test_wiki_chinese_item_maps_to_english(self):
        replies = texts(self.router.handle("#wiki 分裂膛室", self.ctx))
        self.assertEqual(self.wiki.calls, ["Split Chamber"])
        self.assertIn("中文「分裂膛室」→ 英文词条「Split Chamber」", replies[0])

    def test_wiki_rejects_unrelated_search_hit(self):
        # 「Rhino Set」被搜索兜底成「Rhino Animation Set」时应换下一个候选
        self.wiki.wrong_titles = {"Rhino Set": "Rhino Animation Set"}
        replies = texts(self.router.handle("#wiki 牛甲", self.ctx))
        self.assertEqual(self.wiki.calls, ["Rhino Set", "Rhino"])
        self.assertIn("英文词条「Rhino」", replies[0])
        self.assertNotIn("Rhino Animation Set", replies[0])

    def test_wiki_chinese_term_maps_to_english(self):
        texts(self.router.handle("#wiki 赤毒要塞", self.ctx))
        self.assertEqual(self.wiki.calls, ["Kuva Fortress"])

    def test_wiki_english_query_is_not_rewritten(self):
        texts(self.router.handle("#wiki Volt Prime", self.ctx))
        self.assertEqual(self.wiki.calls, ["Volt Prime"])

    def test_wiki_tries_stripped_set_title(self):
        # “牛甲”是别名，先试 Rhino Set，维基没有该词条时退到 Rhino
        self.wiki.missing = ["Rhino Set"]
        texts(self.router.handle("#wiki 牛甲", self.ctx))
        self.assertEqual(self.wiki.calls, ["Rhino Set", "Rhino"])

    def test_translate(self):
        replies = texts(self.router.handle("#翻译 分裂膛室", self.ctx))
        self.assertIn("中文：分裂膛室", replies[0])
        self.assertIn("英文：Split Chamber", replies[0])

    def test_wiki_empty(self):
        replies = texts(self.router.handle("#wiki", self.ctx))
        self.assertIn("用法", replies[0])

    def test_wiki_missing(self):
        replies = texts(self.router.handle("#wiki 空", self.ctx))
        self.assertIn("没有找到", replies[0])

    def test_market_failure_message(self):
        router, market, _ = make_router(fail_market=True)
        replies = texts(router.handle("#查 mesa", self.ctx))
        self.assertIn("查询失败", replies[0])

    def test_wm_realtime_force(self):
        router, market, _ = make_router()
        router.handle("#wm mesa", self.ctx)
        self.assertTrue(market.force)
        self.assertTrue(all(market.force))

    def test_unknown_command_falls_back_to_query(self):
        replies = texts(self.router.handle("#牛甲", self.ctx))
        self.assertIn("Rhino Prime 一套", replies[0])

    def test_non_command_returns_empty(self):
        self.assertEqual(self.router.handle("随便聊聊", self.ctx), [])

    def test_set_breakdown(self):
        replies = self.router.handle("#拆件 mesa", self.ctx)
        self.assertIn("拆件比价", replies[0].text)
        self.assertTrue(replies[0].has_image)

    def test_set_breakdown_frame_alias(self):
        replies = self.router.handle("#拆件 女枪", self.ctx)
        self.assertIn("拆件比价", replies[0].text)
        self.assertTrue(replies[0].has_image)

    def test_riven_disabled_without_client(self):
        replies = texts(self.router.handle("#紫卡 绝路", self.ctx))
        self.assertIn("未启用", replies[0])

    def test_baro_disabled_without_client(self):
        replies = texts(self.router.handle("#奸商", self.ctx))
        self.assertIn("未启用", replies[0])


class AlertCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wf-alert-")
        self.store = AlertStore(os.path.join(self.tmp, "alerts.json"))
        self.router, self.market, _ = make_router(alerts=self.store)
        self.ctx = Context(user_id="1", group_id="2", is_group=True, prefix="#")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_list_remove(self):
        replies = texts(self.router.handle("#提醒 绝路 50", self.ctx))
        self.assertIn("已添加提醒", replies[0])
        alerts = self.store.list_for("group", "2")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].target, 50)

        listed = texts(self.router.handle("#提醒列表", self.ctx))
        self.assertIn("绝路", listed[0])
        self.assertIn("50p", listed[0])

        removed = texts(self.router.handle("#取消提醒 all", self.ctx))
        self.assertIn("已取消", removed[0])
        self.assertEqual(self.store.list_for("group", "2"), [])

    def test_bad_target(self):
        replies = texts(self.router.handle("#提醒 绝路 五十", self.ctx))
        self.assertIn("目标价要写数字", replies[0])

    def test_platform_before_price(self):
        self.router.handle("#提醒 绝路 ps 50", self.ctx)
        alerts = self.store.list_for("group", "2")
        self.assertEqual(alerts[0].platform, "ps4")
        self.assertEqual(alerts[0].target, 50)

    def test_platform_after_price(self):
        self.router.handle("#提醒 绝路 50 xbox", self.ctx)
        alerts = self.store.list_for("group", "2")
        self.assertEqual(alerts[0].platform, "xbox")

    def test_duplicate(self):
        self.router.handle("#提醒 绝路 50", self.ctx)
        replies = texts(self.router.handle("#提醒 绝路 40", self.ctx))
        self.assertIn("已经提醒过", replies[0])

    def test_remove_missing(self):
        replies = texts(self.router.handle("#取消提醒 zzzz", self.ctx))
        self.assertIn("没有找到", replies[0])


class FakeSubWorld:
    """订阅服务只用到这三个取数方法。"""

    def fissures(self):
        return []

    def void_storms(self):
        return []

    def fetch(self, path):
        return None


class SubscribeCommandTest(unittest.TestCase):
    def setUp(self):
        from bot.subscriptions import SubscriptionService, SubscriptionStore
        self.dir = tempfile.mkdtemp()
        self.store = SubscriptionStore(os.path.join(self.dir, "subs.json"))
        self.service = SubscriptionService(self.store, FakeSubWorld())
        self.ctx = Context(user_id="1", group_id="2", is_group=True)
        self.router, _, _ = make_router(subscriptions=self.service)

    def test_add_and_list(self):
        reply = self.router.handle("#订阅 裂缝 生存", self.ctx)[0]
        self.assertIn("已订阅", reply.text)
        self.assertIn("裂缝·生存", reply.text)
        subs = self.store.list_for("group", "2")
        self.assertEqual(subs[0].kind, "fissure")
        self.assertEqual(subs[0].keyword, "生存")
        listed = self.router.handle("#订阅列表", self.ctx)[0]
        self.assertIn("裂缝·生存", listed.text)

    def test_duplicate(self):
        self.router.handle("#订阅 仲裁", self.ctx)
        reply = self.router.handle("#订阅 仲裁", self.ctx)[0]
        self.assertIn("已经订阅过", reply.text)

    def test_unknown_kind(self):
        reply = self.router.handle("#订阅 绝路", self.ctx)[0]
        self.assertIn("看不懂", reply.text)
        self.assertEqual(self.store.list_for("group", "2"), [])

    def test_help_without_argument(self):
        reply = self.router.handle("#订阅", self.ctx)[0]
        self.assertIn("裂缝", reply.text)
        self.assertIn("仲裁", reply.text)

    def test_unsubscribe(self):
        self.router.handle("#订阅 回廊", self.ctx)
        reply = self.router.handle("#取消订阅 回廊", self.ctx)[0]
        self.assertIn("已取消", reply.text)
        self.assertEqual(self.store.list_for("group", "2"), [])

    def test_price_alert_still_works(self):
        alerts = AlertStore(os.path.join(self.dir, "alerts.json"))
        router, _, _ = make_router(alerts=alerts)
        router.handle("#提醒 绝路 50", self.ctx)
        self.assertEqual(len(alerts.list_for("group", "2")), 1)


class RivenAnalysisCommandTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(user_id="1", group_id="2", is_group=True)

    def test_parse_command(self):
        router, _, _ = make_router()
        self.assertEqual(router.parse("#wm 绝路"), ("query", "绝路"))
        self.assertEqual(router.parse("#紫卡分析 绝路"),
                         ("riven_analysis", "绝路"))
        self.assertIsNone(router.parse("#唤醒"))

    def test_parse_bare_command(self):
        router, _, _ = make_router()
        self.assertEqual(router.parse("wm 绝路", allow_bare=True),
                         ("query", "绝路"))
        self.assertEqual(router.parse("帮助", allow_bare=True), ("help", ""))
        self.assertEqual(router.parse("价格 绝路", allow_bare=True),
                         ("price", "绝路"))
        self.assertEqual(router.parse("#希图斯"), ("cetus", ""))
        self.assertEqual(router.parse("希图斯赏金", allow_bare=True),
                         ("cetus", ""))
        self.assertEqual(router.parse("#虚空风暴"), ("storms", ""))
        self.assertEqual(router.parse("九重天", allow_bare=True),
                         ("storms", ""))
        self.assertEqual(router.parse("wm绝路", allow_bare=True),
                         ("query", "绝路"))
        self.assertIsNone(router.parse("绝路", allow_bare=True))
        self.assertIsNone(router.parse("随便聊聊", allow_bare=True))
        self.assertIsNone(router.parse("唤醒", allow_bare=True))
        self.assertEqual(router.parse("#紫卡分析绝路"),
                         ("riven_analysis", "绝路"))

    def test_parse_bare_requires_whole_word(self):
        """裸词唤醒只认独立的词：句子里夹带「仲裁」「回廊」不该触发。"""
        router, _, _ = make_router()
        self.assertEqual(router.parse("仲裁", allow_bare=True), ("arbitration", ""))
        self.assertEqual(router.parse("回廊", allow_bare=True), ("circuit", ""))
        for sentence in ("仲裁什么时候刷新", "仲裁 怎么打", "回廊怎么进",
                         "今天仲裁是啥", "突击打哪个好"):
            self.assertIsNone(router.parse(sentence, allow_bare=True), sentence)
        # 带参数的命令仍支持连写，但明显是聊天句子就不接
        self.assertEqual(router.parse("查绝路", allow_bare=True), ("query", "绝路"))
        self.assertIsNone(router.parse("紫卡分析这个怎么样", allow_bare=True))

    def test_split_roll_args(self):
        self.assertEqual(split_roll_args("绝路 伤害=165 多重=90"),
                         ("绝路", [("伤害", 165.0), ("多重", 90.0)]))
        self.assertEqual(split_roll_args("绝路 伤害 165 多重 90"),
                         ("绝路", [("伤害", 165.0), ("多重", 90.0)]))
        weapon, pairs = split_roll_args("绝路 165")
        self.assertEqual(pairs, [])
        self.assertEqual(weapon, "绝路 165")

    def test_analysis_ranges_and_score(self):
        riven = FakeRiven()
        router, _, _ = make_router(riven=riven)
        replies = router.handle("#紫卡分析 绝路 伤害=165", self.ctx)
        self.assertEqual(len(replies), 1)
        self.assertIn("紫卡分析", replies[0].text)
        self.assertIn("绝路", replies[0].text)
        self.assertIn("范围", replies[0].text)
        self.assertTrue(riven.queries)

    def test_analysis_ranges_only(self):
        riven = FakeRiven()
        router, _, _ = make_router(riven=riven)
        replies = router.handle("#紫卡分析 绝路", self.ctx)
        self.assertEqual(len(replies), 1)
        self.assertIn("正向范围", replies[0].text)
        self.assertTrue(riven.queries)

    def test_analysis_alias(self):
        riven = FakeRiven()
        router, _, _ = make_router(riven=riven)
        replies = router.handle("#紫卡 分析 绝路", self.ctx)
        self.assertIn("紫卡分析", replies[0].text)
        self.assertTrue(riven.queries)

    def test_analysis_disabled(self):
        router, _, _ = make_router(riven=None)
        replies = router.handle("#紫卡分析 绝路", self.ctx)
        self.assertIn("未启用", replies[0].text)


class UpdateCommandTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(user_id="1", group_id="2", is_group=True)

    def test_parse(self):
        router, _, _ = make_router()
        self.assertEqual(router.parse("#更新"), ("update", ""))
        self.assertEqual(router.parse("#数据状态"), ("data_status", ""))

    def test_update(self):
        updater = FakeUpdater()
        router, _, _ = make_router(updater=updater)
        replies = router.handle("#更新", self.ctx)
        self.assertIn("已开始", replies[0].text)
        self.assertEqual(updater.triggered, 1)

    def test_status(self):
        updater = FakeUpdater()
        router, _, _ = make_router(updater=updater)
        replies = router.handle("#数据状态", self.ctx)
        self.assertIn("数据更新状态", replies[0].text)
        self.assertEqual(updater.prefix, "#")

    def test_disabled(self):
        router, _, _ = make_router(updater=None)
        replies = router.handle("#更新", self.ctx)
        self.assertIn("未启用", replies[0].text)


class FakeArbi:
    def __init__(self):
        self.callisto = ArbiNode("SolNode1", "Callisto", "木星", "拦截",
                                 "Corpus", 15, 20)
        self.everest = ArbiNode("SolNode39", "Everest", "地球", "挖掘",
                                "Grineer", 1, 6)
        self.data_obj = ArbiData(
            nodes={self.callisto.key: self.callisto,
                   self.everest.key: self.everest},
            tiers={self.callisto.key: "S"}, order=["S"])

    def data(self):
        return self.data_obj

    def current_node(self):
        return self.everest, 4102444800.0

    def rating(self, key):
        return self.data_obj.rating(key)

    def rating_source(self, key):
        return self.data_obj.resolve(key)[1]

    def node(self, key):
        return self.data_obj.nodes.get(key)


class FakeWorld:
    def arbitration(self):
        return Arbitration(node="SolNode000", mission="Unknown",
                           enemy="Tenno", node_key="SolNode000",
                           expired=True)


class ArbitrationCommandTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(user_id="1", group_id="2", is_group=True)

    def test_parse_tiers(self):
        router, _, _ = make_router()
        self.assertEqual(router.parse("#仲裁评级"),
                         ("arbi_tiers", ""))

    def test_tiers_reply(self):
        router, _, _ = make_router(arbi=FakeArbi())
        replies = router.handle("#仲裁评级", self.ctx)
        self.assertIn("仲裁评级", replies[0].text)
        self.assertIn("Callisto", replies[0].text)

    def test_arbitration_fallback(self):
        router, _, _ = make_router(arbi=FakeArbi(), worldstate=FakeWorld())
        replies = router.handle("#仲裁", self.ctx)
        self.assertIn("Everest", replies[0].text)
        self.assertIn("推算", replies[0].text)


class FakeBoard:
    """价格榜假数据：命令层只读快照。"""

    def __init__(self, ready=True, interval=1.0):
        self.interval = interval
        self.ready = ready
        self.rarities = (100, 45)

    def item_name(self, slug):
        return {"ash_prime_systems_blueprint": "Ash Prime 系统蓝图",
                "loki_prime_systems_blueprint": "Loki Prime 系统蓝图",
                "saryn_prime_set": "Saryn Prime 一套"}.get(slug, slug.replace("_", " "))

    def junk_stats(self, ducats):
        prices = [7, 25] if self.ready else []
        return {
            "ducats": ducats, "label": "金垃圾", "ready": len(prices),
            "total": 2, "min": prices[0] if prices else 0,
            "median": prices[len(prices) // 2] if prices else 0,
            "updated": 0.0, "orders": 2,
        }

    def junk_quotes(self, ducats):
        if not self.ready:
            return []
        from bot.board import BoardQuote
        return [BoardQuote("ash_prime_systems_blueprint", 7, 6, 1, 0.0, ducats),
                BoardQuote("loki_prime_systems_blueprint", 25, 20, 1, 0.0, ducats)]

    def junk_coverage(self, ducats):
        return (2, 2) if self.ready else (0, 2)

    def coverage(self, kind):
        return (2, 2) if self.ready else (0, 2)

    def frame_coverage(self):
        return (2, 2) if self.ready else (0, 2)

    def riven_coverage(self):
        return (1, 1) if self.ready else (0, 1)

    def frame_quotes(self):
        if not self.ready:
            return []
        from bot.board import BoardQuote
        return [BoardQuote("saryn_prime_set", 260, 240, 1, 0.0),
                BoardQuote("ash_prime_set", 120, 110, 1, 0.0)]

    def riven_quotes(self):
        if not self.ready:
            return []
        from bot.board import RivenQuote
        return [RivenQuote("soma", 5000, True, "Alice",
                           ("暴击几率 +161.1%", "多重射击 +98.1%"), "", 0.0)]


class BoardCommandTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(user_id="1", group_id="2", is_group=True, prefix="#")

    def router(self, board):
        router, _market, _wiki = make_router()
        router.board = board
        return router

    def test_parse_new_commands(self):
        router = self.router(FakeBoard())
        self.assertEqual(router.parse("#金垃圾")[0], "junk_gold")
        self.assertEqual(router.parse("#银垃圾")[0], "junk_silver")
        self.assertEqual(router.parse("#铜垃圾")[0], "junk_bronze")
        self.assertEqual(router.parse("#垃圾")[0], "junk_overview")
        self.assertEqual(router.parse("#战甲榜")[0], "frame_rank")
        self.assertEqual(router.parse("#紫卡榜")[0], "riven_rank")
        # 不带武器名的紫卡 = 零洗紫卡榜
        self.assertEqual(router.parse("#紫卡"), ("riven", ""))

    def test_junk_reply(self):
        reply = self.router(FakeBoard()).handle("#金垃圾", self.ctx)[0]
        self.assertIn("金垃圾", reply.text)
        self.assertIn("7p", reply.text)
        self.assertIn("Ash Prime 系统蓝图", reply.text)
        self.assertTrue(reply.has_image)

    def test_junk_overview(self):
        reply = self.router(FakeBoard()).handle("#垃圾", self.ctx)[0]
        self.assertIn("Prime 垃圾行情", reply.text)
        self.assertIn("金垃圾", reply.text)

    def test_frame_rank(self):
        reply = self.router(FakeBoard()).handle("#战甲榜", self.ctx)[0]
        self.assertIn("Saryn Prime 一套", reply.text)
        self.assertIn("260p", reply.text)

    def test_riven_rank_without_argument(self):
        router = self.router(FakeBoard())
        router.riven = FakeRiven()
        reply = router.handle("#紫卡", self.ctx)[0]
        self.assertIn("零洗紫卡", reply.text)
        self.assertIn("5000p", reply.text)

    def test_scanning_hint_when_empty(self):
        reply = self.router(FakeBoard(ready=False)).handle("#银垃圾", self.ctx)[0]
        self.assertIn("后台扫描", reply.text)

    def test_disabled_without_board(self):
        reply = self.router(None).handle("#金垃圾", self.ctx)[0]
        self.assertIn("未启用", reply.text)


class FakeRotation:
    """轮换商店假数据：命令层只读快照。"""

    def __init__(self, ready=True, error=""):
        self.ready = ready
        self.error = error

    def snapshot(self):
        return {"data": {}, "fetched": {}, "error": self.error}

    def acrithis_view(self):
        rows = ([{"name": "霰弹枪裂罅 Mod", "short": "霰弹裂罅", "cost": 15,
                  "en": "Shotgun Riven Mod"},
                 {"name": "Forma 蓝图", "short": "Forma", "cost": 10,
                  "en": "Forma"},
                 {"name": "步枪裂罅 Mod", "short": "步枪裂罅", "cost": 15,
                  "en": "Rifle Riven Mod"},
                 {"name": "同伴武器裂罅 Mod", "short": "同伴裂罅", "cost": 20,
                  "en": "Companion Weapon Riven Mod"},
                 {"name": "10,000 赤毒", "short": "1 万赤毒", "cost": 10,
                  "en": "10,000 Kuva"}] if self.ready else [])
        return {"observed": "10 月 26 日", "rows": rows,
                "fetched": time.time(), "next": time.time() + 5 * 86400}

    def glast_view(self):
        rows = [{"name": "信条·铁晶磁轨炮", "element": "辐射", "percent": 38.4},
                {"name": "信条·枢密", "element": "磁力", "percent": 43.9}]
        if not self.ready:
            rows = [{"name": "信条·铁晶磁轨炮", "element": "", "percent": 0.0}]
        return {"rows": rows, "fetched": time.time(),
                "next": time.time() + 2 * 86400, "complete": self.ready}

    def eleanor_view(self, now=None):
        rows = [{"name": "终幕·血肢", "element": "冰冻", "percent": 25.6},
                {"name": "终幕·脓痘", "element": "毒素", "percent": 54.3}]
        if not self.ready:
            rows = [{"name": "终幕·血肢", "element": "", "percent": 0.0}]
        return {"batch": "B", "rows": rows, "fetched": time.time(),
                "next": time.time() + 2 * 86400, "complete": self.ready}


class RotationCommandTest(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(user_id="1", group_id="2", is_group=True, prefix="#")

    def router(self, rotation):
        router, _market, _wiki = make_router()
        router.rotation = rotation
        return router

    def test_parse_commands(self):
        router = self.router(FakeRotation())
        self.assertEqual(router.parse("#轮换"), ("rotation", ""))
        self.assertEqual(router.parse("#轮换 言录使"), ("rotation", "言录使"))
        self.assertEqual(router.parse("#言录使")[0], "rotation_acrithis")
        self.assertEqual(router.parse("#格拉斯")[0], "rotation_glast")
        self.assertEqual(router.parse("#埃莉诺")[0], "rotation_eleanor")
        self.assertEqual(router.parse("#轮换商店")[0], "rotation")

    def test_overview(self):
        reply = self.router(FakeRotation()).handle("#轮换", self.ctx)[0]
        self.assertIn("言录使", reply.text)
        self.assertIn("埃尔戈·格拉斯", reply.text)
        self.assertIn("埃莉诺", reply.text)
        self.assertIn("B 批", reply.text)
        self.assertTrue(reply.has_image)

    def test_overview_lists_every_item_and_bonus(self):
        reply = self.router(FakeRotation()).handle("#轮换", self.ctx)[0]
        for name in ("霰弹枪裂罅 Mod", "Forma 蓝图", "步枪裂罅 Mod",
                     "同伴武器裂罅 Mod", "10,000 赤毒"):
            self.assertIn(name, reply.text)
        for text in ("信条·铁晶磁轨炮：辐射 38.4%", "信条·枢密：磁力 43.9%",
                     "终幕·血肢：冰冻 25.6%", "终幕·脓痘：毒素 54.3%"):
            self.assertIn(text, reply.text)
        self.assertIn("15 苦栓", reply.text)
        self.assertIn("10 月 26 日", reply.text)

    def test_acrithis_detail(self):
        reply = self.router(FakeRotation()).handle("#轮换 言录使", self.ctx)[0]
        self.assertIn("15 苦栓", reply.text)
        self.assertIn("Forma 蓝图", reply.text)
        self.assertIn("10 月 26 日", reply.text)
        self.assertTrue(reply.has_image)

    def test_glast_detail(self):
        reply = self.router(FakeRotation()).handle("#轮换 格拉斯", self.ctx)[0]
        self.assertIn("信条·铁晶磁轨炮", reply.text)
        self.assertIn("辐射 38.4%", reply.text)
        self.assertIn("40 堕落全息密钥", reply.text)

    def test_eleanor_detail(self):
        reply = self.router(FakeRotation()).handle("#轮换 埃莉诺", self.ctx)[0]
        self.assertIn("B 批", reply.text)
        self.assertIn("终幕·脓痘", reply.text)
        self.assertIn("毒素 54.3%", reply.text)

    def test_unknown_shop_hint(self):
        reply = self.router(FakeRotation()).handle("#轮换 奸商", self.ctx)[0]
        self.assertIn("怎么查", reply.text)

    def test_partial_data(self):
        reply = self.router(FakeRotation(ready=False)).handle("#轮换 格拉斯",
                                                             self.ctx)[0]
        self.assertIn("未上报", reply.text)
        self.assertIn("还没抓到", reply.text)

    def test_disabled_without_rotation(self):
        reply = self.router(None).handle("#轮换", self.ctx)[0]
        self.assertIn("未启用", reply.text)


class RivenImageOnlyTest(unittest.TestCase):
    def test_image_only_parses_four_stats(self):
        router, _, _ = make_router(riven=FakeImageRiven())
        # 3+1 的合法数值：毒素/暴击 75.9~92.8，多重 101~123.4，对派系 -37.1~-30.4
        text = """视使之触 Sati-acritox
+85.0% 毒素伤害
+90.0% 暴击伤害
+110.0% 多重射击
x0.66 对 Grineer 的伤害"""
        ctx = Context(user_id="1", group_id="2", is_group=True, image=b"fake")
        result = rivenocr.ReadResult(
            candidates=rivenocr.read_texts([text]), texts=[text])
        with mock.patch("bot.rivenocr.read", return_value=result):
            reply = router.handle("#紫卡分析", ctx)[0]
        self.assertIn("3+1", reply.text)
        self.assertIn("对派系伤害", reply.text)
        # 派系（歧视）词条在卡面上是倍率写法，`x0.66` 就是 -34%（负面）
        self.assertIn("x0.66", reply.text)

    def test_nonsense_values_are_flagged(self):
        """数值和这把武器的任何配置都对不上时，照实列出并提示核对原图。"""
        router, _, _ = make_router(riven=FakeImageRiven())
        text = "视使之触\n+95.8% 毒素伤害\n+100.3% 暴击伤害\n+137.1% 多重射击"
        ctx = Context(user_id="1", group_id="2", is_group=True, image=b"fake")
        result = rivenocr.ReadResult(
            candidates=rivenocr.read_texts([text]), texts=[text])
        with mock.patch("bot.rivenocr.read", return_value=result):
            reply = router.handle("#紫卡分析", ctx)[0]
        self.assertIn("核对原图", reply.text)

    def test_image_with_no_stats_asks_for_a_better_shot(self):
        router, _, _ = make_router(riven=FakeImageRiven())
        ctx = Context(user_id="1", group_id="2", is_group=True, image=b"fake")
        result = rivenocr.ReadResult(candidates=[], texts=["视使之触"])
        with mock.patch("bot.rivenocr.read", return_value=result):
            reply = router.handle("#紫卡分析", ctx)[0]
        self.assertIn("没有识别到", reply.text)


if __name__ == "__main__":
    unittest.main()
