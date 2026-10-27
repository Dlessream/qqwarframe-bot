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
        self.assertIn("3 个 Fieldron", reply.text)

    def test_cycles(self):
        reply = present.cycles_reply(
            {"state": "day", "timeLeft": "5m"},
            {"state": "warm", "timeLeft": "—"},
            {"state": "fass", "timeLeft": "1m"})
        self.assertIn("白天", reply.text)
        self.assertIn("温暖", reply.text)
        self.assertIn("法斯", reply.text)

    def test_cetus_tents(self):
        raw = [{
            "syndicate": "Ostrons",
            "jobs": [
                {"type": "Low", "enemyLevels": [5, 15], "expiry": "2030-01-01T00:00:00Z"},
                {"type": "Second", "enemyLevels": [10, 30], "expiry": "2030-01-01T00:00:00Z"},
                {"type": "Mid", "enemyLevels": [20, 40], "expiry": "2030-01-01T00:00:00Z"},
            ],
        }]
        reply = present.cetus_reply(
            {"state": "day", "timeLeft": "1h 20m 0s"}, raw)
        self.assertIn("希图斯赏金", reply.text)
        self.assertIn("白天", reply.text)
        self.assertIn("赏金一 / Low", reply.text)
        self.assertIn("赏金二 / Second", reply.text)
        self.assertIn("帐篷 A", reply.text)
        self.assertIn("间谍捕手", reply.text)
        self.assertIn("帐篷 C", reply.text)

    def test_storms(self):
        raw = [
            {"isStorm": True, "tier": "Lith", "tierNum": 1,
             "missionType": "Skirmish", "node": "Bendar Cluster (Earth)",
             "enemy": "Grineer", "expiry": "2030-01-01T00:00:00Z"},
            {"isStorm": True, "tier": "Meso", "tierNum": 2,
             "missionType": "Volatile", "node": "Beacon Shield Ring (Venus)",
             "enemy": "Corpus", "expiry": "2030-01-01T00:00:00Z"},
            {"isStorm": False, "tier": "Neo", "tierNum": 3,
             "missionType": "Survival", "node": "Ani (Void)",
             "enemy": "Corrupted", "expiry": "2030-01-01T00:00:00Z"},
        ]
        reply = present.storms_reply(raw)
        self.assertIn("虚空风暴", reply.text)
        self.assertIn("前哨战", reply.text)
        self.assertIn("地球比邻星域 - 本达尔星团", reply.text)
        self.assertIn("G系", reply.text)
        self.assertIn("古纪", reply.text)
        self.assertNotIn("Ani", reply.text)

    def test_circuit_shows_chinese_names(self):
        """无尽回廊的可选战甲/武器必须显示国服中文名（截图里全是英文的那次反馈）。"""
        reply = present.circuit_reply({
            "state": "joy",
            "choices": [
                {"category": "normal", "choices": ["Mesa", "Chroma", "Atlas"]},
                {"category": "hard",
                 "choices": ["Vectis", "Stug", "Ballistica", "Destreza", "Obex"]},
            ],
        })
        self.assertIn("普通 · 战甲", reply.text)
        self.assertIn("钢铁 · 武器", reply.text)
        for text in ("圣枪弥撒", "怒炎龙骑", "撼天刑者",
                     "守望者", "史特克", "布里斯提卡", "技巧之剑", "奥比克斯"):
            self.assertIn(text, reply.text)
        # 文字兜底带上英文原名，方便对照 wiki / 交易站
        self.assertIn("圣枪弥撒（Mesa）", reply.text)
        self.assertIn("守望者（Vectis）", reply.text)

    def test_circuit_keeps_unknown_names(self):
        reply = present.circuit_reply({
            "choices": [{"category": "normal", "choices": ["Tennocon 2026 Frame"]}]})
        self.assertIn("Tennocon 2026 Frame", reply.text)

    def test_summary(self):
        reply = present.worldstate_reply({
            "alerts": [1, 2, 3],            "invasions": [1],
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
