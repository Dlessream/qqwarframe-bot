"""仲裁本地评级 + 人工评级 + 今日 24 小时窗口。"""

import json
import os
import tempfile
import unittest

from bot import arbirate
from bot.arbi import ArbiClient, ArbiData, ArbiNode, parse_data

NODES = {
    "nodes": {
        "SolNode1": {"nameZh": "Callisto", "systemNameZh": "木星",
                     "missionNameZh": "拦截", "factionNameZh": "Corpus",
                     "minEnemyLevel": 15, "maxEnemyLevel": 20,
                     "missionType": "MT_TERRITORY"},
        "SolNode2": {"nameZh": "Membrain", "systemNameZh": "土星",
                     "missionNameZh": "叛逃", "factionNameZh": "Grineer",
                     "minEnemyLevel": 8, "maxEnemyLevel": 18,
                     "missionType": "MT_EVACUATION"},
        "SolNode3": {"nameZh": "Amarna", "systemNameZh": "赛德娜",
                     "missionNameZh": "生存", "factionNameZh": "Grineer",
                     "minEnemyLevel": 40, "maxEnemyLevel": 45,
                     "missionType": "MT_SURVIVAL"},
    },
}

TIERS = {"tiers": ["S", "A+", "A", "A-", "B", "C", "未评级"],
         "tierBuckets": {"S": ["SolNode1"], "未评级": ["SolNode2", "SolNode3"]}}


def node(key="k", mission_type="MT_SURVIVAL", max_level=45, mission="生存"):
    return ArbiNode(key=key, name="N", system="地球", mission=mission,
                    faction="Grineer", min_level=1, max_level=max_level,
                    mission_type=mission_type)


class LocalRatingTest(unittest.TestCase):
    def test_survival_is_top(self):
        self.assertGreater(arbirate.score(node("a", "MT_SURVIVAL")),
                           arbirate.score(node("b", "MT_EVACUATION")))

    def test_territory_ranks_high(self):
        # 社区梯队里拦截多是 S/A，本地推算不能把它压到 B
        self.assertIn(arbirate.local_rating(node("c", "MT_TERRITORY")),
                      ("S", "A+"))

    def test_level_bonus_breaks_ties(self):
        high = arbirate.score(node("d", "MT_DEFENSE", 45))
        low = arbirate.score(node("e", "MT_DEFENSE", 12))
        self.assertGreater(high, low)
        self.assertNotEqual(arbirate.local_rating(node("f", "MT_DEFENSE", 45)),
                            arbirate.local_rating(node("g", "MT_DEFENSE", 12)))

    def test_unknown_mission_type_still_rated(self):
        self.assertIn(arbirate.local_rating(node("h", "MT_UNKNOWN")),
                      arbirate.TIER_ORDER)

    def test_none_node(self):
        self.assertEqual(arbirate.local_rating(None), "")

    def test_normalize_tier(self):
        self.assertEqual(arbirate.normalize_tier("s"), "S")
        self.assertEqual(arbirate.normalize_tier("a+"), "A+")
        self.assertEqual(arbirate.normalize_tier("A－"), "A-")
        self.assertEqual(arbirate.normalize_tier("清除"), "")
        self.assertEqual(arbirate.normalize_tier("瞎写"), "")


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.data = parse_data(TIERS, NODES)

    def test_priority(self):
        # 社区梯队优先
        self.assertEqual(self.data.resolve("SolNode1"), ("S", "community"))
        # 没有社区梯队时用本地推算
        self.assertEqual(self.data.resolve("SolNode3"), ("S", "local"))
        # 人工评级插在中间
        self.data.overrides = {"SolNode3": "C", "SolNode2": "B"}
        self.assertEqual(self.data.resolve("SolNode3"), ("C", "manual"))
        self.assertEqual(self.data.resolve("SolNode1"), ("S", "community"))
        self.assertEqual(self.data.rating_label("SolNode2"), "B（人工评级）")

    def test_counts(self):
        self.data.overrides = {"SolNode2": "B"}
        counts = self.data.counts()
        self.assertEqual(counts["community"], 1)
        self.assertEqual(counts["manual"], 1)
        self.assertEqual(counts["local"], 1)
        self.assertEqual(counts["none"], 0)

    def test_groups_exclude_local(self):
        # 梯队总表只放社区 / 人工，本地推算单独一组
        self.assertEqual([tier for tier, _ in self.data.groups()], ["S"])
        local = {node.key for _tier, nodes in self.data.local_groups()
                 for node in nodes}
        self.assertEqual(local, {"SolNode2", "SolNode3"})

    def test_disable_local(self):
        self.data.use_local = False
        self.assertEqual(self.data.rating("SolNode3"), "未评级")
        self.assertEqual(self.data.resolve("SolNode3"), ("", ""))

    def test_nodes_without_mission_type(self):
        data = ArbiData(nodes={"X": ArbiNode("X", "N", "地球", "未知", "")},
                        tiers={}, order=[])
        self.assertEqual(data.rating("X"), "C")


class OverridesFileTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="arbi_")
        self.path = os.path.join(self.folder, "ratings.json")

    def test_round_trip(self):
        arbirate.save_overrides(self.path, {"SolNode1": "S", "SolNode2": ""})
        with open(self.path, encoding="utf-8") as handle:
            raw = json.load(handle)
        self.assertEqual(raw, {"SolNode1": "S"})
        self.assertEqual(arbirate.load_overrides(self.path), {"SolNode1": "S"})

    def test_broken_file(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(arbirate.load_overrides(self.path), {})

    def test_missing_file(self):
        self.assertEqual(arbirate.load_overrides(
            os.path.join(self.folder, "nope.json")), {})


class ScheduleTest(unittest.TestCase):
    def make_client(self, schedule):
        client = ArbiClient({"proxy": "", "ratings_file": ""})
        client._get_json = lambda path: {
            "/data/tierlist.default.json": TIERS,
            "/data/arbys.nodes.zh.json": NODES,
            "/data/arbys.schedule.v2.json": schedule,
        }[path]
        return client

    def test_today_is_rolling_24h(self):
        schedule = {"startTs": 0, "stepSec": 3600,
                    "nodes": ["SolNode1", "SolNode2"], "seq": [0, 1]}
        client = self.make_client(schedule)
        now = 100 * 3600 + 1800        # 落在第 100 格中间
        entries = client.today(now)
        # 当前这一场算在内，往后铺满 24 小时（首尾各可能是半场）
        self.assertEqual(entries[0][0], 100 * 3600)
        self.assertIn(len(entries), (24, 25))
        self.assertLess(entries[-1][0], now + 86400)
        # 已经过时（结束时间早于现在）的场次不再出现
        self.assertTrue(all(end > now for _start, end, _node, _tier in entries))

    def test_today_skips_expired(self):
        schedule = {"startTs": 0, "stepSec": 3600,
                    "nodes": ["SolNode1"], "seq": [0]}
        client = self.make_client(schedule)
        now = 50 * 3600 + 3599
        entries = client.today(now)
        self.assertEqual(entries[0][0], 50 * 3600)
        self.assertTrue(all(end > now for _start, end, _node, _tier in entries))
        # 恰好跨过整点的那一场不会被算成"已过时"
        self.assertEqual(entries[0][1], 51 * 3600)

    def test_effective_uses_local_rating(self):
        schedule = {"startTs": 0, "stepSec": 3600,
                    "nodes": ["SolNode3"], "seq": [0]}
        client = self.make_client(schedule)
        entries = client.effective(now=10 * 3600, hours=1)
        self.assertEqual([entry[3] for entry in entries], ["S"])


class SnapshotTest(unittest.TestCase):
    def test_summarize(self):
        data = parse_data(TIERS, NODES)
        data.overrides = {"SolNode2": "A"}
        rows = arbirate.summarize(list(data.nodes.values()), data.overrides,
                                  data.tiers)
        by_key = {row["key"]: row for row in rows}
        self.assertEqual(by_key["SolNode1"]["source"], "community")
        self.assertEqual(by_key["SolNode2"]["source"], "manual")
        self.assertEqual(by_key["SolNode3"]["source"], "local")
        self.assertEqual(rows[0]["tier"], "S")


if __name__ == "__main__":
    unittest.main()
