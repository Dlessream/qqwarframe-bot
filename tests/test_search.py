import json
import os
import unittest

from bot.market import Item, ItemIndex, MarketClient

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = load_fixture("items.json")
        client = MarketClient({
            "base_url": "http://example.invalid",
            "language": "zh-hans",
            "platform": "pc",
            "crossplay": True,
            "cache_file": "",
            "alias_file": os.path.join(BASE, "data", "aliases.json"),
        })
        items = [client._parse_short(row) for row in raw["data"]]
        aliases = client._load_aliases()
        # 测试夹具没有 Rhino Prime 总图蓝图，用套装保留别名匹配用例。
        aliases["牛甲"] = "rhino_prime_set"
        cls.index = ItemIndex([item for item in items if item], aliases)

    def test_item_count(self):
        self.assertEqual(len(self.index), 12)

    def test_parse_fields(self):
        item = self.index.get("mesa_prime_set")
        self.assertIsNotNone(item)
        self.assertEqual(item.name, "Mesa Prime 一套")
        self.assertEqual(item.name_en, "Mesa Prime Set")
        self.assertEqual(item.ducats, 260)
        self.assertEqual(item.trading_tax, 8000)
        self.assertEqual(item.kind, "warframe")
        self.assertIn("战甲", item.kind_label)
        self.assertIn("套装", item.kind_label)
        self.assertTrue(item.url.endswith("/items/mesa_prime_set"))

    def test_exact_chinese(self):
        hits = self.index.search("分裂膛室")
        self.assertEqual(hits[0].item.slug, "split_chamber")
        self.assertEqual(hits[0].item.kind, "mod")

    def test_chinese_prefix_finds_set_first(self):
        hits = self.index.search("绝路")
        self.assertEqual(hits[0].item.slug, "rubico_prime_set")
        self.assertGreater(hits[0].score, hits[1].score)

    def test_prime_suffix_shorthand(self):
        hits = self.index.search("绝路p")
        self.assertEqual(hits[0].item.slug, "rubico_prime_set")

    def test_english_query(self):
        hits = self.index.search("MESA")
        self.assertEqual(hits[0].item.slug, "mesa_prime_set")

    def test_alias(self):
        hits = self.index.search("牛甲")
        self.assertEqual(hits[0].item.slug, "rhino_prime_set")
        self.assertGreater(hits[0].score, 1500)

    def test_alias_with_spaces(self):
        hits = self.index.search(" 牛 甲 ")
        self.assertEqual(hits[0].item.slug, "rhino_prime_set")

    def test_category_warframe_excludes_mods(self):
        hits = self.index.search("prime", category="warframe", limit=10)
        self.assertTrue(hits)
        for hit in hits:
            self.assertIn("warframe", hit.item.tag_set)
            self.assertNotIn("mod", hit.item.tag_set)
            self.assertNotIn("component", hit.item.tag_set)

    def test_category_weapon(self):
        hits = self.index.search("绝路", category="weapon", limit=10)
        self.assertTrue(hits)
        for hit in hits:
            self.assertNotIn("mod", hit.item.tag_set)

    def test_category_mismatch_returns_empty(self):
        self.assertEqual(self.index.search("mesa", category="weapon"), [])

    def test_blueprint_query(self):
        hits = self.index.search("蓝")
        self.assertTrue(hits)
        self.assertIn("blueprint", hits[0].item.tag_set)

    def test_no_result(self):
        self.assertEqual(self.index.search("根本不存在的物品xyz"), [])

    def test_empty_query(self):
        self.assertEqual(self.index.search("   "), [])

    def test_mod_with_warframe_tag_is_mod(self):
        item = Item(slug="irradiating_disarm", name="辐射缴械",
                    tags=("mod", "warframe", "augment"))
        self.assertEqual(item.kind, "mod")
        self.assertFalse(item.matches_category("warframe"))
        self.assertTrue(item.matches_category("mod"))

    def test_component_kind(self):
        item = self.index.get("rubico_prime_barrel")
        self.assertEqual(item.kind, "component")
        self.assertIn("部件", item.kind_label)

    def test_weapon_kind_label(self):
        item = self.index.get("rubico_prime_set")
        self.assertIn("武器", item.kind_label)
        self.assertIn("主武器", item.kind_label)
        self.assertIn("套装", item.kind_label)

    def test_thumb_url(self):
        item = self.index.get("mesa_prime_set")
        self.assertTrue(item.thumb_url.startswith("https://warframe.market/static/assets/"))

    def test_roundtrip_dict(self):
        item = self.index.get("loki_prime_set")
        restored = Item.from_dict(item.to_dict())
        self.assertEqual(restored.slug, item.slug)
        self.assertEqual(restored.name, item.name)
        self.assertEqual(restored.tags, item.tags)
        self.assertEqual(restored.ducats, item.ducats)


