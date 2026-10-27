"""回复里的 @ 规则：普通回复不 @，订阅推送 @ 订阅人。"""

import unittest

from bot.app import BotApp
from bot.config import load_config
from bot.replies import Reply


class CaptureOneBot:
    def __init__(self):
        self.sent = []
        self.image_prefixes = []

    def send_group_text(self, group_id, text):
        self.sent.append((group_id, text))

    def send_private_text(self, user_id, text):
        self.sent.append((user_id, text))

    def send_group_image(self, group_id, png, prefix=""):
        self.image_prefixes.append(prefix)
        self.sent.append((group_id, "[CQ:image]"))

    def send_private_image(self, user_id, png, prefix=""):
        self.image_prefixes.append(prefix)
        self.sent.append((user_id, "[CQ:image]"))


class FakeSubscriptions:
    def __init__(self, pushes):
        self.pushes = pushes

    def poll(self):
        return list(self.pushes)


def make_app(**behavior):
    config = load_config("config.example.json")
    config["auto_update"]["enabled"] = False
    config["alerts"]["enabled"] = False
    config["behavior"]["preload_items"] = False
    config["behavior"]["cooldown_seconds"] = 0
    config["web"]["enabled"] = False
    config["behavior"]["whitelist_groups"] = []
    config["behavior"]["blacklist_groups"] = []
    config["behavior"]["group_policy"] = "blacklist"
    config["behavior"].update(behavior)
    app = BotApp(config)
    app.onebot = CaptureOneBot()
    app.image_enabled = False
    return app


class ReplyAtTest(unittest.TestCase):
    def test_group_reply_has_no_at_by_default(self):
        app = make_app()
        app._reply([Reply(text="绝路 Prime 60p")], True, "200", "100")
        self.assertEqual(app.onebot.sent, [("200", "绝路 Prime 60p")])

    def test_at_sender_can_be_turned_back_on(self):
        app = make_app(at_sender=True)
        app._reply([Reply(text="hi")], True, "200", "100")
        self.assertEqual(app.onebot.sent[0][1], "[CQ:at,qq=100] hi")

    def test_private_reply_never_ats(self):
        app = make_app(at_sender=True)
        app._reply([Reply(text="hi")], False, "200", "100")
        self.assertEqual(app.onebot.sent, [("100", "hi")])

    def test_subscription_push_ats_subscriber(self):
        app = make_app()
        app.subscriptions = FakeSubscriptions([("200", Reply(text="新裂缝"), "100")])
        app._check_subscriptions()
        self.assertEqual(app.onebot.sent[0][1], "[CQ:at,qq=100] 新裂缝")

    def test_subscription_at_can_be_disabled(self):
        app = make_app(at_subscription=False)
        app.subscriptions = FakeSubscriptions([("200", Reply(text="新裂缝"), "100")])
        app._check_subscriptions()
        self.assertEqual(app.onebot.sent, [("200", "新裂缝")])

    def test_blocked_group_push_is_skipped(self):
        app = make_app(blacklist_groups=["200"])
        app.subscriptions = FakeSubscriptions([("200", Reply(text="新裂缝"), "100")])
        app._check_subscriptions()
        self.assertEqual(app.onebot.sent, [])


if __name__ == "__main__":
    unittest.main()
