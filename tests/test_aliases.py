import json
import os
import tempfile
import unittest

from bot.aliases import AliasStore, DEFAULT_ALIASES
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

    def test_alias_resolves_to_component(self):
        items = [
            Item(slug="volt_prime_set", name="Volt Prime 一套",
                 name_en="Volt Prime Set"),
            Item(slug="volt_prime_neuroptics_blueprint",
                 name="Volt Prime 头部神经光元 蓝图",
                 name_en="Volt Prime Neuroptics Blueprint"),
        ]
        index = ItemIndex(items, DEFAULT_ALIASES)
        hits = index.search("电男头")
        self.assertEqual(hits[0].item.slug,
                         "volt_prime_neuroptics_blueprint")


if __name__ == "__main__":
    unittest.main()
