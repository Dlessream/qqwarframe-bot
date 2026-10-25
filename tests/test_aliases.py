import json
import os
import tempfile
import unittest

from bot.aliases import AliasStore, DEFAULT_ALIASES
from bot.app import _item_category
from bot.market import Item, ItemIndex


class AliasStoreTest(unittest.TestCase):
    def test_save_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aliases.json")
            store = AliasStore(path)
            store.save("测试头", "volt_prime_neuroptics_blueprint")
            self.assertEqual(store.load()["测试头"],
                             "volt_prime_neuroptics_blueprint")
            self.assertTrue(any(item["alias"] == "测试头"
                                for item in store.entries()))
            self.assertTrue(store.delete("测试头"))
            self.assertNotIn("测试头", store.load())
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertIn("_说明", saved)

    def test_builtin_cannot_be_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AliasStore(os.path.join(tmp, "aliases.json"))
            self.assertIn("电男头", DEFAULT_ALIASES)
            with self.assertRaises(ValueError):
                store.delete("电男头")

    def test_frame_defaults_migrate_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aliases.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"_说明": "x"}, handle)
            with open(os.path.join(tmp, "frame_aliases.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"电男": "volt_prime_blueprint"}, handle)
            store = AliasStore(path)
            self.assertEqual(store.ensure_frame_defaults(), 1)
            self.assertEqual(store.load()["电男"], "volt_prime_blueprint")
            self.assertEqual(store.ensure_frame_defaults(), 0)

    def test_component_defaults_migrate_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aliases.json")
            with open(os.path.join(tmp, "component_aliases.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"牛甲头": "rhino_prime_neuroptics_blueprint"}, handle)
            store = AliasStore(path)
            self.assertEqual(store.ensure_component_defaults(), 1)
            self.assertEqual(store.load()["牛甲头"],
                             "rhino_prime_neuroptics_blueprint")
            self.assertEqual(store.ensure_component_defaults(), 0)

    def test_replace_group_keeps_builtin(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = AliasStore(os.path.join(tmp, "aliases.json"))
            store.replace_for_slug("volt_prime_blueprint",
                                   ["电男", "伏特", "阿米尔"])
            self.assertEqual(store.load()["电男"], "volt_prime_blueprint")
            store.replace_for_slug("volt_prime_blueprint", ["电男"])
            self.assertEqual(store.load()["电男"], "volt_prime_blueprint")
            self.assertNotIn("伏特", store.load())
            store.replace_for_slug("volt_prime_neuroptics_blueprint", [])
            self.assertIn("电男头", store.load())

    def test_alias_resolves_to_component(self):
        items = [
            Item(slug="volt_prime_set", name="Volt Prime 一套",
                 name_en="Volt Prime Set"),
            Item(slug="volt_prime_blueprint", name="Volt Prime 蓝图",
                 name_en="Volt Prime Blueprint"),
            Item(slug="volt_prime_neuroptics_blueprint",
                 name="Volt Prime 头部神经光元 蓝图",
                 name_en="Volt Prime Neuroptics Blueprint"),
            Item(slug="volt_prime_chassis_blueprint",
                 name="Volt Prime 机体 蓝图",
                 name_en="Volt Prime Chassis Blueprint"),
            Item(slug="volt_prime_systems_blueprint",
                 name="Volt Prime 系统 蓝图",
                 name_en="Volt Prime Systems Blueprint"),
        ]
        aliases = {"电男": "volt_prime_blueprint"}
        index = ItemIndex(items, aliases)
        expected = {
            "电男": "volt_prime_blueprint",
            "电男p": "volt_prime_blueprint",
            "电男头": "volt_prime_neuroptics_blueprint",
            "电男头部": "volt_prime_neuroptics_blueprint",
            "电男机体": "volt_prime_chassis_blueprint",
            "电男系统": "volt_prime_systems_blueprint",
        }
        for query, slug in expected.items():
            self.assertEqual(index.search(query)[0].item.slug, slug, query)


    def test_weapon_set_parts(self):
        items = [
            Item(slug="rubico_prime_set", name="绝路 Prime 一套"),
            Item(slug="rubico_prime_barrel", name="绝路 Prime 枪管"),
            Item(slug="rubico_prime_receiver", name="绝路 Prime 枪机"),
            Item(slug="rubico_prime_stock", name="绝路 Prime 枪托"),
            Item(slug="rubico_prime_blueprint", name="绝路 Prime 蓝图"),
        ]
        index = ItemIndex(items, {"绝路": "rubico_prime_set"})
        self.assertEqual(index.search("绝路枪管")[0].item.slug,
                         "rubico_prime_barrel")
        self.assertEqual(index.search("绝路枪机")[0].item.slug,
                         "rubico_prime_receiver")
        self.assertEqual(index.search("绝路蓝图")[0].item.slug,
                         "rubico_prime_blueprint")

    def test_categories(self):
        self.assertEqual(_item_category(Item(
            slug="volt_prime_blueprint", tags=("warframe", "prime")))[0],
            "warframe")
        self.assertEqual(_item_category(Item(
            slug="rubico_prime_set", tags=("weapon", "prime", "set")))[0],
            "weapon")
        self.assertEqual(_item_category(Item(
            slug="axi_a1_relic", tags=("relic", "axi")))[0],
            "relic")
        self.assertEqual(_item_category(Item(
            slug="split_chamber", tags=("mod", "rifle")))[0],
            "mod")


if __name__ == "__main__":
    unittest.main()
