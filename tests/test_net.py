"""网络层的单元测试（不联网）：错误分类与「链路不通」的快速失败。"""

from __future__ import annotations

import errno
import socket
import unittest
import urllib.error

from bot.net import HttpClient, HttpError, is_unreachable


class UnreachableTest(unittest.TestCase):
    """区分「这条路根本不通」和「这次没成功」。"""

    def test_network_unreachable(self):
        self.assertTrue(is_unreachable(
            OSError(errno.ENETUNREACH, "Network is unreachable")))

    def test_host_unreachable(self):
        self.assertTrue(is_unreachable(
            OSError(errno.EHOSTUNREACH, "No route to host")))

    def test_connection_refused(self):
        self.assertTrue(is_unreachable(
            OSError(errno.ECONNREFUSED, "Connection refused")))

    def test_urlerror_wrapping_reason(self):
        self.assertTrue(is_unreachable(urllib.error.URLError(
            OSError(errno.ENETUNREACH, "Network is unreachable"))))

    def test_text_only_reason(self):
        self.assertTrue(is_unreachable("[Errno 101] Network is unreachable"))
        self.assertTrue(is_unreachable("Connection refused"))

    def test_timeout_is_not_unreachable(self):
        self.assertFalse(is_unreachable(socket.timeout("timed out")))
        self.assertFalse(is_unreachable("The read operation timed out"))

    def test_unknown_error_is_not_unreachable(self):
        self.assertFalse(is_unreachable(OSError(errno.EPIPE, "Broken pipe")))


class FastFailTest(unittest.TestCase):
    """链路不通时不再重试，直接换代理——否则每次都白等一整个超时。"""

    def _client(self, retries: int = 2) -> HttpClient:
        return HttpClient(proxy="p1", proxy_fallbacks=[], retries=retries,
                          timeout=1, auto_switch=False)

    def _raise(self, reason: BaseException):
        client = self._client()
        calls = []

        class _Opener:
            def open(self, req, timeout=None):
                calls.append(1)
                raise urllib.error.URLError(reason)

        client._opener_for = lambda index: _Opener()  # type: ignore[assignment]
        return client, calls

    def test_unreachable_skips_retries(self):
        client, calls = self._raise(
            OSError(errno.ENETUNREACH, "Network is unreachable"))
        with self.assertRaises(HttpError) as ctx:
            client.request("https://example.com")
        self.assertEqual(len(calls), 1, "不可达时只该请求一次，不该重试")
        self.assertTrue(ctx.exception.unreachable)

    def test_timeout_still_retries(self):
        client, calls = self._raise(socket.timeout("timed out"))
        with self.assertRaises(HttpError) as ctx:
            client.request("https://example.com")
        self.assertEqual(len(calls), client.retries + 1)
        self.assertFalse(ctx.exception.unreachable)

    def test_unreachable_switches_proxy(self):
        client = HttpClient(proxy="dead", proxy_fallbacks=["alive"],
                            retries=2, timeout=1, auto_switch=True)

        def fake(index: int, url: str, *args: object, **kwargs: object) -> bytes:
            if client.proxies[index] == "alive":
                return b"ok"
            raise HttpError("网络错误：[Errno 101] Network is unreachable",
                            status=0, url=url)

        client._request_once = fake  # type: ignore[assignment]
        self.assertEqual(client.request("https://example.com"), b"ok")
        self.assertEqual(client.current_proxy, "alive")


if __name__ == "__main__":
    unittest.main()
