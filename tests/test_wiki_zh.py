import unittest

from bot import names, terms, wiki_zh


class WikiZhTest(unittest.TestCase):
    def test_weapon(self):
        self.assertEqual(wiki_zh.lookup("Rubico"), "绝路")
        self.assertEqual(wiki_zh.lookup("magistar"), "执法者")

    def test_prime_fallback(self):
        """表里没单列的 Prime：国服写法是「圣装 + 基础名」。"""
        self.assertEqual(wiki_zh.lookup("Rubico Prime"), "圣装绝路")
        self.assertEqual(wiki_zh.lookup("Cobra & Crane Prime"), "圣装眼镜蛇与鹤")

    def test_blueprint_suffix(self):
        self.assertEqual(wiki_zh.localize("Rubico Prime Blueprint"), "圣装绝路蓝图")
        self.assertEqual(wiki_zh.localize("Magistar Blueprint"), "执法者蓝图")

    def test_mods_and_resources(self):
        self.assertEqual(wiki_zh.lookup("Primed Continuity"), "高阶持久力")
        self.assertEqual(wiki_zh.lookup("Nyth"), "夜息石")
        self.assertEqual(wiki_zh.lookup("Catchmoon"), "射月")

    def test_unknown_kept_as_is(self):
        self.assertIsNone(wiki_zh.lookup("Not A Real Item"))
        self.assertEqual(wiki_zh.localize("Not A Real Item"), "Not A Real Item")

    def test_names_prefers_wiki(self):
        self.assertEqual(names.chinese("Rubico"), "绝路")
        self.assertEqual(names.chinese("Primed Continuity"), "高阶持久力")
        # 战甲仍走本地对照表，不会被 wiki 的武器表抢走
        self.assertEqual(names.chinese("Atlas Prime"), "圣装撼天刑者")

    def test_terms_reward(self):
        self.assertEqual(terms.localize_reward("Rubico Prime Blueprint"), "圣装绝路蓝图")

    def test_table_not_empty(self):
        counts = wiki_zh.categories()
        self.assertGreater(counts.get("weapons", 0), 400)
        self.assertGreater(counts.get("mods", 0), 300)


if __name__ == "__main__":
    unittest.main()
