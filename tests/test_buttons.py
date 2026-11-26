"""消息按钮、互动回调与游戏场景背景测试。"""

import io
import threading
import unittest

from bot import keyboards, qqevent, replies, scenebg

try:
    from PIL import Image
    PIL_OK = True
except Exception:  # pragma: no cover
    PIL_OK = False


class KeyboardTest(unittest.TestCase):
    def test_arbitration_buttons(self):
        kb = keyboards.arbitration_keyboard()
        rows = kb["content"]["rows"]
        self.assertEqual(len(rows), 2)
        labels = [b["render_data"]["label"] for row in rows
                  for b in row["buttons"]]
        self.assertEqual(labels, ["谷拦仲裁", "镜防仲裁", "高效仲裁",
                                  "今日仲裁", "仲裁表", "仲裁评级"])
        for row in rows:
            for button in row["buttons"]:
                self.assertEqual(button["action"]["type"], 1)
                self.assertEqual(button["action"]["permission"]["type"], 2)
                self.assertTrue(button["action"]["data"].startswith("a:"))

    def test_wm_buttons(self):
        kb = keyboards.wm_keyboard("deadbeef")
        buttons = kb["content"]["rows"][0]["buttons"]
        self.assertEqual([b["render_data"]["label"] for b in buttons],
                         ["行情", "对话文本"])
        self.assertEqual(buttons[0]["action"]["data"], "wm:trend:deadbeef")
        self.assertEqual(buttons[1]["action"]["data"], "wm:chat:deadbeef")

    def test_circuit_buttons(self):
        kb = keyboards.circuit_keyboard()
        buttons = kb["content"]["rows"][0]["buttons"]
        self.assertEqual([b["render_data"]["label"] for b in buttons], ["灵化列表"])
        self.assertEqual(buttons[0]["action"]["data"], "a:灵化列表")
        back = keyboards.circuit_keyboard(back=True)
        back_button = back["content"]["rows"][0]["buttons"][0]
        self.assertEqual(back_button["render_data"]["label"], "本周回廊")
        self.assertEqual(back_button["action"]["data"], "a:回廊")

    def test_reply_holds_keyboard(self):
        kb = keyboards.wm_keyboard("abc")
        reply = replies.Reply(text="x", keyboard=kb)
        self.assertIs(reply.keyboard, kb)


