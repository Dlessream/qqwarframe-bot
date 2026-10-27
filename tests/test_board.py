import json
import os
import tempfile
import time
import unittest

from bot.board import (JUNK_LABELS, BoardClient, BoardQuote, junk_ducats,
                       parse_riven_auctions, parse_top_orders, riven_candidates)
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


class RivenCandidateTest(unittest.TestCase):
    """零洗卡口径：一口价优先、满级优先。"""

    def row(self, price, rank=8, buyout=None, seller="s1", name="soma",
            re_rolls=0):
        return {
            "closed": False,
            "buyout_price": buyout,
            "starting_price": price,
            "item": {"url_name": name, "re_rolls": re_rolls, "mod_rank": rank,
                     "attributes": [{"url_name": "critical_chance", "value": 161.1,
                                     "positive": True}]},
            "owner": {"ingame_name": seller},
            "created": "2026-10-26T10:00:00.000+00:00",
        }

    def test_buyout_beats_higher_starting_price(self):
        quotes = riven_candidates([self.row(9000), self.row(1200, buyout=1200)])
        self.assertEqual([quote.price for quote in quotes], [1200])
        self.assertTrue(quotes[0].buyout)

    def test_max_rank_preferred_over_zero_rank_bait(self):
        quotes = riven_candidates([
            self.row(50000, rank=0, buyout=50000),
            self.row(3500, rank=8, buyout=3500),
        ])
        self.assertEqual(quotes[0].price, 3500)
        self.assertEqual(quotes[0].rank, 8)

    def test_zero_rank_kept_when_only_choice(self):
        quote = parse_riven_auctions([self.row(400, rank=0, buyout=400)])
        self.assertEqual(quote.price, 400)
        self.assertEqual(quote.rank, 0)

    def test_candidates_sorted_desc(self):
        quotes = riven_candidates([
            self.row(900, buyout=900), self.row(2400, buyout=2400),
            self.row(1500, buyout=1500)])
        self.assertEqual([quote.price for quote in quotes], [2400, 1500, 900])


