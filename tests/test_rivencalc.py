import io
import unittest

from PIL import Image

from bot import imagecard
from bot.riven import RivenWeapon
from bot.rivencalc import (canonical_name, calculate, evaluate_roll, find_stat,
                           infer_config_label, looks_like_stat)


def weapon(slug="rubico", name="绝路", name_en="Rubico", riven_type="rifle",
           group="primary", disposition=0.95):
    return RivenWeapon(slug=slug, name=name, name_en=name_en,
                       riven_type=riven_type, group=group,
                       disposition=disposition, mastery_rank=12)


class CalculateTest(unittest.TestCase):
    def setUp(self):
        self.analysis = calculate(weapon())

    def test_disposition(self):
        self.assertAlmostEqual(self.analysis.disposition, 0.95)
        self.assertEqual(self.analysis.dots, "●●●○○")
        self.assertEqual(self.analysis.category, "rifle")

    def test_multishot_ranges(self):
        for group, expected in (
                (self.analysis.two, "76.2 ~ 93.1%"),
                (self.analysis.two_neg, "95.2 ~ 116.4%"),
                (self.analysis.three, "57.7 ~ 70.5%"),
                (self.analysis.three_neg, "72.1 ~ 88.2%")):
            stat = find_stat(group, "多重射击")
            self.assertIsNotNone(stat)
            self.assertEqual(stat.range_text(), expected)

    def test_damage_ranges(self):
        self.assertEqual(find_stat(self.analysis.two, "伤害").range_text(),
                         "139.7 ~ 170.7%")
        self.assertEqual(find_stat(self.analysis.three, "伤害").range_text(),
                         "105.8 ~ 129.3%")

    def test_negative_ranges(self):
        self.assertEqual(
            find_stat(self.analysis.negatives_two, "多重射击").range_text(),
            "-46.6 ~ -38.1%")
        self.assertEqual(
            find_stat(self.analysis.negatives_three, "多重射击").range_text(),
            "-70.5 ~ -57.7%")

    def test_fusion_stats(self):
        analysis = calculate(weapon(disposition=1.2))
        for name in ("异常状态伤害", "毒气伤害", "腐蚀伤害", "病毒伤害",
                     "辐射伤害", "爆炸伤害", "磁力伤害"):
            stat = find_stat(analysis.three_neg, name)
            self.assertIsNotNone(stat, name)
            self.assertEqual(stat.range_text(), "91.1 ~ 111.4%", name)
        negatives = {stat.name for stat in analysis.negatives_three}
        for name in ("毒气伤害", "腐蚀伤害", "病毒伤害", "辐射伤害",
                     "爆炸伤害", "磁力伤害"):
            self.assertNotIn(name, negatives)

    def test_fusion_melee(self):
        analysis = calculate(weapon("nikana_prime", "侍刃 Prime", "Nikana Prime",
                                    "melee", "melee", 1.2))
        self.assertIsNone(find_stat(analysis.three_neg, "毒气伤害"))
        self.assertEqual(
            find_stat(analysis.three_neg, "异常状态伤害").range_text(),
            "91.1 ~ 111.4%")

    def test_positive_only_not_in_negatives(self):
        names = {stat.name for stat in self.analysis.negatives_two}
        for name in ("火焰伤害", "冰冻伤害", "电击伤害", "毒素伤害", "穿透"):
            self.assertNotIn(name, names)

    def test_flat_stat(self):
        self.assertEqual(find_stat(self.analysis.two, "穿透").range_text(),
                         "2.3 ~ 2.8")

    def test_ammo_efficiency(self):
        analysis = calculate(weapon(disposition=1.0))
        self.assertEqual(find_stat(analysis.two, "弹药效率").range_text(),
                         "40.1 ~ 49%")
        self.assertEqual(find_stat(analysis.three_neg, "弹药效率").range_text(),
                         "38 ~ 46.4%")
        self.assertEqual(
            find_stat(analysis.negatives_three, "弹药效率").range_text(),
            "-37.1 ~ -30.4%")
        self.assertEqual(canonical_name("弹效"), "弹药效率")
        self.assertEqual(canonical_name("+45% 弹药效率"), "弹药效率")

    def test_recoil_inverted(self):
        positive = find_stat(self.analysis.two, "后坐力")
        negative = find_stat(self.analysis.negatives_two, "后坐力")
        self.assertEqual(positive.range_text(), "-93.1 ~ -76.2%")
        self.assertEqual(negative.range_text(), "38.1 ~ 46.6%")

    def test_melee(self):
        analysis = calculate(weapon("nikana_prime", "侍刃 Prime", "Nikana Prime",
                                    "melee", "melee", 0.95))
        self.assertEqual(analysis.category, "melee")
        names = {stat.name for stat in analysis.two}
        self.assertIn("攻击速度", names)
        self.assertIn("重击效率", names)
        self.assertNotIn("多重射击", names)

    def test_kitgun_and_archgun(self):
        self.assertEqual(calculate(weapon("gaze", "凝目", "Gaze", "kitgun",
                                          "kitgun")).category, "pistol")
        self.assertEqual(calculate(weapon("larkspur", "拉克斯普", "Larkspur",
                                          "rifle", "archgun")).category,
                         "archgun")

    def test_dots(self):
        self.assertEqual(calculate(weapon(disposition=1.4)).dots, "●●●●●")
        self.assertEqual(calculate(weapon(disposition=1.2)).dots, "●●●●○")
        self.assertEqual(calculate(weapon(disposition=0.8)).dots, "●●○○○")
        self.assertEqual(calculate(weapon(disposition=0.5)).dots, "●○○○○")