class ImageKeyboardDeliveryTest(unittest.TestCase):
    """带按钮的卡片走官方 Markdown 图文（一条消息），失败才退回富媒体 + 按钮文本。"""

    class Capture:
        def __init__(self, card=True):
            self.calls = []
            self.event_log = None
            if card:
                self.send_group_card = self._card
            # card=False 时不挂这个方法，用来验证回退路径

        def _card(self, group_id, png, reply_ctx=None, keyboard=None,
                  caption=""):
            self.calls.append(("card", caption, keyboard))

        def send_group_image(self, group_id, png, prefix="", reply_ctx=None):
            self.calls.append(("image", prefix, None))

        def send_group_text(self, group_id, text, reply_ctx=None,
                            keyboard=None):
            self.calls.append(("text", text, keyboard))

    def _app(self, card=True):
        from bot.app import BotApp
        from bot.config import load_config
        config = load_config("config.example.json")
        config["auto_update"]["enabled"] = False
        config["alerts"]["enabled"] = False
        config["behavior"]["preload_items"] = False
        config["behavior"]["cooldown_seconds"] = 0
        config["web"]["enabled"] = False
        app = BotApp(config)
        app.qqbot = self.Capture(card)
        app.image_enabled = True
        return app

    def test_keyboard_rides_on_single_markdown_card(self):
        app = self._app()
        kb = keyboards.wm_keyboard("tok")
        reply = replies.Reply(text="文字版", image=lambda: b"png",
                              keyboard=kb)
        self.assertTrue(app._deliver(True, "G1", reply))
        self.assertEqual([call[0] for call in app.qqbot.calls], ["card"])
        self.assertIs(app.qqbot.calls[0][2], kb)

    def test_falls_back_to_image_plus_text(self):
        app = self._app(card=False)
        kb = keyboards.wm_keyboard("tok")
        reply = replies.Reply(text="文字版", image=lambda: b"png",
                              keyboard=kb, button_hint="▼ 行情走势")
        self.assertTrue(app._deliver(True, "G1", reply))
        self.assertEqual([call[0] for call in app.qqbot.calls],
                         ["image", "text"])
        self.assertEqual(app.qqbot.calls[1][1], "▼ 行情走势")
        self.assertIs(app.qqbot.calls[1][2], kb)

    def test_card_error_falls_back(self):
        app = self._app()
        app.qqbot.send_group_card = self._boom
        reply = replies.Reply(text="文字版", image=lambda: b"png",
                              keyboard=keyboards.wm_keyboard("tok"))
        self.assertTrue(app._deliver(True, "G1", reply))
        self.assertEqual([call[0] for call in app.qqbot.calls],
                         ["image", "text"])

    def _boom(self, *args, **kwargs):
        from bot.qqofficial import QQOfficialError
        raise QQOfficialError("40035006 素材服务异常")

    def test_no_keyboard_single_image(self):
        app = self._app()
        reply = replies.Reply(text="文字版", image=lambda: b"png")
        self.assertTrue(app._deliver(True, "G1", reply))
        self.assertEqual([call[0] for call in app.qqbot.calls], ["image"])

    def test_passive_rejected_retries_as_active(self):
        """被动回复被拒时要逐级降级，最后用主动消息送达。"""
        class Strict(self.Capture):
            def __init__(self):
                super().__init__(card=False)
                self.ctxs = []

            def send_group_text(self, group_id, text, reply_ctx=None,
                                keyboard=None):
                from bot.qqofficial import QQOfficialError
                self.ctxs.append(reply_ctx)
                if reply_ctx != {}:  # 除主动消息外一律拒绝
                    raise QQOfficialError(
                        "/messages 失败：请求参数msg_id无效或越权 / 错误码 40034024")
                self.calls.append(("text", text, keyboard))

        app = self._app(card=False)
        app.qqbot = Strict()
        reply = replies.Reply(text="行情结果")
        self.assertTrue(app._deliver(True, "G1", reply,
                                     reply_ctx={"msg_id": "IID"}))
        # 先按指定 msg_id（拒）-> 再按最近消息（拒）-> 最后主动消息（成）
        self.assertEqual(app.qqbot.ctxs, [{"msg_id": "IID"}, None, {}])
        self.assertEqual(len(app.qqbot.calls), 1)


class InteractionReplyContextTest(unittest.TestCase):
    """互动事件不提供被动窗口：按钮结果只能回最近用户消息或发主动消息。"""

    def _app(self):
        from bot.app import BotApp
        from bot.config import load_config
        config = load_config("config.example.json")
        config["auto_update"]["enabled"] = False
        config["alerts"]["enabled"] = False
        config["behavior"]["preload_items"] = False
        config["behavior"]["cooldown_seconds"] = 0
        config["behavior"]["group_policy"] = "blacklist"
        config["behavior"]["whitelist_groups"] = []
        config["behavior"]["blacklist_groups"] = []
        config["web"]["enabled"] = False
        app = BotApp(config)
        app.image_enabled = False
        return app

    def test_button_reply_ignores_interaction_id(self):
        app = self._app()
        captured = {}

        def fake_deliver(is_group, chat_id, reply, at_user="", reply_ctx=None):
            captured["ctx"] = reply_ctx
            return True

        app._deliver = fake_deliver
        app.router.handle_button = lambda data, ctx: [replies.Reply(text="ok")]
        app._on_interaction({
            "post_type": "interaction", "message_type": "group",
            "group_id": "G1", "user_id": "U1", "self_id": "BOT",
            "button_data": "wm:trend:tok", "button_id": "wm-trend",
            "qq_interaction_id": "INT-1", "qq_envelope_id": "INTERACTION_CREATE:1",
        })
        # 不能把 interaction id 当 msg_id（40034024），交给"最近用户消息"逻辑
        self.assertIsNone(captured["ctx"])


