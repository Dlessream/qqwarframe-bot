import time
import unittest

from bot.cache import TTLCache
from bot.market import Item, MarketClient, MarketStats, Order
from bot.riven import RivenClient
from bot.wiki import WikiClient
from bot.worldstate import WorldStateClient


class TTLCacheTest(unittest.TestCase):
    def test_hit_and_expiry(self):
        cache = TTLCache({"enabled": True, "default_seconds": 60}, prefix="t")
        cache.set("a", 1, 0.05)
        self.assertEqual(cache.get("a"), 1)
        time.sleep(0.07)
        self.assertIsNone(cache.get("a"))

    def test_disabled(self):
        cache = TTLCache()
        cache.set("a", 1)
        self.assertIsNone(cache.get("a"))
        self.assertEqual(cache.get_or_set("a", lambda: 2), 2)
        self.assertIsNone(cache.get("a"))

    def test_prefix_and_clear(self):
        cache = TTLCache({"enabled": True}, prefix="p")
        cache.set("k", "v")
        self.assertEqual(len(cache), 1)
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_none_is_cached(self):
        cache = TTLCache({"enabled": True})
        calls = []
        cache.get_or_set("k", lambda: calls.append(1) or None)
        cache.get_or_set("k", lambda: calls.append(1) or None)
        self.assertEqual(len(calls), 1)


class MarketCacheTest(unittest.TestCase):
    def make_client(self):
        client = MarketClient({
            "base_url": "http://example.invalid",
            "language": "zh-hans",
            "platform": "pc",
            "cache_file": "",
            "alias_file": "",
        }, cache_config={"enabled": True, "orders_seconds": 60,
                         "stats_seconds": 900, "item_seconds": 3600})
        return client

    def test_orders_cached(self):
        client = self.make_client()
        calls = []

        def fake(slug, limit=5, platform=None):
            calls.append((slug, limit, platform))
            return [Order("sell", 10, 1, "a", "ingame")], []

        client._top_orders_uncached = fake
        first = client.top_orders("rubico", 3, "pc")
        second = client.top_orders("rubico", 3, "pc")
        self.assertEqual(len(calls), 1)
        self.assertEqual(first[0][0].platinum, 10)
        self.assertEqual(second[0][0].platinum, 10)

    def test_statistics_cached(self):
        client = self.make_client()
        calls = []

        def fake(slug, platform=None):
            calls.append(slug)
            return MarketStats()

        client._statistics_uncached = fake
        client.statistics("rubico", "pc")
        client.statistics("rubico", "pc")
        self.assertEqual(len(calls), 1)

    def test_item_detail_cached(self):
        client = self.make_client()
        calls = []

        def fake(slug):
            calls.append(slug)
            return Item(item_id="1", slug=slug, name="绝路")

        client._item_detail_uncached = fake
        client.item_detail("rubico")
        client.item_detail("rubico")
        self.assertEqual(len(calls), 1)


class WikiCacheTest(unittest.TestCase):
    def test_lookup_cached(self):
        client = WikiClient({
            "api_url": "http://example.invalid/api.php",
            "site_url": "http://example.invalid/wiki/",
        }, cache_config={"enabled": True, "wiki_seconds": 600})
        calls = []

        def fake(query, limit=3):
            calls.append(query)
            return None, []

        client._lookup_uncached = fake
        client.lookup("高斯")
        client.lookup("高斯")
        self.assertEqual(len(calls), 1)


class WorldStateCacheTest(unittest.TestCase):
    def test_get_cached(self):
        client = WorldStateClient({
            "base_url": "http://example.invalid",
            "language": "zh",
        }, cache_config={"enabled": True, "worldstate_seconds": 60})
        calls = []

        def fake(path):
            calls.append(path)
            return {"path": path}

        client._get_uncached = fake
        self.assertEqual(client._get("voidTrader"), {"path": "voidTrader"})
        self.assertEqual(client._get("voidTrader"), {"path": "voidTrader"})
        self.assertEqual(len(calls), 1)


class RivenCacheTest(unittest.TestCase):
    def test_auctions_cached(self):
        market = MarketClient({
            "base_url": "http://example.invalid",
            "cache_file": "",
            "alias_file": "",
        })
        client = RivenClient(market, cache_config={"enabled": True, "riven_seconds": 300})
        calls = []

        def fake(slug, limit=5):
            calls.append(slug)
            return [], "暂无"

        client._auctions_uncached = fake
        client.auctions("rubico")
        client.auctions("rubico")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
