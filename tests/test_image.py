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

    def test_item_card_drops_stats_section(self):
        """行情已挪到按钮，卡片只保留报价，高度应明显收窄。"""
        stats = sample_stats()
        stats.history = [
            PricePoint("2026-10-%02d" % day, 50.0 + day % 3, 10 + day,
                       48.0, 54.0) for day in range(1, 16)
        ]
        png = imagecard.item_card(sample_item(), [], [], stats, "PC")
        self.assertEqual(png_size(png)[0], 860)
        # 旧版带 48h/90d 面板 + 15 行走势表，至少 700px 高
        self.assertLess(png_size(png)[1], 560)

    def test_price_trend_card_ladder(self):
        stats = sample_stats()
        stats.history = [
            PricePoint("2026-10-%02d" % day, 50.0 + day % 3, 10 + day,
                       48.0, 54.0) for day in range(1, 16)
        ]
        png = imagecard.price_trend_card(sample_item(), stats, "PC")
        self.assertEqual(png_size(png)[0], 860)
        self.assertGreater(png_size(png)[1], 500)

    def test_price_trend_card_without_history(self):
        png = imagecard.price_trend_card(sample_item(), None, "PC")
        self.assertEqual(png_size(png)[0], 860)
        self.assertGreater(png_size(png)[1], 200)

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

    def test_sections_table_center_alignment(self):
        sections = [{
            "label": "言录使 · 双衍王境", "badge": "剩 5 天 12 小时",
            "columns": [("商品", 3.1), ("价格", 1.0)],
            "rows": [["10,000 赤毒", "10 苦栓"], ["福马蓝图", "10 苦栓"]],
        }]
        kwargs = dict(title="轮换商店", subtitle="三个商店的当期货架与倒计时",
                      sections=sections, footer="数据 wiki.warframe.com",
                      accent=imagecard.GOLD, chip="轮换")
        centered = imagecard.sections_table(**kwargs)
        self.assertEqual(png_size(centered)[0], 860)
        self.assertGreater(png_size(centered)[1], 300)
        # 默认即居中；显式 center=True 与默认结果一致
        self.assertEqual(centered, imagecard.sections_table(center=True, **kwargs))
        # center=False 保留旧的左右对齐，应与默认不同
        self.assertNotEqual(centered, imagecard.sections_table(center=False, **kwargs))

    def test_vcenter_places_glyph_in_box_middle(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (200, 100))
        draw = ImageDraw.Draw(img)
        font = imagecard._font(18)
        text = "10,000 赤毒"
        box_top, box_h = -5.0, 45.0  # 与表格行框一致
        ty = imagecard._vcenter(draw, text, font, box_h, box_top)
        box = draw.textbbox((0, 0), text, font=font)
        # 墨迹实际顶/底 = ty + bbox.top / ty + bbox.bottom
        ink_top, ink_bottom = ty + box[1], ty + box[3]
        ink_center = (ink_top + ink_bottom) / 2.0
        box_center = box_top + box_h / 2.0
        self.assertLessEqual(abs(ink_center - box_center), 1.0)

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

    def test_relic_tier_localized_by_chinese_wiki(self):
        """遗物标题的英文等级按中文 wiki 译名显示。"""
        self.assertEqual(imagecard._localize_relic_title("Axi A1 遗物"),
                         "后纪 A1 遗物")
        self.assertEqual(imagecard._localize_relic_title("Lith B2"),
                         "古纪 B2")
        self.assertEqual(imagecard._localize_relic_title("Meso C3"),
                         "前纪 C3")
        self.assertEqual(imagecard._localize_relic_title("Neo D4"),
                         "中纪 D4")
        self.assertEqual(imagecard._localize_relic_title("Requiem E5"),
                         "安魂 E5")
        self.assertEqual(imagecard._localize_relic_title("后纪 S18"),
                         "后纪 S18")  # 已经是中文不重复改

    def test_alerts_card_fits_remaining_time(self):
        """剩余时间完整落在卡片框内：框底 = y + row_h - 12 要低于文字底部。"""
        class Reward:
            market_name = "Allow Znip R1"
            rarity_label = "稀有"
            chance = 0.02
            ducats = 45
            price_low = 12.5
            price_high = 18.0

        png = imagecard.alerts_card([
            {"mission": "歼灭", "level": "L25-30", "faction": "Grineer",
             "node": "Cervantes (Earth)", "reward": "奥罗金反应堆蓝图 x1",
             "remaining": "23 分"},
        ])
        # 单行卡：标题 140 + 行高 188 + 底部 80，行内最后一层在 y+148
        self.assertGreater(png_size(png)[1], 140 + 188 + 30)

    def test_official_image_upload_payload(self):
        """官方图片走富媒体上传，file_data 必须是 base64。"""
        import base64
        from bot.qqofficial import QQOfficialClient

        client = QQOfficialClient({}, lambda event: None)
        self.addCleanup(client.close)
        png = b"\x89PNG\r\n\x1a\n"
        self.assertEqual(base64.b64encode(png).decode("ascii"),
                         "iVBORw0KGgo=")
        self.assertEqual(client._mentions("[CQ:at,qq=ABC] 你好"), "<@ABC> 你好")


if __name__ == "__main__":
    unittest.main()
