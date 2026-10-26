"""中转文档落盘快照：中转挂了也能继续用上次数据。"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from bot.worldstate import WorldStateClient


def make_client(path: str) -> WorldStateClient:
    return WorldStateClient({
        "relay": "https://relay.example/",
        "supplement_file": path,
        "supplement_seconds": 0,
        "supplement_stale_seconds": 3600,
    }, cache_config={"enabled": False})


class SupplementSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.path = os.path.join(self.temp, "supplement.json")

    def test_success_writes_snapshot(self):
        client = make_client(self.path)
        client._fetch_json = lambda target: {"news": [{"message": "hi"}]}
        data = client._supplement_uncached()
        self.assertEqual(data["news"][0]["message"], "hi")
        self.assertTrue(os.path.isfile(self.path))

    def test_failure_uses_snapshot(self):
        client = make_client(self.path)
        client._fetch_json = lambda target: {"news": [{"message": "old"}]}
        client._supplement_uncached()

        def boom(target):
            raise RuntimeError("relay down")

        client._fetch_json = boom
        data = client._supplement_uncached()
        self.assertEqual(data["news"][0]["message"], "old")

    def test_expired_snapshot_ignored(self):
        client = make_client(self.path)
        client._fetch_json = lambda target: {"news": [{"message": "old"}]}
        client._supplement_uncached()
        # 把文件时间改成两小时前，超过 1 小时的新鲜期
        old = time.time() - 7200
        os.utime(self.path, (old, old))
        client._fetch_json = lambda target: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(client._supplement_uncached(), {})

    def test_no_relay_returns_empty(self):
        client = WorldStateClient({}, cache_config={"enabled": False})
        self.assertEqual(client._supplement_uncached(), {})

    def test_broken_snapshot_file_ignored(self):
        client = make_client(self.path)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("not json")
        client._fetch_json = lambda target: (_ for _ in ()).throw(RuntimeError("x"))
        self.assertEqual(client._supplement_uncached(), {})


if __name__ == "__main__":
    unittest.main()