class RivenBaitFilterTest(unittest.TestCase):
    """钓鱼/占位价过滤：同价刷多把武器、同一卖家刷多把武器。"""

    def board(self):
        board, _market, _http = build_board(DEFAULT_TOPS)
        return board

    def put(self, board, slug, candidates):
        board._quotes["riven"][slug] = {"t": 0.0, "c": candidates}

    def test_same_price_across_weapons_is_bait(self):
        board = self.board()
        for slug in ("a", "b", "c"):
            self.put(board, slug, [
                [50000, 0, "troll", ["暴击几率 +161.1%"], "", 1],
                [3500, 8, "honest", ["暴击几率 +161.1%"], "", 1],
            ])
        quotes = {quote.slug: quote.price for quote in board.riven_quotes()}
        self.assertEqual(quotes, {"a": 3500, "b": 3500, "c": 3500})
        self.assertTrue(board.riven_bait_stats())

    def test_seller_spamming_many_weapons_is_bait(self):
        board = self.board()
        for index, slug in enumerate(("a", "b", "c", "d")):
            self.put(board, slug, [
                [4000 + index * 111, 8, "spammer", [], "", 1],
                [900, 8, "honest", [], "", 1],
            ])
        quotes = {quote.slug: quote.price for quote in board.riven_quotes()}
        self.assertEqual(set(quotes.values()), {900})

    def test_legacy_cheap_price_still_listed(self):
        path = os.path.join(tempfile.mkdtemp(), "board.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "riven": {
                "soma": [800, 1, "seller", ["多重射击 +98.1%"],
                         "2026-10-26T10:00:00", 1e9]}}, handle)
        board, _market, _http = build_board(DEFAULT_TOPS, config={"cache_file": path})
        quotes = board.riven_quotes()
        self.assertEqual([(quote.slug, quote.price) for quote in quotes],
                         [("soma", 800)])

    def test_legacy_entry_rescanned_first(self):
        board = self.board()
        board._targets["riven"] = ["fresh_one", "legacy_one"]
        self.put(board, "fresh_one", [[1200, 8, "a", [], "", 1]] * 2)
        board._quotes["riven"]["fresh_one"]["t"] = time.time()
        self.put(board, "legacy_one", [[9000, 0, "b", [], "", 1]])
        board._quotes["riven"]["legacy_one"]["v"] = 1
        board._quotes["riven"]["legacy_one"]["t"] = time.time()
        self.assertEqual(board._next_stale("riven", board._targets["riven"],
                                           board.riven_ttl, time.time()),
                         "legacy_one")

    def test_cheap_prices_not_filtered(self):
        board = self.board()
        for slug in ("a", "b", "c"):
            self.put(board, slug, [[500, 8, "anyone", ["多重射击 +98.1%"], "", 1]])
        self.assertEqual([quote.price for quote in board.riven_quotes()],
                         [500, 500, 500])

    def test_legacy_cache_kept_but_marked_stale(self):
        path = os.path.join(tempfile.mkdtemp(), "board.json")
        # v1 格式：每把武器只有一条 [价格, 一口价, 卖家, 词条, 上架时间, 抓取时间]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "riven": {
                "soma": [50000, 1, "troll", ["暴击几率 +161.1%"],
                         "2026-10-26T10:00:00", 1e9]}}, handle)
        board, _market, _http = build_board(DEFAULT_TOPS, config={"cache_file": path})
        entry = board._quotes["riven"]["soma"]
        self.assertEqual(entry["c"][0][0], 50000)
        self.assertEqual(entry["t"], 0.0)          # 立刻进重扫队列
        self.assertEqual(entry["v"], 1)            # 未核实标记
        self.assertEqual(board._legacy_riven, 1)
        # 未核实的高价（>= 3000）先不上榜，避免假价霸榜
        self.assertEqual(board.riven_quotes(), [])

    def test_single_candidate_cache_without_flag_is_unverified(self):
        path = os.path.join(tempfile.mkdtemp(), "board.json")
        # 迁移时合并进来的条目：v2 格式但只有一条候选、没有标志位
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 2, "riven": {
                "vesper_77": [1.7e9, [[50000, 0, "mushroom",
                                       ["暴击伤害 +108.3%"], "", 1]]],
                "soma": [1.7e9, [[800, 8, "honest",
                                  ["多重射击 +98.1%"], "", 1]]]}}, handle)
        board, _market, _http = build_board(DEFAULT_TOPS, config={"cache_file": path})
        self.assertEqual(board._quotes["riven"]["vesper_77"]["v"], 1)
        self.assertEqual(board._quotes["riven"]["vesper_77"]["t"], 0.0)
        # 两条都是单候选，都标成待重扫；便宜的那条照常上榜
        self.assertEqual(board._legacy_riven, 2)
        self.assertEqual([(quote.slug, quote.price) for quote in board.riven_quotes()],
                         [("soma", 800)])

    def test_single_candidate_after_full_fetch_is_listed(self):
        path = os.path.join(tempfile.mkdtemp(), "board.json")
        # 标志位 bit1（=2）：已按新接口完整抓过，市场真的只有这一单
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"version": 2, "riven": {
                "rare_weapon": [1.7e9, [[4200, 8, "only_one",
                                         ["多重射击 +98.1%"], "", 1]], 2]}}, handle)
        board, _market, _http = build_board(DEFAULT_TOPS, config={"cache_file": path})
        self.assertNotIn("v", board._quotes["riven"]["rare_weapon"])
        self.assertEqual(board._legacy_riven, 0)
        self.assertEqual([quote.price for quote in board.riven_quotes()], [4200])

    def test_scan_marks_entry_as_fully_fetched(self):
        weapons = {"rare_weapon": RivenWeapon("rare_weapon", "稀有武器", "Rare",
                                             "rifle", "primary", 1.2, 6)}
        auctions = {"rare_weapon": [
            {"closed": False, "buyout_price": 4200, "starting_price": 4200,
             "item": {"url_name": "rare_weapon", "re_rolls": 0, "mod_rank": 8,
                      "attributes": [{"url_name": "multishot", "value": 98.1,
                                      "positive": True}]},
             "owner": {"ingame_name": "only_one"},
             "created": "2026-10-26T10:00:00Z"}]}
        board, _market, _http = build_board(DEFAULT_TOPS, auctions=auctions,
                                            weapons=weapons)
        board._targets["riven"] = ["rare_weapon"]
        board._quotes["riven"]["rare_weapon"] = {
            "t": 0.0, "v": 1,
            "c": [[50000, 0, "troll", ["暴击伤害 +108.3%"], "", 1]]}
        for _ in range(20):
            board.step()
        entry = board._quotes["riven"]["rare_weapon"]
        self.assertEqual(entry["f"], 1)
        self.assertNotIn("v", entry)
        self.assertEqual([quote.price for quote in board.riven_quotes()], [4200])

    def test_cache_roundtrip_keeps_candidates(self):
        path = os.path.join(tempfile.mkdtemp(), "board.json")
        board, _market, _http = build_board(DEFAULT_TOPS, config={"cache_file": path})
        self.put(board, "soma", [
            [3000, 8, "s1", ["多重射击 +98.1%"], "2026-10-26T10:00:00", 1],
            [1200, 8, "s2", ["暴击几率 +161.1%"], "2026-10-26T10:00:00", 1],
        ])
        board._dirty = 1
        board._save(force=True)

        reloaded, _market2, _http2 = build_board(DEFAULT_TOPS, config={"cache_file": path})
        rows = reloaded._quotes["riven"]["soma"]["c"]
        self.assertEqual([row[0] for row in rows], [3000, 1200])
        self.assertEqual(rows[0][1], 8)
        self.assertEqual(rows[0][3], ["多重射击 +98.1%"])
        self.assertEqual(reloaded._quotes["riven"]["soma"]["t"], 0.0)


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
