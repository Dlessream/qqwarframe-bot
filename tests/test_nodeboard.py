"""节点背景板卡片测试。"""
import unittest

from bot import nodeboard


class NodeboardTest(unittest.TestCase):
    def setUp(self):
        self.entries = [
            {"node": "神后塔", "mission": "捕获", "tier": "中纪遗物 · T3",
             "level": "110-115", "faction": "Orokin", "faction_cn": "奥罗金",
             "remaining": "12:34", "steel": True},
            {"node": "哈迪斯", "mission": "歼灭", "tier": "后纪遗物 · T4",
             "level": "80-85", "faction": "Grineer", "faction_cn": "克隆尼",
             "remaining": "08:12"},
        ]

    def test_emblem_key_mapping(self):
        self.assertEqual(nodeboard.emblem_key("奥罗金"), "orokin")
        self.assertEqual(nodeboard.emblem_key("Corrupted"), "orokin")
        self.assertEqual(nodeboard.emblem_key("Grineer"), "grineer")
        self.assertEqual(nodeboard.emblem_key("C系"), "corpus")
        self.assertEqual(nodeboard.emblem_key("感染者"), "infested")
        self.assertEqual(nodeboard.emblem_key("Sentient"), "sentient")
        self.assertEqual(nodeboard.emblem_key("未知"), "orokin")

    def test_faction_en(self):
        # 英文原样透传，中文映射到游戏英文名
        self.assertEqual(nodeboard.faction_en({"faction": "Orokin"}), "Orokin")
        self.assertEqual(nodeboard.faction_en({"faction": "奥罗金"}), "Orokin")
        self.assertEqual(nodeboard.faction_en({"faction_cn": "克隆尼"}), "Grineer")
        self.assertEqual(nodeboard.faction_en({"faction": "Corpus"}), "Corpus")
        self.assertEqual(nodeboard.faction_en({"faction": "Infested"}), "Infested")
        # 全部本地化派系（含新派系）都要映射回英文
        self.assertEqual(nodeboard.faction_en({"faction": "低语者"}), "Murmur")
        self.assertEqual(nodeboard.faction_en({"faction": "灵煞"}), "Sentient")
        self.assertEqual(nodeboard.faction_en({"faction": "堕落者"}), "Corrupted")
        self.assertEqual(nodeboard.faction_en({}), "")

    def test_emblem_assets_are_transparent_png(self):
        if not nodeboard.PIL_AVAILABLE:
            self.skipTest("no Pillow")
        from PIL import Image
        for key in ("orokin", "grineer", "corpus", "infested", "sentient"):
            img = nodeboard._emblem(key)
            self.assertIsNotNone(img, key)
            self.assertEqual(img.mode, "RGBA")
            self.assertLess(img.getchannel("A").getextrema()[0], 255,
                            "%s should have transparent pixels" % key)

    def test_render_returns_png(self):
        if not nodeboard.PIL_AVAILABLE:
            self.skipTest("no Pillow")
        png = nodeboard.render(self.entries, "fissure")
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_render_empty_returns_none(self):
        self.assertIsNone(nodeboard.render([], "fissure"))

    def test_render_all_entries(self):
        """条目必须全量出图，不能像以前那样只留 4 条。"""
        if not nodeboard.PIL_AVAILABLE:
            self.skipTest("no Pillow")
        import io
        from PIL import Image
        entries = [dict(self.entries[0], node="节点 %02d" % i) for i in range(16)]
        png = nodeboard.render(entries, "fissure", title="普通裂缝")
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.height,
                         nodeboard.HEAD_H + nodeboard.ROW_H * 16 + 12)

    def test_render_caps_boards(self):
        if not nodeboard.PIL_AVAILABLE:
            self.skipTest("no Pillow")
        entries = self.entries * 40
        png = nodeboard.render(entries, "fissure")
        self.assertIsNotNone(png)
        # 只在超过安全上限时才截断，高度按上限封顶
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.height, nodeboard.ROW_H * nodeboard.MAX_BOARDS + 12)


if __name__ == "__main__":
    unittest.main()


class TilesetMappingTest(unittest.TestCase):
    """节点按星球+派系映射到不同 tile set 场景图。"""

    def test_distinct_planets_get_distinct_scenes(self):
        from bot import tilesets
        cases = [
            ("Aphrodite (Venus)", "Corpus", "venus_corpus"),
            ("Casta (Ceres)", "Grineer", "ceres_grineer"),
            ("Galatea (Neptune)", "Corpus", "neptune_corpus"),
            ("Hades (Pluto)", "Corpus", "pluto_corpus"),
            ("Void Node (Void)", "Orokin", "void_orokin"),
            ("Eris Node (Eris)", "Infested", "eris_infested"),
        ]
        keys = set()
        for name, fac, expect in cases:
            key = tilesets.scene_key(name, fac)
            self.assertEqual(key, expect, (name, fac, key))
            keys.add(key)
        self.assertEqual(len(keys), len(cases))

    def test_chinese_planet_name(self):
        from bot import tilesets
        self.assertEqual(tilesets.planet_of("Galatea（海王星）"), "neptune")
        self.assertEqual(tilesets.scene_key("卡斯塔（谷神星）", "Grineer"),
                         "ceres_grineer")

    def test_all_scene_images_exist(self):
        from bot import tilesets
        keys = tilesets.all_keys()
        self.assertGreaterEqual(len(keys), 20)
        for key in keys:
            self.assertIsNotNone(tilesets.scene_image(key), key)

    def test_board_uses_per_node_scene(self):
        from bot import nodeboard
        entries = [
            {"node": "阿佛洛狄忒（金星）", "node_raw": "Aphrodite (Venus)",
             "mission": "捕获", "faction": "Corpus", "remaining": "12:34"},
            {"node": "卡斯塔（谷神星）", "node_raw": "Casta (Ceres)",
             "mission": "防御", "faction": "Grineer", "remaining": "08:12"},
        ]
        png = nodeboard.render(entries, "fissure")
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))
