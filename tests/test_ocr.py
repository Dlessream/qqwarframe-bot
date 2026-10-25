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

    def test_ignores_unknown(self):
        text = "+50% 不存在词条\n完成 3 次任务"
        self.assertEqual(parse_rolls(text), [])


if __name__ == "__main__":
    unittest.main()
