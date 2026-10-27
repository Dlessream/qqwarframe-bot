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
from bot.rivencalc import calculate, evaluate_roll

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "riven_ocr_samples.json")

FURIS = RivenWeapon("furis", "盗贼", "Furis", "pistol", "secondary", 1.2, 5)
MAGISTAR = RivenWeapon("magistar", "执法者", "Magistar", "melee", "melee", 1.2, 5)
STRUN = RivenWeapon("strun", "斯特朗", "Strun", "shotgun", "primary", 1.2, 5)


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


class UnrankedCardTest(unittest.TestCase):
    """没升满的紫卡：卡面显示的是当前等级的数值，只有满级的 1/9 ~ 8/9。"""

    @classmethod
    def setUpClass(cls):
        cls.analysis = calculate(STRUN)

    def _candidates(self):
        """斯特朗 Sati-vexitis 的真实卡面（Lv.0），含一条读混出来的重影。"""
        return [
            rivenocr.Candidate("电击伤害", [(11.6, 12)], signs={"11.6": (6, 0)}),
            rivenocr.Candidate("暴击伤害", [(11.2, 10)], signs={"11.2": (4, 0)}),
            rivenocr.Candidate("暴击几率", [(11.2, 3)], signs={"11.2": (2, 0)}),
            rivenocr.Candidate("多重射击", [(16.1, 12)], signs={"16.1": (6, 0)}),
            rivenocr.Candidate("弹匣容量", [(-4.7, 8)], signs={"-4.7": (0, 6)}),
        ]

    def test_card_values_are_reported_verbatim(self):
        """输出必须和卡面逐字一致：`-` 就是负的，一条不多一条不少。"""
        result = rivenocr.resolve(self._candidates(), self.analysis)
        self.assertEqual(result.config, "3+1")
        self.assertEqual(sorted(result.pairs), sorted([
            ("电击伤害", 11.6), ("暴击伤害", 11.2),
            ("多重射击", 16.1), ("弹匣容量", -4.7)]))

    def test_unranked_card_is_detected(self):
        result = rivenocr.resolve(self._candidates(), self.analysis)
        self.assertAlmostEqual(result.scale, 1.0 / 9.0, places=6)
        self.assertTrue(any("还没升满" in note for note in result.notes))

    def test_shadow_crit_stat_is_dropped(self):
        """「暴击伤害 / 暴击几率」读混出来的重影不能变成第五条。"""
        names = [name for name, _value in
                 rivenocr.resolve(self._candidates(), self.analysis).pairs]
        self.assertEqual(len(names), 4)
        self.assertNotIn("暴击几率", names)

    def test_rank_for_scale(self):
        self.assertEqual(rivenocr.rank_for_scale(1.0 / 9.0), 0)
        self.assertEqual(rivenocr.rank_for_scale(1.0), 8)
        self.assertEqual(rivenocr.rank_for_scale(5.0 / 9.0), 4)


class SignTest(unittest.TestCase):
    """卡面符号说了算：`+` 就是正向、`-` 就是负向。"""

    def setUp(self):
        self.analysis = calculate(FURIS)

    def _card(self, ammo_options, ammo_signs):
        return [
            rivenocr.Candidate("多重射击", [(143.1, 15)], signs={"143.1": (6, 0)}),
            rivenocr.Candidate("触发几率", [(102.9, 12)], signs={"102.9": (6, 0)}),
            rivenocr.Candidate("火焰伤害", [(92.1, 9)], signs={"92.1": (6, 0)}),
            rivenocr.Candidate("弹药最大值", ammo_options, signs=ammo_signs),
        ]

    def test_minus_stays_minus(self):
        result = rivenocr.resolve(self._card([(-79.3, 8)], {"-79.3": (0, 8)}),
                                  self.analysis)
        self.assertEqual(result.config, "3+1")
        self.assertEqual(dict(result.pairs)["弹药最大值"], -79.3)
        self.assertEqual(result.notes, [])

    def test_minus_is_not_flipped_when_it_looks_positive(self):
        """数值更像正向范围也不许翻正——符号是卡面写明的。"""
        result = rivenocr.resolve(self._card([(-101.0, 8)], {"-101.0": (0, 8)}),
                                  self.analysis)
        self.assertEqual(dict(result.pairs)["弹药最大值"], -101.0)
        self.assertTrue(any("核对" in note for note in result.notes))

    def test_minus_beats_a_more_popular_signless_reading(self):
        """票更多的那个读数没读到符号，也不能盖掉读到 `-` 的那个。"""
        result = rivenocr.resolve(
            self._card([(79.3, 20), (-79.3, 3)], {"-79.3": (0, 3)}),
            self.analysis)
        self.assertEqual(dict(result.pairs)["弹药最大值"], -79.3)

    def test_positive_only_stat_ignores_a_bogus_minus(self):
        """火焰伤害不可能为负，把 `+` 读成 `-` 时按正向算。"""
        cards = self._card([(-79.3, 8)], {"-79.3": (0, 8)})
        cards[2] = rivenocr.Candidate("火焰伤害", [(-92.1, 9)],
                                      signs={"-92.1": (0, 9)})
        result = rivenocr.resolve(cards, self.analysis)
        self.assertEqual(dict(result.pairs)["火焰伤害"], 92.1)

    def test_lost_sign_still_comes_back_from_range(self):
        """符号全丢了才用范围补，补出来照样是负的。"""
        result = rivenocr.resolve(self._card([(79.3, 8)], {}), self.analysis)
        self.assertEqual(dict(result.pairs)["弹药最大值"], -79.3)

    def test_confusable_pair_is_mutually_exclusive(self):
        """同一条数值 + 同一个词族，只能留一个（暴击伤害/暴击几率 读混）。"""
        conflicts = rivenocr._conflicts([
            rivenocr.Candidate("暴击伤害", [(11.2, 4)]),
            rivenocr.Candidate("暴击几率", [(11.2, 3)]),
            rivenocr.Candidate("多重射击", [(11.2, 3)]),
        ])
        self.assertEqual(conflicts, [[0, 1]])

    def test_shadow_reading_does_not_add_a_line(self):
        """多出来的一条同族同值候选，不能把结果撑成 5 条。"""
        cards = self._card([(-79.3, 8)], {"-79.3": (0, 8)})
        cards.append(rivenocr.Candidate("弹药效率", [(-79.3, 1)],
                                        signs={"-79.3": (0, 1)}))
        result = rivenocr.resolve(cards, self.analysis)
        self.assertEqual(len(result.pairs), 4)
        self.assertNotIn("弹药效率", [name for name, _value in result.pairs])


