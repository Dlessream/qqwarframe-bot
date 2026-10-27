"""紫卡截图识别：词条解析、多份投票、按武器范围校验。

`fixtures/riven_ocr_samples.json` 是真实截图（盗贼 Sati-hexapha、执法者
Acri-argicron）在容器里跑 Tesseract 得到的 6 份预处理文本，直接当夹具用，
这样不用装 Tesseract 也能回归整条「识别 → 投票 → 校验」链路。
"""

import json
import os
import unittest

from bot import ocr, rivenocr
from bot.riven import RivenWeapon
from bot.rivencalc import calculate

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "riven_ocr_samples.json")

FURIS = RivenWeapon("furis", "盗贼", "Furis", "pistol", "secondary", 1.2, 5)
MAGISTAR = RivenWeapon("magistar", "执法者", "Magistar", "melee", "melee", 1.2, 5)


def load_variants() -> dict:
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_texts(texts, weapon):
    analysis = calculate(weapon)
    return rivenocr.resolve(rivenocr.read_texts(texts), analysis)


class RealCardTest(unittest.TestCase):
    """真实截图：词条、数值、正负号必须和卡面完全一致。"""

    @classmethod
    def setUpClass(cls):
        cls.variants = load_variants()

    def test_furis_card(self):
        result = resolve_texts(self.variants["card_furis.png"], FURIS)
        self.assertEqual(result.config, "3+1")
        self.assertEqual(sorted(result.pairs), sorted([
            ("多重射击", 143.1), ("触发几率", 102.9),
            ("弹药最大值", -79.3), ("火焰伤害", 92.1)]))
        self.assertEqual(result.notes, [])

    def test_furis_ammo_stays_negative(self):
        """-79.3% 曾被读成 -19.3%／79.3%，靠 3+1 的负面范围才纠得回来。"""
        result = resolve_texts(self.variants["card_furis.png"], FURIS)
        pairs = dict(result.pairs)
        self.assertEqual(pairs["弹药最大值"], -79.3)

    def test_magistar_card(self):
        result = resolve_texts(self.variants["card_magistar.png"], MAGISTAR)
        self.assertEqual(result.config, "3+1")
        self.assertEqual(sorted(result.pairs), sorted([
            ("对派系伤害", 50.0), ("暴击几率", 184.7),
            ("暴击伤害", 99.9), ("处决伤害", -111.5)]))
        self.assertEqual(result.notes, [])

    def test_no_extra_stat(self):
        """旧版会凭空多出一条「攻击速度」，新版只能认出卡面上的四条。"""
        names = [name for name, _value in
                 resolve_texts(self.variants["card_furis.png"], FURIS).pairs]
        self.assertEqual(len(names), 4)
        self.assertNotIn("攻击速度", names)


class ParseTest(unittest.TestCase):
    def test_signs_and_units(self):
        text = "+165.2% 伤害\n+90.1% 多重射击\n-30.5% 变焦"
        self.assertEqual([(item.name, item.value)
                          for item in rivenocr.read_texts([text])],
                         [("伤害", 165.2), ("多重射击", 90.1), ("变焦", -30.5)])

    def test_stat_before_number(self):
        text = "暴击几率 89.2%"
        self.assertEqual(rivenocr.parse_text(text), [("暴击几率", 89.2)])

    def test_split_decimal(self):
        """`102. 9%`、`143 1%` 都是小数点没读准，要合回一个数。"""
        self.assertEqual(rivenocr.parse_text("+102. 9% 触发几率"),
                         [("触发几率", 102.9)])
        self.assertEqual(rivenocr.parse_text("+143 1% 多重射击"),
                         [("多重射击", 143.1)])

    def test_extra_digit_keeps_a_low_vote_reading(self):
        """`492.1%` 要同时留下 492.1 和去掉首位的 92.1，让范围校验去挑。"""
        candidates = rivenocr.read_texts(["+492.1% 火焰伤害"])
        self.assertEqual(len(candidates), 1)
        values = {value for value, _count in candidates[0].options}
        self.assertEqual(values, {492.1, 92.1})
        self.assertEqual(candidates[0].value, 492.1)  # 票多的仍是原读数

    def test_multiplier_faction(self):
        text = "x1.5 对 Grineer 的伤害"
        self.assertEqual(rivenocr.parse_text(text), [("对派系伤害", 50.0)])

    def test_garbage_after_number_is_ignored(self):
        text = "+92.1%6 火焰伤害"
        self.assertEqual(rivenocr.parse_text(text), [("火焰伤害", 92.1)])

    def test_votes_merge_noisy_readings(self):
        texts = ["+92.19% 火焰伤害", "+92.1% 火焰伤害", "+92.1% 火焰伤害"]
        candidates = rivenocr.read_texts(texts)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "火焰伤害")
        self.assertEqual(candidates[0].value, 92.1)
        self.assertGreater(candidates[0].options[0][1], 1)

    def test_fuzzy_alias(self):
        """「触友几率」这种错字也要认成触发几率，只是票力减半。"""
        self.assertEqual([name for name, _value in
                          rivenocr.parse_text("+102.9% 触友几率")],
                         ["触发几率"])


