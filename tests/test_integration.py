"""端到端测试：官方 Bot Webhook 事件 + mock 市场/Wiki/QQ OpenAPI + 真实 BotApp。"""

import json
import os
import threading
import time
import unittest

from bot.app import BotApp

from .mock_http import MockServer, json_response

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def official_event(user_openid, text, group_openid="20002", at_bot=False,
                   event_id="EVENT_1", msg_id="MSG_1"):
    """构造 QQ 官方 GROUP_AT/MESSAGE_CREATE 事件。"""
    event_type = "GROUP_AT_MESSAGE_CREATE" if at_bot else "GROUP_MESSAGE_CREATE"
    payload = {
        "id": event_id, "op": 0, "t": event_type, "s": 1,
        "d": {
            "id": msg_id,
            "author": {"id": user_openid, "member_openid": user_openid,
                       "username": "tester"},
            "content": text,
            "group_openid": group_openid,
            "message_scene": {"source": "default", "ext": []},
        },
    }
    if at_bot:
        payload["d"]["mentions"] = [
            {"id": "BOT1", "is_you": True, "member_openid": "BOT1"}]
    return payload


def build_app_config(http_base):
    return {
        "qqbot": {
            "enabled": True,
            "transport": "webhook",
            "app_id": "11111111",
            "app_secret": "secret",
            "self_id": "BOT1",
            "api_base": http_base,
            "token_url": http_base + "/app/getAppAccessToken",
            "callback_path": "/qq/callback",
            "signature_tolerance_seconds": 0,
        },
        "behavior": {
            "prefixes": ["#"],
            "allow_private": True,
            "respond_to_at": True,
            "at_sender": True,
            "whitelist_groups": [],
            "blacklist_groups": [],
            # 集成测试要模拟「群里正常得到回复」，用黑名单制放开
            "group_policy": "blacklist",
            "cooldown_seconds": 0,
            "max_replies_per_minute": 100,
            "max_reply_chars": 1200,
            "preload_items": False,
        },
        "warframe_market": {
            "base_url": http_base,
            "language": "zh-hans",
            "platform": "pc",
            "crossplay": True,
            "timeout_seconds": 5,
            "retries": 0,
            "max_requests_per_second": 0,
            "proxy": "",
            "insecure_tls": False,
            "cache_ttl_seconds": 3600,
            "cache_file": "",
            "alias_file": os.path.join(BASE, "data", "aliases.json"),
            "top_orders": 3,
            "price_orders": 5,
            "user_agent": "QQWarframeBot/test",
        },
        "wiki": {
            "api_url": http_base + "/api.php",
            "site_url": "https://warframe.huijiwiki.com/wiki/",
            "timeout_seconds": 5,
            "retries": 0,
            "proxy": "",
            "summary_chars": 280,
            "user_agent": "QQWarframeBot/test",
        },
        # 图片走单独测试，这里只验证文本收发链路
        "image": {"enabled": False, "font": "", "font_bold": ""},
        "worldstate": {"enabled": False, "base_url": http_base},
        "auto_update": {"enabled": False},
        "web": {"enabled": False},
        "alerts": {"enabled": False,
                   "file": os.path.join(BASE, "data", "test_alerts.json")},
        "log": {"level": "CRITICAL", "file": ""},
    }


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.http = MockServer()
        self.http.add_json("/v2/items", load_fixture("items.json"))
        self.http.add_json("/v2/item/mesa_prime_set", load_fixture("item_mesa.json"))
        self.http.add_json("/v2/orders/item/mesa_prime_set/top",
                           load_fixture("orders_mesa.json"))
        self.http.add_json("/v1/items/mesa_prime_set/statistics",
                           load_fixture("stats_mesa.json"))
        self.http.add("/api.php", self._wiki)
        self.http.add_json("/app/getAppAccessToken", {
            "access_token": "TEST_TOKEN", "expires_in": 7200})
        self.http.add_json("/v2/groups/20002/messages", {"id": "SENT_1"})
        self.base_url = self.http.start()

        self.config = build_app_config(self.base_url)
        self.app = BotApp(self.config)
        self.app.start()
        self.app.qqbot._webhook_ready = True

    def tearDown(self):
        self.app.stop()
        self.http.stop()

    @staticmethod
    def _wiki(query, headers, index):
        action = (query.get("action") or [""])[0]
        if action == "parse":
            return json_response(load_fixture("wiki_parse.json"))
        if (query.get("list") or [""])[0] == "search":
            return json_response(load_fixture("wiki_search.json"))
        return json_response(load_fixture("wiki_extract.json"))

    def _sent_messages(self):
        return [json.loads(item["body"].decode("utf-8"))
                for item in self.http.requests
                if item.get("method") == "POST"
                and item["path"] == "/v2/groups/20002/messages"]

    def _wait_messages(self, count, timeout=15):
        deadline = time.time() + timeout
        while len(self._sent_messages()) < count and time.time() < deadline:
            time.sleep(0.05)

    def test_replies(self):
        # 逐条发送，避免线程池并发导致断言顺序不稳定
        self.app.qqbot.handle_webhook(
            official_event("10001", "#查 mesa", event_id="E1", msg_id="M1"))
        self._wait_messages(1)
        self.app.qqbot.handle_webhook(
            official_event("10004", " 查 绝路", at_bot=True,
                           event_id="E2", msg_id="M2"))
        self._wait_messages(2)

        sent = self._sent_messages()
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].get("group_id"), None)  # openid 在 URL 里
        self.assertTrue(sent[0]["content"].startswith("<@10001> "))
        self.assertTrue(sent[1]["content"].startswith("<@10004> "))
        self.assertTrue(sent[0]["content"].find("Mesa") >= 0
                        or "Mesa" in sent[0]["content"])
        self.assertEqual(sent[0]["msg_id"], "M1")
        self.assertEqual(sent[1]["msg_id"], "M2")
        self.assertEqual(sent[0]["msg_seq"], 1)
        self.assertEqual(sent[1]["msg_seq"], 1)

        # 普通聊天没有触发第三条回复
        self.app.qqbot.handle_webhook(
            official_event("10005", "今天天气不错", event_id="E3", msg_id="M3"))
        time.sleep(0.8)
        self.assertEqual(len(self._sent_messages()), 2)

    def test_market_requests(self):
        self.app.qqbot.handle_webhook(
            official_event("10001", "#查 mesa", event_id="E1", msg_id="M1"))
        self._wait_messages(1)
        self.assertTrue(self.http.count("/v2/items") >= 1)
        self.assertTrue(self.http.count("/v2/item/mesa_prime_set") >= 1)
        self.assertTrue(self.http.count("/v2/orders/item/mesa_prime_set/top") >= 1)
        self.assertTrue(self.http.count("/v1/items/mesa_prime_set/statistics") >= 1)


if __name__ == "__main__":
    unittest.main()
