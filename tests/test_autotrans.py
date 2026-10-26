"""自动翻译兜底（英文短句 → 中文）的判定与缓存测试。"""

from __future__ import annotations

import os
import tempfile
import unittest

from bot import autotrans


class NeedsTranslationTest(unittest.TestCase):
    def test_english_short_needs(self):
        self.assertTrue(autotrans.needs_translation("Two For One"))
        self.assertTrue(autotrans.needs_translation("Tanked"))

    def test_chinese_skipped(self):
        self.assertFalse(autotrans.needs_translation("一石二鸟"))

    def test_mixed_skipped(self):
        self.assertFalse(autotrans.needs_translation("第 18 季 Tier"))

    def test_too_long_skipped(self):
        self.assertFalse(autotrans.needs_translation("word " * 40))

    def test_url_skipped(self):
        self.assertFalse(autotrans.needs_translation("https://warframe.com"))

    def test_short_noise_skipped(self):
        self.assertFalse(autotrans.needs_translation("XII"))
        self.assertFalse(autotrans.needs_translation("—"))


class ToChineseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        os.environ["QQWF_AUTOTRANS_CACHE"] = os.path.join(self.temp, "cache.json")
        autotrans.clear()
        self._original = autotrans._fetch

    def tearDown(self):
        autotrans._fetch = self._original
        autotrans.clear()

    def test_translate_and_cache(self):
        calls = []

        def fake(text):
            calls.append(text)
            return "二合一"

        autotrans._fetch = fake
        self.assertEqual(autotrans.to_chinese("Two For One"), "二合一")
        self.assertEqual(autotrans.to_chinese("Two For One"), "二合一")
        self.assertEqual(calls, ["Two For One"])  # 第二次走缓存

    def test_failure_keeps_original(self):
        def boom(text):
            raise RuntimeError("网络不可用")

        autotrans._fetch = boom
        self.assertEqual(autotrans.to_chinese("Tanked"), "Tanked")

    def test_english_result_rejected(self):
        autotrans._fetch = lambda text: "Two For One"
        self.assertEqual(autotrans.to_chinese("Two For One"), "Two For One")

    def test_limit(self):
        autotrans._fetch = lambda text: "不该被调用"
        long_text = "A" * 120
        self.assertEqual(autotrans.to_chinese(long_text, limit=20), long_text)


if __name__ == "__main__":
    unittest.main()
