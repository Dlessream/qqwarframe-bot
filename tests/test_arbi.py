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

    def test_upcoming(self):
        client = self.make_client()
        week = client.upcoming(7, now=3600)
        # 7 天 × 24 场，含当前这一场
        self.assertEqual(len(week), 168)
        self.assertEqual(week[0][0], 3600.0)
        self.assertEqual(week[-1][0], 3600.0 + 167 * 3600)
        # 天数会被夹到 1 - 7
        self.assertEqual(len(client.upcoming(0, now=3600)), 24)
        self.assertEqual(len(client.upcoming(99, now=3600)), 168)
        self.assertEqual(len(client.upcoming("3", now=3600)), 72)
        self.assertEqual(len(client.upcoming("abc", now=3600)), 168)


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


class FilterTest(unittest.TestCase):
    """谷拦 / 镜防 / 高效三个筛选方法。"""

    def make_client(self):
        nodes = {
            "ceres": {"nameZh": "Cinxia", "systemNameZh": "谷神星",
                      "missionNameZh": "拦截", "missionType": "MT_TERRITORY"},
            "mars": {"nameZh": "Tyana Pass", "systemNameZh": "火星",
                     "missionNameZh": "镜像防御", "missionType": "MT_DEFENSE"},
            "deimos": {"nameZh": "墓垒", "systemNameZh": "火卫二",
                       "missionNameZh": "镜像防御", "missionType": "MT_DEFENSE"},
            "earth": {"nameZh": "Everest", "systemNameZh": "地球",
                      "missionNameZh": "挖掘", "missionType": "MT_EXCAVATE"},
            "void": {"nameZh": "Mithra", "systemNameZh": "虚空",
                     "missionNameZh": "生存", "missionType": "MT_SURVIVAL"},
        }
        tiers = {"tiers": ["S", "A+", "A", "A-", "B", "C", "未评级"],
                 "tierBuckets": {"S": ["ceres", "void"], "A+": ["mars", "deimos"],
                                 "A": [], "A-": [], "B": [], "C": ["earth"],
                                 "未评级": []}}
        schedule = {"startTs": 0, "stepSec": 3600,
                    "nodes": ["ceres", "mars", "deimos", "earth", "void"],
                    "seq": [0, 1, 2, 3, 4]}

        class Http:
            def get_json(self, url, headers=None):
                if url.endswith("tierlist.default.json"):
                    return tiers
                if url.endswith("arbys.nodes.zh.json"):
                    return {"nodes": nodes}
                if url.endswith("arbys.schedule.v2.json"):
                    return schedule
                raise AssertionError(url)

        client = ArbiClient({"base_url": "https://arbi.wf.wiki",
                             "proxy": "none", "cache_seconds": 3600},
                            logger=logging.getLogger("test.arbi.filter"))
        client.http = Http()
        return client

    def test_ceres_intercept(self):
        entries = self.make_client().filtered("ceres_intercept", days=1, now=0)
        self.assertTrue(entries)
        self.assertTrue(all(entry[2].system == "谷神星"
                            and entry[2].mission == "拦截" for entry in entries))

    def test_mirror_defense(self):
        entries = self.make_client().filtered("mirror_defense", days=1, now=0)
        self.assertTrue(entries)
        self.assertEqual(set(entry[2].system for entry in entries),
                         {"火星", "火卫二"})
        self.assertTrue(all(entry[2].mission == "镜像防御" for entry in entries))

    def test_effective_only_s_ap(self):
        entries = self.make_client().filtered("effective", days=1, now=0)
        self.assertTrue(entries)
        self.assertTrue(all(entry[3] in ("S", "A+") for entry in entries))
        # 轮换表 5 个节点里 earth 是 C，其余 4 个是 S/A+，按比例被筛掉
        self.assertEqual(len(entries), 19)

    def test_unknown_kind_is_empty(self):
        self.assertEqual(
            self.make_client().filtered("nope", days=1, now=0), [])


if __name__ == "__main__":
    unittest.main()
