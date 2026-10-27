import json
import os
import tempfile
import unittest

from bot.app import BotApp
from bot.config import load_config


class FakeOneBot:
    def __init__(self):
        self.sent = []

    def send_group_text(self, group_id, text):
        self.sent.append(("text", group_id, text))

    def send_private_text(self, user_id, text):
        self.sent.append(("text", user_id, text))

    def send_group_image(self, group_id, png, prefix=""):
        self.sent.append(("image", group_id, png))

    def send_private_image(self, user_id, png, prefix=""):
        self.sent.append(("image", user_id, png))


def make_app(whitelist=None, blacklist=None, mode="whitelist"):
    config = load_config("config.example.json")
    config["auto_update"]["enabled"] = False
    config["alerts"]["enabled"] = False
    config["behavior"]["preload_items"] = False
    config["behavior"]["cooldown_seconds"] = 0
    config["web"]["enabled"] = False
    config["behavior"]["whitelist_groups"] = list(whitelist or [])
    config["behavior"]["blacklist_groups"] = list(blacklist or [])
    config["behavior"]["group_policy"] = mode
    return BotApp(config)


class GroupPolicyTest(unittest.TestCase):
    def test_whitelist_only(self):
        app = make_app(["100", "200"])
        self.assertTrue(app._group_allowed("100"))
        self.assertTrue(app._group_allowed(200))
        self.assertFalse(app._group_allowed("300"))

    def test_blacklist_mode(self):
        """黑名单制：白名单为空，除黑名单外都服务。"""
        app = make_app(blacklist=["300"], mode="blacklist")
        self.assertTrue(app._group_allowed("100"))
        self.assertFalse(app._group_allowed("300"))

    def test_empty_lists_deny_all(self):
        """默认白名单制：什么都没配时，任何群都不服务。"""
        app = make_app()
        self.assertFalse(app._group_allowed("100"))
        self.assertFalse(app._group_allowed("200"))
        self.assertEqual(app.group_policy_snapshot()["mode"], "whitelist")
        self.assertIn("白名单为空", app.group_policy_snapshot()["summary"])

    def test_unknown_gid_denied(self):
        app = make_app(["100"])
        self.assertFalse(app._group_allowed(""))
        self.assertFalse(app._group_allowed(None))

    def test_blocked_groups_recorded(self):
        app = make_app(["100"])
        self.assertFalse(app._group_allowed("555"))
        self.assertFalse(app._group_allowed("555"))
        blocked = app.group_policy_snapshot()["blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["group_id"], "555")
        self.assertEqual(blocked[0]["count"], 2)
        self.assertEqual(blocked[0]["reason"], "not_whitelisted")
        self.assertIn("不在白名单", blocked[0]["reason_text"])

    def test_blacklist_overrides_whitelist(self):
        app = make_app(["100", "200"], ["200"])
        self.assertTrue(app._group_allowed("100"))
        self.assertFalse(app._group_allowed("200"))

    def test_blacklisted_group_silent(self):
        app = make_app(blacklist=["300"], mode="blacklist")
        fake = FakeOneBot()
        app.onebot = fake
        app.on_event({
            "post_type": "message", "message_type": "group",
            "group_id": "300", "user_id": "1", "self_id": "9",
            "message": [{"type": "text", "data": {"text": "#帮助"}}],
            "raw_message": "#帮助",
        })
        self.assertEqual(fake.sent, [])

    def test_whitelist_blocks_other_groups(self):
        app = make_app(["100"])
        fake = FakeOneBot()
        app.onebot = fake
        app.on_event({
            "post_type": "message", "message_type": "group",
            "group_id": "101", "user_id": "1", "self_id": "9",
            "message": [{"type": "text", "data": {"text": "#帮助"}}],
            "raw_message": "#帮助",
        })
        self.assertEqual(fake.sent, [])


    def test_bare_command_in_allowed_group(self):
        app = make_app(["100"])
        fake = FakeOneBot()
        app.onebot = fake
        app.on_event({
            "post_type": "message", "message_type": "group",
            "group_id": "100", "user_id": "1", "self_id": "9",
            "message": [{"type": "text", "data": {"text": "帮助"}}],
            "raw_message": "帮助",
        })
        self.assertTrue(fake.sent)

    def test_plain_chat_ignored(self):
        app = make_app(["100"])
        fake = FakeOneBot()
        app.onebot = fake
        app.on_event({
            "post_type": "message", "message_type": "group",
            "group_id": "100", "user_id": "1", "self_id": "9",
            "message": [{"type": "text", "data": {"text": "今天天气不错"}}],
            "raw_message": "今天天气不错",
        })
        self.assertEqual(fake.sent, [])

    def test_update_group_policy_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = load_config("config.example.json")
            config["auto_update"]["enabled"] = False
            config["alerts"]["enabled"] = False
            config["web"]["enabled"] = False
            config["behavior"]["preload_items"] = False
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False)
            app = BotApp(config, config_path=path)
            result = app.update_group_policy(["100001", "200002"], ["200002"])
            self.assertEqual(result["whitelist"], ["100001", "200002"])
            self.assertEqual(result["blacklist"], ["200002"])
            self.assertTrue(app._group_allowed("100001"))
            self.assertFalse(app._group_allowed("200002"))
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["behavior"]["whitelist_groups"],
                             ["100001", "200002"])
            self.assertEqual(saved["behavior"]["blacklist_groups"], ["200002"])

    def test_invalid_group_id_rejected(self):
        app = make_app()
        with self.assertRaises(ValueError):
            app.update_group_policy(["abc"], [])
        with self.assertRaises(ValueError):
            app.update_group_policy([], ["123"])
        with self.assertRaises(ValueError):
            app.update_group_policy([], [], "whatever")

    def test_allow_group_persists_and_clears_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = load_config("config.example.json")
            config["auto_update"]["enabled"] = False
            config["alerts"]["enabled"] = False
            config["web"]["enabled"] = False
            config["behavior"]["preload_items"] = False
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False)
            app = BotApp(config, config_path=path)
            self.assertFalse(app._group_allowed("1109499758"))
            self.assertEqual(len(app.group_policy_snapshot()["blocked"]), 1)
            result = app.allow_group("1109499758")
            self.assertEqual(result["whitelist"], ["1109499758"])
            self.assertEqual(result["blocked"], [])
            self.assertTrue(app._group_allowed("1109499758"))
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["behavior"]["whitelist_groups"], ["1109499758"])

    def test_deny_group_removes_from_whitelist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = load_config("config.example.json")
            config["auto_update"]["enabled"] = False
            config["alerts"]["enabled"] = False
            config["web"]["enabled"] = False
            config["behavior"]["preload_items"] = False
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False)
            app = BotApp(config, config_path=path)
            app.allow_group("100001")
            result = app.deny_group("100001")
            self.assertEqual(result["whitelist"], [])
            self.assertEqual(result["blacklist"], ["100001"])
            self.assertFalse(app._group_allowed("100001"))

    def test_switch_mode_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = load_config("config.example.json")
            config["auto_update"]["enabled"] = False
            config["alerts"]["enabled"] = False
            config["web"]["enabled"] = False
            config["behavior"]["preload_items"] = False
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(config, handle, ensure_ascii=False)
            app = BotApp(config, config_path=path)
            self.assertFalse(app._group_allowed("100"))
            app.set_group_policy_mode("blacklist")
            self.assertTrue(app._group_allowed("100"))
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["behavior"]["group_policy"], "blacklist")
            with self.assertRaises(ValueError):
                app.set_group_policy_mode("open")


if __name__ == "__main__":
    unittest.main()
