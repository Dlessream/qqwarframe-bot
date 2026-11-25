"""术语本地化：派系 / 任务类型 / 奖励名。"""

import unittest

from bot import terms


class FactionTest(unittest.TestCase):
    def test_english_names(self):
        self.assertEqual(terms.localize_faction("Grineer"), "克隆尼")
        self.assertEqual(terms.localize_faction("Corpus"), "科普斯")
        self.assertEqual(terms.localize_faction("Infested"), "感染者")

    def test_infestation_variant(self):
        # arbi.wf.wiki 的 factionNameZh 给的是 Infestation，不是 Infested
        self.assertEqual(terms.localize_faction("Infestation"), "感染者")

    def test_chinese_passthrough(self):
        self.assertEqual(terms.localize_faction("奥罗金"), "奥罗金")
        self.assertEqual(terms.localize_faction("低语者"), "低语者")

    def test_unknown_keeps_original(self):
        self.assertEqual(terms.localize_faction("Whatever"), "Whatever")


class MissionTest(unittest.TestCase):
    def test_shouting(self):
        self.assertEqual(terms.localize_mission("ASSAULT"), "强袭")
        self.assertEqual(terms.localize_mission("EXTERMINATION"), "歼灭")

    def test_camel_and_spaced(self):
        self.assertEqual(terms.localize_mission("Dual Defense"), "镜像防御")
        self.assertEqual(terms.localize_mission("MirrorDefense"), "镜像防御")

    def test_alchemy(self):
        self.assertEqual(terms.localize_mission("Alchemy"), "元素转换")


class RewardTest(unittest.TestCase):
    def test_event_resource(self):
        self.assertEqual(terms.localize_reward("Road To Tau Event Resource"),
                         "「通往 Tau 之路」活动资源")

    def test_riven_mod(self):
        self.assertEqual(terms.localize_reward("Rifle Riven Mod"), "步枪裂罅模组")


class NodeTest(unittest.TestCase):
    """星图节点名：官方客户端汉化过的要跟随官方，没汉化的保留英文。"""

    def test_official_chinese_node(self):
        # 官方简中客户端里的 Apollodorus 是「阿波罗多罗斯撞击坑」
        self.assertEqual(terms.localize_node("Apollodorus (Mercury)"),
                         "阿波罗多罗斯撞击坑（水星）")

    def test_untranslated_node_keeps_english(self):
        # Tessera / Venera 这些官方中文客户端也显示英文原名，不臆造译名
        self.assertEqual(terms.localize_node("Tessera (Venus)"), "Tessera（金星）")
        self.assertEqual(terms.localize_node("Nabuk (Kuva Fortress)"),
                         "Nabuk（赤毒要塞）")

    def test_railjack_node_uses_proxima_region(self):
        self.assertEqual(terms.localize_node("Korm's Belt (Earth)"),
                         "克姆地带（地球比邻星域）")
        self.assertEqual(terms.localize_node("Numina (Veil)"),
                         "努秘（面纱比邻星域）")

    def test_storm_node_format(self):
        self.assertEqual(terms.localize_storm_node("Bifrost Echo (Venus)"),
                         "金星比邻星域 - 虹桥回声")
        self.assertEqual(terms.localize_storm_node("Peregrine Axis (Pluto)"),
                         "冥王星比邻星域 - 外域星轴")

    def test_unknown_node_falls_back(self):
        self.assertEqual(terms.localize_node("Whatever (Mars)"), "Whatever（火星）")
        self.assertEqual(terms.localize_node(""), "")

    def test_every_storm_node_translated(self):
        """航道星舰节点表不许残留英文，也不许有漏译。"""
        for english, chinese in terms.STORM_NODES.items():
            self.assertTrue(chinese.strip(), english)
            self.assertNotEqual(chinese, english, "%s 没翻译" % english)
            self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in chinese),
                            "%s 没有中文：%s" % (english, chinese))

    def test_official_table_loaded(self):
        names = terms._node_names_zh()
        self.assertGreater(len(names), 50)
        self.assertEqual(names.get("Apollodorus"), "阿波罗多罗斯撞击坑")


if __name__ == "__main__":
    unittest.main()
