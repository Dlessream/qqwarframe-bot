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
        reply = present.circuit_rotation_reply(None)
        self.assertIn("无尽回廊", reply.text)


class RotationReplyTest(unittest.TestCase):
    def test_text_and_keyboard(self):
        reply = present.circuit_rotation_reply(CURRENT)
        self.assertIn("本周 D 组", reply.text)
        self.assertIn("雷克斯", reply.text)      # 国服译名
        self.assertIn("迦楼罗", reply.text)
        self.assertIn("下周 E 组", reply.text)
        buttons = reply.keyboard["content"]["rows"][0]["buttons"]
        self.assertEqual([b["render_data"]["label"] for b in buttons],
                         ["本周灵化", "灵化列表"])
        self.assertEqual([b["action"]["data"] for b in buttons],
                         ["a:回廊", "a:灵化列表"])

    def test_current_page_button_is_grey(self):
        active = keyboards.circuit_keyboard("list")["content"]["rows"][0]["buttons"]
        idle = keyboards.circuit_keyboard("now")["content"]["rows"][0]["buttons"]
        self.assertEqual(active[1]["render_data"]["style"], 0)
        self.assertEqual(active[0]["render_data"]["style"], 1)
        self.assertEqual(idle[0]["render_data"]["style"], 0)
        self.assertEqual(idle[1]["render_data"]["style"], 1)

    def test_week_row_is_highlighted_in_card(self):
        """卡片里当周那一行的底纹必须和其他周期不同。"""
        if not imagecard.available():
            self.skipTest("没有 Pillow")
        d_group = present.circuit_rotation_reply(CURRENT).image()
        other = dict(CURRENT)
        other["choices"] = [{"category": "normal",
                             "choices": ["Excalibur", "Trinity", "Ember"]},
                            {"category": "hard",
                             "choices": ["Braton", "Lato", "Skana", "Paris",
                                         "Kunai"]}]
        a_group = present.circuit_rotation_reply(other).image()
        self.assertTrue(d_group)
        self.assertTrue(a_group)
        self.assertNotEqual(d_group, a_group)
