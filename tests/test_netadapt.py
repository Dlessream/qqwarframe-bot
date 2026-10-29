"""网络适配与代理链的离线测试（不联网）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from bot import netadapt
from bot.net import HttpClient, HttpError


def _cfg(**network: object) -> dict:
    base = {
        "warframe_market": {"proxy": "http://127.0.0.1:10809"},
        "worldstate": {"proxy": "http://127.0.0.1:10809"},
        "wiki": {"proxy": "http://127.0.0.1:10809"},
        "arbi": {"proxy": "http://127.0.0.1:10809"},
        "rotation": {"proxy": "http://127.0.0.1:10809"},
        "web": {"update": {"proxy": ""}},
        "network": {
            "auto_adapt": True,
            "discover_local": False,
            "allow_direct": True,
            "proxies": [],
            "prefer": "order",
        },
    }
    base["network"].update(network)  # type: ignore[arg-type]
    return base


def _ok(ms: int = 100, status: int = 200) -> dict:
    return {"ok": True, "status": status, "ms": ms, "blocked": False}


def _down(error: str = "timeout") -> dict:
    return {"ok": False, "status": 0, "ms": 6000, "blocked": False,
            "error": error}


def _blocked(status: int = 403) -> dict:
    return {"ok": False, "status": status, "ms": 200, "blocked": True}


class CandidatesTest(unittest.TestCase):
    def test_module_proxy_comes_first(self):
        cfg = _cfg(proxies=["http://b:2"])
        order = netadapt.candidates(cfg, "market")
        self.assertEqual(order[0], "http://127.0.0.1:10809")
        self.assertIn("http://b:2", order)

    def test_missing_proxy_means_system(self):
        cfg = _cfg()
        cfg["warframe_market"].pop("proxy")
        self.assertEqual(netadapt.candidates(cfg, "market")[0], "")

    def test_direct_always_last(self):
        cfg = _cfg(proxies=["http://b:2"])
        order = netadapt.candidates(cfg, "market")
        self.assertEqual(order[-1], "none")
        self.assertEqual(order.count("none"), 1)

    def test_dedupe(self):
        cfg = _cfg(proxies=["http://127.0.0.1:10809", "none"])
        order = netadapt.candidates(cfg, "market")
        self.assertEqual(len(order), len(set(order)))

    def test_local_discovery_can_be_disabled(self):
        cfg = _cfg(discover_local=True)
        with mock.patch.object(netadapt, "_port_open", return_value=True):
            found = netadapt.local_proxy_candidates(cfg)
        self.assertTrue(found)
        self.assertEqual(found[0].split(":")[-1], "7890")

    def test_local_discovery_off(self):
        self.assertEqual(netadapt.local_proxy_candidates(_cfg()), [])


class PickTest(unittest.TestCase):
    def test_order_picks_first_usable(self):
        result = {"a": _down(), "b": _ok(300), "c": _ok(50)}
        self.assertEqual(netadapt.pick(result, "order"), "b")

    def test_fastest_picks_lowest_latency(self):
        result = {"a": _down(), "b": _ok(300), "c": _ok(50)}
        self.assertEqual(netadapt.pick(result, "fastest"), "c")

    def test_fastest_skips_inherited(self):
        result = {"a": _ok(0, 200) | {"inherited": True}, "b": _ok(400)}
        self.assertEqual(netadapt.pick(result, "fastest"), "b")

    def test_blocked_is_not_usable(self):
        self.assertIsNone(netadapt.pick({"a": _blocked()}, "order"))

    def test_nothing_usable(self):
        self.assertIsNone(netadapt.pick({"a": _down(), "none": _down()}))


class SuggestTest(unittest.TestCase):
    def test_keeps_current_proxy_when_it_works(self):
        cfg = _cfg()
        result = {"market": {"http://127.0.0.1:10809": _ok(80),
                             "none": _ok(20)}}
        self.assertEqual(netadapt.suggest(cfg, result), {})

    def test_switches_when_current_is_down(self):
        cfg = _cfg()
        result = {"market": {"http://127.0.0.1:10809": _down(),
                             "none": _ok(20)}}
        self.assertEqual(netadapt.suggest(cfg, result), {"market": "none"})

    def test_switches_when_current_is_blocked(self):
        cfg = _cfg()
        result = {"wiki": {"http://127.0.0.1:10809": _blocked(),
                           "none": _ok()}}
        self.assertEqual(netadapt.suggest(cfg, result)["wiki"], "none")

    def test_keeps_silent_when_nothing_works(self):
        cfg = _cfg()
        result = {"market": {"http://127.0.0.1:10809": _down(),
                             "none": _down()}}
        self.assertEqual(netadapt.suggest(cfg, result), {})


class EffectiveTest(unittest.TestCase):
    """最终生效链路：当前配的能用时也算数，不能只算「需要改」的那些。"""

    def test_current_proxy_counts_as_available(self):
        cfg = _cfg()
        result = {"arbi": {"none": _ok(161), "http://b:2": _down()}}
        cfg["arbi"]["proxy"] = "none"
        final = netadapt.effective(cfg, result)
        self.assertEqual(final["arbi"], "none")

    def test_uses_suggestion_when_current_is_down(self):
        cfg = _cfg()
        result = {"market": {"http://127.0.0.1:10809": _down(),
                             "none": _ok(20)}}
        final = netadapt.effective(cfg, result)
        self.assertEqual(final["market"], "none")

    def test_empty_when_nothing_works(self):
        cfg = _cfg()
        result = {"market": {"http://127.0.0.1:10809": _down(),
                             "none": _blocked()}}
        self.assertEqual(netadapt.effective(cfg, result), {})

    def test_system_proxy_is_a_real_value(self):
        """空串代表系统代理，`if value` 会把它误判成没有。"""
        cfg = _cfg()
        cfg["web"]["update"]["proxy"] = ""
        result = {"update": {"": _ok(20), "none": _down()}}
        self.assertEqual(netadapt.effective(cfg, result)["update"], "")
        text = netadapt.report(cfg, result)
        self.assertIn("系统代理", text)
        self.assertNotIn("无可用链路", text)

    def test_report_shows_kept_proxy(self):
        cfg = _cfg()
        cfg["arbi"]["proxy"] = "none"
        result = {"arbi": {"none": _ok(161)}}
        text = netadapt.report(cfg, result)
        self.assertIn("沿用配置", text)
        self.assertNotIn("无可用链路", text)


class ApplyTest(unittest.TestCase):
    def test_writes_back_and_sets_fallbacks(self):
        cfg = _cfg(proxies=["http://b:2"])
        changes = netadapt.apply_suggestion(cfg, {"market": "none"})
        self.assertEqual(cfg["warframe_market"]["proxy"], "none")
        self.assertTrue(changes)
        self.assertIn("none", netadapt.global_fallbacks()
                      if hasattr(netadapt, "global_fallbacks") else ["none"])

    def test_no_change_when_same(self):
        cfg = _cfg()
        self.assertEqual(
            netadapt.apply_suggestion(cfg, {"market": "http://127.0.0.1:10809"}),
            [])

    def test_nested_update_proxy(self):
        cfg = _cfg()
        netadapt.apply_suggestion(cfg, {"update": "none"})
        self.assertEqual(cfg["web"]["update"]["proxy"], "none")


class AdaptTest(unittest.TestCase):
    def setUp(self):
        # 每个用例用独立缓存文件，免得读到仓库里 data/net_probe.json 的缓存
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = os.path.join(self._tmp.name, "net.json")

    def _fresh(self, **network: object) -> dict:
        cfg = _cfg(**network)
        cfg["network"]["probe_cache_file"] = self.cache
        return cfg

    def test_disabled_does_nothing(self):
        cfg = self._fresh(auto_adapt=False)
        with mock.patch.object(netadapt, "probe") as probe:
            self.assertEqual(netadapt.adapt(cfg), {})
        probe.assert_not_called()

    def test_uses_cache_within_ttl(self):
        cfg = self._fresh()
        netadapt.save_cache(cfg, {"market": {"none": _ok()}})
        with mock.patch.object(netadapt, "probe") as probe:
            netadapt.adapt(cfg)
        probe.assert_not_called()

    def test_force_ignores_cache(self):
        cfg = self._fresh()
        netadapt.save_cache(cfg, {"market": {"none": _ok()}})
        with mock.patch.object(netadapt, "probe",
                               return_value={"market": {"none": _down()}}):
            netadapt.adapt(cfg, force=True)

    def test_probe_failure_is_not_fatal(self):
        cfg = self._fresh()
        with mock.patch.object(netadapt, "probe", side_effect=OSError("boom")):
            self.assertEqual(netadapt.adapt(cfg), {})

    def test_cache_roundtrip(self):
        cfg = self._fresh()
        payload = {"market": {"none": _ok(42)}}
        netadapt.save_cache(cfg, payload)
        loaded = netadapt.load_cache(cfg)
        self.assertEqual(loaded["market"]["none"]["ms"], 42)

    def test_cache_expired(self):
        cfg = self._fresh(probe_ttl_seconds=1)
        netadapt.save_cache(cfg, {"market": {"none": _ok()}})
        import json
        with open(self.cache, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        data["ts"] -= 3600
        with open(self.cache, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        self.assertIsNone(netadapt.load_cache(cfg))



class ReportTest(unittest.TestCase):
    def test_report_lists_every_proxy(self):
        cfg = _cfg(proxies=["http://b:2"])
        result = {"market": {"http://127.0.0.1:10809": _down(),
                             "http://b:2": _ok(30),
                             "none": _blocked()}}
        text = netadapt.report(cfg, result)
        self.assertIn("市场报价", text)
        self.assertIn("http://b:2", text)
        self.assertIn("采用", text)
        self.assertIn("被拦", text)


class ProxyChainTest(unittest.TestCase):
    """HttpClient 的代理链：失败自动换下一条，成功后记住。"""

    def _client(self, proxy: str, fallbacks: list) -> HttpClient:
        return HttpClient(proxy=proxy, proxy_fallbacks=fallbacks, retries=0,
                          timeout=1, auto_switch=True)

    def test_falls_back_to_working_proxy(self):
        client = self._client("bad1", ["bad2", "none"])

        def fake(index: int, url: str, *args: object, **kwargs: object) -> bytes:
            if client.proxies[index] == "none":
                return b"ok"
            raise HttpError("网络错误：down", status=0, url=url)

        client._request_once = fake  # type: ignore[assignment]
        self.assertEqual(client.request("https://example.com"), b"ok")
        self.assertEqual(client.current_proxy, "none")

    def test_403_triggers_switch(self):
        client = self._client("blocked", ["none"])

        def fake(index: int, url: str, *args: object, **kwargs: object) -> bytes:
            if client.proxies[index] == "none":
                return b"ok"
            raise HttpError("HTTP 403", status=403, url=url)

        client._request_once = fake  # type: ignore[assignment]
        self.assertEqual(client.request("https://example.com"), b"ok")
        self.assertEqual(client.current_proxy, "none")

    def test_business_error_does_not_switch(self):
        client = self._client("p1", ["none"])

        def fake(index: int, url: str, *args: object, **kwargs: object) -> bytes:
            raise HttpError("HTTP 404", status=404, url=url)

        client._request_once = fake  # type: ignore[assignment]
        with self.assertRaises(HttpError):
            client.request("https://example.com")
        self.assertEqual(client.current_proxy, "p1")

    def test_all_down_raises_last_error(self):
        client = self._client("bad1", ["bad2"])

        def fake(index: int, url: str, *args: object, **kwargs: object) -> bytes:
            raise HttpError("网络错误：down", status=0, url=url)

        client._request_once = fake  # type: ignore[assignment]
        with self.assertRaises(HttpError):
            client.request("https://example.com")

    def test_auto_switch_off(self):
        client = self._client("bad1", ["none"])
        client.auto_switch = False

        def fake(index: int, url: str, *args: object, **kwargs: object) -> bytes:
            if client.proxies[index] == "none":
                return b"ok"
            raise HttpError("网络错误：down", status=0, url=url)

        client._request_once = fake  # type: ignore[assignment]
        with self.assertRaises(HttpError):
            client.request("https://example.com")

    def test_global_fallbacks_used_by_default(self):
        with mock.patch.object(netadapt, "global_fallbacks",
                               return_value=["http://g:1", "none"]):
            from bot import net as net_module
            net_module.set_global_fallbacks(["http://g:1", "none"])
            try:
                client = HttpClient(proxy="p1", timeout=1, retries=0)
                self.assertEqual(client.proxies[1:], ["http://g:1", "none"])
            finally:
                net_module.set_global_fallbacks([])


if __name__ == "__main__":
    unittest.main()
