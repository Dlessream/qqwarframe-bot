import json
import lzma
import os
import tempfile
import time
import unittest

from bot import baronames

INDEX_TEXT = "\n".join([
    "ExportUpgrades_zh.json!00_AAA",
    "ExportWeapons_zh.json!00_BBB",
    "ExportManifest.json!00_CCC",
])

EXPORT_PAYLOAD = {
    "ExportWeapons": [
        {"uniqueName": "/Lotus/Weapons/VoidTrader/VTDetron",
         "name": "苦痛·德特昂"},
        {"uniqueName": "/Lotus/Weapons/Tenno/Rifle/X",
         "name": "某步枪"},
    ],
    "ExportModSet": [],
}


class ParseIndexTest(unittest.TestCase):
    def test_keeps_version(self):
        raw = lzma.compress(INDEX_TEXT.encode("utf-8"))
        index = baronames.parse_index(raw)
        self.assertEqual(index["ExportWeapons_zh"], "ExportWeapons_zh.json!00_BBB")
        self.assertNotIn("ExportUpgrades", index)

    def test_ignores_blank_and_broken_lines(self):
        raw = lzma.compress(b"\n\nno-hash-line\nExportGear_zh.json!00_X\n")
        index = baronames.parse_index(raw)
        self.assertEqual(index, {"ExportGear_zh": "ExportGear_zh.json!00_X"})


class ParseExportTest(unittest.TestCase):
    def test_both_prefixes(self):
        names = baronames.parse_export(
            json.dumps(EXPORT_PAYLOAD, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(names["/Lotus/Weapons/VoidTrader/VTDetron"], "苦痛·德特昂")
        # 商店路径也存一份，查表时不用再拼
        self.assertEqual(
            names["/Lotus/StoreItems/Weapons/VoidTrader/VTDetron"], "苦痛·德特昂")

    def test_skips_rows_without_name(self):
        payload = {"X": [{"uniqueName": "/Lotus/A"}, {"name": "只有名字"}]}
        self.assertEqual(baronames.parse_export(json.dumps(payload).encode()), {})

    def test_bad_json(self):
        with self.assertRaises(baronames.HttpError):
            baronames.parse_export(b"<html>")


class NameTest(unittest.TestCase):
    def setUp(self):
        baronames._names = {
            "/Lotus/StoreItems/Weapons/VoidTrader/VTDetron": "苦痛·德特昂",
        }

    def test_lookup_hit(self):
        self.assertEqual(
            baronames.display("/Lotus/StoreItems/Weapons/VoidTrader/VTDetron"),
            "苦痛·德特昂")

    def test_readable_fallback(self):
        self.assertEqual(baronames.readable("/Lotus/StoreItems/BaroTreasureBox"),
                         "Baro Treasure Box")
        self.assertEqual(baronames.readable("/Lotus/StoreItems/BaroCape"), "Baro Cape")

    def test_display_falls_back(self):
        self.assertEqual(baronames.display("/Lotus/StoreItems/UnknownThing"),
                         "Unknown Thing")

    def test_extra_names(self):
        # 官方导出没收录的物品，按末段兜底
        self.assertEqual(
            baronames.display("/Lotus/Types/StoreItems/Boosters/CreditBooster3DayStoreItem"),
            "3 天星币加成")
        self.assertEqual(
            baronames.display("/Lotus/StoreItems/Types/BoosterPacks/BaroTreasureBox"),
            "奸商宝箱")

    def test_empty(self):
        self.assertIsNone(baronames.lookup(""))
        self.assertEqual(baronames.display("/"), "未知物品")


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "baro.json")
        baronames._config = {"baro_names": {"cache_file": self.path}}
        baronames._names = {}
        baronames._fetched_at = 0.0

    def tearDown(self):
        baronames._config = None
        baronames._names = {}
        baronames._fetched_at = 0.0

    def test_roundtrip(self):
        baronames._names = {"/Lotus/A": "甲"}
        baronames._fetched_at = time.time()
        baronames.save_cache()
        baronames._names = {}
        self.assertEqual(baronames.load_cache(), 1)
        self.assertEqual(baronames.lookup("/Lotus/A"), "甲")
        self.assertFalse(baronames.stale())

    def test_missing_cache_is_stale(self):
        self.assertEqual(baronames.load_cache(), 0)
        self.assertTrue(baronames.stale())

    def test_broken_cache_is_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(baronames.load_cache(), 0)
        self.assertTrue(baronames.stale())


if __name__ == "__main__":
    unittest.main()
