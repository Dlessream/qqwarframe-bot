import json
import os
import tempfile
import unittest
from unittest import mock

from bot.update import UpdateChecker, read_local_commit


class LocalCommitTest(unittest.TestCase):
    def test_read_git_head(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".git", "refs", "heads"))
            with open(os.path.join(root, ".git", "HEAD"), "w", encoding="utf-8") as handle:
                handle.write("ref: refs/heads/main\n")
            with open(os.path.join(root, ".git", "refs", "heads", "main"),
                      "w", encoding="utf-8") as handle:
                handle.write("a" * 40 + "\n")
            self.assertEqual(read_local_commit(root), "a" * 40)

    def test_build_commit(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, ".build_commit"), "w", encoding="utf-8") as handle:
                handle.write("b" * 40 + "\n")
            self.assertEqual(read_local_commit(root), "b" * 40)


class RemoteCheckTest(unittest.TestCase):
    def make_checker(self):
        checker = UpdateChecker({
            "repo": "example/repo",
            "branch": "main",
            "check_seconds": 600,
        }, proxy="none")
        checker.local_commit = "a" * 40
        return checker

    def test_remote_ahead(self):
        checker = self.make_checker()

        def request(url, headers=None, method="GET", body=None):
            if "/compare/" in url:
                return json.dumps({"status": "ahead"}).encode()
            return json.dumps({
                "sha": "b" * 40,
                "html_url": "https://github.com/example/repo/commit/bbb",
                "commit": {
                    "message": "new version",
                    "committer": {"date": "2026-01-01T00:00:00Z"},
                },
            }).encode()

        checker.http.request = request
        info = checker.check_remote()
        self.assertTrue(info["available"])
        self.assertEqual(info["remote_short"], "b" * 7)
        self.assertEqual(info["state"], "available")

    def test_remote_behind_is_not_update(self):
        checker = self.make_checker()

        def request(url, headers=None, method="GET", body=None):
            if "/compare/" in url:
                return json.dumps({"status": "behind"}).encode()
            return json.dumps({
                "sha": "b" * 40,
                "commit": {"message": "old", "committer": {"date": ""}},
            }).encode()

        checker.http.request = request
        info = checker.check_remote()
        self.assertFalse(info["available"])
        self.assertEqual(info["state"], "local_ahead")

    def test_local_not_on_remote(self):
        checker = self.make_checker()

        def request(url, headers=None, method="GET", body=None):
            if "/compare/" in url:
                raise RuntimeError("HTTP 404 Not Found")
            return json.dumps({
                "sha": "b" * 40,
                "commit": {"message": "remote", "committer": {"date": ""}},
            }).encode()

        checker.http.request = request
        info = checker.check_remote()
        self.assertFalse(info["available"])
        self.assertEqual(info["state"], "local_only")


if __name__ == "__main__":
    unittest.main()