def _frame(slug, name, tags):
    return Item(slug=slug, name=name, name_en=name, tags=tags)


class FrameAliasExpansionTest(unittest.TestCase):
    """战甲：中文别名 / 英文名 + 总图·蓝图·部件 都要能精确命中。"""

    @classmethod
    def setUpClass(cls):
        parts = ("neuroptics", "chassis", "systems")
        items = [
            _frame("volt_prime_set", "Volt Prime 一套", ("warframe", "prime", "set")),
            _frame("volt_prime_blueprint", "Volt Prime 蓝图",
                   ("warframe", "prime", "blueprint")),
            _frame("primed_charged_shell", "充电弹头 Prime", ("mod", "legendary")),
        ]
        for part in parts:
            items.append(_frame(
                "volt_prime_%s_blueprint" % part, "Volt Prime %s 蓝图" % part,
                ("warframe", "prime", "component", "blueprint")))
        cls.index = ItemIndex(items, {"电男": "volt_prime_set"})

    def _top(self, query):
        hits = self.index.search(query, limit=1)
        self.assertTrue(hits, "查询没有结果：%s" % query)
        return hits[0].item.slug

    def test_frame_name(self):
        self.assertEqual(self._top("电男"), "volt_prime_set")
        self.assertEqual(self._top("圣装电男"), "volt_prime_set")
        self.assertEqual(self._top("电男p"), "volt_prime_set")

    def test_frame_english_name(self):
        for query in ("Volt Prime", "volt prime", "voltprime", "volt"):
            self.assertEqual(self._top(query), "volt_prime_set", query)

    def test_main_blueprint(self):
        for query in ("电男总图", "电男蓝图", "电男图", "电男图纸", "圣装电男总图",
                      "电男p图", "Volt Prime Blueprint", "volt prime 总图",
                      "volt 蓝图", "voltprime图"):
            self.assertEqual(self._top(query), "volt_prime_blueprint", query)

    def test_parts_by_chinese(self):
        self.assertEqual(self._top("电男头"), "volt_prime_neuroptics_blueprint")
        self.assertEqual(self._top("电男头部"), "volt_prime_neuroptics_blueprint")
        self.assertEqual(self._top("电男头部神经光元"),
                         "volt_prime_neuroptics_blueprint")
        self.assertEqual(self._top("电男机体"), "volt_prime_chassis_blueprint")
        self.assertEqual(self._top("电男系统"), "volt_prime_systems_blueprint")
        self.assertEqual(self._top("电男p头"), "volt_prime_neuroptics_blueprint")
        self.assertEqual(self._top("圣装电男机体"), "volt_prime_chassis_blueprint")
        self.assertEqual(self._top("电男头图"), "volt_prime_neuroptics_blueprint")
        self.assertEqual(self._top("电男机体蓝图"), "volt_prime_chassis_blueprint")

    def test_parts_by_english(self):
        cases = {
            "volt prime neuroptics": "volt_prime_neuroptics_blueprint",
            "volt neuroptics": "volt_prime_neuroptics_blueprint",
            "voltprime neuroptics": "volt_prime_neuroptics_blueprint",
            "volt prime chassis": "volt_prime_chassis_blueprint",
            "volt systems": "volt_prime_systems_blueprint",
            "volt prime 机体": "volt_prime_chassis_blueprint",
            "volt 头": "volt_prime_neuroptics_blueprint",
        }
        for query, slug in cases.items():
            self.assertEqual(self._top(query), slug, query)

    def test_manual_alias_wins(self):
        index = ItemIndex(
            [_frame("volt_prime_set", "Volt Prime 一套", ("warframe", "prime", "set"))],
            {"电男": "volt_prime_set", "电男总图": "volt_prime_set"})
        self.assertEqual(index.search("电男总图")[0].item.slug, "volt_prime_set")


if __name__ == "__main__":
    unittest.main()
