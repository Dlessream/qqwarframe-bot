import json
import os
import shutil
import tempfile
import unittest
import urllib.error
import urllib.request

from db import build as dbbuild
from db.server import DatabaseServer


class DatabaseServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="qqwf-db-server-")
        cls.path = os.path.join(cls.tmp, "warframe.db")
        conn = dbbuild.build(cls.path, online=False, verbose=False)
        conn.close()
        cls.server = DatabaseServer(cls.path, "127.0.0.1", 0, token="secret")
        cls.server.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.port

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, path, headers=None):
        req = urllib.request.Request(self.base + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def auth(self):
        return {"X-API-Token": "secret"}

    def test_health_is_open(self):
        status, body = self.request("/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["schema_version"], "2")
        self.assertEqual(body["items"], 3893)

    def test_auth_required(self):
        status, _ = self.request("/stats")
        self.assertEqual(status, 401)
        status, _ = self.request("/stats", headers={"X-API-Token": "bad"})
        self.assertEqual(status, 401)

    def test_stats(self):
        status, body = self.request("/stats", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertGreater(body["key"]["items"], 3800)
        self.assertGreater(body["key"]["item_relations"], 100)

    def test_search_and_item(self):
        status, body = self.request(
            "/search?q=%E5%A5%B3%E6%9E%AA", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["slug"], "mesa_prime_set")
        status, body = self.request(
            "/item?q=mesa_prime_set", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertTrue(body["item"]["parts"])

    def test_parts(self):
        status, body = self.request(
            "/parts?q=mesa_prime_blueprint", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(body["slug"], "mesa_prime_set")
        self.assertTrue(body["parts"])

    def test_validate(self):
        status, body = self.request("/validate", headers=self.auth())
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_not_found(self):
        status, _ = self.request("/nope", headers=self.auth())
        self.assertEqual(status, 404)

    def test_web_ui_assets_are_public(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("WARFRAME DATABASE", body)
            self.assertIn("/static/app.js", body)
        with urllib.request.urlopen(self.base + "/static/app.js",
                                    timeout=5) as response:
            script = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("/search?q=", script)
        with urllib.request.urlopen(self.base + "/static/style.css",
                                    timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("--accent", response.read().decode("utf-8"))

    def test_missing_database(self):
        server = DatabaseServer(os.path.join(self.tmp, "missing.db"),
                                "127.0.0.1", 0)
        server.start()
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:%d/health" % server.port)
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("expected 503")
            except urllib.error.HTTPError as exc:
                self.assertEqual(exc.code, 503)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
