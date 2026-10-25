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


def make_app(whitelist=None, blacklist=None):
    config = load_config("config.example.json")
    config["auto_update"]["enabled"] = False
    config["alerts"]["enabled"] = False
    config["behavior"]["preload_items"] = False
    config["behavior"]["cooldown_seconds"] = 0
    config["web"]["enabled"] = False
    config["behavior"]["whitelist_groups"] = list(whitelist or [])
    config["behavior"]["blacklist_groups"] = list(blacklist or [])
    return BotApp(config)


class GroupPolicyTest(unittest.TestCase):
    def test_whitelist_only(self):
        app = make_app(["100", "200"])
        self.assertTrue(app._group_allowed("100"))
        self.assertTrue(app._group_allowed(200))
        self.assertFalse(app._group_allowed("300"))

    def test_blacklist(self):
        app = make_app(blacklist=["300"])
        self.assertTrue(app._group_allowed("100"))
        self.assertFalse(app._group_allowed("300"))

    def test_blacklist_overrides_whitelist(self):
        app = make_app(["100", "200"], ["200"])
        self.assertTrue(app._group_allowed("100"))
        self.assertFalse(app._group_allowed("200"))

    def test_blacklisted_group_silent(self):
        app = make_app(blacklist=["300"])
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


if __name__ == "__main__":
    unittest.main()
