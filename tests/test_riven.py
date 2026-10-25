import unittest

from bot.market import Item
from bot.net import HttpError
from bot.riven import ATTRIBUTE_NAMES, RivenClient, format_auction

WEAPONS = {"data": [
    {"id": "1", "slug": "rubico", "group": "primary", "rivenType": "rifle",
     "disposition": 0.95, "reqMasteryRank": 6,
     "i18n": {"en": {"name": "Rubico"}}},
    {"id": "2", "slug": "soma", "group": "primary", "rivenType": "rifle",
     "disposition": 1.15, "reqMasteryRank": 6,
     "i18n": {"en": {"name": "Soma"}}},
    {"id": "3", "slug": "lex", "group": "secondary", "rivenType": "pistol",
     "disposition": 1.25, "reqMasteryRank": 3,
     "i18n": {"en": {"name": "Lex"}}},
]}

AUCTIONS = {"payload": {"auctions": [
    {"buyout_price": 120, "starting_price": 100, "closed": False,
     "rank": 8, "re_rolls": 3,
     "owner": {"ingame_name": "SellerA"},
     "attributes": [
         {"url_name": "critical_chance", "value": 89.2, "positive": True},
         {"url_name": "damage", "value": 120.4, "positive": True},
         {"url_name": "zoom", "value": 30.0, "positive": False},
     ]},
    {"buyout_price": 80, "starting_price": 70, "closed": False,
     "rank": 0, "re_rolls": 0,
     "owner": {"ingame_name": "SellerB"},
     "attributes": []},
    {"buyout_price": 50, "closed": True, "owner": {"ingame_name": "Closed"}},
]}}

WEAPON = {"slug": "rubico", "name": "绝路", "name_en": "Rubico",
          "riven_type": "rifle", "group": "primary", "disposition": 0.95,
          "mastery_rank": 6}


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, headers=None):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeMarket:
    base_url = "http://example.invalid"
    language = "zh-hans"

    def __init__(self, auctions=AUCTIONS):
        self.auctions = auctions
        self.http = FakeHttp(auctions)
        self.paths = []

    def _get(self, path):
        self.paths.append(path)
        return WEAPONS

    def _headers(self, platform=None):
        return {"Language": "zh-hans"}


class WeaponTableTest(unittest.TestCase):
    def test_parse_weapons(self):
        client = RivenClient(FakeMarket())
        weapons = client.weapons()
        self.assertEqual(len(weapons), 3)
        self.assertEqual(weapons["rubico"].disposition, 0.95)
        self.assertEqual(weapons["rubico"].riven_type, "rifle")
        self.assertEqual(weapons["rubico"].disposition_label, "低")
        self.assertEqual(weapons["soma"].disposition_label, "中")

    def test_disposition_override(self):
        client = RivenClient(FakeMarket(), disposition_override=1.2)
        weapons = client.weapons()
        for weapon in weapons.values():
            self.assertEqual(weapon.disposition, 1.2)
            self.assertEqual(weapon.disposition_label, "中")

    def test_cache(self):
        market = FakeMarket()
        client = RivenClient(market)
        client.weapons()
        client.weapons()
        self.assertEqual(len(market.paths), 1)

    def test_find_by_slug(self):
        client = RivenClient(FakeMarket())
        item = Item(slug="rubico", name="绝路", name_en="Rubico")
        self.assertEqual(client.find(item).slug, "rubico")

    def test_find_by_prime_suffix(self):
        client = RivenClient(FakeMarket())
        item = Item(slug="rubico_prime_set", name="绝路 Prime 一套",
                    name_en="Rubico Prime Set", tags=("set", "prime", "weapon"))
        self.assertEqual(client.find(item).slug, "rubico")

    def test_find_by_name(self):
        client = RivenClient(FakeMarket())
        item = Item(slug="custom-slug", name="绝路", name_en="Rubico Prime")
        self.assertEqual(client.find(item).slug, "rubico")

    def test_find_none_for_warframe(self):
        client = RivenClient(FakeMarket())
        item = Item(slug="mesa_prime_set", name="Mesa Prime 一套",
                    name_en="Mesa Prime Set", tags=("warframe",))
        self.assertIsNone(client.find(item))


class AuctionTest(unittest.TestCase):
    def test_parse_and_sort(self):
        client = RivenClient(FakeMarket())
        auctions, note = client.auctions("rubico", limit=5)
        self.assertEqual(note, "")
        self.assertEqual([auction.price for auction in auctions], [80, 120])
        first = auctions[1]
        self.assertEqual(first.seller, "SellerA")
        self.assertEqual(first.rank, 8)
        self.assertTrue(any("暴击几率" in attr for attr in first.attributes))
        self.assertTrue(any("变焦" in attr and "-" in attr for attr in first.attributes))

    def test_nested_item_shape(self):
        payload = {"payload": {"auctions": [
            {"buyout_price": 55, "closed": False,
             "owner": {"ingame_name": "NestedSeller"},
             "item": {"mod_rank": 8, "re_rolls": 2,
                      "attributes": [
                          {"url_name": "critical_chance", "value": 90.2,
                           "positive": True},
                      ]}},
        ]}}
        client = RivenClient(FakeMarket(auctions=payload))
        auctions, note = client.auctions("rubico", limit=5)
        self.assertEqual(note, "")
        self.assertEqual(auctions[0].price, 55)
        self.assertEqual(auctions[0].rank, 8)
        self.assertEqual(auctions[0].re_rolls, 2)
        self.assertIn("暴击几率", auctions[0].attributes[0])

    def test_limit(self):
        client = RivenClient(FakeMarket())
        auctions, _ = client.auctions("rubico", limit=1)
        self.assertEqual(len(auctions), 1)

    def test_api_unavailable(self):
        client = RivenClient(FakeMarket(auctions=HttpError("gone", status=403)))
        auctions, note = client.auctions("rubico")
        self.assertEqual(auctions, [])
        self.assertIn("不可用", note)

    def test_format_auction(self):
        client = RivenClient(FakeMarket())
        auctions, _ = client.auctions("rubico")
        text = format_auction(auctions[0])
        self.assertIn("80p", text)
        self.assertIn("SellerB", text)

    def test_attribute_map(self):
        self.assertEqual(ATTRIBUTE_NAMES["multishot"], "多重射击")
        self.assertIn("critical_damage", ATTRIBUTE_NAMES)


if __name__ == "__main__":
    unittest.main()
