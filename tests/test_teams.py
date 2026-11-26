"""组队服务：认证、满员、任务到场与视图。"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bot import teams as team_mod  # noqa: E402


class Node:
    def __init__(self, key, name, system="海王星", mission="防御",
                 faction="Corpus", low=40, high=45):
        self.key = key
        self.name = name
        self.system = system
        self.mission = mission
        self.mission_type = mission
        self.faction = faction
        self.min_level = low
        self.max_level = high

    @property
    def display_name(self):
        return "%s（%s）" % (self.name, self.system) if self.system else self.name

    @property
    def level_text(self):
        return "%d - %d" % (self.min_level, self.max_level) if self.max_level else "—"


class FakeWorld:
    def __init__(self):
        self.now = time.time()
        self.fissure_rows = [
            {"id": "n1", "node": "Mithra（虚空）", "missionType": "Survival",
             "tier": "Axi", "enemy": "Corrupted", "isHard": True,
             "isStorm": False, "expiry": self.iso(self.now + 1800)},
            {"id": "n2", "node": "Bode（地球）", "missionType": "Capture",
             "tier": "Lith", "enemy": "Grineer", "isHard": False,
             "isStorm": False, "expiry": self.iso(self.now + 1500)},
        ]
        self.storm_rows = [
            {"id": "s1", "node": "Iota Temple (Earth)", "missionType": "Skirmish",
             "tier": "Lith", "enemy": "Grineer", "isHard": False,
             "isStorm": True, "expiry": self.iso(self.now + 2400)},
        ]
        self.cetus = {"state": "day", "isDay": True,
                      "expiry": self.iso(self.now + 600)}
        self.syndicates = [{
            "syndicate": "Ostrons", "expiry": self.iso(self.now + 900),
            "jobs": [{"type": "捕获 · 沙漠难民", "expiry": self.iso(self.now + 900)}],
        }]

    @staticmethod
    def iso(ts):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    def fissures(self):
        return self.fissure_rows

    def void_storms(self):
        return self.storm_rows

    def fetch(self, path):
        if path == "cetusCycle":
            return self.cetus
        if path == "syndicateMissions":
            return self.syndicates
        return None


class FakeArbi:
    def __init__(self, now=None):
        self.now = now or time.time()

    def schedule_entries(self, start_ts, end_ts):
        first = self.now
        rows = []
        for index in range(3):
            begin = first + index * 3600
            if begin + 3600 <= start_ts or begin >= end_ts:
                continue
            rows.append((begin, begin + 3600,
                         Node("n%d" % index, "Proteus%d" % index,
                              mission="防御" if index == 0 else "拦截"),
                         "A+" if index == 1 else "A"))
        return rows


class TeamTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = time.time()
        self.world = FakeWorld()
        self.arbi = FakeArbi(self.now)
        self.store = team_mod.TeamStore(os.path.join(self.tmp.name, "teams.json"))
        self.service = team_mod.TeamService(
            self.store, self.world, self.arbi, size=4,
            public_url="http://example.test", secret="unit-test")

    def fissure_item(self, mission="生存"):
        for item in self.service.missions("fissure")["items"]:
            if item["mission"] == mission:
                return item
        raise AssertionError("找不到测试裂缝")


class AuthTest(TeamTestBase):
    def test_token_roundtrip_and_single_use(self):
        url = self.service.entry_url("group-1", "user-a")
        self.assertTrue(url.startswith("http://example.test/team?t="))
        token = url.split("t=", 1)[1]
        first = self.service.redeem(token)
        profile = self.service.profile(first["sid"])
        self.assertEqual(profile["group"], "group-1")
        self.assertEqual(profile["openid"], "user-a")
        self.assertFalse(profile["named"])
        with self.assertRaises(team_mod.TeamError):
            self.service.redeem(token)

    def test_bad_and_expired_token(self):
        url = self.service.entry_url("group-1", "user-a")
        token = url.split("t=", 1)[1]
        with self.assertRaises(team_mod.TeamError):
            self.service.redeem(token + "x")
        short = team_mod.TeamService(
            self.store, secret="unit-test", token_seconds=60)
        # 手工构造过期 payload
        payload = team_mod._b64(
            ('{"g":"g","u":"u","n":"n1","e":%d}' % int(self.now - 10)).encode())
        expired = "%s.%s" % (payload, short._sign(payload))
        with self.assertRaises(team_mod.TeamError):
            short.redeem(expired)

    def test_rename(self):
        url = self.service.entry_url("g", "u")
        sid = self.service.redeem(url.split("t=", 1)[1])["sid"]
        renamed = self.service.rename(sid, "  蛋总  ")
        self.assertEqual(renamed["name"], "蛋总")
        self.assertTrue(renamed["named"])
        self.assertEqual(self.service.profile(sid)["name"], "蛋总")


class TeamFlowTest(TeamTestBase):
    def test_create_and_fill_four(self):
        item = self.fissure_item("生存")
        team = self.service.create("g1", "host", {
            "category": "fissure", "mission_key": item["key"], "size": 4})
        self.assertEqual(team["count"], 1)
        self.assertTrue(team["host"])
        self.assertEqual(team["mode"], "now")
        for index in range(3):
            view = self.service.join("g1", "u%d" % index, team["id"])
        self.assertTrue(view["full"])
        notices = self.service.poll(now=self.now + 5)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["kind"], "fill")
        self.assertIn("<@host>", notices[0]["text"])
        self.assertIn("<@u0>", notices[0]["text"])
        # 不重复提醒
        self.assertEqual(self.service.poll(now=self.now + 10), [])

    def test_cap_and_duplicate_join(self):
        item = self.fissure_item("生存")
        team = self.service.create("g1", "host", {
            "category": "fissure", "mission_key": item["key"], "size": 2})
        self.service.join("g1", "u1", team["id"])
        with self.assertRaises(team_mod.TeamError):
            self.service.join("g1", "u2", team["id"])
        # 已在别的车队里
        with self.assertRaises(team_mod.TeamError):
            self.service.create("g1", "u1", {
                "category": "fissure", "mission_key": item["key"]})

    def test_leave_and_host_close(self):
        item = self.fissure_item("生存")
        team = self.service.create("g1", "host", {
            "category": "fissure", "mission_key": item["key"], "size": 4})
        self.service.join("g1", "u1", team["id"])
        view = self.service.leave("g1", "u1", team["id"])
        self.assertEqual(view["count"], 1)
        closed = self.service.leave("g1", "host", team["id"])
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(self.service.state("g1", "host")["teams"], [])

    def test_state_isolated_per_group(self):
        item = self.fissure_item("生存")
        self.service.create("g1", "host", {
            "category": "fissure", "mission_key": item["key"]})
        self.assertEqual(len(self.service.state("g1", "host")["teams"]), 1)
        self.assertEqual(self.service.state("g2", "host")["teams"], [])

    def test_expired_team_closes(self):
        item = self.fissure_item("生存")
        team = self.service.create("g1", "host", {
            "category": "fissure", "mission_key": item["key"]})
        self.service.poll(now=self.now + 4000)
        stored = self.store.get(team["id"])
        self.assertEqual(stored["status"], "closed")

    def test_watch_seeds_current_and_fires_on_new(self):
        team = self.service.create("g1", "host", {
            "category": "fissure", "size": 4,
            "watch": {"mission": "生存", "tier": "", "hard": None}})
        self.assertEqual(team["mode"], "watch")
        # 当前已经有生存裂缝，创建时记成基线，不能立刻提醒
        self.assertEqual(self.service.poll(now=self.now + 5), [])
        # 新来一条钢铁中纪生存
        self.world.fissure_rows.append({
            "id": "n9", "node": "Mithra（虚空）", "missionType": "Survival",
            "tier": "Neo", "enemy": "Corrupted", "isHard": True,
            "isStorm": False, "expiry": FakeWorld.iso(self.now + 2000)})
        self.service._cache.clear()
        notices = self.service.poll(now=self.now + 8)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["kind"], "arrive")
        self.assertIn("中纪", notices[0]["text"])
        self.assertIn("<@host>", notices[0]["text"])

    def test_tridolon_time_trigger(self):
        item = self.service.missions("tridolon")["items"][0]
        self.assertEqual(item["badge"], "待入夜")
        team = self.service.create("g1", "host", {
            "category": "tridolon", "mission_key": item["key"]})
        self.assertEqual(team["mode"], "time")
        self.assertEqual(self.service.poll(now=self.now + 30), [])
        notices = self.service.poll(now=self.now + 620)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["kind"], "arrive")
        self.assertIn("三傻", notices[0]["text"])

    def test_only_host_can_close(self):
        item = self.fissure_item("生存")
        team = self.service.create("g1", "host", {
            "category": "fissure", "mission_key": item["key"]})
        self.service.join("g1", "u1", team["id"])
        with self.assertRaises(team_mod.TeamError):
            self.service.close("g1", "u1", team["id"])


class MissionTest(TeamTestBase):
    def test_fissure_items_are_localized(self):
        items = self.service.missions("fissure")["items"]
        self.assertEqual(len(items), 2)
        survival = next(item for item in items if item["mission"] == "生存")
        self.assertEqual(survival["tier"], "后纪")
        self.assertEqual(survival["badge"], "钢铁")
        self.assertIn("虚空", survival["subtitle"])
        # 列表按剩余时间升序，先结束的排前面
        self.assertLessEqual(items[0]["end"], items[1]["end"])

    def test_storms_are_separate(self):
        storms = self.service.missions("railjack")["items"]
        self.assertEqual(len(storms), 1)
        self.assertIn("九重天", team_mod.CATEGORIES["railjack"])
        self.assertEqual(storms[0]["category"], "railjack")

    def test_arbitration_has_current_and_next(self):
        items = self.service.missions("arbitration")["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["badge"], "当前")
        self.assertEqual(items[1]["badge"], "下一场")
        self.assertEqual(items[1]["target"]["start"] > self.now, True)

    def test_bounty_items(self):
        items = self.service.missions("bounty")["items"]
        self.assertEqual(len(items), 1)
        self.assertIn("希图斯", items[0]["title"])
        self.assertIn("捕获", items[0]["subtitle"])

    def test_unknown_category(self):
        with self.assertRaises(team_mod.TeamError):
            self.service.missions("nope")


class NameTest(TeamTestBase):
    def test_default_name_and_setter(self):
        self.assertTrue(self.service.store.name_of("abcdef123456").startswith("队员"))
        self.service.store.set_profile("abcdef123456", "  蛋 总  ")
        self.assertEqual(self.service.store.name_of("abcdef123456"), "蛋 总")
        with self.assertRaises(team_mod.TeamError):
            self.service.store.set_profile("x", "   ")


if __name__ == "__main__":
    unittest.main()
