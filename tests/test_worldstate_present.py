import unittest

from bot import present


class WorldPresentTest(unittest.TestCase):
    def test_alerts(self):
        raw = [{
            "expiry": "2030-01-01T00:00:00.000Z",
            "mission": {
                "node": "Nimus (Eris)", "type": "Hive",
                "faction": "Infested",
                "reward": {"credits": 17000,
                           "countedItems": [{"type": "Fieldron", "count": 3}]},
            },
        }]
        reply = present.alerts_reply(raw)
        self.assertIn("世界警报", reply.text)
        self.assertIn("Nimus", reply.text)
        self.assertIn("3xFieldron", reply.text)

    def test_cycles(self):
        reply = present.cycles_reply(
            {"state": "day", "timeLeft": "5m"},
            {"state": "warm", "timeLeft": "—"},
            {"state": "fass", "timeLeft": "1m"})
        self.assertIn("白天", reply.text)
        self.assertIn("温暖", reply.text)
        self.assertIn("法斯", reply.text)

    def test_summary(self):
        reply = present.worldstate_reply({
            "alerts": [1, 2, 3],
            "invasions": [1],
            "fissures": [1, 2],
            "news": [1],
            "events": [],
            "dailyDeals": [],
            "voidTrader": {"location": "Strata", "expiry": 0},
            "sortie": {"boss": "Alad V", "expiry": 0},
            "arbitration": {"node": "暂无有效数据", "expiry": 0},
            "steelPath": {"currentReward": {"name": "Endo"}, "incursions": {}},
            "nightwave": {"season": 18, "activeChallenges": [1, 2]},
            "cetusCycle": {"state": "day", "timeLeft": "5m"},
            "duviriCycle": {"state": "fear", "expiry": 0},
            "archonHunt": {"boss": "Amar", "expiry": 0},
        })
        self.assertIn("世界状态", reply.text)
        self.assertIn("警报 / 3 条", reply.text)
        self.assertIn("平原 / 白天", reply.text)


if __name__ == "__main__":
    unittest.main()
