import logging
import time
import unittest
from types import SimpleNamespace

from bot.updater import DataUpdater, format_age


class FakeMarket:
    def __init__(self):
        self.refresh_calls = 0
        self.load_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        return 100

    def load_items(self):
        self.load_calls += 1
        return SimpleNamespace(items=[0] * 100)

    def cache_age(self):
        return 3600

    def item_count(self):
        return 100


class FakeRiven:
    def __init__(self):
        self.refresh_calls = 0
        self.load_calls = 0

    def refresh(self):
        self.refresh_calls += 1
        return 420

    def weapons(self):
        self.load_calls += 1
        return {}

    def cache_age(self):
        return 7200

    def weapon_count(self):
        return 420


class UpdaterTest(unittest.TestCase):
    def make_updater(self, **overrides):
        config = {"interval_seconds": 3600, "manual_cooldown_seconds": 600}
        config.update(overrides)
        market, riven = FakeMarket(), FakeRiven()
        updater = DataUpdater(market, riven, config,
                              logger=logging.getLogger("test.updater"))
        return updater, market, riven

    def test_refresh_all_force(self):
        updater, market, riven = self.make_updater()
        results = updater.refresh_all(force=True)
        self.assertEqual(market.refresh_calls, 1)
        self.assertEqual(riven.refresh_calls, 1)
        self.assertIn("100", results["market"])
        self.assertIn("420", results["riven"])
        self.assertNotIn("tenno", results)
        self.assertGreater(updater.last_run, 0)

    def test_refresh_all_soft(self):
        updater, market, riven = self.make_updater()
        updater.refresh_all(force=False)
        self.assertEqual(market.load_calls, 1)
        self.assertEqual(riven.load_calls, 1)

    def test_disable_sources(self):
        updater, market, riven = self.make_updater(market=False, riven=False)
        results = updater.refresh_all()
        self.assertEqual(results, {})
        self.assertEqual(market.refresh_calls, 0)

    def test_trigger_and_cooldown(self):
        updater, market, riven = self.make_updater()
        message = updater.trigger()
        self.assertIn("已开始", message)
        deadline = time.time() + 2
        while updater.last_run == 0 and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(market.refresh_calls, 1)
        again = updater.trigger()
        self.assertIn("刚刚更新过", again)

    def test_trigger_disabled(self):
        updater, _, _ = self.make_updater(enabled=False)
        self.assertIn("未启用", updater.trigger())

    def test_force_clears_short_caches(self):
        class FakeCache:
            def __init__(self):
                self.cleared = False

            def clear(self):
                self.cleared = True

        cache = FakeCache()
        updater, _, _ = self.make_updater()
        updater.caches = [cache]
        updater.refresh_all(force=True)
        self.assertTrue(cache.cleared)

    def test_status(self):
        updater, _, _ = self.make_updater()
        updater.refresh_all()
        text = updater.status_text("#")
        self.assertIn("数据更新状态", text)
        self.assertIn("市场物品：100 条", text)
        self.assertIn("紫卡武器：420 把", text)
        self.assertNotIn("紫卡分析", text)
        self.assertIn("#更新", text)

    def test_format_age(self):
        self.assertEqual(format_age(-1), "未加载")
        self.assertEqual(format_age(30), "刚刚")
        self.assertEqual(format_age(120), "2 分钟前")
        self.assertEqual(format_age(7200), "2.0 小时前")
        self.assertEqual(format_age(90000), "1.0 天前")


if __name__ == "__main__":
    unittest.main()
