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
        """无尽回廊的战甲写玩家别名 + 英文，武器写国服中文名。"""
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
        for text in ("守望者", "史特克", "布里斯提卡", "技巧之剑", "奥比克斯"):
            self.assertIn(text, reply.text)
        # 战甲：别名（English），两者同时出现
        self.assertIn("女枪（Mesa）", reply.text)
        self.assertIn("龙骑（Chroma）", reply.text)
        self.assertIn("土甲（Atlas）", reply.text)
        # 武器沿用「中文名（English）」，方便对照 wiki / 交易站
        self.assertIn("守望者（Vectis）", reply.text)

    def test_circuit_alias_fallback(self):
        """没收录别名的战甲退回国服译名，不编造别名。"""
        reply = present.circuit_reply({
            "choices": [{"category": "normal", "choices": ["Caliban", "Mesa Prime"]}]})
        self.assertIn("卡利班（Caliban）", reply.text)
        self.assertIn("女枪P（Mesa Prime）", reply.text)

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


class BountyStaticTest(unittest.TestCase):
    """官方 Jobs 为空的三处据点：按固定档位出表，不再写「暂无可接赏金」。"""

    RAW = [
        {"syndicate": "Cavia", "jobs": [], "expiry": "2030-01-01T00:00:00.000Z"},
        {"syndicate": "The Hex", "jobs": [], "expiry": "2030-01-01T00:00:00.000Z"},
        {"syndicate": "The Holdfasts", "jobs": [],
         "expiry": "2030-01-01T00:00:00.000Z"},
    ]

    def test_tiers_show_up_instead_of_placeholder(self):
        reply = present.bounties_reply(list(self.RAW))
        self.assertNotIn("暂无可接赏金", reply.text)
        self.assertIn("坚守者 · 扎里曼", reply.text)
        self.assertIn("六人组 · 霍瓦尼亚", reply.text)
        self.assertIn("卡维亚 · 解剖圣所", reply.text)

    def test_tier_levels_and_counts(self):
        reply = present.bounties_reply(list(self.RAW))
        lines = [line for line in reply.text.splitlines() if "T" in line]
        # 5（扎里曼）+ 7（霍瓦尼亚）+ 5（圣所）= 17 档
        self.assertEqual(len(lines), 17)
        self.assertIn("130", reply.text)          # 霍瓦尼亚 T7 125–130
        self.assertIn("115–120", reply.text)      # 圣所 T5
        self.assertIn("虚空羽翎", reply.text)      # 扎里曼奖励

    def test_single_location(self):
        reply = present.bounties_reply(list(self.RAW), location="The Hex")
        self.assertIn("霍瓦尼亚 赏金", reply.text)
        self.assertNotIn("扎里曼", reply.text)
        self.assertNotIn("解剖圣所", reply.text)
        # 单据点卡把任务类型写进底部说明
        self.assertIn("地狱净化", reply.text)

    def test_static_table_matches_wiki(self):
        from bot import bountystatic
        self.assertEqual(len(bountystatic.tiers("The Holdfasts")), 5)
        self.assertEqual(len(bountystatic.tiers("Cavia")), 5)
        self.assertEqual(len(bountystatic.tiers("The Hex")), 7)
        self.assertEqual(bountystatic.tiers("Cavia")[0]["level"], "55–60")
        self.assertEqual(bountystatic.tiers("The Holdfasts")[-1]["level"], "110–115")
        self.assertEqual(bountystatic.tiers("The Hex")[-1]["reward"],
                         "7500 现金 + 反病毒模组")
        self.assertEqual(bountystatic.tiers("Ostrons"), [])


if __name__ == "__main__":
    unittest.main()
