"""帮助菜单动态背景与分组卡片测试。"""

import os
import tempfile
import unittest

from bot import helpbg, imagecard, present

try:
    from PIL import Image
    PIL_OK = True
except Exception:  # pragma: no cover
    PIL_OK = False


class UrlFilterTest(unittest.TestCase):
    def test_only_official_https_images_kept(self):
        news = [
            {"imageLink": "https://cdn.warframestat.us/genesis/img/news-placeholder.png"},
            {"imageLink": "https://www-static.warframe.com/uploads/a_1600x900.png"},
            {"imageLink": "http://evil.example.com/fake.png"},
            {"imageLink": "https://www-static.warframe.com/uploads/a_1600x900.png"},
            {"imageLink": "https://content.warframe.com/uploads/b.jpg"},
        ]
        urls = helpbg.official_image_urls(news)
        self.assertEqual(urls, [
            "https://www-static.warframe.com/uploads/a_1600x900.png",
            "https://content.warframe.com/uploads/b.jpg",
        ])

    def test_placeholder_keyword_filtered(self):
        news = [{"imageLink": "https://www-static.warframe.com/x/default.png"}]
        self.assertEqual(helpbg.official_image_urls(news), [])

    def test_key_stable_and_changes_with_news(self):
        news1 = [{"imageLink": "https://www-static.warframe.com/uploads/a.png"}]
        news2 = [{"imageLink": "https://www-static.warframe.com/uploads/b.png"}]
        k1 = helpbg.background_key(news1)
        self.assertEqual(k1, helpbg.background_key([dict(news1[0])]))
        self.assertNotEqual(k1, helpbg.background_key(news2))
        self.assertIsNone(helpbg.background_key([]))


class PrepareTest(unittest.TestCase):
    @unittest.skipUnless(PIL_OK, "Pillow 未安装")
    def test_prepare_scales_and_rejects_small(self):
        import io
        big = Image.new("RGB", (1920, 1080), (30, 60, 90))
        buf = io.BytesIO()
        big.save(buf, format="PNG")
        out = helpbg.HelpBackground._prepare(buf.getvalue())
        self.assertIsNotNone(out)
        self.assertLessEqual(out.width, helpbg.TARGET_W)
        self.assertLessEqual(out.height, helpbg.TARGET_H)

        small = Image.new("RGB", (100, 60), (0, 0, 0))
        buf2 = io.BytesIO()
        small.save(buf2, format="PNG")
        self.assertIsNone(helpbg.HelpBackground._prepare(buf2.getvalue()))


class HelpCardTest(unittest.TestCase):
    @unittest.skipUnless(PIL_OK, "Pillow 未安装")
    def test_card_groups_and_commands(self):
        from PIL import Image
        import io
        png = imagecard.help_card("#")
        self.assertGreater(len(png), 1000)
        image = Image.open(io.BytesIO(png))
        # 帮助卡为横板，尺寸固定
        self.assertEqual(image.width, imagecard.HELP_W)
        self.assertEqual(image.height, imagecard.HELP_H)
        self.assertGreater(image.width, image.height)
        groups = imagecard._help_groups("#")
        self.assertEqual([g[0] for g in groups],
                         ["市场行情", "紫卡与遗物", "世界状态", "仲裁排期", "订阅与工具"])
        all_cmds = [cmd for _, _, rows in groups for cmd, _ in rows]
        self.assertGreater(len(all_cmds), 40)
        self.assertTrue(any("帮助" in c for c in all_cmds))

    @unittest.skipUnless(PIL_OK, "Pillow 未安装")
    def test_card_with_background(self):
        bg = Image.new("RGB", (1920, 1080), (40, 80, 120))
        png = imagecard.help_card("#", background=bg)
        self.assertGreater(len(png), 1000)

    @unittest.skipUnless(PIL_OK, "Pillow 未安装")
    def test_disabled_commands_hidden(self):
        full = imagecard.help_card("#")
        hidden = imagecard.help_card("#", disabled=("wiki", "relic"))
        # 横板尺寸固定，禁用命令后画面内容应发生变化
        self.assertNotEqual(full, hidden)
        from PIL import Image
        import io
        self.assertEqual(Image.open(io.BytesIO(hidden)).height,
                         imagecard.HELP_H)

    def test_help_reply_provider_and_fallback(self):
        calls = []
        def provider():
            calls.append(1)
            return None
        reply = present.help_reply("#", bg_provider=provider)
        self.assertGreater(len(reply.image()), 500)
        self.assertEqual(len(calls), 1)

        def broken():
            raise RuntimeError("download failed")
        reply2 = present.help_reply("#", bg_provider=broken)
        self.assertGreater(len(reply2.image()), 500)


if __name__ == "__main__":
    unittest.main()
