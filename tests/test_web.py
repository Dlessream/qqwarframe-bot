import json
import unittest
import urllib.error
import urllib.request

from bot.web import AdminServer


class FakeUpdate:
    def __init__(self):
        self.checked = 0

    def snapshot(self):
        return {"available": False, "local_short": "abc1234"}

    def check_now(self):
        self.checked += 1
        return {"available": True, "local_short": "abc1234",
                "remote_short": "def5678"}

    def acknowledge_local(self):
        pass

    def dismiss_remote(self):
        pass


class FakeApp:
    def __init__(self):
        self.update_checker = FakeUpdate()
        self.restarted = False
        self.applied = 0
        self.groups = {"whitelist": ["100"], "blacklist": []}
        self.aliases = {"电男头": "volt_prime_neuroptics_blueprint"}
        self.refreshed = 0

    def status_snapshot(self):
        return {
            "version": "2.0.0",
            "uptime": 12,
            "onebot": {"connected": False, "url": "ws://test"},
            "market": {"items": 3},
            "riven": {"weapons": 1},
            "updater": {"last_run": 0},
            "alerts": 0,
            "groups": self.groups,
            "update": self.update_checker.snapshot(),
        }

    def update_group_policy(self, whitelist, blacklist):
        self.groups = {"whitelist": list(whitelist), "blacklist": list(blacklist)}
        return {"ok": True, **self.groups}

    def group_policy_snapshot(self):
        return dict(self.groups)

    def alias_snapshot(self):
        return {
            "source": {"items": 3, "last_refresh_seconds": 2,
                       "aliases": len(self.aliases)},
            "aliases": [{"alias": key, "slug": value,
                         "name": "Volt Prime 头部神经光元 蓝图",
                         "builtin": False}
                        for key, value in self.aliases.items()],
        }

    def alias_groups(self):
        return {
            "source": {"items": 3, "last_refresh_seconds": 2},
            "groups": [{"slug": "volt_prime_blueprint",
                        "name": "Volt Prime 蓝图",
                        "name_en": "Volt Prime Blueprint",
                        "missing": False,
                        "aliases": ["电男", "伏特", "阿米尔"],
                        "builtin": []}],
        }

    def save_alias_group(self, slug, aliases):
        return {"ok": True, "group": {"slug": slug, "aliases": list(aliases)}}

    def search_market_items(self, query):
        return [{"slug": "volt_prime_neuroptics_blueprint",
                 "name": "Volt Prime 头部神经光元 蓝图", "name_en": "Volt"}]

    def translate_item(self, query):
        return {"zh": "Volt Prime 头部神经光元 蓝图",
                "en": "Volt Prime Neuroptics Blueprint",
                "source": "warframe.market"}

    def save_alias(self, alias, target):
        self.aliases[alias] = target
        return {"ok": True, "alias": {"alias": alias, "slug": target}}

    def save_aliases(self, aliases, target):
        for alias in aliases:
            self.aliases[alias] = target
        return {"ok": True, "aliases": list(aliases),
                "entry": {"alias": aliases[0], "slug": target}}

    def delete_alias(self, alias):
        self.aliases.pop(alias, None)
        return {"ok": True, "removed": True}

    def refresh_market_data(self):
        self.refreshed += 1
        return {"ok": True, "source": {"items": 4}}

    def apply_update(self):
        self.applied += 1
        return {"ok": False, "manual": True,
                "command": "docker compose up -d --build",
                "message": "manual"}

    def request_restart(self):
        self.restarted = True

    def arbi_snapshot(self):
        return {
            "enabled": True,
            "nodes": [{"key": "SolNode1", "name": "Callisto", "system": "木星",
                       "mission": "拦截", "levels": "15 - 20",
                       "community": "S", "manual": "", "local": "S",
                       "tier": "S", "source": "community"}],
            "counts": {"community": 1, "manual": 0, "local": 0, "none": 0},
            "tiers": ["S", "A+", "A", "A-", "B", "C"],
            "local_rating": True,
            "ratings_file": "data/arbi_ratings.json",
            "current": "SolNode1",
        }

    def save_arbi_rating(self, key, tier):
        if not key:
            raise ValueError("缺少节点编号。")
        self.arbi_saved = (key, tier)
        return {"ok": True, "key": key, "tier": tier or "S", "source": "manual"}

    def arbi_schedule(self, days=7):
        self.arbi_schedule_days = days
        return {
            "enabled": True,
            "days": days,
            "total": days * 2,
            "groups": [{
                "date": "2026-10-28",
                "label": "今天 · 10-28 周三",
                "count": 2,
                "highlights": [{"tier": "S", "count": 1}],
                "entries": [
                    {"start": 0, "end": 3600, "time": "00:00",
                     "node": "Callisto（木星）", "key": "SolNode1",
                     "system": "木星", "mission": "拦截",
                     "faction": "Corpus", "level": "15 - 20", "tier": "S"},
                    {"start": 3600, "end": 7200, "time": "01:00",
                     "node": "Everest（地球）", "key": "SolNode39",
                     "system": "地球", "mission": "挖掘",
                     "faction": "Grineer", "level": "1 - 6", "tier": "A+"},
                ],
            }],
            "missions": {"拦截": "#A889E2", "挖掘": "#EBBC60"},
        }

    def mission_colors_snapshot(self):
        return {
            "entries": [
                {"name": "生存", "color": "#60CB8F",
                 "default": "#60CB8F", "custom": False},
                {"name": "防御", "color": "#123456",
                 "default": "#62BEDB", "custom": True},
            ],
            "file": "data/mission_colors.json",
            "custom": 1,
            "total": 2,
        }

    def save_mission_color(self, name, color):
        if not name:
            raise ValueError("缺少任务类型名称。")
        self.mission_saved = (name, color)
        return {"ok": True, "name": name, "color": color,
                "text": "%s → %s" % (name, color)}

    def save_mission_colors(self, items):
        self.mission_batch = dict(items)
        return {"ok": True, "count": len(items), "entries": []}

    def reset_mission_colors(self):
        self.mission_reset = True
        return {"ok": True, "count": 0, "entries": []}