class DeskewTest(unittest.TestCase):
    """倾斜角靠「行亮度起伏」估计，用画出来的横条验证，不需要 Tesseract。"""

    def _barred(self, angle: float):
        from PIL import Image, ImageDraw

        image = Image.new("L", (600, 800), 0)
        draw = ImageDraw.Draw(image)
        for top in range(360, 780, 40):
            draw.rectangle([40, top, 560, top + 22], fill=255)
        return image.convert("RGB").rotate(
            angle, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0))

    def test_upright_is_zero(self):
        self.assertAlmostEqual(ocr.deskew_angle(self._barred(0.0)), 0.0, delta=1.0)

    def test_detects_clockwise_tilt(self):
        # 图顺时针歪了 10°，要返回 +10 才能转回来
        self.assertAlmostEqual(ocr.deskew_angle(self._barred(-10.0)), 10.0, delta=2.0)

    def test_detects_counter_clockwise_tilt(self):
        self.assertAlmostEqual(ocr.deskew_angle(self._barred(7.0)), -7.0, delta=2.0)

    def test_retry_angles_follow_the_hint(self):
        angles = ocr.retry_angles(self._barred(-10.0))
        self.assertEqual(len(angles), 3)
        self.assertTrue(all(abs(angle - 10.0) <= 3.0 for angle in angles))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.analysis = calculate(FURIS)
        self.base = [
            rivenocr.Candidate("多重射击", [(143.1, 15)]),
            rivenocr.Candidate("触发几率", [(102.9, 12)]),
            rivenocr.Candidate("弹药最大值", [(-79.3, 8)]),
            rivenocr.Candidate("火焰伤害", [(92.1, 9)]),
        ]

    def test_drops_stat_that_does_not_exist_on_weapon(self):
        candidates = self.base + [rivenocr.Candidate("穿透", [(1.8, 9)])]
        result = rivenocr.resolve(candidates, self.analysis)
        self.assertEqual(len(result.pairs), 4)
        self.assertNotIn("穿透", [name for name, _value in result.pairs])
        self.assertTrue(any("穿透" in note for note in result.notes))

    def test_drops_extra_stat_that_breaks_the_count(self):
        """多认一条时，丢掉凑不进 3+1 的那条，而不是整份放弃。"""
        candidates = self.base + [
            rivenocr.Candidate("攻击速度", [(92.19, 2)])]
        result = rivenocr.resolve(candidates, self.analysis)
        self.assertEqual(result.config, "3+1")
        self.assertEqual(len(result.pairs), 4)
        self.assertNotIn("攻击速度", [name for name, _value in result.pairs])

    def test_recovers_lost_minus(self):
        """四种预处理都把负号读丢了，也要按负面范围补回来。"""
        candidates = [
            rivenocr.Candidate(name, [(abs(value), votes)])
            for name, [(value, votes)] in
            [(item.name, item.options) for item in self.base]
        ]
        result = rivenocr.resolve(candidates, self.analysis)
        self.assertEqual(dict(result.pairs)["弹药最大值"], -79.3)

    def test_wrong_reading_is_overruled_by_range(self):
        """-19.3 与 79.3 两个读数混在一起时，按范围挑出真正合规的那个。"""
        candidates = [
            rivenocr.Candidate("多重射击", [(143.1, 15)]),
            rivenocr.Candidate("触发几率", [(102.9, 12)]),
            rivenocr.Candidate("弹药最大值", [(-19.3, 3), (79.3, 1)]),
            rivenocr.Candidate("火焰伤害", [(92.1, 9)]),
        ]
        result = rivenocr.resolve(candidates, self.analysis)
        self.assertEqual(dict(result.pairs)["弹药最大值"], -79.3)

    def test_unknown_values_fall_back_instead_of_guessing(self):
        """数值全都离谱时不硬凑配置，按图上读到的列出并提示核对。"""
        candidates = [
            rivenocr.Candidate("多重射击", [(900.0, 5)]),
            rivenocr.Candidate("触发几率", [(800.0, 5)]),
        ]
        result = rivenocr.resolve(candidates, self.analysis)
        self.assertEqual(result.config, "")
        self.assertTrue(result.notes)
        self.assertIn("核对原图", " ".join(result.notes))

    def test_nothing_recognised(self):
        result = rivenocr.resolve([], self.analysis)
        self.assertEqual(result.pairs, [])


if __name__ == "__main__":
    unittest.main()