class InvertedStatTest(unittest.TestCase):
    """后坐力这类「有益方向是负值」的词条：卡面上的 `-` 不代表它是负面词条。

    wiki 的属性表里后坐力的正面基础值写的就是 -90%，所以正面词条显示成
    `-105.0% 后坐力`，而它属于 2+/3+ 里的正向词条；反过来 `+80% 后坐力`
    （后坐力变大）才是负面词条。
    """

    def setUp(self):
        self.analysis = calculate(FURIS)

    def test_negative_recoil_is_a_positive_stat(self):
        result = rivenocr.resolve([
            rivenocr.Candidate("后坐力", [(-105.0, 10)], signs={"-105.0": (0, 8)}),
            rivenocr.Candidate("多重射击", [(143.1, 10)], signs={"143.1": (8, 0)}),
        ], self.analysis)
        self.assertEqual(result.config, "2+")
        self.assertEqual(result.pairs, [("后坐力", -105.0), ("多重射击", 143.1)])
        self.assertEqual(result.notes, [])

    def test_positive_recoil_is_the_curse(self):
        result = rivenocr.resolve([
            rivenocr.Candidate("多重射击", [(160.0, 10)], signs={"160.0": (8, 0)}),
            rivenocr.Candidate("触发几率", [(130.0, 10)], signs={"130.0": (8, 0)}),
            rivenocr.Candidate("火焰伤害", [(130.0, 10)], signs={"130.0": (8, 0)}),
            rivenocr.Candidate("后坐力", [(80.0, 10)], signs={"80.0": (8, 0)}),
        ], self.analysis)
        self.assertEqual(result.config, "3+1")
        self.assertEqual(dict(result.pairs)["后坐力"], 80.0)
        evaluations = evaluate_roll(self.analysis, result.pairs, result.scale)
        recoil = [item for item in evaluations if item.name == "后坐力"][0]
        self.assertTrue(recoil.negative)

    def test_minus_recoil_is_not_the_curse(self):
        """以前的实现在这里会反过来：`-105% 后坐力` 被当成负面词条。"""
        result = rivenocr.resolve([
            rivenocr.Candidate("后坐力", [(-105.0, 10)], signs={"-105.0": (0, 8)}),
            rivenocr.Candidate("多重射击", [(143.1, 10)], signs={"143.1": (8, 0)}),
        ], self.analysis)
        evaluations = evaluate_roll(self.analysis, result.pairs, result.scale)
        recoil = [item for item in evaluations if item.name == "后坐力"][0]
        self.assertFalse(recoil.negative)
        self.assertEqual(recoil.value, -105.0)


class OrderTest(unittest.TestCase):
    """词条顺序必须和卡面一致：卡面上在上面的一条，结果里也在上面。"""

    CARD = ["多重射击", "触发几率", "弹药最大值", "火焰伤害"]
    LINES = ["+143.1% 多重射击", "+102.9% 触发几率",
             "-79.3% 弹药最大值", "+92.1% 火焰伤害"]

    def test_order_follows_the_card(self):
        texts = ["\n".join(self.LINES)]
        self.assertEqual([item.name for item in rivenocr.read_texts(texts)],
                         self.CARD)

    def test_scrambled_variant_does_not_reorder(self):
        """有一份预处理把行读反了，也不能把顺序带歪。"""
        texts = ["\n".join(self.LINES), "\n".join(reversed(self.LINES)),
                 "\n".join(self.LINES)]
        self.assertEqual([item.name for item in rivenocr.read_texts(texts)],
                         self.CARD)

    def test_resolve_keeps_the_order(self):
        texts = ["\n".join(reversed(self.LINES)), "\n".join(self.LINES),
                 "\n".join(self.LINES)]
        result = resolve_texts(texts, FURIS)
        self.assertEqual([name for name, _value in result.pairs], self.CARD)


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
