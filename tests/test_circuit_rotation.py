"""无尽回廊轮换表：灵化之源 9 组 / 战甲 11 周，以及「本周」高亮。"""

import unittest

from bot import circuit_rotation, imagecard, keyboards, present

# 2026-11-23 00:00 UTC 那一周的官方数据：钢铁 D 组、普通第 11 周
WEEK_START = 1795392000.0
CURRENT = {
    "state": "本周",
    "activation": "2026-11-23T00:00:00",
    "expiry": "2026-11-30T00:00:00",
    "choices": [
        {"category": "normal", "choices": ["Garuda", "Baruuk", "Hildryn"]},
        {"category": "hard", "choices": ["Lex", "Magistar", "Boltor",
                                         "Bronco", "Ceramic Dagger"]},
    ],
}


class RotationTest(unittest.TestCase):
    def test_current_group_from_official(self):
        data = circuit_rotation.rotation(CURRENT, now=WEEK_START)
        self.assertEqual(data["incarnon"]["key"], "D")
        self.assertEqual(data["warframe"]["key"], "11")
        self.assertEqual(data["week_start"], WEEK_START)
        self.assertEqual(data["week_end"], WEEK_START + circuit_rotation.WEEK)
        self.assertFalse(data["incarnon"]["degraded"])
        self.assertFalse(data["warframe"]["degraded"])

    def test_exactly_one_current_row(self):
        data = circuit_rotation.rotation(CURRENT, now=WEEK_START)
        for key in ("incarnon", "warframe"):
            marked = [c for c in data[key]["cycles"] if c["current"]]
            self.assertEqual(len(marked), 1, key)
            self.assertEqual(marked[0]["offset"], 0)

    def test_offsets_and_weekly_dates(self):
        data = circuit_rotation.rotation(CURRENT, now=WEEK_START)
        incarnon = data["incarnon"]["cycles"]
        self.assertEqual(len(incarnon), 9)
        by_key = {c["key"]: c for c in incarnon}
        self.assertEqual(by_key["D"]["offset"], 0)
        self.assertEqual(by_key["E"]["offset"], 1)
        self.assertEqual(by_key["C"]["offset"], 8)
        self.assertEqual(by_key["A"]["ordinal"], 1)
        self.assertEqual(by_key["D"]["ordinal"], 4)
        self.assertEqual(by_key["E"]["start"], WEEK_START + circuit_rotation.WEEK)
        starts = sorted(c["start"] for c in incarnon)
        gaps = {round(b - a, 3) for a, b in zip(starts, starts[1:])}
        self.assertEqual(gaps, {circuit_rotation.WEEK})

        warframe = data["warframe"]["cycles"]
        self.assertEqual(len(warframe), 11)
        first = [c for c in warframe if c["key"] == "1"][0]
        self.assertEqual(first["offset"], 1)  # 本周是第 11 周，第 1 周在下周

    def test_week_start_is_monday_utc(self):
        # 2026-11-23 是周一；周二 10 点也应该回落到同一个刷新点
        self.assertEqual(circuit_rotation.week_start(WEEK_START + 34 * 3600),
                         WEEK_START)

    def test_partial_pool_still_matches(self):
        """DE 往池子里塞新武器时，命中 4/5 也要认出来。"""
        raw = dict(CURRENT)
        raw["choices"] = [{"category": "normal",
                           "choices": ["Garuda", "Baruuk", "Hildryn"]},
                          {"category": "hard",
                           "choices": ["Lex", "Magistar", "Boltor", "Bronco",
                                       "BrandNewGun"]}]
        data = circuit_rotation.rotation(raw, now=WEEK_START)
        self.assertFalse(data["incarnon"]["degraded"])
        self.assertEqual(data["incarnon"]["key"], "D")

    def test_unknown_pool_degrades_to_this_week(self):
        raw = {"activation": "2026-11-23T00:00:00",
               "expiry": "2026-11-30T00:00:00",
               "choices": [{"category": "hard", "choices": ["UnknownA"]}]}
        data = circuit_rotation.rotation(raw, now=WEEK_START)
        self.assertTrue(data["incarnon"]["degraded"])
        self.assertEqual(len(data["incarnon"]["cycles"]), 1)
        self.assertEqual(data["incarnon"]["cycles"][0]["names"], ["UnknownA"])

    def test_missing_data_does_not_crash(self):
        data = circuit_rotation.rotation(None, now=WEEK_START)
        self.assertEqual(data["incarnon"]["cycles"], [])
        self.assertTrue(data["incarnon"]["degraded"])
        reply = present.circuit_reply(None)
        self.assertIn("无尽回廊", reply.text)


