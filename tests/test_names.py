"""英文名 → 国服中文名对照测试。"""

import unittest

from bot import names


class NamesTest(unittest.TestCase):
    def test_warframe(self):
        self.assertEqual(names.warframe("Mesa"), "圣枪弥撒")
        self.assertEqual(names.warframe("Chroma"), "怒炎龙骑")
        self.assertEqual(names.warframe("Atlas"), "撼天刑者")
        self.assertEqual(names.kind("Kullervo"), "战甲")

    def test_weapon(self):
        self.assertEqual(names.weapon("Vectis"), "守望者")
        self.assertEqual(names.weapon("Obex"), "奥比克斯")
        self.assertEqual(names.kind("Obex"), "武器")

    def test_case_and_space_insensitive(self):
        self.assertEqual(names.weapon("  vectis "), "守望者")
        self.assertEqual(names.warframe("excalibur  umbra"), "暗影圣剑")

    def test_prime_suffix(self):
        # 战甲表里直接有 Prime；武器表只有基础名，按「圣装 + 基础名」拼
        self.assertEqual(names.warframe("Mesa Prime"), "圣装圣枪弥撒")
        self.assertEqual(names.weapon("Vectis Prime"), "圣装守望者")

    def test_set_suffix(self):
        self.assertEqual(names.warframe("Mesa Prime Set"), "圣装圣枪弥撒")
        self.assertEqual(names.weapon("Vectis Set"), "守望者")

    def test_unknown_kept_as_is(self):
        self.assertIsNone(names.warframe("Tennocon 2026 Frame"))
        self.assertIsNone(names.weapon("Tennocon 2026 Frame"))
        self.assertEqual(names.kind("Tennocon 2026 Frame"), "")
        self.assertEqual(names.chinese("Tennocon 2026 Frame"), "Tennocon 2026 Frame")
        self.assertEqual(names.label("Tennocon 2026 Frame"), "Tennocon 2026 Frame")

    def test_label(self):
        self.assertEqual(names.label("Mesa"), "圣枪弥撒（Mesa）")
        self.assertEqual(names.label("Mesa", with_english=False), "圣枪弥撒")
        self.assertEqual(names.label(""), "—")

    def test_alias(self):
        # 玩家俗称：回廊战甲那一栏显示「别名（English）」
        self.assertEqual(names.alias("Mesa"), "女枪")
        self.assertEqual(names.alias("  chroma "), "龙骑")
        self.assertEqual(names.alias("Atlas"), "土甲")
        self.assertEqual(names.alias("Excalibur Umbra"), "黑咖喱")
        self.assertIsNone(names.alias("Vectis"))          # 武器没有别名
        self.assertIsNone(names.alias("Tennocon 2026 Frame"))

    def test_alias_prime(self):
        # 圣装沿用基础名 + P（国服习惯：牛P / 女枪P）
        self.assertEqual(names.alias("Mesa Prime"), "女枪P")
        self.assertEqual(names.alias("Rhino Prime Set"), "犀牛P")

    def test_alias_label(self):
        self.assertEqual(names.alias_label("Mesa"), "女枪（Mesa）")
        self.assertEqual(names.alias_label("Vectis"), "守望者（Vectis）")  # 无别名退回译名
        self.assertEqual(names.alias_label("Caliban"), "卡利班（Caliban）")
        self.assertEqual(names.alias_label("Tennocon 2026 Frame"),
                         "Tennocon 2026 Frame")
        self.assertEqual(names.alias_label("Mesa", with_english=False), "女枪")
        self.assertEqual(names.alias_label(""), "—")


if __name__ == "__main__":
    unittest.main()
