"""端到端测试：mock OneBot 服务端 + mock 市场/Wiki HTTP 服务 + 真实 BotApp。"""

import json
import os
import socket
import threading
import time
import unittest

from bot import ws
from bot.app import BotApp

from .mock_http import MockServer, json_response

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class MockOneBotServer:
    """极简 OneBot 11 服务端：发事件、收 action、回 echo。"""

    def __init__(self, events, action_timeout: float = 10.0):
        self.events = events
        self.action_timeout = action_timeout
        self.received = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self._thread = None
        self.error = None

    @property
    def url(self):
        return "ws://127.0.0.1:%d" % self.port

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            conn, _ = self.listener.accept()
            socket_ws = ws.accept(conn, timeout=10)
            try:
                for event in self.events:
                    socket_ws.send_text(json.dumps(event, ensure_ascii=False))
                    self.received.append(
                        self._wait_action(socket_ws, "send_group_msg", self.action_timeout))
                # 普通聊天不应触发回复
                socket_ws.send_text(json.dumps({
                    "post_type": "message", "message_type": "group", "self_id": 30003,
                    "user_id": 10001, "group_id": 20002,
                    "message": [{"type": "text", "data": {"text": "今天天气不错"}}],
                    "raw_message": "今天天气不错",
                }, ensure_ascii=False))
                time.sleep(0.6)
            finally:
                socket_ws.close()
                conn.close()
        except Exception as exc:  # pragma: no cover - 测试辅助
            self.error = exc

    def _wait_action(self, socket_ws, action, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.2, deadline - time.time())
            payload = json.loads(socket_ws.recv_text(timeout=remaining))
            if payload.get("action") == action:
                socket_ws.send_text(json.dumps({
                    "status": "ok", "retcode": 0,
                    "data": {"message_id": len(self.received) + 1},
                    "echo": payload.get("echo"),
                }))
                return payload.get("params") or {}
        raise AssertionError("等待 %s 超时" % action)

    def stop(self):
        try:
            self.listener.close()
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)


def build_app_config(http_base, ws_url):
    return {
        "onebot": {
            "ws_url": ws_url,
            "access_token": "",
            "reconnect_min_seconds": 1,
            "reconnect_max_seconds": 2,
            "heartbeat_seconds": 25,
            "heartbeat_timeout_seconds": 75,
            "api_timeout_seconds": 5,
        },
        "behavior": {
            "prefixes": ["#"],
            "allow_private": True,
            "respond_to_at": True,
            "at_sender": True,
            "whitelist_groups": [],
            "blacklist_groups": [],
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
        "image": {"enabled": True, "font": "", "font_bold": ""},
        "worldstate": {"enabled": False, "base_url": http_base},
        "auto_update": {"enabled": False},
        "alerts": {"enabled": False, "file": os.path.join(BASE, "data", "test_alerts.json")},
        "log": {"level": "CRITICAL", "file": ""},
    }


def group_message(user_id, text, group_id=20002, self_id=30003, at_bot=False):
    segments = []
    if at_bot:
        segments.append({"type": "at", "data": {"qq": str(self_id)}})
    segments.append({"type": "text", "data": {"text": text}})
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "self_id": self_id,
        "user_id": user_id,
        "group_id": group_id,
        "message": segments,
        "raw_message": ("[CQ:at,qq=%s] " % self_id if at_bot else "") + text,
        "time": int(time.time()),
        "sender": {"user_id": user_id, "nickname": "tester"},
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
        self.base_url = self.http.start()

        self.events = [
            group_message(10001, "#查 mesa"),
            group_message(10004, " 查 绝路", at_bot=True),
        ]
        self.bot_server = MockOneBotServer(self.events)
        self.bot_server.start()

        self.config = build_app_config(self.base_url, self.bot_server.url)
        self.app = BotApp(self.config)
        self.app_thread = threading.Thread(target=self.app.run, daemon=True)
        self.app_thread.start()
        # 等待连接建立
        for _ in range(50):
            if self.app.onebot.connected:
                break
            time.sleep(0.05)

    def tearDown(self):
        self.bot_server.stop()
        self.app.stop()
        self.app_thread.join(timeout=5)
        self.http.stop()

    @staticmethod
    def _wiki(query, headers, index):
        action = (query.get("action") or [""])[0]
        if action == "parse":
            return json_response(load_fixture("wiki_parse.json"))
        if (query.get("list") or [""])[0] == "search":
            return json_response(load_fixture("wiki_search.json"))
        return json_response(load_fixture("wiki_extract.json"))

    def test_replies(self):
        deadline = time.time() + 15
        while len(self.bot_server.received) < 2 and time.time() < deadline:
            time.sleep(0.05)
        if self.bot_server.error:
            raise self.bot_server.error

        self.assertEqual(len(self.bot_server.received), 2)

        from bot import imagecard

        first = self.bot_server.received[0]
        self.assertEqual(first.get("group_id"), 20002)
        self.assertTrue(first.get("message", "").startswith("[CQ:at,qq=10001] "))
        second = self.bot_server.received[1]
        self.assertTrue(second.get("message", "").startswith("[CQ:at,qq=10004] "))

        if imagecard.available():
            self.assertIn("[CQ:image,file=base64://", first["message"])
            self.assertIn("[CQ:image,file=base64://", second["message"])
        else:
            self.assertIn("Mesa Prime", first["message"])
            self.assertIn("绝路 Prime", second["message"])

        # 普通聊天没有触发第三条回复
        time.sleep(0.8)
        self.assertEqual(len(self.bot_server.received), 2)

    def test_market_requests(self):
        deadline = time.time() + 15
        while len(self.bot_server.received) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.http.count("/v2/items") >= 1)
        self.assertTrue(self.http.count("/v2/item/mesa_prime_set") >= 1)
        self.assertTrue(self.http.count("/v2/orders/item/mesa_prime_set/top") >= 1)
        self.assertTrue(self.http.count("/v1/items/mesa_prime_set/statistics") >= 1)


if __name__ == "__main__":
    unittest.main()

