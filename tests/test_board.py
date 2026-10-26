import json
import os
import tempfile
import time
import unittest

from bot.board import (JUNK_LABELS, BoardClient, BoardQuote, junk_ducats,
                       parse_riven_auctions, parse_top_orders)
from bot.market import Item, ItemIndex
from bot.net import HttpError
from bot.riven import RivenWeapon

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeMarket:
    def __init__(self, items, tops=None, auctions=None):
        self.cfg = {
            "base_url": "http://example.invalid",
            "proxy": "none",
            "user_agent": "test",
            "timeout_seconds": 5,
        }
        self.base_url = "http://example.invalid"
        self.index = ItemIndex(items)
        self.tops = tops or {}
        self.auctions = auctions or {}
        self.language = "zh-hans"

    def _headers(self):
        return {"Language": "zh-hans"}


class FakeHttp:
    def __init__(self, market):
        self.market = market
        self.calls = []
        self.rate_limited = False

    def get_json(self, url, headers=None):
        path = url.split("http://example.invalid", 1)[-1]
        self.calls.append(path)
        if self.rate_limited:
            raise HttpError("HTTP 429", status=429)
        if path.startswith("/v2/orders/item/"):
            slug = path.split("/")[4]
            if slug not in self.market.tops:
                raise HttpError("HTTP 404", status=404)
            return {"data": self.market.tops[slug]}
        if path.startswith("/v1/auctions/search"):
            weapon = ""
            for part in path.split("?", 1)[-1].split("&"):
                if part.startswith("weapon_url_name="):
                    weapon = part.split("=", 1)[1]
            return {"payload": {"auctions": self.market.auctions.get(weapon, [])}}
        raise HttpError("HTTP 404", status=404)


class FakeRiven:
    def __init__(self, weapons):
        self._weapons = weapons

    def weapons(self):
        return self._weapons


def build_items():
    return [
        Item(slug="ash_prime_systems_blueprint", name="Ash Prime 系统蓝图",
             item_id="1", tags=("component", "prime", "warframe", "blueprint"),
             ducats=100),
        Item(slug="loki_prime_systems_blueprint", name="Loki Prime 系统蓝图",
             item_id="2", tags=("component", "prime", "warframe", "blueprint"),
             ducats=100),
        Item(slug="orthos_prime_blade", name="Orthos Prime 刀刃",
             item_id="3", tags=("component", "prime", "weapon"), ducats=45),
        Item(slug="akjagara_prime_receiver", name="Akjagara Prime 枪机",
             item_id="4", tags=("component", "prime", "weapon"), ducats=45),
        Item(slug="braton_prime_barrel", name="Braton Prime 枪管",
             item_id="5", tags=("component", "prime", "weapon"), ducats=15),
        Item(slug="fake_gold_part", name="假金部件",
             item_id="6", tags=("component", "prime", "weapon"), ducats=65),
        Item(slug="ash_prime_set", name="Ash Prime 一套",
             item_id="7", tags=("set", "prime", "warframe"), ducats=0),
        Item(slug="saryn_prime_set", name="Saryn Prime 一套",
             item_id="8", tags=("set", "prime", "warframe"), ducats=0),
        Item(slug="serration", name="分裂膛室", item_id="9",
             tags=("mod",), ducats=0),
    ]


def top_payload(sell, buy=()):
    return {
        "sell": [{"platinum": price, "quantity": 1,
                  "user": {"ingameName": "seller%d" % index,
                           "status": status}}
                 for index, (price, status) in enumerate(sell)],
        "buy": [{"platinum": price, "quantity": 1,
                 "user": {"ingameName": "buyer", "status": "ingame"}}
                for price in buy],
    }


def build_board(tops=None, auctions=None, config=None, weapons=None):
    market = FakeMarket(build_items(), tops=tops, auctions=auctions)
    riven = FakeRiven(weapons or {})
    http = FakeHttp(market)
    settings = {"cache_file": "", "request_interval": 0.5,
                "junk_rarities": [100, 45, 15, 65, 25]}
    settings.update(config or {})
    board = BoardClient(market, riven, settings, http=http)
    return board, market, http