class InteractionEventTest(unittest.TestCase):
    def test_button_click_mapped(self):
        payload = {
            "t": "INTERACTION_CREATE", "id": "evt-123",
            "d": {
                "id": "INT-UUID-1",
                "type": 11, "scene": "group", "group_openid": "G1",
                "group_member_openid": "U1", "application_id": "1905720698",
                "data": {"type": 11, "resolved": {
                    "button_data": "a:仲裁表", "button_id": "arb-week"}},
            },
        }
        event = qqevent.to_onebot_event(payload)
        self.assertEqual(event["post_type"], "interaction")
        self.assertEqual(event["message_type"], "group")
        self.assertEqual(event["group_id"], "G1")
        self.assertEqual(event["user_id"], "U1")
        self.assertEqual(event["button_data"], "a:仲裁表")
        # 回执必须用事件体里的 id（d.id），信封 id 平台只返回 200 但不生效
        self.assertEqual(event["qq_interaction_id"], "INT-UUID-1")
        self.assertEqual(event["qq_envelope_id"], "evt-123")

    def test_missing_body_id_falls_back_to_envelope(self):
        payload = {"t": "INTERACTION_CREATE", "id": "evt-123",
                   "d": {"type": 11, "scene": "c2c", "user_openid": "U1",
                         "data": {"resolved": {"button_data": "a:帮助"}}}}
        event = qqevent.to_onebot_event(payload)
        self.assertEqual(event["qq_interaction_id"], "evt-123")

    def test_non_click_interaction_ignored(self):
        payload = {"t": "INTERACTION_CREATE", "id": "x",
                   "d": {"type": 13, "data": {"resolved": {}}}}
        self.assertIsNone(qqevent.to_onebot_event(payload))


class SceneBackgroundTest(unittest.TestCase):
    @unittest.skipUnless(PIL_OK, "Pillow 未安装")
    def test_known_scene_loads(self):
        image = scenebg.scene_image("fissure")
        self.assertIsNotNone(image)
        self.assertLessEqual(image.width, 1000)

    def test_unknown_scene_returns_none(self):
        self.assertIsNone(scenebg.scene_image("not_a_scene"))


class SceneCardTest(unittest.TestCase):
    @unittest.skipUnless(PIL_OK, "Pillow 未安装")
    def test_table_card_accepts_background(self):
        from bot import imagecard
        bg = Image.new("RGB", (1200, 800), (40, 70, 110))
        png = imagecard.build_table(
            "测试", "场景背景", ["A", "B"], [["1", "2"]], "footer",
            background=bg)
        self.assertGreater(len(png), 1000)
        self.assertEqual(Image.open(io.BytesIO(png)).width, imagecard.WIDTH)


class ButtonRoutingTest(unittest.TestCase):
    def _router(self):
        from bot.commands import CommandRouter
        router = CommandRouter.__new__(CommandRouter)
        router.prefixes = ["#"]
        router._button_contexts = {}
        router._button_lock = threading.Lock()
        router.log = __import__("logging").getLogger("test-buttons")
        return router

    def test_command_button_delegates_to_handle(self):
        router = self._router()
        seen = []
        router.handle = lambda text, ctx: (seen.append(text), [])[1]
        router.handle_button("a:仲裁表", object())
        self.assertEqual(seen, ["#仲裁表"])

    def test_wm_button_uses_stored_context(self):
        from bot.market import Item, Order
        router = self._router()
        item = Item(slug="test", name="测试物品", name_en="Test Item",
                    description="说明文本")
        sell = [Order("sell", 20, 2, "SellerOne", "ingame")]
        buy = [Order("buy", 13, 1, "BuyerOne", "ingame")]
        token = router._store_button_context({
            "item": item, "stats": None, "platform": "pc",
            "sell": sell, "buy": buy})
        replies_ = router.handle_button("wm:chat:%s" % token, object())
        self.assertEqual(len(replies_), 1)
        text = replies_[0].text
        self.assertIn("私聊话术", text)
        self.assertIn("/w SellerOne Hi! I wtb Test Item for 20p", text)
        self.assertIn("/w BuyerOne Hi! I wts Test Item for 13p", text)
        # 子消息继续带同一套按钮，可来回切换
        self.assertEqual(replies_[0].keyboard["content"]["rows"][0]["buttons"][0]
                         ["action"]["data"], "wm:trend:%s" % token)
        # 旧版按钮数据仍可解析（历史消息里残留的 wm:desc）
        legacy = router.handle_button("wm:desc:%s" % token, object())
        self.assertIn("/w SellerOne", legacy[0].text)
        self.assertIsNone(router._get_button_context("bad-token"))

    def test_expired_button_context(self):
        router = self._router()
        token = router._store_button_context({"item": None})
        router._button_contexts[token]["at"] -= 2000
        self.assertIsNone(router._get_button_context(token))


if __name__ == "__main__":
    unittest.main()
