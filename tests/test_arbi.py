import json
import logging
import unittest

from bot.arbi import ArbiClient, parse_data

TIERS = {
    "schema": 1,
    "tiers": ["S", "A+", "A", "A-", "B", "C", "未评级"],
    "tierBuckets": {
        "S": ["SolNode1"],
        "A+": ["SolNode2"],
        "A": [],
        "A-": [],
        "B": [],
        "C": ["SolNode3"],
        "未评级": ["SolNode4"],
    },
}

NODES = {
    "schema": 1,
    "nodes": {
        "SolNode1": {"nodeKey": "SolNode1", "nameZh": "Callisto",
                     "systemNameZh": "木星", "missionNameZh": "拦截",
                     "factionNameZh": "Corpus", "minEnemyLevel": 15,
                     "maxEnemyLevel": 20, "missionType": "MT_TERRITORY"},
        "SolNode2": {"nodeKey": "SolNode2", "nameZh": "Everest",
                     "systemNameZh": "地球", "missionNameZh": "挖掘",
                     "factionNameZh": "Grineer", "minEnemyLevel": 1,
                     "maxEnemyLevel": 6, "missionType": "MT_EXCAVATE"},
        "SolNode3": {"nodeKey": "SolNode3", "nameZh": "Rhea",
                     "systemNameZh": "土星", "missionNameZh": "拦截",
                     "factionNameZh": "Grineer", "minEnemyLevel": 21,
                     "maxEnemyLevel": 26, "missionType": "MT_TERRITORY"},
        "SolNode4": {"nodeKey": "SolNode4", "nameZh": "Mithra",
                     "systemNameZh": "虚空", "missionNameZh": "生存",
                     "factionNameZh": "Orokin", "minEnemyLevel": 40,
                     "maxEnemyLevel": 45, "missionType": "MT_SURVIVAL"},
    },
}

SCHEDULE = {
    "schema": 2,
    "startTs": 0,
    "stepSec": 3600,
    "nodes": ["SolNode1", "SolNode2", "SolNode4"],
    "seq": [0, 1, 2],
}


class FakeHttp:
    def __init__(self):
        self.calls = []

    def get_json(self, url, headers=None):
        self.calls.append(url)
        if url.endswith("tierlist.default.json"):
            return TIERS
        if url.endswith("arbys.nodes.zh.json"):
            return NODES
        if url.endswith("arbys.schedule.v2.json"):
            return SCHEDULE
        raise AssertionError(url)


class ParseTest(unittest.TestCase):
    def test_parse(self):
        data = parse_data(TIERS, NODES)
        self.assertEqual(len(data.nodes), 4)
        self.assertEqual(data.rated_count, 3)
        self.assertEqual(data.rating("SolNode1"), "S")
        # 社区梯队没收录的节点，按任务类型 + 等级本地推算
        self.assertEqual(data.resolve("SolNode4"), ("S", "local"))
        data.use_local = False
        self.assertEqual(data.rating("SolNode4"), "未评级")
        self.assertEqual([tier for tier, _ in data.groups()],
                         ["S", "A+", "C"])

    def test_display(self):
        data = parse_data(TIERS, NODES)
        node = data.nodes["SolNode2"]
        self.assertEqual(node.display_name, "Everest（地球）")
        self.assertEqual(node.level_text, "1 - 6")


class ClientTest(unittest.TestCase):
    def make_client(self):
        client = ArbiClient({
            "base_url": "https://arbi.wf.wiki",
            "proxy": "none",
            "cache_seconds": 3600,
        }, logger=logging.getLogger("test.arbi"))
        client.http = FakeHttp()
        return client

    def test_data_and_cache(self):
        client = self.make_client()
        data = client.data()
        self.assertEqual(data.rated_count, 3)
        calls = len(client.http.calls)
        client.data()
        self.assertEqual(len(client.http.calls), calls)

    def test_current_node(self):
        client = self.make_client()
        node, expiry = client.current_node(now=3600)
        self.assertEqual(node.key, "SolNode2")
        self.assertEqual(expiry, 7200.0)

    def test_schedule_entries(self):
        client = self.make_client()
        entries = client.schedule_entries(0, 10800)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0][2].key, "SolNode1")
        self.assertEqual(entries[0][3], "S")

    def test_effective(self):
        client = self.make_client()
        entries = client.effective(now=3600, hours=3)
        # SolNode4 现在有本地推算评级（生存），也会出现在高效仲裁里
        self.assertEqual([entry[3] for entry in entries], ["S", "S", "A+"])

    def test_find_node(self):
        client = self.make_client()
        self.assertEqual(client.find_node("Callisto（木星）").key, "SolNode1")
        self.assertEqual(client.find_node("Everest (Earth)").key, "SolNode2")
        self.assertIsNone(client.find_node("不存在"))

    def test_rating(self):
        client = self.make_client()
        self.assertEqual(client.rating("SolNode1"), "S")
        self.assertEqual(client.rating_source("SolNode1"), "community")
        self.assertEqual(client.rating("SolNode4"), "S")
        self.assertEqual(client.rating_source("SolNode4"), "local")
        self.assertEqual(client.rating_label("SolNode4"), "S（本地推算）")

    def test_refresh(self):
        client = self.make_client()
        self.assertEqual(client.refresh(), 3)
        self.assertGreater(client.cache_age(), -1)


if __name__ == "__main__":
    unittest.main()
