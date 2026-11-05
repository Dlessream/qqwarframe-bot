import io
import os
import tempfile
import unittest

from PIL import Image

from bot import present
from bot.market import Item, MarketStats, StatSummary
from bot.relics import RelicClient, parse_tier_code


RAW = [
    {
        "name": "Axi S18 Relic",
        "vaulted": True,
        "rewards": [
            {"rarity": "Common", "chance": 25.33,
             "item": {"name": "Paris Prime Upper Limb",
                      "warframeMarket": {"url_name": "paris_prime_upper_limb"}}},
            {"rarity": "Uncommon", "chance": 11.0,
             "item": {"name": "Forma Blueprint",
                      "warframeMarket": {"url_name": "forma_blueprint"}}},
        ],
    },
    {
        "name": "Lith A1 Relic",
        "vaulted": False,
        "rewards": [
            {"rarity": "Rare", "chance": 2.0,
             "item": {"name": "Boltor Prime Barrel",
                      "warframeMarket": {"url_name": "boltor_prime_barrel"}}},
        ],
    },
]


class FakeWorld:
    def __init__(self, raw=RAW):
        self.raw = raw

    def fetch(self, path, force=False):
        return self.raw


class FakeIndex:
    def __init__(self):
        self.by_slug = {
            "paris_prime_upper_limb": Item(
                slug="paris_prime_upper_limb", name="帕里斯 Prime 弓弦",
                name_en="Paris Prime Upper Limb", ducats=15),
            "forma_blueprint": Item(
                slug="forma_blueprint", name="Forma 蓝图",
                name_en="Forma Blueprint", ducats=0),
            "boltor_prime_barrel": Item(
                slug="boltor_prime_barrel", name="螺钉步枪 Prime 枪管",
                name_en="Boltor Prime Barrel", ducats=45),
        }


class FakeMarket:
    def __init__(self):
        self.index = FakeIndex()

    def statistics(self, slug, platform=None):
        values = {
            "paris_prime_upper_limb": (3.8, 3.94, 12, 33),
            "forma_blueprint": (7.0, 7.5, 100, 200),
            "boltor_prime_barrel": (4.3, 10.56, 8, 20),
        }
        h48, d90, v48, v90 = values[slug]
        return MarketStats(
            h48=StatSummary(v48, h48, 1, h48, h48),
            d90=StatSummary(v90, d90, 1, d90, d90))


class TierParseTest(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(parse_tier_code("后纪s18"), ("Axi", "S18"))
        self.assertEqual(parse_tier_code("后s18"), ("Axi", "S18"))
        self.assertEqual(parse_tier_code("前纪a1"), ("Meso", "A1"))
        self.assertEqual(parse_tier_code("中纪n12"), ("Neo", "N12"))
        self.assertEqual(parse_tier_code("古纪a1"), ("Lith", "A1"))
        self.assertEqual(parse_tier_code("Axi S18 Relic"), ("Axi", "S18"))


class RelicClientTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cache = os.path.join(self.dir, "relics.json")
        self.client = RelicClient(FakeWorld(), FakeMarket(),
                                  cache_file=self.cache)

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.remove(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def test_search_and_enrich(self):
        relic = self.client.search("后s18")[0]
        self.assertEqual(relic.title, "后纪 S18")
        self.assertTrue(relic.vaulted)
        self.client.enrich(relic)
        rows = {row.slug: row for row in relic.rewards}
        self.assertEqual(rows["paris_prime_upper_limb"].ducats, 15)
        self.assertAlmostEqual(rows["paris_prime_upper_limb"].price48, 3.8)
        self.assertEqual(rows["forma_blueprint"].ducats, 0)

    def test_tier_aliases_are_not_mixed(self):
        self.assertEqual(self.client.search("古a1")[0].tier, "Lith")
        self.assertEqual(self.client.search("后a1"), [])
        self.assertEqual(self.client.search("Axi S18")[0].code, "S18")

    def test_wfcd_drop_shape(self):
        from bot.relics import parse_relic
        relic = parse_relic({
            "tier": "Axi", "relicName": "S18", "vaulted": True,
            "rewards": [{"itemName": "Paris Prime Upper Limb",
                         "rarity": "Common", "chance": 25.33}],
        })
        self.assertEqual(relic.title, "后纪 S18")
        self.assertEqual(relic.rewards[0].name, "Paris Prime Upper Limb")

    def test_refinement_changes_chance(self):
        text, refinement = self.client.extract_refinement("后s18 光辉")
        self.assertEqual((text, refinement), ("后s18", "Radiant"))
        relic = self.client.search("后s18 光辉")[0]
        self.client.enrich(relic, refinement=refinement)
        self.assertEqual(relic.refinement_label, "光辉")
        self.assertEqual(relic.rewards[1].chance, 20.0)  # 银卡
        rare = self.client.search("后s18")[0]
        self.client.enrich(rare, refinement="Radiant")
        self.assertEqual(rare.vault_label, "已入库")

    def test_relic_card(self):
        relic = self.client.search("后s18")[0]
        self.client.enrich(relic)
        reply = present.relic_reply(relic, "pc")
        self.assertIn("后纪 S18", reply.text)
        self.assertIn("已入库", reply.text)
        self.assertIn("15 D", reply.text)
        png = reply.image()
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(Image.open(io.BytesIO(png)).size[0], 860)


if __name__ == "__main__":
    unittest.main()
