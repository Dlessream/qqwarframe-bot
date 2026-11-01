import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from db import build as dbbuild  # noqa: E402
from db import query  # noqa: E402
from bot.database import DatabaseService  # noqa: E402


class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="qqwf-db-")
        cls.path = os.path.join(cls.tmp, "warframe.db")
        conn = dbbuild.build(cls.path, online=False, verbose=False)
        conn.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def connect(self):
        return query.connect(self.path)

    def test_stats(self):
        conn = self.connect()
        try:
            result = query.stats(conn)
        finally:
            conn.close()
        self.assertGreaterEqual(result["key"]["items"], 3800)
        self.assertGreaterEqual(result["key"]["item_tags"], 10000)
        self.assertGreaterEqual(result["key"]["baro_names"], 29000)
        self.assertGreaterEqual(result["key"]["item_relations"], 100)
        self.assertGreaterEqual(len(result["sources"]), 19)
        self.assertGreater(result["total_rows"], 50000)

    def test_frame_alias_and_item_alias(self):
        conn = self.connect()
        try:
            alias = query.resolve_alias(conn, "女枪")
            kinds = {hit["type"] for hit in alias["hits"]}
            self.assertIn("item_alias", kinds)
            self.assertIn("frame_alias", kinds)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM frame_alias_zh").fetchone()[0],
                48)
        finally:
            conn.close()

    def test_search_and_item_detail(self):
        conn = self.connect()
        try:
            hits = query.search_items(conn, "女枪", limit=5)
            self.assertTrue(hits)
            self.assertEqual(hits[0]["slug"], "mesa_prime_set")
            item = query.item_detail(conn, "mesa_prime_set")
            self.assertEqual(item["name_en"], "Mesa Prime Set")
            self.assertIn("aliases", item)
            self.assertTrue(item["parts"])
            self.assertIn("mesa_prime_blueprint",
                          [part["child_slug"] for part in item["parts"]])
            parts = query.set_parts(conn, "mesa_prime_blueprint")
            self.assertEqual(parts["slug"], "mesa_prime_set")
        finally:
            conn.close()

    def test_views_and_ranks(self):
        conn = self.connect()
        try:
            views = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'")}
            self.assertIn("v_item_detail", views)
            self.assertIn("v_junk", views)
            self.assertIn("v_riven_rank", views)
            self.assertIn("v_set_parts", views)
            frames = query.rank(conn, "frames", 5)
            self.assertTrue(all("price" in row for row in frames))
        finally:
            conn.close()

    def test_bounties_and_rotation(self):
        conn = self.connect()
        try:
            bounty = query.bounties(conn, "希图斯")
            self.assertTrue(bounty["locations"])
            self.assertGreater(len(bounty["locations"][0]["rewards"]), 0)
            rotation = query.rotation(conn)
            self.assertGreaterEqual(len(rotation["vendors"]), 3)
            self.assertTrue(any(v["items"] for v in rotation["vendors"]))
        finally:
            conn.close()

    def test_validate(self):
        conn = self.connect()
        try:
            result = query.validate(conn)
        finally:
            conn.close()
        self.assertTrue(result["ok"], result["errors"])
        self.assertTrue(any("riven_weapons" in item for item in result["warnings"]))

    def test_riven_offline_is_explicit(self):
        conn = self.connect()
        try:
            with self.assertRaises(query.QueryError) as ctx:
                query.riven_weapon(conn, "翁")
            self.assertIn("--online", str(ctx.exception))
        finally:
            conn.close()

    def test_query_cli_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = query.main(["--db", self.path, "--json", "stats"])
        self.assertEqual(code, 0)
        data = json.loads(output.getvalue())
        self.assertGreater(data["key"]["items"], 3800)

    def test_database_service(self):
        service = DatabaseService({"enabled": True, "path": self.path})
        snapshot = service.snapshot()
        self.assertTrue(snapshot["available"])
        self.assertGreater(snapshot["items"], 3800)
        self.assertEqual(snapshot["schema_version"], "2")
        found = service.search("女枪", 3)
        self.assertTrue(found["items"])
        parts = service.parts("mesa_prime_blueprint")
        self.assertEqual(parts["slug"], "mesa_prime_set")
        self.assertTrue(parts["parts"])
        result = service.validate()
        self.assertTrue(result["ok"], result["errors"])

    def test_database_service_missing(self):
        service = DatabaseService(
            {"enabled": True, "path": os.path.join(self.tmp, "missing.db")})
        snapshot = service.snapshot()
        self.assertFalse(snapshot["available"])
        with self.assertRaises(RuntimeError):
            service.search("女枪")

    def test_build_is_idempotent(self):
        before = os.path.getsize(self.path)
        conn = dbbuild.build(self.path, online=False, verbose=False)
        try:
            items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            alerts = conn.execute("SELECT COUNT(*) FROM item_aliases").fetchone()[0]
        finally:
            conn.close()
        self.assertGreater(items, 3800)
        self.assertGreater(alerts, 1000)
        self.assertGreater(os.path.getsize(self.path), before // 2)


if __name__ == "__main__":
    unittest.main()
