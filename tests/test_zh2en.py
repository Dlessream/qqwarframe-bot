import unittest

from bot import zh2en


class FakeHit:
    def __init__(self, name, name_en):
        self.item = type("Item", (), {"name": name, "name_en": name_en})()


def lookup(*pairs):
    table = [FakeHit(zh, en) for zh, en in pairs]

    def _lookup(query, limit=3):
        return table

    return _lookup


class HasCjkTest(unittest.TestCase):
    def test_chinese(self):
        self.assertTrue(zh2en.has_cjk("高斯"))

    def test_mixed(self):
        self.assertTrue(zh2en.has_cjk("Volt 电男"))

    def test_english(self):
        self.assertFalse(zh2en.has_cjk("Volt Prime"))


class TermTableTest(unittest.TestCase):
    def test_planet(self):
        self.assertEqual(zh2en.term_to_english("地球"), "Earth")
        self.assertEqual(zh2en.term_to_english("地球 "), "Earth")

    def test_kuva_fortress(self):
        self.assertEqual(zh2en.term_to_english("赤毒要塞"), "Kuva Fortress")

    def test_faction(self):
        self.assertEqual(zh2en.term_to_english("克隆尼"), "Grineer")

    def test_mission(self):
        self.assertEqual(zh2en.term_to_english("生存"), "Survival")

    def test_boss_with_dot(self):
        # “莱希·克里尔”去掉间隔号也能命中
        self.assertEqual(zh2en.term_to_english("莱希克里尔"), "Lech Kril")

    def test_unknown(self):
        self.assertEqual(zh2en.term_to_english("完全不存在的词"), "")


class EnglishTitlesTest(unittest.TestCase):
    def test_term_first(self):
        titles = zh2en.english_titles("赤毒要塞")
        self.assertEqual(titles, ["Kuva Fortress"])

    def test_item_lookup(self):
        titles = zh2en.english_titles(
            "分裂膛室", item_lookup=lookup(("分裂膛室", "Split Chamber")))
        self.assertEqual(titles, ["Split Chamber"])

    def test_set_suffix_stripped(self):
        titles = zh2en.english_titles(
            "电男", item_lookup=lookup(("伏特 Prime 一套", "Volt Prime Set")))
        self.assertEqual(titles, ["Volt Set", "Volt",
                                  "Volt Prime Set", "Volt Prime"])

    def test_prime_query_keeps_prime_first(self):
        titles = zh2en.english_titles(
            "电男p", item_lookup=lookup(("伏特 Prime 一套", "Volt Prime Set")))
        self.assertEqual(titles[:2], ["Volt Prime Set", "Volt Prime"])

    def test_component_suffix_stripped(self):
        titles = zh2en.english_titles(
            "电男头", item_lookup=lookup(
                ("伏特 Prime 头部神经光元蓝图", "Volt Prime Neuroptics Blueprint")))
        self.assertIn("Volt Prime Neuroptics Blueprint", titles)
        self.assertIn("Volt Neuroptics", titles)
        self.assertIn("Volt", titles)

    def test_lookup_failure_is_ignored(self):
        def boom(query, limit=3):
            raise RuntimeError("索引挂了")

        self.assertEqual(zh2en.english_titles("地球", item_lookup=boom), ["Earth"])

    def test_english_query_untouched(self):
        self.assertEqual(zh2en.english_titles("Volt Prime"), [])

    def test_empty(self):
        self.assertEqual(zh2en.english_titles("  "), [])


class TitleCompatibleTest(unittest.TestCase):
    def test_exact(self):
        self.assertTrue(zh2en.title_compatible("Split Chamber", "Split Chamber"))

    def test_prefix_article(self):
        self.assertTrue(zh2en.title_compatible("Steel Path", "The Steel Path"))

    def test_unrelated_rejected(self):
        self.assertFalse(zh2en.title_compatible("Volt Set", "Volt Animation Set"))

    def test_short_base_rejected_against_set(self):
        self.assertFalse(zh2en.title_compatible("Gauss Set", "Gauss Prime Set"))

    def test_empty(self):
        self.assertFalse(zh2en.title_compatible("", "Volt"))


if __name__ == "__main__":
    unittest.main()
