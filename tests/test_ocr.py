import unittest

from bot.ocr import parse_rolls


class ParseRollsTest(unittest.TestCase):
    def test_chinese_rolls(self):
        text = """
        +165.2% 伤害
        +90.1% 多重射击
        -30.5% 变焦
        紫卡 Mod
        """
        self.assertEqual(parse_rolls(text), [
            ("伤害", 165.2), ("多重射击", 90.1), ("变焦", -30.5)])

    def test_space_separated(self):
        text = "+ 120.0 % 暴击几率\n- 30.5 % 变焦"
        self.assertEqual(parse_rolls(text), [
            ("暴击几率", 120.0), ("变焦", -30.5)])

    def test_multiplier_faction_negative(self):
        text = """+95.8% 毒素伤害
+100.3% 暴击伤害
+137.1% 多重射击
x0.59 对 Grineer 的伤害"""
        self.assertEqual(parse_rolls(text), [
            ("毒素伤害", 95.8), ("暴击伤害", 100.3),
            ("多重射击", 137.1), ("对派系伤害", -41.0)])

    def test_negative_unicode_sign_without_percent(self):
        text = "暴击几率 89.2%\n变焦 −30.5\n后坐力 - 25.0%"
        self.assertEqual(parse_rolls(text), [
            ("暴击几率", 89.2), ("变焦", -30.5), ("后坐力", -25.0)])

    def test_negative_wins_when_second_pass_has_sign(self):
        text = "变焦 30.5%\n变焦 -30.5%"
        self.assertEqual(parse_rolls(text), [("变焦", -30.5)])

    def test_ignores_unknown(self):
        text = "+50% 不存在词条\n完成 3 次任务"
        self.assertEqual(parse_rolls(text), [])


if __name__ == "__main__":
    unittest.main()
