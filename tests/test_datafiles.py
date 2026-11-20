"""静态数据定位：data/ 被宿主机挂载覆盖时回退到 builtin-data/。

线上事故背景：docker-compose 把宿主机 ./data 挂到 /app/data，部署时 data/
不参与代码同步，新增的 relics_local.json 缺失，遗物查询直接报
`[Errno 2] No such file or directory: '/app/data/relics_local.json'`。
"""

import json
import os
import shutil
import tempfile
import unittest

from bot import datafiles


RAW_RELIC = {
    "name": "Lith A1 Relic",
    "vaulted": False,
    "rewards": [
        {"rarity": "Rare", "chance": 2.0,
         "item": {"name": "Boltor Prime Barrel",
                  "warframeMarket": {"url_name": "boltor_prime_barrel"}}},
    ],
}


class _TempLayout(unittest.TestCase):
    """把 datafiles.ROOT / BUILTIN_DIR 指到临时目录。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qqwf-datafiles-")
        self._old_root = datafiles.ROOT
        self._old_builtin = datafiles.BUILTIN_DIR
        # 切到临时目录：resolve 的 cwd 候选才不会命中真实的项目 data/
        self._old_cwd = os.getcwd()
        os.chdir(self.tmp)
        datafiles.ROOT = os.path.join(self.tmp, "app")
        datafiles.BUILTIN_DIR = os.path.join(datafiles.ROOT, "builtin-data")
        os.makedirs(os.path.join(datafiles.ROOT, "data"))
        os.makedirs(datafiles.BUILTIN_DIR)

    def tearDown(self):
        os.chdir(self._old_cwd)
        datafiles.ROOT = self._old_root
        datafiles.BUILTIN_DIR = self._old_builtin
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, path, payload):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return path


class ResolveTest(_TempLayout):
    def rel(self, name):
        return os.path.join("data", name)

    def test_prefers_runtime_data_dir(self):
        live = self.write(os.path.join(datafiles.ROOT, "data", "relics_local.json"),
                          {"relics": [RAW_RELIC]})
        self.write(os.path.join(datafiles.BUILTIN_DIR, "relics_local.json"),
                   {"relics": []})
        self.assertEqual(os.path.normpath(datafiles.resolve(self.rel("relics_local.json"))),
                         os.path.normpath(live))

    def test_falls_back_to_builtin_when_data_missing(self):
        builtin = self.write(os.path.join(datafiles.BUILTIN_DIR, "relics_local.json"),
                             {"relics": [RAW_RELIC]})
        self.assertEqual(os.path.normpath(datafiles.resolve(self.rel("relics_local.json"))),
                         os.path.normpath(builtin))

    def test_absolute_path_also_falls_back(self):
        # Aliases 这类模块传进来的是 abspath，也必须能兜底
        builtin = self.write(os.path.join(datafiles.BUILTIN_DIR, "aliases.json"),
                             {"电男": "volt_prime_set"})
        missing = os.path.join(datafiles.ROOT, "data", "aliases.json")
        self.assertEqual(datafiles.resolve(missing), builtin)

    def test_returns_first_candidate_when_all_missing(self):
        expected = os.path.join(datafiles.ROOT, "data", "nope.json")
        self.assertEqual(os.path.normpath(datafiles.resolve(self.rel("nope.json"))),
                         os.path.normpath(expected))

    def test_candidates_include_builtin(self):
        paths = [os.path.normpath(p) for p in datafiles.candidates(self.rel("relics_local.json"))]
        self.assertIn(os.path.normpath(os.path.join(datafiles.BUILTIN_DIR, "relics_local.json")),
                      paths)


class RelicFallbackTest(_TempLayout):
    def test_client_loads_builtin_when_data_missing(self):
        self.write(os.path.join(datafiles.BUILTIN_DIR, "relics_local.json"),
                   {"updated": "2026-11-01", "relics": [RAW_RELIC]})
        from bot.relics import RelicClient
        client = RelicClient(None, None)
        self.assertTrue(client.available())
        self.assertEqual(client.last_error, "")
        self.assertEqual(len(client.relics()), 1)
        self.assertTrue(client.local_file.endswith("builtin-data/relics_local.json")
                        or "builtin-data" in client.local_file)

    def test_client_prefers_runtime_file(self):
        self.write(os.path.join(datafiles.ROOT, "data", "relics_local.json"),
                   {"relics": [RAW_RELIC, RAW_RELIC]})
        self.write(os.path.join(datafiles.BUILTIN_DIR, "relics_local.json"),
                   {"relics": [RAW_RELIC]})
        from bot.relics import RelicClient
        client = RelicClient(None, None)
        self.assertEqual(len(client.relics()), 2)


class ModNamesFallbackTest(_TempLayout):
    def test_path_falls_back_to_builtin(self):
        payload = {"warframes": {}, "weapons": {}}
        builtin = self.write(
            os.path.join(datafiles.BUILTIN_DIR, "mod_names_zh.json"), payload)
        from bot import mod_names_zh
        self.assertEqual(mod_names_zh._path(), builtin)

    def test_prefers_full_snapshot(self):
        self.write(os.path.join(datafiles.ROOT, "data", "mod_names_zh.json"), {})
        full = self.write(
            os.path.join(datafiles.ROOT, "data", "mod_names_zh_full.json"), {})
        from bot import mod_names_zh
        self.assertEqual(mod_names_zh._path(), full)


class DockerPackagingTest(unittest.TestCase):
    """Dockerfile / entrypoint 必须真的把静态数据打进 builtin-data。"""

    def root(self):
        return datafiles.ROOT

    def test_dockerfile_packages_builtin_data(self):
        path = os.path.join(self.root(), "Dockerfile")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("/app/builtin-data", text)
        # 只打包静态数据，不能把运行时缓存（board_cache/subscriptions）也塞进去
        self.assertIn("relics_local.json", text)
        self.assertIn("mod_names_zh_full.json", text)
        self.assertNotIn("board_cache.json", text)

    def test_entrypoint_backfills_missing_static_data(self):
        path = os.path.join(self.root(), "deploy", "docker-entrypoint.sh")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("/app/builtin-data", text)
        self.assertIn('cp "$src" "/app/data/$name"', text)

    def test_self_update_covers_new_static_data(self):
        from bot import update
        for name in ("relics_local.json", "mod_names_zh.json",
                     "mod_names_zh_full.json", "bounty_static.json"):
            self.assertIn(name, update.SELF_UPDATE_DATA_FILES)


if __name__ == "__main__":
    unittest.main()
