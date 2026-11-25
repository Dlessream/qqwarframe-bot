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

    def test_reply_holds_keyboard(self):
        kb = keyboards.wm_keyboard("abc")
        reply = replies.Reply(text="x", keyboard=kb)
        self.assertIs(reply.keyboard, kb)


class InteractionEventTest(unittest.TestCase):
    def test_button_click_mapped(self):
        payload = {
            "t": "INTERACTION_CREATE", "id": "evt-123",
            "d": {
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
