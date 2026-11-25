"""QQ 官方文档接入层测试：事件归一化、Ed25519 验签、回复上下文。"""

import json
import os
import tempfile
import unittest

from bot import config as config_module
from bot.qqevent import to_onebot_event
from bot.qqofficial import QQOfficialClient
from bot.qqsignature import (sign, validation_response, verify,
                             verify_request)


SECRET = "DG5g3B4j9X2KOErG"


class SignatureTest(unittest.TestCase):
    def test_validation_matches_official_example(self):
        payload = {
            "op": 13,
            "d": {"plain_token": "Arq0D5A61EgUu4OxUvOp",
                  "event_ts": "1725442341"},
        }
        result = validation_response(SECRET, payload)
        self.assertEqual(result["plain_token"], "Arq0D5A61EgUu4OxUvOp")
        self.assertEqual(
            result["signature"],
            "87befc99c42c651b3aac0278e71ada338433ae26fcb24307bdc5ad38c1"
            "adc2d01bcfcadc0842edac85e85205028a1132afe09280305f13aa6909"
            "ffc2d652c706")

    def test_request_signature_roundtrip(self):
        body = json.dumps({"op": 0, "t": "C2C_MESSAGE_CREATE", "d": {}},
                          ensure_ascii=False).encode("utf-8")
        import time
        ts = str(int(time.time()))
        signature = sign(SECRET, ts.encode("ascii") + body)
        headers = {
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": ts,
        }
        ok, error = verify_request(headers, body, SECRET)
        self.assertTrue(ok, error)
        self.assertTrue(verify(SECRET, ts.encode("ascii") + body, signature))
        bad = dict(headers, **{"X-Signature-Ed25519": "00" * 64})
        self.assertFalse(verify_request(bad, body, SECRET)[0])
        self.assertFalse(verify_request(
            {"X-Signature-Ed25519": signature}, body, SECRET)[0])


class EventNormalizeTest(unittest.TestCase):
    SELF = "BOTOPENID0000000000000000000001"

    def test_group_at_event(self):
        payload = {
            "id": "EVENT_1", "op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "s": 7,
            "d": {
                "id": "MSG_1",
                "author": {"id": "USER_1", "member_openid": "MEMBER_1",
                           "username": "小明"},
                "content": " /wm 战甲 ",
                "group_openid": "GROUP_1",
            },
        }
        event = to_onebot_event(payload, self.SELF)
        self.assertEqual(event["post_type"], "message")
        self.assertEqual(event["message_type"], "group")
        self.assertEqual(event["group_id"], "GROUP_1")
        self.assertEqual(event["user_id"], "MEMBER_1")
        self.assertTrue(event["qq_at_me"])
        self.assertEqual(event["qq_msg_id"], "MSG_1")
        self.assertEqual(event["qq_event_id"], "EVENT_1")
        self.assertIn("/wm 战甲", event["raw_message"])
        self.assertIn("[CQ:at,qq=%s]" % self.SELF, event["raw_message"])

    def test_group_full_event_strips_mentions(self):
        payload = {
            "id": "EVENT_2", "op": 0, "t": "GROUP_MESSAGE_CREATE",
            "d": {
                "id": "MSG_2",
                "author": {"id": "USER_2"},
                "content": "<@%s> #wm 战甲" % self.SELF,
                "group_openid": "GROUP_2",
                "mentions": [{"id": self.SELF, "is_you": True}],
            },
        }
        event = to_onebot_event(payload, self.SELF)
        self.assertTrue(event["qq_at_me"])
        self.assertIn("#wm 战甲", event["raw_message"])
        self.assertNotIn("<@", event["raw_message"])

    def test_c2c_event(self):
        payload = {
            "id": "EVENT_3", "op": 0, "t": "C2C_MESSAGE_CREATE",
            "d": {"id": "MSG_3", "author": {"id": "USER_3"},
                  "content": "#帮助", "user_openid": "USER_3"},
        }
        event = to_onebot_event(payload, self.SELF)
        self.assertEqual(event["message_type"], "private")
        self.assertEqual(event["group_id"], "")
        self.assertEqual(event["user_id"], "USER_3")
        self.assertEqual(event["raw_message"], "#帮助")

    def test_unsupported_event_returns_none(self):
        self.assertIsNone(to_onebot_event(
            {"op": 0, "t": "GROUP_ADD_ROBOT", "d": {}}, self.SELF))


