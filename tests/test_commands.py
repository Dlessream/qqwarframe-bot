import json
import os
import tempfile
import unittest

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

    def lookup(self, query, limit=3):
        self.calls.append(query)
        if query == "空":
            return None, []
        return WikiPage(title="高斯", extract="高斯是一款以速度为主题的战甲。",
                        url="https://warframe.huijiwiki.com/wiki/高斯"), []


def make_router(fail_market=False, alerts=None, riven=None, updater=None,
                arbi=None, worldstate=None):
    market = FakeMarket(build_index())
    market.fail = fail_market
    wiki = FakeWiki()
    config = {
        "behavior": {"prefixes": ["#", "＃"]},
        "warframe_market": {"top_orders": 3, "price_orders": 5, "platform": "pc"},
    }
    return CommandRouter(market, wiki, config, alerts=alerts, riven=riven,
                         updater=updater, arbi=arbi,
                         worldstate=worldstate), market, wiki


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
        self.assertEqual(self.router.parse("#?"), ("help", ""))

    def test_fullwidth_prefix(self):
        self.assertEqual(self.router.parse("＃查 绝路"), ("query", "绝路"))

    def test_bare_prefix_is_help(self):
        self.assertEqual(self.router.parse("#"), ("help", ""))

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


if __name__ == "__main__":
    unittest.main()
