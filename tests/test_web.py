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

    def search_market_items(self, query):
        return [{"slug": "volt_prime_neuroptics_blueprint",
                 "name": "Volt Prime 头部神经光元 蓝图", "name_en": "Volt"}]

    def save_alias(self, alias, target):
        self.aliases[alias] = target
        return {"ok": True, "alias": {"alias": alias, "slug": target}}

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

    def test_html(self):
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Warframe Ops".encode("utf-8"), body)

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

    def test_aliases_save(self):
        status, body = self.request(
            "/api/aliases", method="POST",
            headers={"X-Admin-Token": "secret"},
            body={"alias": "测试头", "target": "volt_prime_neuroptics_blueprint"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertIn("测试头", self.app.aliases)

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

    def test_market_refresh(self):
        status, body = self.request(
            "/api/data/refresh", method="POST",
            headers={"X-Admin-Token": "secret"}, body={})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(self.app.refreshed, 1)


if __name__ == "__main__":
    unittest.main()