class AdminServerTest(unittest.TestCase):
    def setUp(self):
        self.app = FakeApp()
        self.server = AdminServer(self.app, {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 0,
            "token": "secret",
            "log_file": "",
        })
        self.server.start()
        self.base = "http://127.0.0.1:%d" % self.server.actual_port

    def tearDown(self):
        self.server.stop()

    def request(self, path, method="GET", headers=None, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers or {})
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_arbi_get(self):
        status, body = self.request(
            "/api/arbi", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["nodes"][0]["key"], "SolNode1")
        self.assertEqual(payload["current"], "SolNode1")
        status, _ = self.request("/api/arbi")
        self.assertEqual(status, 401)

    def test_arbi_schedule(self):
        status, body = self.request(
            "/api/arbi-schedule?days=3", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["days"], 3)
        self.assertEqual(payload["groups"][0]["entries"][0]["node"],
                         "Callisto（木星）")
        self.assertEqual(self.app.arbi_schedule_days, 3)
        # 天数非法时回落 7 天
        status, body = self.request(
            "/api/arbi-schedule?days=xx", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.arbi_schedule_days, 7)
        status, _ = self.request("/api/arbi-schedule")
        self.assertEqual(status, 401)

    def test_arbi_post(self):
        status, body = self.request(
            "/api/arbi", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"key": "SolNode1", "tier": "A"})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.arbi_saved, ("SolNode1", "A"))
        status, body = self.request(
            "/api/arbi", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"key": "", "tier": "A"})
        self.assertEqual(status, 400)

    def test_mission_colors(self):
        status, body = self.request(
            "/api/mission-colors", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["entries"][0]["name"], "生存")
        self.assertTrue(payload["entries"][1]["custom"])

        status, _ = self.request("/api/mission-colors")
        self.assertEqual(status, 401)

        status, body = self.request(
            "/api/mission-colors", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"name": "生存", "color": "#00FF00"})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.mission_saved, ("生存", "#00FF00"))

        status, body = self.request(
            "/api/mission-colors", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"colors": {"生存": "#00FF00", "防御": "#0000FF"}})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.app.mission_batch), 2)

        status, body = self.request(
            "/api/mission-colors", method="POST",
            headers={"X-Admin-Token": "secret"}, body={"reset": True})
        self.assertEqual(status, 200)
        self.assertTrue(self.app.mission_reset)

        status, body = self.request(
            "/api/mission-colors", method="POST",
            headers={"X-Admin-Token": "secret"}, body={"name": ""})
        self.assertEqual(status, 400)

    def test_html(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Warframe Ops".encode("utf-8"), body)
        # 仲裁列表整行按任务类型上色：图例容器 + 就地改色的入口都在
        self.assertIn("arbiLegend".encode("utf-8"), body)
        self.assertIn("arbiMissionColors".encode("utf-8"), body)
        self.assertIn("?v=".encode("utf-8"), body)

    def test_auth(self):
        status, _ = self.request("/api/status")
        self.assertEqual(status, 401)
        status, body = self.request(
            "/api/status", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], "2.0.0")

    def test_update_check(self):
        status, body = self.request(
            "/api/update/check", method="POST",
            headers={"X-Admin-Token": "secret"}, body={})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["update"]["available"])
        self.assertEqual(self.app.update_checker.checked, 1)

    def test_apply_manual(self):
        status, body = self.request(
            "/api/update/apply", method="POST",
            headers={"X-Admin-Token": "secret"}, body={"confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["manual"])
        self.assertEqual(self.app.applied, 1)

    def test_restart(self):
        status, body = self.request(
            "/api/restart", method="POST",
            headers={"X-Admin-Token": "secret"}, body={"confirm": True})
        self.assertEqual(status, 200)
        self.assertTrue(self.app.restarted)

    def test_groups_get(self):
        status, body = self.request(
            "/api/groups", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["whitelist"], ["100"])

    def test_groups_update(self):
        status, body = self.request(
            "/api/groups", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"whitelist": ["100", "200"], "blacklist": ["300"]})
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["whitelist"], ["100", "200"])
        self.assertEqual(saved["blacklist"], ["300"])
        self.assertEqual(self.app.groups["blacklist"], ["300"])

    def test_aliases_get(self):
        status, body = self.request(
            "/api/aliases", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["aliases"][0]["alias"], "电男头")

    def test_alias_groups_get(self):
        status, body = self.request(
            "/api/alias-groups", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["groups"][0]["slug"], "volt_prime_blueprint")
        self.assertEqual(data["groups"][0]["aliases"],
                         ["电男", "伏特", "阿米尔"])

    def test_alias_group_update(self):
        status, body = self.request(
            "/api/alias-groups", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"slug": "volt_prime_blueprint",
                  "aliases": ["电男", "伏特"]})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["group"]["aliases"], ["电男", "伏特"])

    def test_aliases_save(self):
        status, body = self.request(
            "/api/aliases", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"alias": "测试头", "target": "volt_prime_neuroptics_blueprint"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertIn("测试头", self.app.aliases)

    def test_aliases_save_many(self):
        status, body = self.request(
            "/api/aliases", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"aliases": ["电男", "伏特", "阿米尔"],
                  "target": "volt_prime_blueprint"})
        self.assertEqual(status, 200)
        saved = json.loads(body)
        self.assertEqual(saved["aliases"], ["电男", "伏特", "阿米尔"])
        self.assertIn("阿米尔", self.app.aliases)

    def test_aliases_delete(self):
        status, body = self.request(
            "/api/aliases/delete", method="POST",
            headers={"X-Admin-Token": "secret"}, body={"alias": "电男头"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["removed"])
        self.assertNotIn("电男头", self.app.aliases)

    def test_item_search(self):
        status, body = self.request(
            "/api/items?q=volt", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["items"][0]["slug"],
                         "volt_prime_neuroptics_blueprint")

    def test_translate(self):
        status, body = self.request(
            "/api/translate?q=volt", headers={"X-Admin-Token": "secret"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["zh"], "Volt Prime 头部神经光元 蓝图")

    def test_market_refresh(self):
        status, body = self.request(
            "/api/data/refresh", method="POST",
            headers={"X-Admin-Token": "secret"}, body={})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(self.app.refreshed, 1)


if __name__ == "__main__":
    unittest.main()
