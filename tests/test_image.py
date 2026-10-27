import struct
import unittest

from bot import imagecard
from bot.market import Item, MarketStats, Order, PricePoint, StatSummary


def png_size(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def sample_item(name="Mesa Prime 一套"):
    return Item(
        slug="mesa_prime_set",
        name=name,
        name_en="Mesa Prime Set",
        tags=("set", "prime", "warframe"),
        ducats=260,
        trading_tax=8000,
        description="用这位致命的执法者投下长长的阴影。",
        set_root=True,
    )


def sample_stats():
    return MarketStats(
        h48=StatSummary(412, 61.8, 62, 55, 70),
        d90=StatSummary(13404, 71.5, 70, 55, 89),
    )


class ImageCardTest(unittest.TestCase):
    @unittest.skipUnless(imagecard.available(), "需要 Pillow 与中文字体")
    def test_available(self):
        self.assertTrue(imagecard.available())

    def test_item_card(self):
        png = imagecard.item_card(
            sample_item(),
            [Order("sell", 60, 12, "adadaAS", "ingame"),
             Order("sell", 63, 11, "Novaky_Nov", "ingame")],
            [Order("buy", 50, 25, "Tayfun0606", "ingame")],
            sample_stats(), "PC")
        self.assertEqual(png_size(png)[0], 860)
        self.assertGreater(len(png), 8000)

    def test_item_card_empty_orders(self):
        png = imagecard.item_card(sample_item(), [], [], None, "PC")
        self.assertGreater(len(png), 5000)

    def test_item_card_with_trend(self):
        stats = sample_stats()
        stats.history = [
            PricePoint("2026-10-%02d" % day, 50.0 + day % 3, 10 + day,
                       48.0, 54.0) for day in range(1, 16)
        ]
        png = imagecard.item_card(sample_item(), [], [], stats, "PC")
        self.assertEqual(png_size(png)[0], 860)
        self.assertGreater(png_size(png)[1], 700)

    def test_long_name_wraps(self):
        png = imagecard.item_card(
            sample_item("非常非常长的战甲名字 " * 6), [], [], None, "Switch")
        self.assertGreater(png_size(png)[1], 200)

    def test_table_card(self):
        png = imagecard.build_table(
            "拆件比价", "套装 60p ｜ 拆件 85p",
            ["部件", "最低在售", "最高收购"],
            [["蓝图", "12p", "9p"], ["合计", "12p", "9p"]],
            "warframe.market", chip="拆件", note="结论：买套装。",
            highlights=[[None, imagecard.GOLD, imagecard.CYAN]] * 2,
            bold_last_row=True, ratios=[2.4, 1, 1],
            aligns=["left", "right", "right"])
        self.assertEqual(png_size(png)[0], 860)

    def test_tall_table(self):
        rows = [["物品 %02d" % index, "%d" % (index * 10), "100,000"]
                for index in range(40)]
        png = imagecard.build_table(
            "奸商库存", "共 40 件", ["物品", "杜卡德", "星币"], rows,
            "warframestat.us", chip="奸商")
        width, height = png_size(png)
        self.assertEqual(width, 860)
        self.assertGreater(height, 1500)

    def test_schedule_table(self):
        days = []
        for day in range(7):
            rows = [[imagecard.Cell("%02d:00" % hour), "Callisto（木星）",
                     imagecard.Cell("拦截", color=imagecard.PURPLE, bold=True),
                     imagecard.Cell("A+", color=imagecard.GREEN, bold=True)]
                    for hour in range(24)]
            days.append({"label": "10-%02d 周三" % (day + 28), "badge": "24 场 · S×2",
                         "rows": rows})
        png = imagecard.schedule_table(
            "仲裁表", "未来 7 天共 168 场", days, "arbi.wf.wiki", chip="7 天")
        width, height = png_size(png)
        self.assertEqual(width, 860)
        # 两列排布：每天 12 行，七天不会堆成一张超长图
        self.assertLess(height, 4200)
        self.assertGreater(height, 2500)

    def test_wiki_card(self):
        png = imagecard.wiki_card(
            "高斯", "高斯是一款以速度为主题的战甲。" * 30,
            "https://warframe.huijiwiki.com/wiki/高斯")
        width, height = png_size(png)
        self.assertEqual(width, 860)
        self.assertGreater(height, 300)

    def test_image_cq(self):
        from bot.onebot import image_cq
        cq = image_cq(b"\x89PNG\r\n\x1a\n")
        self.assertTrue(cq.startswith("[CQ:image,file=base64://"))
        self.assertTrue(cq.endswith("]"))


if __name__ == "__main__":
    unittest.main()