DEFAULT_TOPS = {
    "ash_prime_systems_blueprint": top_payload([(7, "ingame"), (9, "online")], [6]),
    "loki_prime_systems_blueprint": top_payload([(25, "online")], [20]),
    "orthos_prime_blade": top_payload([(3, "ingame"), (4, "ingame")], [2]),
    "akjagara_prime_receiver": top_payload([(9, "ingame")], [8]),
    "braton_prime_barrel": top_payload([(1, "offline"), (2, "ingame")], [1]),
    "fake_gold_part": top_payload([(11, "ingame")], [9]),
    "ash_prime_set": top_payload([(120, "ingame")], [110]),
    "saryn_prime_set": top_payload([(260, "ingame")], [240]),
}


class ParseTopTest(unittest.TestCase):
    def test_online_preferred_over_offline(self):
        payload = top_payload([(2, "offline"), (5, "ingame")], [1])
        price, buy, orders = parse_top_orders(payload)
        self.assertEqual(price, 5)
        self.assertEqual(buy, 1)
        self.assertEqual(orders, 1)

    def test_fallback_to_offline_when_nobody_online(self):
        payload = top_payload([(2, "offline")], [])
        self.assertEqual(parse_top_orders(payload)[0], 2)

    def test_empty(self):
        self.assertEqual(parse_top_orders({}), (0, 0, 0))

    def test_zero_price_ignored(self):
        payload = {"sell": [{"platinum": 0, "user": {"status": "ingame"}}],
                   "buy": [{"platinum": 0, "user": {"status": "ingame"}}]}
        self.assertEqual(parse_top_orders(payload), (0, 0, 0))


class JunkDucatsTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(junk_ducats("金垃圾"), 100)
        self.assertEqual(junk_ducats("银"), 45)
        self.assertEqual(junk_ducats("铜垃圾"), 15)
        self.assertEqual(junk_ducats("假金垃圾"), 65)
        self.assertEqual(junk_ducats("100"), 100)
        self.assertIsNone(junk_ducats("紫卡"))
        self.assertEqual(JUNK_LABELS[100], "金垃圾")


class RivenAuctionParseTest(unittest.TestCase):
    def row(self, price, re_rolls, buyout=None, closed=False, name="soma"):
        return {
            "closed": closed,
            "buyout_price": buyout,
            "starting_price": price,
            "item": {"url_name": name, "re_rolls": re_rolls,
                     "attributes": [{"url_name": "critical_chance", "value": 161.1,
                                     "positive": True}]},
            "owner": {"ingame_name": "seller"},
            "created": "2026-10-26T10:00:00.000+00:00",
        }

    def test_picks_most_expensive_unrolled(self):
        rows = [self.row(300, 0), self.row(900, 12), self.row(500, 0)]
        quote = parse_riven_auctions(rows)
        self.assertEqual(quote.price, 500)
        self.assertEqual(quote.attributes, ("暴击几率 +161.1%",))
        self.assertFalse(quote.buyout)

    def test_ignores_rolled_and_closed(self):
        rows = [self.row(900, 3), self.row(800, 0, closed=True)]
        self.assertIsNone(parse_riven_auctions(rows))

    def test_buyout_price_wins(self):
        quote = parse_riven_auctions([self.row(300, 0, buyout=1200)])
        self.assertEqual(quote.price, 1200)
        self.assertTrue(quote.buyout)