class GatewayClientTest(unittest.TestCase):
    def _client(self, transport="websocket"):
        client = QQOfficialClient({
            "transport": transport, "app_id": "123", "app_secret": SECRET,
            "self_id": "BOT", "callback_path": "/qq/callback",
        }, lambda event: None)
        self.addCleanup(client.close)
        return client

    def test_reply_context_generates_incrementing_seq(self):
        client = self._client()
        client.remember_reply_context({
            "group_id": "G1", "user_id": "U1", "qq_msg_id": "M1",
            "qq_event_id": "E1",
        })
        first = client._reply_fields("G1")
        second = client._reply_fields("G1")
        self.assertEqual(first["msg_id"], "M1")
        self.assertEqual(first["msg_seq"], 1)
        self.assertEqual(second["msg_seq"], 2)
        self.assertEqual(client._reply_fields("G2"), {})

    def test_reply_context_seq_unique_per_send(self):
        """同一条被动消息回多条（卡片图 + 按钮条）时 msg_seq 必须递增。"""
        client = self._client()
        ctx = client.reply_context({
            "group_id": "G9", "user_id": "U9", "qq_msg_id": "M9"})
        first = client._reply_fields("G9", ctx)
        second = client._reply_fields("G9", ctx)
        self.assertEqual(first["msg_id"], "M9")
        self.assertNotEqual(first["msg_seq"], second["msg_seq"])
        self.assertGreater(second["msg_seq"], first["msg_seq"])

    def test_image_message_never_carries_keyboard(self):
        """富媒体 msg_type=7 平台不认 keyboard，塞了也只会静默丢失。"""
        client = self._client()
        captured = {}

        def fake_post(path, body):
            captured["path"], captured["body"] = path, body
            return {}

        client._post = fake_post
        client._upload_image = lambda kind, chat_id, png: "FILE_INFO"
        client.send_group_image("G1", b"png")
        self.assertEqual(captured["body"]["msg_type"], 7)
        self.assertNotIn("keyboard", captured["body"])

    def test_png_size_reads_ihdr(self):
        from PIL import Image
        import io
        buffer = io.BytesIO()
        Image.new("RGB", (860, 420), (0, 0, 0)).save(buffer, "PNG")
        self.assertEqual(QQOfficialClient._png_size(buffer.getvalue()),
                         (860, 420))
        self.assertEqual(QQOfficialClient._png_size(b"not-a-png"), (0, 0))

    def test_card_is_markdown_with_media_and_keyboard(self):
        """有按钮的卡片：一条 Markdown 消息里放 qqbot-media 图 + keyboard。"""
        from PIL import Image
        import io
        from bot import keyboards

        buffer = io.BytesIO()
        Image.new("RGB", (860, 420), (10, 10, 10)).save(buffer, "PNG")
        client = self._client()
        captured = {}
        dropped = []

        def fake_post(path, body):
            captured["path"], captured["body"] = path, body
            return {}

        client._post = fake_post
        client._upload_material = lambda png: ("qqbot-media://image/v1/TOK",
                                               "IMG1")
        client._drop_material = dropped.append
        kb = keyboards.wm_keyboard("t")
        client.send_group_card("G1", buffer.getvalue(), keyboard=kb)
        body = captured["body"]
        self.assertEqual(body["msg_type"], 2)
        self.assertIn("qqbot-media://image/v1/TOK",
                      body["markdown"]["content"])
        self.assertIn("#860px", body["markdown"]["content"])
        self.assertIs(body["keyboard"], kb)
        self.assertNotIn("media", body)
        self.assertEqual(dropped, ["IMG1"])

    def test_interaction_ack_goes_to_sgroup(self):
        """互动回执必须打到 api.sgroup.qq.com（bot 域名回 200 {} 但不生效）。"""
        client = self._client()
        seen = {}

        def fake_request(url, headers, method="GET", body=None, **kwargs):
            seen["url"] = url
            seen["method"] = method
            seen["body"] = body
            return b"{}"

        client.http.request = fake_request
        client.tokens.get = lambda: "TOKEN"
        client.ack_interaction({"qq_interaction_id": "IID1",
                                "qq_timestamp": ""})
        self.assertTrue(seen["url"].startswith(
            "https://api.sgroup.qq.com/interactions/IID1"))
        self.assertEqual(seen["method"], "PUT")
        self.assertEqual(json.loads(seen["body"]), {"code": 0})

    def test_mentions_translation(self):
        client = self._client()
        self.assertEqual(client._mentions("[CQ:at,qq=G1] 你好"),
                         "<@G1> 你好")

    def test_webhook_status_and_callback(self):
        client = self._client("webhook")
        self.assertFalse(client.connected)
        self.assertEqual(client.url, "/qq/callback")
        answer = client.handle_webhook({
            "op": 13,
            "d": {"plain_token": "Arq0D5A61EgUu4OxUvOp",
                  "event_ts": "1725442341"},
        })
        self.assertEqual(answer["plain_token"], "Arq0D5A61EgUu4OxUvOp")
        self.assertEqual(client.handle_webhook({"op": 0, "d": {}}), {"op": 12})


class ApiErrorDetailTest(unittest.TestCase):
    def test_http_error_reveals_official_message(self):
        """HTTP 400 必须把官方 message/err_code 带出来，不能只显示 HTTP 状态。"""
        from bot.net import HttpError
        from bot.qqofficial import _api_error_message

        exc = HttpError(
            "HTTP 400 Bad Request", status=400,
            body=json.dumps({"message": "请求的资源不存在(用户/群已注销)",
                             "code": 11255, "err_code": 40011028,
                             "trace_id": "abc"}))
        text = _api_error_message("/v2/groups/X/messages", exc)
        self.assertIn("请求的资源不存在", text)
        self.assertIn("40011028", text)
        self.assertIn("HTTP 400", text)

    def test_http_error_without_body_still_readable(self):
        from bot.net import HttpError
        from bot.qqofficial import _api_error_message

        exc = HttpError("HTTP 502 Bad Gateway", status=502)
        self.assertIn("HTTP 502", _api_error_message("/v2/users/X/messages", exc))


class TestTargetCheckTest(unittest.TestCase):
    def test_reject_bot_self_id_and_old_qq_number(self):
        from bot.app import BotApp

        check = BotApp._check_test_target
        with self.assertRaises(ValueError) as ctx:
            check("12396883208788349398", "12396883208788349398")
        self.assertIn("机器人自己的 ID", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            check("1105800953", "12396883208788349398")
        self.assertIn("旧的 QQ 群号", str(ctx.exception))
        # 合法的 openid 不拦
        check("c5f9a1b2d3e4f5a6", "12396883208788349398")


class ConfigTest(unittest.TestCase):
    def test_example_config_has_qqbot(self):
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "config.example.json")
        cfg = config_module.load_config(path)
        self.assertEqual(cfg["qqbot"]["transport"], "websocket")
        self.assertEqual(cfg["qqbot"]["intents"], (1 << 25) | (1 << 26))
        self.assertNotIn("onebot", cfg)


if __name__ == "__main__":
    unittest.main()
