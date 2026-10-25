import io
import json
import os
import tarfile
import tempfile
import unittest
from unittest import mock

from bot import update as update_module
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

    def test_local_missing_shows_remote(self):
        checker = self.make_checker()
        checker.local_commit = ""

        def request(url, headers=None, method="GET", body=None):
            return json.dumps({
                "sha": "b" * 40,
                "commit": {"message": "latest", "committer": {"date": ""}},
            }).encode()

        checker.http.request = request
        info = checker.check_remote()
        self.assertFalse(info["available"])
        self.assertTrue(info["local_missing"])
        self.assertEqual(info["remote_short"], "b" * 7)
        self.assertEqual(info["state"], "local_unknown")
        self.assertIn("BUILD_COMMIT", info["last_error"])

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


class ArchiveUpdateTest(unittest.TestCase):
    def test_archive_update_replaces_code_and_keeps_data(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "bot"))
            os.makedirs(os.path.join(root, "web"))
            os.makedirs(os.path.join(root, "data"))
            os.makedirs(os.path.join(root, "logs"))
            with open(os.path.join(root, "bot", "app.py"), "w",
                      encoding="utf-8") as handle:
                handle.write("old\n")
            with open(os.path.join(root, "run.py"), "w", encoding="utf-8") as handle:
                handle.write("old-run\n")
            with open(os.path.join(root, "data", "aliases.json"), "w",
                      encoding="utf-8") as handle:
                handle.write('{"mine": "volt_prime_blueprint"}\n')

            buffer = io.BytesIO()
            files = {
                "bot/app.py": "new\n",
                "web/index.html": "<html>new</html>\n",
                "run.py": "new-run\n",
                "requirements.txt": "requests\n",
                "data/component_aliases.json": '{"牛甲头": "x"}\n',
                "data/frame_aliases.json": '{"牛甲": "x"}\n',
                "data/sol_nodes.json": '{}\n',
            }
            with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
                for name, text in files.items():
                    data = text.encode("utf-8")
                    info = tarfile.TarInfo("repo-main/" + name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            archive = buffer.getvalue()

            class FakeHttp:
                def request(self, url, headers=None, method="GET", body=None):
                    return archive

            with mock.patch.object(update_module, "ROOT", root):
                checker = UpdateChecker({
                    "repo": "example/repo",
                    "branch": "main",
                }, proxy="none")
                checker.http = FakeHttp()
                checker.remote_commit = "c" * 40
                result = checker.apply_update()

            self.assertTrue(result["ok"])
            self.assertTrue(result["restart"])
            with open(os.path.join(root, "bot", "app.py"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new\n")
            with open(os.path.join(root, "run.py"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "new-run\n")
            with open(os.path.join(root, "data", "aliases.json"),
                      encoding="utf-8") as handle:
                self.assertIn("mine", handle.read())
            with open(os.path.join(root, ".build_commit"),
                      encoding="utf-8") as handle:
                self.assertEqual(handle.read().strip(), "c" * 40)


if __name__ == "__main__":
    unittest.main()
