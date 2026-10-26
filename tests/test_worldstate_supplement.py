"""中转补齐（WARP → r.jina.ai → warframestat）相关测试。"""

import json
import time
import unittest

from bot import worldfallback
from bot.worldstate import WorldStateClient


def ms(seconds):
    return {"$date": {"$numberLong": str(int(seconds * 1000))}}


NODES = {
    "CrewBattleNode522": {"name": "Bendar Cluster (Earth)",
                          "enemy": "Grineer", "type": "Skirmish"},
    "CrewBattleNode529": {"name": "Profit Margin (Pluto)",
                          "enemy": "Corpus", "type": "Volatile"},
}


class VoidStormTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.data = {
            "VoidStorms": [
                {"_id": {"$oid": "a1"}, "Node": "CrewBattleNode522",
                 "Activation": ms(self.now - 600),
                 "Expiry": ms(self.now + 600),
                 "ActiveMissionTier": "VoidT1"},
                {"_id": {"$oid": "a2"}, "Node": "CrewBattleNode529",
                 "Activation": ms(self.now - 600),
                 "Expiry": ms(self.now + 900),
                 "ActiveMissionTier": "VoidT4"},
                # 已过期，应被过滤
                {"_id": {"$oid": "a3"}, "Node": "CrewBattleNode522",
                 "Activation": ms(self.now - 900),
                 "Expiry": ms(self.now - 60),
                 "ActiveMissionTier": "VoidT1"},
            ],
        }

    def test_maps_nodes_and_tiers(self):
        rows = worldfallback.official_void_storms(self.data, NODES)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tier"], "Lith")
        self.assertEqual(rows[0]["missionType"], "Skirmish")
        self.assertTrue(rows[0]["isStorm"])
        self.assertEqual(rows[1]["tier"], "Axi")
        self.assertEqual(rows[1]["missionType"], "Volatile")

    def test_enemy_kept_in_english(self):
        rows = worldfallback.official_void_storms(self.data, NODES)
        self.assertEqual(rows[0]["enemy"], "Grineer")

    def test_sorted_by_tier(self):
        rows = worldfallback.official_void_storms(self.data, NODES)
        self.assertEqual([row["tierNum"] for row in rows], [1, 4])

    def test_missing_field_is_safe(self):
        self.assertEqual(worldfallback.official_void_storms({}, NODES), [])


class NightwaveTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.source = worldfallback.OfficialWorldState(http=None, nodes=NODES)
        self.data = {"SeasonInfo": {
            "Season": 18, "Phase": 0,
            "AffiliationTag": "RadioLegionIntermission16Syndicate",
            "Expiry": ms(self.now + 86400),
            "ActiveChallenges": [
                {"_id": {"$oid": "1"}, "Daily": True,
                 "Expiry": ms(self.now + 3600),
                 "Challenge": "/Lotus/Types/Challenges/Seasons/Daily/SeasonDailyTwoForOne"},
                {"_id": {"$oid": "2"}, "Daily": False,
                 "Expiry": ms(self.now + 7200),
                 "Challenge": "/Lotus/Types/Challenges/Seasons/Weekly/SeasonWeeklyCompleteSortie"},
                # 已过期
                {"_id": {"$oid": "3"}, "Daily": True,
                 "Expiry": ms(self.now - 60),
                 "Challenge": "/Lotus/Types/Challenges/Seasons/Daily/SeasonDailyOld"},
            ],
        }}

    def test_season_and_challenges(self):
        nw = self.source._nightwave(self.data)
        self.assertEqual(nw["season"], 18)
        self.assertEqual(len(nw["activeChallenges"]), 2)

    def test_titles_humanized(self):
        nw = self.source._nightwave(self.data)
        titles = [row["title"] for row in nw["activeChallenges"]]
        self.assertEqual(titles, ["Two For One", "Complete Sortie"])

    def test_daily_first(self):
        nw = self.source._nightwave(self.data)
        self.assertTrue(nw["activeChallenges"][0]["isDaily"])
        self.assertFalse(nw["activeChallenges"][1]["isDaily"])

    def test_no_season_is_none(self):
        self.assertIsNone(self.source._nightwave({}))


class RelayTests(unittest.TestCase):
    def make(self, text="", relay="https://r.jina.ai/"):
        client = WorldStateClient({
            "base_url": "https://api.warframestat.us",
            "language": "zh",
            "relay": relay,
            "official_fallback": True,
        })

        class FakeHttp:
            def get_text(self, url, headers=None):
                return text

            def get_json(self, url, headers=None):
                return {}

        client.http = FakeHttp()
        return client

    def test_strips_markdown_wrapper(self):
        raw = ('Title: \n\nURL Source: https://api.warframestat.us/pc/zh\n\n'
               'Markdown Content:\n{"timestamp":"2026-10-26T12:29:19.000Z"}')
        client = self.make(raw)
        data = client._fetch_json("https://api.warframestat.us/pc/zh")
        self.assertEqual(data["timestamp"], "2026-10-26T12:29:19.000Z")

    def test_plain_json_passthrough(self):
        client = self.make('{"ok": true}')
        self.assertEqual(client._fetch_json("x")["ok"], True)

    def test_non_json_raises(self):
        client = self.make("rate limited, try again later")
        with self.assertRaises(Exception):
            client._fetch_json("x")

    def test_no_relay_uses_get_json(self):
        client = self.make("", relay="")
        self.assertEqual(client._fetch_json("x"), {})

    def test_merge_fills_missing_only(self):
        client = self.make("")
        client._supplement = lambda: {
            "steelPath": {"currentReward": {"name": "Umbra Forma"}},
            "sortie": {"boss": "来自中转"},
        }
        state = {"steelPath": None, "sortie": {"boss": "官方"}}
        client._merge_supplement(state)
        self.assertEqual(state["steelPath"]["currentReward"]["name"], "Umbra Forma")
        self.assertEqual(state["sortie"]["boss"], "官方")

    def test_merge_prefers_titled_nightwave(self):
        client = self.make("")
        client._supplement = lambda: {
            "nightwave": {"season": 18, "activeChallenges": [
                {"title": "正式标题", "reputation": 4500}]}}
        state = {"nightwave": {"season": 18, "activeChallenges": [
            {"title": "Two For One", "reputation": 0}]}}
        client._merge_supplement(state)
        self.assertEqual(state["nightwave"]["activeChallenges"][0]["title"], "正式标题")

    def test_merge_fills_storms_from_relay(self):
        client = self.make("")
        client._supplement = lambda: {"fissures": [
            {"node": "A", "isStorm": True}, {"node": "B", "isStorm": False}]}
        state = {"voidStorms": []}
        client._merge_supplement(state)
        self.assertEqual(len(state["voidStorms"]), 1)


if __name__ == "__main__":
    unittest.main()