class AliasTest(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(canonical_name("暴击"), "暴击几率")
        self.assertEqual(canonical_name("暴率"), "暴击几率")
        self.assertEqual(canonical_name("触发时间"), "状态持续时间")
        self.assertEqual(canonical_name("状态"), "触发几率")
        self.assertEqual(canonical_name("后坐力"), "后坐力")
        self.assertEqual(canonical_name("范围"), "攻击范围")
        self.assertEqual(canonical_name("多重"), "多重射击")
        self.assertEqual(canonical_name("毒气"), "毒气伤害")
        self.assertEqual(canonical_name("腐蚀"), "腐蚀伤害")
        self.assertEqual(canonical_name("异况"), "异常状态伤害")

    def test_looks_like_stat(self):
        self.assertTrue(looks_like_stat("伤害"))
        self.assertFalse(looks_like_stat("绝路"))


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.analysis = calculate(weapon())

    def test_three_plus_negative(self):
        results = evaluate_roll(self.analysis, [
            ("伤害", 140), ("多重", 80), ("暴击", 130), ("变焦", -30)])
        self.assertEqual(results[0].stat.range_text(), "132.3 ~ 161.6%")
        self.assertEqual(results[3].grade, "轻微")
        self.assertTrue(results[3].negative)
        self.assertEqual(infer_config_label(results), "3+1")

    def test_two_positive(self):
        results = evaluate_roll(self.analysis, [("伤害", 160), ("多重", 90)])
        self.assertEqual(results[0].stat.range_text(), "139.7 ~ 170.7%")
        self.assertEqual(infer_config_label(results), "2+")

    def test_recoil_negative_is_positive(self):
        results = evaluate_roll(self.analysis, [
            ("伤害", 140), ("多重", 80), ("后坐力", -65)])
        recoil = results[-1]
        self.assertFalse(recoil.negative)
        self.assertEqual(recoil.stat.range_text(), "-70.5 ~ -57.7%")

    def test_recoil_positive_is_negative(self):
        results = evaluate_roll(self.analysis, [
            ("伤害", 140), ("多重", 80), ("后坐力", 50)])
        recoil = results[-1]
        self.assertTrue(recoil.negative)
        self.assertEqual(recoil.grade, "负面过大")

    def test_unknown(self):
        results = evaluate_roll(self.analysis, [("攻速", 30)])
        self.assertEqual(results[0].grade, "未识别")


class ImageTest(unittest.TestCase):
    def test_card(self):
        analysis = calculate(weapon())
        png = imagecard.riven_range_card(analysis)
        self.assertTrue(png.startswith(b"\x89PNG"))
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size[0], 860)
        self.assertGreaterEqual(image.size[1], 760)


if __name__ == "__main__":
    unittest.main()
