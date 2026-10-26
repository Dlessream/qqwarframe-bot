import unittest

from bot.market import Item, MarketStats, Order, PricePoint, SearchHit, StatSummary
from bot.render import (chunk_text, fmt_int, fmt_price, render_candidates, render_help,
                        render_item, render_not_found, render_price, render_wiki)
from bot.wiki import WikiHit, WikiPage


def sample_item():
    return Item(
        slug="mesa_prime_set",
        name="Mesa Prime 一套",
        name_en="Mesa Prime Set",
        tags=("set", "prime", "warframe"),
        ducats=260,
        mastery_rank=0,
        trading_tax=8000,
        description="用这位致命的执法者投下长长的阴影。",
        set_root=True,
    )


def sample_orders():
    sell = [Order("sell", 60, 12, "adadaAS", "ingame"),
            Order("sell", 63, 11, "Novaky_Nov", "ingame")]
    buy = [Order("buy", 50, 25, "Tayfun0606", "ingame")]
    return sell, buy


def sample_stats():
    return MarketStats(
        h48=StatSummary(volume=412, avg=61.8, median=62, min_price=55, max_price=70),
        d90=StatSummary(volume=13404, avg=71.5, median=70, min_price=55, max_price=89),
    )


class FormatTest(unittest.TestCase):
    def test_int(self):
        self.assertEqual(fmt_int(1234), "1,234")
        self.assertEqual(fmt_int(62.0), "62")
        self.assertEqual(fmt_int(62.5), "62.5")

    def test_price(self):
        self.assertEqual(fmt_price(60), "60")
        self.assertEqual(fmt_price(12345), "1.2万")


class RenderItemTest(unittest.TestCase):
    def test_card_contains_core_fields(self):
        sell, buy = sample_orders()
        text = render_item(sample_item(), sell, buy, sample_stats(), order_limit=2)
        self.assertIn("【Mesa Prime 一套】", text)
        self.assertIn("战甲", text)
        self.assertIn("杜卡德 260", text)

    def test_trend_summary(self):
        sell, buy = sample_orders()
        stats = sample_stats()
        stats.history = [
            PricePoint("2026-10-10", 60, 10, 55, 65),
            PricePoint("2026-10-11", 63, 12, 58, 68),
        ]
        text = render_item(sample_item(), sell, buy, stats, order_limit=2)
        self.assertIn("近 2 天走势", text)
        self.assertIn("变化 +5.0%", text)
        self.assertIn("交易税 8,000", text)
        self.assertIn("Mesa Prime Set", text)
        self.assertIn("warframe.market/zh-hans/items/mesa_prime_set", text)
        self.assertIn("最低在售 60p", text)
        self.assertIn("最高收购 50p", text)
        self.assertIn("60p ×12", text)
        self.assertIn("adadaAS", text)
        self.assertIn("游戏中", text)
        self.assertIn("48小时", text)
        self.assertIn("90天", text)
        self.assertIn("13,404", text)

    def test_no_orders(self):
        text = render_item(sample_item(), [], [], None)
        self.assertIn("暂无在线卖家", text)
        self.assertIn("暂无在线买家", text)
        self.assertNotIn("▍行情", text)

    def test_empty_stats(self):
        text = render_item(sample_item(), [], [], MarketStats())
        self.assertNotIn("48小时", text)

    def test_price_card(self):
        sell, buy = sample_orders()
        text = render_price(sample_item(), sell, buy, sample_stats(), order_limit=1)
        self.assertIn("价格", text)
        self.assertIn("60p", text)
        self.assertIn("48小时", text)
        self.assertNotIn("杜卡德", text)


class RenderWikiTest(unittest.TestCase):
    def test_page(self):
        page = WikiPage(title="高斯", extract="高斯是一款以速度为主题的战甲。",
                        url="https://warframe.huijiwiki.com/wiki/高斯")
        text = render_wiki("高斯", page, [])
        self.assertIn("【高斯】中文维基", text)
        self.assertIn("速度为主题", text)
        self.assertIn("huijiwiki", text)

    def test_page_with_other_hits(self):
        page = WikiPage(title="高斯", extract="简介", url="https://x/高斯")
        hits = [WikiHit("高斯", "", 1), WikiHit("高斯/技能", "", 2)]
        text = render_wiki("高斯", page, hits)
        self.assertIn("其他结果", text)
        self.assertIn("高斯/技能", text)

    def test_missing(self):
        text = render_wiki("zzz", None, [])
        self.assertIn("没有找到", text)
        self.assertIn("wiki.warframe.com", text)

    def test_page_with_chinese_note(self):
        page = WikiPage(title="Volt", extract="Volt is a Warframe.",
                        url="https://wiki.warframe.com/w/Volt")
        text = render_wiki("电男", page, [],
                           note="中文「电男」→ 英文词条「Volt」")
        self.assertIn("中文「电男」→ 英文词条「Volt」", text)
        self.assertIn("Volt is a Warframe.", text)

    def test_missing_with_note(self):
        text = render_wiki("某词", None, [], note="中文「某词」→ 英文词条「X」")
        self.assertIn("没有找到", text)
        self.assertIn("英文词条「X」", text)


class RenderMiscTest(unittest.TestCase):
    def test_candidates(self):
        item = sample_item()
        hits = [SearchHit(800, item), SearchHit(700, item)]
        text = render_candidates("绝路", hits)
        self.assertIn("没有找到唯一匹配", text)
        self.assertIn("Mesa Prime 一套", text)

    def test_not_found(self):
        self.assertIn("zzz", render_not_found("zzz"))

    def test_help(self):
        text = render_help("#")
        for keyword in ("#wm", "#价格", "#战甲", "#武器", "#mod", "#wiki", "#帮助"):
            self.assertIn(keyword, text)

    def test_help_custom_prefix(self):
        self.assertIn("！wm", render_help("！"))


class ChunkTest(unittest.TestCase):
    def test_short_text(self):
        self.assertEqual(chunk_text("abc", 100), ["abc"])

    def test_long_text_preserved(self):
        text = "\n".join("第%d行 内容内容内容" % index for index in range(200))
        chunks = chunk_text(text, 200)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))
        self.assertEqual("\n".join(chunks), text)

    def test_very_long_line(self):
        text = "x" * 5000
        chunks = chunk_text(text, 500)
        self.assertTrue(all(len(chunk) <= 500 for chunk in chunks))
        self.assertEqual("".join(chunks), text)


class CqEscapeTest(unittest.TestCase):
    def test_escape(self):
        from bot.onebot import escape_cq
        self.assertEqual(escape_cq("a[b]&c"), "a&#91;b&#93;&amp;c")

    def test_id_value(self):
        from bot.onebot import _id_value
        self.assertEqual(_id_value("12345"), 12345)
        self.assertEqual(_id_value("abc"), "abc")


if __name__ == "__main__":
    unittest.main()
