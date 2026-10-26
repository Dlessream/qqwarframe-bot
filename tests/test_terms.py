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


if __name__ == "__main__":
    unittest.main()
