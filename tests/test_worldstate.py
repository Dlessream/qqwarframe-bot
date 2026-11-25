import time
import unittest

from bot.worldstate import (Arbitration, WorldStateClient, WorldStateError,
                            countdown, format_duration, parse_timestamp)


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, headers=None):
        self.calls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def make_client(payload):
    client = WorldStateClient({"base_url": "http://example.invalid", "language": "zh"})
    client.http = FakeHttp(payload)
    return client


def future(seconds):
    import datetime
    return (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)) \
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")


class TimeTest(unittest.TestCase):
    def test_parse_timestamp(self):
        self.assertGreater(parse_timestamp("2026-10-24T13:00:00.000Z"), 0)
        self.assertEqual(parse_timestamp(""), 0)
        self.assertEqual(parse_timestamp("bad"), 0)

    def test_format_duration(self):
        self.assertEqual(format_duration(90), "1 分")
        self.assertEqual(format_duration(3700), "1 小时 1 分")
        self.assertEqual(format_duration(90000), "1 天 1 小时")

    def test_countdown(self):
        self.assertEqual(countdown(0), "未知")
        self.assertEqual(countdown(time.time() - 10), "已结束")
        self.assertIn("小时", countdown(time.time() + 3700))


class VoidTraderTest(unittest.TestCase):
    def test_active_with_inventory(self):
        payload = {
            "character": "Baro Ki'Teer",
            "location": "Orcus Relay (Pluto)",
            "activation": future(-3600),
            "expiry": future(3600),
            "inventory": [
                {"item": "Primed Continuity", "ducats": 350, "credits": 100000, "count": 1},
                {"item": "Orokin Catalyst", "ducats": 0, "credits": 75000, "count": 2},
            ],
        }
        trader = make_client(payload).void_trader()
        self.assertTrue(trader.active)
        self.assertEqual(trader.location, "奥库斯中继站（冥王星）")
        self.assertEqual(len(trader.inventory), 2)
        self.assertEqual(trader.inventory[0].ducats, 350)

    def test_inactive(self):
        payload = {
            "activation": future(3600),
            "expiry": future(7200),
            "inventory": [],
        }
        trader = make_client(payload).void_trader()
        self.assertFalse(trader.active)
        self.assertEqual(trader.inventory, [])


class SortieTest(unittest.TestCase):
    def test_parse(self):
        payload = {
            "boss": "Sargas Ruk",
            "faction": "Grineer",
            "expiry": future(3600),
            "variants": [
                {"missionType": "Extermination", "node": "Taveuni (Kuva Fortress)",
                 "modifierDescription": "敌人有额外护盾"},
                {"missionType": "Survival", "node": "Piscinas (Saturn)",
                 "modifier": "Energy Drain"},
            ],
        }
        sortie = make_client(payload).sortie()
        self.assertEqual(sortie.boss, "萨加斯·鲁克")
        self.assertEqual(len(sortie.variants), 2)
        self.assertEqual(sortie.variants[0].mission, "歼灭")
        self.assertEqual(sortie.variants[0].node, "Taveuni（赤毒要塞）")
        self.assertEqual(sortie.variants[0].modifier, "敌人有额外护盾")


class ArbitrationTest(unittest.TestCase):
    def test_parse(self):
        payload = {"node": "Mithra (Void)", "type": "Defense",
                   "enemy": "Corpus", "expiry": future(600)}
        arbitration = make_client(payload).arbitration()
        # 官方中文客户端里 Mithra 已汉化为「光理塔」，卡片与游戏内保持一致
        self.assertEqual(arbitration.node, "光理塔（虚空）")
        self.assertEqual(arbitration.mission, "防御")
        self.assertEqual(arbitration.enemy, "科普斯")


class ErrorTest(unittest.TestCase):
    def test_http_error_wrapped(self):
        from bot.net import HttpError
        client = make_client(HttpError("boom", status=403))
        with self.assertRaises(WorldStateError) as ctx:
            client.void_trader()
        self.assertIn("Cloudflare", str(ctx.exception))


class OfficialFissureTest(unittest.TestCase):
    def test_official_active_missions(self):
        expiry = int((time.time() + 600) * 1000)
        client = WorldStateClient({
            "base_url": "http://example.invalid",
            "language": "zh",
            "official_proxy": "none",
        })
        client.official_http = FakeHttp({"ActiveMissions": [{
            "Node": "SolNode128",
            "MissionType": "MT_EXTERMINATION",
            "Modifier": "VoidT1",
            "Hard": True,
            "Expiry": {"$date": {"$numberLong": str(expiry)}},
        }]})
        rows = client.official_fissures()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node"], "E Gate（金星）")
        self.assertEqual(rows[0]["missionType"], "Extermination")
        self.assertEqual(rows[0]["tier"], "Lith")
        self.assertTrue(rows[0]["isHard"])

    def _fissure_client(self, node, modifier="VoidT1"):
        expiry = int((time.time() + 600) * 1000)
        client = WorldStateClient({
            "base_url": "http://example.invalid",
            "language": "zh",
            "official_proxy": "none",
        })
        client.official_http = FakeHttp({"ActiveMissions": [{
            "Node": node,
            "MissionType": "MT_EXTERMINATION",
            "Modifier": modifier,
            "Expiry": {"$date": {"$numberLong": str(expiry)}},
        }]})
        return client

    def test_fissure_enemy_is_corrupted(self):
        """裂缝会腐化敌人，游戏里显示的敌人恒为堕落者。"""
        row = self._fissure_client("SolNode128").official_fissures()[0]
        self.assertEqual(row["enemy"], "Corrupted")
        # 场景贴图仍要按节点原生阵营选，所以原生阵营单独保留
        self.assertEqual(row["nodeEnemy"], "Corpus")

    def test_omnia_deimos_keeps_murmur(self):
        """火卫二的 Omnia 裂缝以原生低语者为主。"""
        row = self._fissure_client("SolNode717", "VoidT6").official_fissures()[0]
        self.assertEqual(row["tier"], "Omnia")
        self.assertEqual(row["enemy"], "The Murmur")

    def test_omnia_zariman_is_corrupted(self):
        row = self._fissure_client("SolNode230", "VoidT6").official_fissures()[0]
        self.assertEqual(row["enemy"], "Corrupted")

    def test_void_storms_use_official_channel(self):
        """九重天单独走官方 VoidStorms，不依赖整包兜底快照。"""
        expiry = int((time.time() + 600) * 1000)
        client = WorldStateClient({
            "base_url": "http://example.invalid",
            "language": "zh",
            "official_proxy": "none",
        })
        client.official_http = FakeHttp({"VoidStorms": [{
            "Node": "CrewBattleNode519",
            "ActiveMissionTier": "VoidT1",
            "Expiry": {"$date": {"$numberLong": str(expiry)}},
        }]})
        rows = client.void_storms()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node"], "Korm's Belt (Earth)")
        self.assertEqual(rows[0]["tier"], "Lith")
        self.assertTrue(rows[0]["isStorm"])


if __name__ == "__main__":
    unittest.main()
