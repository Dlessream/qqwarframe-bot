"""紫卡截图分析：解析、武器匹配、评分与命令层。

OCR 本身（PP-OCRv5 onnx）依赖 numpy + onnxruntime，单测里不跑模型，
用 mock 把「识别出的行」直接喂进解析层——解析与评分才是要守住的部分。
"""

import unittest
from unittest import mock

from bot import ppocr, rivenscan
from bot.commands import Context
from bot.riven import RivenWeapon
from bot.rivencalc import RivenAnalyzer

FULL_CARD = """执法者
Laci-cron
+185.4% 伤害
+101.2% 多重射击
+168.7% 暴击几率
-53.9% 变焦
段位 15 循环 0 Madurai"""

# 同一张卡没升满（Lv.0，卡面数值是满级的 1/9）
UNRANKED_CARD = """执法者
Laci-cron
+20.6% 伤害
+11.2% 多重射击
+18.7% 暴击几率
-6.0% 变焦
段位 15 循环 0 Madurai"""


def weapon(slug="cron", name="执法者", name_en="Cron", riven_type="rifle",
           disposition=1.2):
    return RivenWeapon(slug=slug, name=name, name_en=name_en,
                       riven_type=riven_type, group="primary",
                       disposition=disposition, mastery_rank=10)


def lines(text):
    return [ppocr.TextLine(line, 0.9, (0, index * 40, 400, index * 40 + 30))
            for index, line in enumerate(text.splitlines())]


class ParseTest(unittest.TestCase):
    def test_full_card_four_stats(self):
        candidates = rivenscan.read_texts([FULL_CARD])
        names = [item.name for item in candidates]
        self.assertEqual(names, ["伤害", "多重射击", "暴击几率", "变焦"])
        self.assertEqual(candidates[-1].value, -53.9)

    def test_resolve_picks_3plus1(self):
        analysis = RivenAnalyzer().analyze(weapon())
        resolution = rivenscan.resolve(rivenscan.read_texts([FULL_CARD]), analysis)
        self.assertEqual(resolution.config, "3+1")
        self.assertEqual(resolution.scale, 1.0)
        self.assertEqual([name for name, _ in resolution.pairs],
                         ["伤害", "多重射击", "暴击几率", "变焦"])
        self.assertEqual(resolution.notes, [])

    def test_unranked_card_scale(self):
        """没升满的卡按 1/9 换算，卡面数值保持不变。"""
        analysis = RivenAnalyzer().analyze(weapon())
        resolution = rivenscan.resolve(
            rivenscan.read_texts([UNRANKED_CARD]), analysis)
        self.assertLess(resolution.scale, 0.2)
        self.assertEqual(resolution.pairs[0], ("伤害", 20.6))
        self.assertTrue(any("没升满" in note for note in resolution.notes))
        evaluations = analysis.evaluate(resolution.pairs, scale=resolution.scale)
        self.assertTrue(all(not item.negative or item.percentile >= 0
                            for item in evaluations))

    def test_score_grade_for_mid_roll(self):
        analysis = RivenAnalyzer().analyze(weapon())
        resolution = rivenscan.resolve(rivenscan.read_texts([FULL_CARD]), analysis)
        evaluations = analysis.evaluate(resolution.pairs, scale=resolution.scale)
        self.assertEqual(len(evaluations), 4)
        self.assertTrue(all(item.stat is not None for item in evaluations))
        self.assertTrue(all(item.grade for item in evaluations))


class WeaponGuessTest(unittest.TestCase):
    def setUp(self):
        self.weapons = {"cron": weapon()}

    def test_exact_name(self):
        hit, text, how = rivenscan.guess_weapon(
            lines(FULL_CARD), lambda value: None, self.weapons)
        self.assertIsNone(hit)
        hit, text, how = rivenscan.guess_weapon(
            ["执法者"], lambda value: self.weapons["cron"], self.weapons)
        self.assertIs(hit, self.weapons["cron"])
        self.assertEqual(how, "exact")

    def test_fuzzy_when_one_char_wrong(self):
        hit, text, how = rivenscan.guess_weapon(
            ["执法看"], lambda value: None, self.weapons)
        self.assertIs(hit, self.weapons["cron"])
        self.assertEqual(how, "fuzzy")

    def test_stat_lines_ignored(self):
        """带数字/百分号的行不能当武器名。"""
        hit, _text, _how = rivenscan.guess_weapon(
            ["+185.4% 伤害"], lambda value: None, {})
        self.assertIsNone(hit)


class ScanTest(unittest.TestCase):
    def _scan(self, text, hint=""):
        analysis = RivenAnalyzer()
        with mock.patch.object(ppocr, "recognize", return_value=lines(text)):
            return rivenscan.scan(b"png", lambda value: weapon() if "执法" in value
                                  else None, lambda: analysis,
                                  weapons={"cron": weapon()}, weapon_hint=hint)

    def test_scan_full_card(self):
        result = self._scan(FULL_CARD)
        self.assertTrue(result.ok)
        self.assertEqual(result.weapon.name, "执法者")
        self.assertEqual(result.config, "3+1")
        self.assertEqual(len(result.evaluations), 4)

    def test_scan_without_weapon(self):
        analysis = RivenAnalyzer()
        with mock.patch.object(ppocr, "recognize", return_value=lines(FULL_CARD)):
            result = rivenscan.scan(b"png", lambda value: None, lambda: analysis,
                                    weapons=None)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.pairs), 4)
        self.assertTrue(any("没认出武器名" in note for note in result.notes))

    def test_scan_weapon_hint(self):
        result = self._scan(FULL_CARD, hint="执法者")
        self.assertTrue(result.ok)
        self.assertEqual(result.weapon_match, "hint")

    def test_scan_blank_image(self):
        with mock.patch.object(ppocr, "recognize", return_value=[]):
            result = rivenscan.scan(b"png", lambda value: weapon(),
                                    lambda: RivenAnalyzer())
        self.assertFalse(result.ok)
        self.assertEqual(result.pairs, [])


class CommandTest(unittest.TestCase):
    def _router(self):
        from tests.test_commands import FakeRiven, make_router

        return make_router(riven=FakeRiven())

    def test_no_image_shows_usage(self):
        router, _, _ = self._router()
        reply = router.handle("#紫卡分析", Context(user_id="U", group_id="G",
                                                  is_group=True))[0]
        self.assertIn("发给我", reply.text)

    def test_scan_disabled_ocr(self):
        router, _, _ = self._router()
        ctx = Context(user_id="U", group_id="G", is_group=True, image=b"png")
        with mock.patch.object(ppocr, "available", return_value=False), \
                mock.patch.object(ppocr, "status",
                                  return_value={"error": "缺少 onnxruntime"}):
            reply = router.handle("#紫卡分析", ctx)[0]
        self.assertIn("没就绪", reply.text)


class ModelFilesTest(unittest.TestCase):
    def test_models_present(self):
        """随仓库发布的模型三件套要在（部署时靠它跑识别）。"""
        self.assertIsNotNone(ppocr.model_dir())

    def test_missing_dependency_reports_reason(self):
        status = ppocr.status()
        self.assertIn("available", status)
        self.assertIn("models", status)


if __name__ == "__main__":
    unittest.main()