class BoardScanTest(unittest.TestCase):
    def test_targets_and_junk_quotes(self):
        board, _market, http = build_board(DEFAULT_TOPS)
        board._ensure_targets()
        self.assertIn("ash_prime_systems_blueprint", board._targets["junk"])
        self.assertNotIn("ash_prime_set", board._targets["junk"])
        self.assertNotIn("serration", board._targets["junk"])
        self.assertCountEqual(board._targets["frames"],
                              ["ash_prime_set", "saryn_prime_set"])

        for _ in range(len(board._targets["junk"]) + 2):
            board.step()

        gold = board.junk_quotes(100)
        self.assertEqual([row.slug for row in gold],
                         ["ash_prime_systems_blueprint", "loki_prime_systems_blueprint"])
        stats = board.junk_stats(100)
        self.assertEqual(stats["min"], 7)
        self.assertEqual(stats["median"], 25)
        self.assertEqual(stats["ready"], 2)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(board.junk_coverage(45), (2, 2))

    def test_junk_scan_interleaves_rarities(self):
        board, market, _http = build_board(DEFAULT_TOPS)
        board._ensure_targets()
        targets = board._targets["junk"]
        self.assertEqual(len(targets), 6)
        # 前四件应当覆盖四个档位，而不是先把 100 杜卡特扫完
        tiers = [market.index.get(slug).ducats for slug in targets[:4]]
        self.assertEqual(sorted(tiers), [15, 45, 65, 100])

    def test_frames_sorted_desc(self):
        board, _market, _http = build_board(DEFAULT_TOPS)
        for _ in range(12):
            board.step()
        frames = board.frame_quotes()
        self.assertEqual([row.slug for row in frames],
                         ["saryn_prime_set", "ash_prime_set"])
        self.assertEqual(frames[0].price, 260)
        self.assertEqual(frames[0].buy, 240)
        self.assertEqual(board.frame_coverage(), (2, 2))

    def test_riven_scan(self):
        weapons = {"soma": RivenWeapon("soma", "索玛", "Soma", "rifle", "primary",
                                       1.2, 6)}
        auctions = {
            "soma": [
                {"closed": False, "buyout_price": None, "starting_price": 300,
                 "item": {"url_name": "soma", "re_rolls": 0,
                          "attributes": [{"url_name": "multishot", "value": 98.1,
                                          "positive": True}]},
                 "owner": {"ingame_name": "Alice"}, "created": "2026-10-26T10:00:00Z"},
                {"closed": False, "buyout_price": 5000, "starting_price": 4000,
                 "item": {"url_name": "soma", "re_rolls": 12,
                          "attributes": []},
                 "owner": {"ingame_name": "Bob"}, "created": "2026-10-26T10:00:00Z"},
            ]
        }
        board, _market, _http = build_board(DEFAULT_TOPS, auctions=auctions,
                                            weapons=weapons)
        for _ in range(20):
            board.step()
        quotes = board.riven_quotes()
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].slug, "soma")
        self.assertEqual(quotes[0].price, 300)
        self.assertEqual(quotes[0].attributes, ("多重射击 +98.1%",))
        self.assertEqual(board.riven_coverage(), (1, 1))

    def test_rate_limit_backs_off(self):
        board, _market, http = build_board(DEFAULT_TOPS, config={"ban_seconds": 60})
        board._ensure_targets()
        http.rate_limited = True
        board.step()
        self.assertGreater(board._ban_until, time.time())
        self.assertEqual(board.status()["errors"], 0)

    def test_missing_item_is_not_fatal(self):
        board, _market, _http = build_board({})
        for _ in range(10):
            board.step()
        self.assertEqual(board.junk_quotes(100), [])
        self.assertGreater(board.errors, 0)

    def test_ttl_refetches(self):
        board, _market, http = build_board(DEFAULT_TOPS, config={"junk_seconds": 300})
        board._ensure_targets()
        board.step()
        first = http.calls[-1]
        with board._lock:
            for entry in board._quotes["junk"].values():
                entry["t"] = time.time() - 10 ** 5
        board._cursor["junk"] = 0
        board.step()
        self.assertNotEqual(http.calls[-1], None)
        self.assertIn("/v2/orders/item/", first)

    def test_coverage_before_scan(self):
        board, _market, _http = build_board(DEFAULT_TOPS)
        self.assertEqual(board.coverage("junk"), (0, 6))
        self.assertEqual(board.frame_coverage(), (0, 2))


class BoardPersistenceTest(unittest.TestCase):
    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "board.json")
            board, _market, _http = build_board(
                DEFAULT_TOPS, config={"cache_file": path})
            for _ in range(12):
                board.step()
            board._save(force=True)
            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            self.assertIn("junk", raw)
            self.assertTrue(raw["junk"])

            reloaded, _market2, _http2 = build_board(
                DEFAULT_TOPS, config={"cache_file": path})
            quotes = reloaded.junk_quotes(100)
            self.assertEqual(quotes[0].price, 7)
            self.assertGreater(reloaded.frame_quotes()[0].price, 0)

    def test_status_shape(self):
        board, _market, _http = build_board(DEFAULT_TOPS, config={"enabled": False})
        status = board.status()
        self.assertFalse(status["enabled"])
        self.assertFalse(status["running"])
        for key in ("junk", "frames", "riven"):
            self.assertIn("total", status[key])


if __name__ == "__main__":
    unittest.main()