class RotationReplyTest(unittest.TestCase):
    def test_week_card_has_button_not_full_table(self):
        """主卡只留本周，9 / 11 周轮换挂在「灵化列表」按钮里。"""
        reply = present.circuit_reply(CURRENT, keyboard=keyboards.circuit_keyboard())
        self.assertIsNotNone(reply.keyboard)
        button = reply.keyboard["content"]["rows"][0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "灵化列表")
        self.assertEqual(button["action"]["data"], "a:灵化列表")
        self.assertIn("钢铁 · 武器", reply.text)
        self.assertIn("雷克斯", reply.text)        # 灵化之源用国服译名
        self.assertNotIn("第九周（I）", reply.text)  # 完整轮换不在首屏

    def test_table_reply_lists_full_rotation(self):
        reply = present.circuit_table_reply(CURRENT)
        self.assertIn("本周可选", reply.text)
        self.assertIn("雷克斯", reply.text)
        self.assertIn("第一周（A）", reply.text)    # 周期按第一周、第二周往后列
        self.assertIn("第九周（I）", reply.text)
        self.assertIn("第十一周", reply.text)
        self.assertIn("Garuda", reply.text)        # 战甲用英文
        self.assertNotIn("迦楼罗", reply.text)

    def test_table_has_back_button(self):
        reply = present.circuit_table_reply(
            CURRENT, keyboard=keyboards.circuit_keyboard(back=True))
        button = reply.keyboard["content"]["rows"][0]["buttons"][0]
        self.assertEqual(button["render_data"]["label"], "本周回廊")
        self.assertEqual(button["action"]["data"], "a:回廊")

    def test_week_is_marked_not_relative(self):
        reply = present.circuit_table_reply(CURRENT)
        for word in ("本周 D 组", "下周", "周后"):
            self.assertNotIn(word, reply.text)

    def test_warframe_section_is_english(self):
        reply = present.circuit_table_reply(CURRENT)
        self.assertIn("Baruuk", reply.text)
        self.assertIn("Hildryn", reply.text)

    def test_current_week_row_moves_with_official_data(self):
        other = dict(CURRENT)
        other["choices"] = [{"category": "normal",
                             "choices": ["Excalibur", "Trinity", "Ember"]},
                            {"category": "hard",
                             "choices": ["Braton", "Lato", "Skana", "Paris",
                                         "Kunai"]}]
        reply = present.circuit_table_reply(other)
        self.assertIn("▶ 本周  第一周（A）", reply.text)
        self.assertIn("▶ 本周  第一周 11/23–11/30：Excalibur", reply.text)

    def test_week_row_is_highlighted_in_card(self):
        """当周那一行的底纹必须和其他周期不同。"""
        if not imagecard.available():
            self.skipTest("没有 Pillow")
        d_group = present.circuit_table_reply(CURRENT).image()
        other = dict(CURRENT)
        other["choices"] = [{"category": "normal",
                             "choices": ["Excalibur", "Trinity", "Ember"]},
                            {"category": "hard",
                             "choices": ["Braton", "Lato", "Skana", "Paris",
                                         "Kunai"]}]
        a_group = present.circuit_table_reply(other).image()
        self.assertTrue(d_group)
        self.assertTrue(a_group)
        self.assertNotEqual(d_group, a_group)
