import io
import json
import os
import tempfile
import threading
import time
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

    def top_orders(self, slug, limit=5, platform=None, force=False):
        from bot.market import Order
        values = {
            "paris_prime_upper_limb": [3.8, 4.0, 4.2],
            "forma_blueprint": [7.0, 7.2],
            "boltor_prime_barrel": [4.3, 5.0, 6.1],
        }
        prices = values.get(slug, [])
        sells = [Order(side="sell", platinum=p, quantity=1,
                       user="tester", status="ingame")
                 for p in prices]
        return sells[:limit], []

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
        with open(self.cache, "w", encoding="utf-8") as handle:
            json.dump({"relics": RAW}, handle)
        self.client = RelicClient(FakeWorld(), FakeMarket(),
                                  local_file=self.cache)

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
        self.assertAlmostEqual(rows["paris_prime_upper_limb"].price_low, 3.8)
        self.assertAlmostEqual(rows["paris_prime_upper_limb"].price_high, 4.2)
        self.assertEqual(rows["forma_blueprint"].ducats, 0)

    def test_tier_aliases_are_not_mixed(self):
        self.assertEqual(self.client.search("古a1")[0].tier, "Lith")
        self.assertEqual(self.client.search("后a1"), [])
        self.assertEqual(self.client.search("Axi S18")[0].code, "S18")

    def test_local_relic_database(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "relics_local.json")
        client = RelicClient(None, FakeMarket(), local_file=path)
        self.assertTrue(client.available())
        self.assertGreaterEqual(len(client.relics()), 800)
        relic = client.search("后纪s18")[0]
        self.assertEqual(relic.title, "后纪 S18")
        self.assertEqual(relic.vault_label, "已入库")
        self.assertEqual(len(relic.rewards), 6)

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
        self.assertIn("15 杜卡德", reply.text)
        self.assertIn("最低 3.80p", reply.text)
        self.assertIn("最高 4.20p", reply.text)
        png = reply.image()
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(Image.open(io.BytesIO(png)).size[0], 860)

    def test_enrich_runs_reward_lookups_concurrently(self):
        """6 个奖励的行情请求应并发执行，耗时远小于逐个串行。"""
        delay = 0.25
        lock = threading.Lock()
        peak = [0]
        active = [0]

        class SlowMarket(FakeMarket):
            def top_orders(self, slug, limit=5, platform=None, force=False):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                try:
                    time.sleep(delay)
                    return super().top_orders(slug, limit, platform, force)
                finally:
                    with lock:
                        active[0] -= 1

        # 用自建的 3 奖励遗物，全部命中 FakeMarket，避免真实库缺项干扰。
        raw = [{
            "name": "Axi S18 Relic", "vaulted": True,
            "rewards": [
                {"rarity": "Common", "item": {
                    "name": "Paris Prime Upper Limb",
                    "warframeMarket": {"url_name": "paris_prime_upper_limb"}}},
                {"rarity": "Uncommon", "item": {
                    "name": "Forma Blueprint",
                    "warframeMarket": {"url_name": "forma_blueprint"}}},
                {"rarity": "Rare", "item": {
                    "name": "Boltor Prime Barrel",
                    "warframeMarket": {"url_name": "boltor_prime_barrel"}}},
            ],
        }]
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "relics.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"relics": raw}, handle)
        try:
            client = RelicClient(None, SlowMarket(), local_file=path)
            relic = client.search("后s18")[0]
            started = time.time()
            client.enrich(relic)
            elapsed = time.time() - started
            self.assertGreaterEqual(peak[0], 2, "奖励行情应并发请求")
            # 并发时总耗时接近单次延迟，串行会是 delay * 奖励数。
            self.assertLess(elapsed, delay * len(relic.rewards) * 0.8)
            # 顺序保持不变，价格正确落到对应奖励。
            self.assertEqual(
                [row.name for row in relic.rewards],
                ["Paris Prime Upper Limb", "Forma Blueprint", "Boltor Prime Barrel"])
            self.assertAlmostEqual(relic.rewards[0].price_low, 3.8)
            self.assertAlmostEqual(relic.rewards[0].price_high, 4.2)
            self.assertAlmostEqual(relic.rewards[2].price_low, 4.3)
        finally:
            for name in os.listdir(tmp):
                os.remove(os.path.join(tmp, name))
            os.rmdir(tmp)


if __name__ == "__main__":
    unittest.main()
