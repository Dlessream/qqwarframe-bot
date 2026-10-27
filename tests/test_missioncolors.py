"""任务类型配色：仲裁卡片按任务类型上色，配色表可读写。"""

import json
import os
import tempfile
import unittest

from bot import missioncolors, present
from bot.arbi import ArbiNode
from bot.worldstate import Arbitration


def make_node(mission="生存", mission_type="MT_SURVIVAL"):
    return ArbiNode(key="SolNode1", name="E Prime", system="地球",
                    mission=mission, faction="Grineer", min_level=20,
                    max_level=30, mission_type=mission_type)


class ColorTableTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "mission_colors.json")
        missioncolors.configure(self.path)

    def tearDown(self):
        missioncolors.configure("")

    def test_defaults_cover_arbitration_missions(self):
        for name in ("防御", "生存", "拦截", "挖掘", "中断", "叛逃",
                     "镜像防御", "虚空洪流", "虚空覆涌", "虚空决战",
                     "联结生存", "元素转换", "感染者资源回收"):
            self.assertIn(name, missioncolors.DEFAULT_COLORS)

    def test_rgb_and_hex(self):
        self.assertEqual(missioncolors.hex_of("生存"), "#60CB8F")
        self.assertEqual(missioncolors.rgb("生存"), (96, 203, 143))
        self.assertNotEqual(missioncolors.rgb("防御"), missioncolors.rgb("生存"))

    def test_unknown_mission_falls_back(self):
        self.assertEqual(missioncolors.hex_of("不存在的任务"),
                         missioncolors.FALLBACK)

    def test_infested_prefix_matches_bare_name(self):
        self.assertEqual(missioncolors.hex_of("INFESTED 资源回收"),
                         missioncolors.hex_of("资源回收"))

    def test_save_and_reload(self):
        missioncolors.set_color("生存", "#123456")
        self.assertEqual(missioncolors.hex_of("生存"), "#123456")
        with open(self.path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["colors"]["生存"], "#123456")
        # 文件里只存改过的项，默认色仍在
        self.assertNotIn("防御", payload["colors"])
        self.assertEqual(missioncolors.hex_of("防御"), "#62BEDB")

    def test_save_accepts_bare_hex(self):
        missioncolors.set_color("挖掘", "ff8800")
        self.assertEqual(missioncolors.hex_of("挖掘"), "#FF8800")

    def test_invalid_color_rejected(self):
        with self.assertRaises(ValueError):
            missioncolors.set_color("", "#ffffff")
        self.assertEqual(missioncolors.normalize("红"), "")

    def test_clear_color_restores_default(self):
        missioncolors.set_color("生存", "#123456")
        missioncolors.set_color("生存", "")
        self.assertEqual(missioncolors.hex_of("生存"),
                         missioncolors.DEFAULT_COLORS["生存"])

    def test_reset(self):
        missioncolors.set_color("生存", "#123456")
        missioncolors.set_color("防御", "#654321")
        result = missioncolors.reset()
        self.assertTrue(result["ok"])
        self.assertEqual(missioncolors.hex_of("生存"),
                         missioncolors.DEFAULT_COLORS["生存"])
        self.assertEqual(missioncolors.stats()["custom"], 0)

    def test_entries_flags_custom(self):
        missioncolors.set_color("生存", "#123456")
        rows = {row["name"]: row for row in missioncolors.entries()}
        self.assertTrue(rows["生存"]["custom"])
        self.assertEqual(rows["生存"]["color"], "#123456")
        self.assertFalse(rows["防御"]["custom"])

    def test_set_colors_batch(self):
        result = missioncolors.set_colors({"生存": "#111111",
                                           "防御": "#222222",
                                           "拦截": ""})
        self.assertEqual(result["count"], 2)
        self.assertEqual(missioncolors.hex_of("生存"), "#111111")
        self.assertEqual(missioncolors.hex_of("拦截"),
                         missioncolors.DEFAULT_COLORS["拦截"])


class ArbitrationCardTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        missioncolors.configure(os.path.join(self.dir, "mission_colors.json"))

    def tearDown(self):
        missioncolors.configure("")

    def make_reply(self, mission="生存"):
        arbitration = Arbitration(node="E Prime", mission=mission,
                                 enemy="Grineer", end=4102444800.0,
                                 node_key="SolNode1")
        return present.arbitration_reply(
            arbitration, rating="A+", node=make_node(mission),
            rating_source="community"), arbitration

    def test_card_uses_mission_color(self):
        """卡片主色跟任务类型走：颜色改完，下一次出图就是新色。"""
        reply, arbitration = self.make_reply("生存")
        missioncolors.set_color("生存", "#FF0000")
        png = reply.image()
        self.assertGreater(len(png), 1000)
        self.assertIn("类型：生存", reply.text)

    def test_card_renders_without_node(self):
        arbitration = Arbitration(node="Mithra", mission="防御",
                                  enemy="科普斯", end=4102444800.0)
        reply = present.arbitration_reply(arbitration)
        self.assertIn("类型：防御", reply.text)
        self.assertGreater(len(reply.image()), 1000)


if __name__ == "__main__":
    unittest.main()
