import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from bot.config import ConfigError, ensure_config, load_config

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(BASE, "config.example.json")


class ConfigTest(unittest.TestCase):
    def test_load_example(self):
        config = load_config(EXAMPLE)
        self.assertEqual(config["onebot"]["ws_url"], "ws://127.0.0.1:3001")
        self.assertEqual(config["warframe_market"]["language"], "zh-hans")
        self.assertTrue(config["image"]["enabled"])
        self.assertEqual(config["alerts"]["check_seconds"], 300)
        self.assertTrue(config["auto_update"]["enabled"])
        self.assertEqual(config["auto_update"]["interval_seconds"], 21600)
        self.assertTrue(config["cache"]["enabled"])
        self.assertEqual(config["cache"]["orders_seconds"], 60)

    def test_env_overrides(self):
        env = {
            "QQWF_WS_URL": "ws://napcat:3001",
            "QQWF_ACCESS_TOKEN": "secret",
            "QQWF_PROXY": "none",
            "QQWF_PLATFORM": "ps4",
            "QQWF_IMAGE": "0",
            "QQWF_ALERTS": "false",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = load_config(EXAMPLE)
        self.assertEqual(config["onebot"]["ws_url"], "ws://napcat:3001")
        self.assertEqual(config["onebot"]["access_token"], "secret")
        self.assertEqual(config["warframe_market"]["proxy"], "none")
        self.assertEqual(config["warframe_market"]["platform"], "ps4")
        self.assertFalse(config["image"]["enabled"])
        self.assertFalse(config["alerts"]["enabled"])

    def test_env_bool_true(self):
        with mock.patch.dict(os.environ, {"QQWF_IMAGE": "true"}, clear=False):
            config = load_config(EXAMPLE)
        self.assertTrue(config["image"]["enabled"])

    def test_invalid_ws_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(EXAMPLE, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            raw["onebot"]["ws_url"] = "http://example.com"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle)
            with self.assertRaises(ConfigError) as ctx:
                load_config(path)
            self.assertIn("ws://", str(ctx.exception))

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config(os.path.join(BASE, "no-such-config.json"))

    def test_ensure_config_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "config.json")
            self.assertTrue(ensure_config(target, EXAMPLE))
            self.assertTrue(os.path.isfile(target))
            self.assertFalse(ensure_config(target, EXAMPLE))

    def test_unknown_fields_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(EXAMPLE, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            raw["custom"] = {"hello": "world"}
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle)
            config = load_config(path)
            self.assertEqual(config["custom"]["hello"], "world")


if __name__ == "__main__":
    unittest.main()
