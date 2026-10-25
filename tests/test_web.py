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

    def status_snapshot(self):
        return {
            "version": "2.0.0",
            "uptime": 12,
            "onebot": {"connected": False, "url": "ws://test"},
            "market": {"items": 3},
            "riven": {"weapons": 1},
            "updater": {"last_run": 0},
            "alerts": 0,
            "update": self.update_checker.snapshot(),
        }

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


if __name__ == "__main__":
    unittest.main()
