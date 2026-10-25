import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from bot.market import MarketClient

from .mock_http import MockServer, json_response

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def build_config(base_url, cache_file, **overrides):
    config = {
        "base_url": base_url,
        "language": "zh-hans",
        "platform": "pc",
        "crossplay": True,
        "cache_file": cache_file,
        "cache_ttl_seconds": 3600,
        "alias_file": os.path.join(BASE, "data", "aliases.json"),
        "user_agent": "QQWarframeBot/test",
        "timeout_seconds": 5,
        "retries": 1,
        "max_requests_per_second": 0,
        "proxy": "",
        "insecure_tls": False,
    }
    config.update(overrides)
    return config


class MarketClientTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wf-test-")
        self.server = MockServer()
        self.server.add_json("/v2/items", load_fixture("items.json"))
        self.server.add_json("/v2/item/mesa_prime_set", load_fixture("item_mesa.json"))
        self.server.add_json("/v2/orders/item/mesa_prime_set/top",
                             load_fixture("orders_mesa.json"))
        self.server.add_json("/v1/items/mesa_prime_set/statistics",
                             load_fixture("stats_mesa.json"))
        self.server.add("/v2/item/unknown", lambda q, h, i: json_response({"error": {}}, 404))
        self.base_url = self.server.start()
        self.cache_file = os.path.join(self.tmpdir, "items_cache.json")
        self.client = MarketClient(build_config(self.base_url, self.cache_file))

    def tearDown(self):
        self.server.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------ 索引
    def test_load_items_and_cache(self):
        index = self.client.load_items()
        self.assertEqual(len(index), 12)
        self.assertTrue(os.path.isfile(self.cache_file))

        requests_before = self.server.count("/v2/items")
        self.client.load_items()
        self.assertEqual(self.server.count("/v2/items"), requests_before)

    def test_language_header_sent(self):
        self.client.load_items()
        headers = self.server.requests[0]["headers"]
        self.assertEqual(headers.get("language"), "zh-hans")
        self.assertEqual(headers.get("platform"), "pc")

    def test_force_refresh_hits_network(self):
        self.client.load_items()
        before = self.server.count("/v2/items")
        self.client.load_items(force=True)
        self.assertEqual(self.server.count("/v2/items"), before + 1)

    def test_cache_used_when_offline(self):
        self.client.load_items()
        self.server.stop()

        cached_client = MarketClient(build_config(self.base_url, self.cache_file))
        index = cached_client.load_items()
        self.assertEqual(len(index), 12)
        self.assertEqual(index.search("绝路")[0].item.slug, "rubico_prime_set")

    def test_cache_file_is_readable_json(self):
        self.client.load_items()
        with open(self.cache_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["language"], "zh-hans")
        self.assertEqual(payload["count"], 12)
        self.assertTrue(payload["items"])

    # ------------------------------------------------------------------ 详情
    def test_item_detail(self):
        item = self.client.item_detail("mesa_prime_set")
        self.assertIsNotNone(item)
        self.assertEqual(item.ducats, 260)
        self.assertEqual(item.mastery_rank, 0)
        self.assertIn("致命", item.description)
        self.assertIn("huijiwiki", item.wiki_link)

    def test_item_detail_404(self):
        self.assertIsNone(self.client.item_detail("unknown"))

    # ------------------------------------------------------------------ 订单
    def test_top_orders(self):
        sell, buy = self.client.top_orders("mesa_prime_set", limit=3)
        self.assertEqual([order.platinum for order in sell], [60, 63, 65])
        self.assertEqual([order.platinum for order in buy], [50, 45, 40])
        self.assertEqual(sell[0].user, "adadaAS")
        self.assertEqual(sell[0].status_label, "游戏中")
        self.assertEqual(sell[0].quantity, 12)

    def test_top_orders_limit(self):
        sell, buy = self.client.top_orders("mesa_prime_set", limit=1)
        self.assertEqual(len(sell), 1)
        self.assertEqual(len(buy), 1)

    # ------------------------------------------------------------------ 行情
    def test_statistics_aggregation(self):
        stats = self.client.statistics("mesa_prime_set")
        self.assertFalse(stats.empty)
        self.assertEqual(stats.h48.volume, 35)
        self.assertAlmostEqual(stats.h48.avg, 2190 / 35, places=3)
        self.assertAlmostEqual(stats.h48.median, 64)
        self.assertEqual(stats.h48.min_price, 55)
        self.assertEqual(stats.h48.max_price, 70)

        self.assertEqual(stats.d90.volume, 400)
        self.assertAlmostEqual(stats.d90.avg, 71.5, places=3)
        self.assertAlmostEqual(stats.d90.median, 72)
        self.assertEqual(stats.d90.min_price, 60)
        self.assertEqual(stats.d90.max_price, 90)
        self.assertGreaterEqual(len(stats.history), 2)
        self.assertEqual(stats.history[-1].date, "2026-10-21")

    def test_statistics_404_returns_empty(self):
        stats = self.client.statistics("unknown")
        self.assertTrue(stats.empty)

    # ------------------------------------------------------------------ 重试
    def test_retry_on_429(self):
        calls = {"count": 0}

        def flaky(query, headers, index):
            calls["count"] += 1
            if calls["count"] < 3:
                return 429, b'{"error":"rate limited"}'
            return json_response(load_fixture("items.json"))

        server = MockServer()
        server.add("/v2/items", flaky)
        base_url = server.start()
        try:
            client = MarketClient(build_config(base_url, os.path.join(self.tmpdir, "b.json"),
                                              retries=2))
            with mock.patch("bot.net.time.sleep", return_value=None):
                index = client.load_items(force=True)
            self.assertEqual(len(index), 12)
            self.assertEqual(calls["count"], 3)
        finally:
            server.stop()

    def test_403_gives_friendly_error(self):
        server = MockServer()
        server.add("/v2/items", lambda q, h, i: (403, b"Just a moment..."))
        base_url = server.start()
        try:
            client = MarketClient(build_config(base_url, os.path.join(self.tmpdir, "c.json")))
            with mock.patch("bot.net.time.sleep", return_value=None):
                with self.assertRaises(Exception) as ctx:
                    client.load_items(force=True)
            self.assertIn("403", str(ctx.exception))
        finally:
            server.stop()

    def test_failure_backoff(self):
        server = MockServer()
        server.add("/v2/items", lambda q, h, i: (500, b"boom"))
        base_url = server.start()
        try:
            client = MarketClient(build_config(base_url, os.path.join(self.tmpdir, "f.json"),
                                              retries=0))
            with mock.patch("bot.net.time.sleep", return_value=None):
                with self.assertRaises(Exception):
                    client.load_items()
                calls = server.count("/v2/items")
                with self.assertRaises(Exception) as ctx:
                    client.load_items()
            self.assertEqual(server.count("/v2/items"), calls)
            self.assertIn("稍后再试", str(ctx.exception))
        finally:
            server.stop()

    # ------------------------------------------------------------------ 兼容旧格式
    def test_v1_item_row(self):
        row = {
            "id": "54a74454e779892d5e515644",
            "url_name": "split_chamber",
            "item_name": "Split Chamber",
            "thumb": "items/images/en/split_chamber.png",
            "ducats": 15,
            "tags": ["mod", "primary"],
            "i18n": {
                "en": {"name": "Split Chamber"},
                "zh-hans": {"name": "分裂膛室"},
            },
        }
        item = self.client._parse_short(row)
        self.assertIsNotNone(item)
        self.assertEqual(item.slug, "split_chamber")
        self.assertEqual(item.name, "分裂膛室")
        self.assertEqual(item.name_en, "Split Chamber")
        self.assertEqual(item.ducats, 15)

    def test_v1_items_payload(self):
        server = MockServer()
        server.add_json("/v2/items", {"payload": {"items": [{
            "url_name": "split_chamber",
            "item_name": "Split Chamber",
            "tags": ["mod"],
            "i18n": {"zh-hans": {"name": "分裂膛室"}},
        }]}})
        base_url = server.start()
        try:
            client = MarketClient(build_config(base_url, os.path.join(self.tmpdir, "d.json")))
            index = client.load_items(force=True)
            self.assertEqual(len(index), 1)
            self.assertEqual(index.search("分裂膛室")[0].item.slug, "split_chamber")
        finally:
            server.stop()

    def test_v1_orders_payload(self):
        server = MockServer()
        server.add("/v2/orders/item/legacy/top",
                   lambda q, h, i: json_response({"error": {"code": "not_found"}}, 404))
        server.add_json("/v2/orders/item/legacy", {"payload": {"orders": [
            {"platinum": 12, "quantity": 2, "order_type": "sell",
             "user": {"ingame_name": "Seller", "status": "ingame"}},
            {"platinum": 8, "quantity": 1, "order_type": "buy",
             "user": {"ingame_name": "Buyer", "status": "online"}},
        ]}})
        base_url = server.start()
        try:
            client = MarketClient(build_config(base_url, os.path.join(self.tmpdir, "e.json")))
            sell, buy = client.top_orders("legacy", limit=5)
            self.assertEqual(len(sell), 1)
            self.assertEqual(sell[0].platinum, 12)
            self.assertEqual(sell[0].user, "Seller")
            self.assertEqual(buy[0].platinum, 8)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
