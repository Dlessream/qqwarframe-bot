"""科研词条本地化（官方中文字典）与科研推送内容测试。"""

from __future__ import annotations

import unittest

from bot import conquest, present, t2s


def item(**overrides):
    base = {
        "typeKey": "C T_ L A B",
        "kind": "LabConquest",
        "expiry": "2026-11-02T00:00:00Z",
        "missions": [
            {
                "missionType": "Dual Defense",
                "missionTypeKey": "DualDefense",
                "variantKey": "HostileSecurity",
                "deviation": {"name": "Hostile Security"},
                "conditionKeys": ["VoidAberration", "Quicksand"],
                "risks": [{"name": "Void Aberration"}, {"name": "Quicksand"}],
            },
            {
                "missionType": "Artifact",
                "missionTypeKey": "Artifact",
                "variantKey": "StickyFingers",
                "deviation": {"name": "Sticky Fingers"},
                "conditionKeys": ["DrainingResiduals", "ExplosiveCrawlers"],
                "risks": [{"name": "Draining Residuals"},
                          {"name": "Explosive Crawlers"}],
            },
        ],
        "personalKeys": ["AntiGuard", "DecayingFlesh"],
        "personalModifiers": [{"name": "Anti Guard"}, {"name": "Decaying Flesh"}],
    }
    base.update(overrides)
    return base


class ConquestTest(unittest.TestCase):
    def test_mission_type(self):
        self.assertEqual(conquest.mission_type("DualDefense"), "镜像防御")
        self.assertEqual(conquest.mission_type("Artifact"), "中断")
        self.assertEqual(conquest.mission_type("Exterminate"), "歼灭")
        self.assertEqual(conquest.mission_type("Unknown"), "")

    def test_variant_uses_internal_key(self):
        # 内部代号和游戏内名字是错位的：FortifiedFoes 其实是「密闭装甲」
        self.assertEqual(conquest.variant("FortifiedFoes")["name"], "密闭装甲")
        self.assertEqual(conquest.variant("HostileSecurity")["name"], "侵蚀感官")
        self.assertEqual(
            conquest.variant("HostileSecurity", "HexConquest")["name"], "破坏之音")

    def test_risk_and_personal(self):
        self.assertEqual(conquest.risk("Quicksand")["name"], "牵连")
        self.assertEqual(conquest.risk("VoidAberration")["name"], "吸血界影")
        self.assertEqual(conquest.personal("AntiGuard")["name"], "超宏衰减")
        self.assertEqual(conquest.personal("TimeDilation")["name"], "缩短技能")
        self.assertIn("50", conquest.personal("TimeDilation")["desc"])

    def test_descriptions_present(self):
        entry = conquest.risk("ExplosiveCrawlers")
        self.assertEqual(entry["name"], "易爆潜能")
        self.assertTrue(entry["desc"])
        self.assertNotIn("<", entry["desc"])  # 官方富文本标记要清掉

    def test_english_fallback(self):
        info = conquest.describe("variants", "", "LabConquest", "Sealed Armor")
        self.assertEqual(info["name"], "密闭装甲")

    def test_label(self):
        self.assertEqual(conquest.label_of("C T_ L A B"), "深层科研")
        self.assertEqual(conquest.label_of("C T_ H E X"), "时光科研")


class T2STest(unittest.TestCase):
    def test_traditional_to_simplified(self):
        self.assertEqual(t2s.convert("防禦 殲滅 捕獲"), "防御 歼灭 捕获")

    def test_simplified_untouched(self):
        text = "防御 歼灭 镜像防御"
        self.assertEqual(t2s.convert(text), text)


class ResearchReplyTest(unittest.TestCase):
    def test_text_contains_complete_information(self):
        reply = present.research_reply([item()], "深层科研")
        text = reply.text
        self.assertIn("1. 镜像防御", text)
        self.assertIn("偏差 · 侵蚀感官", text)
        self.assertIn("风险 · 吸血界影", text)
        self.assertIn("风险 · 牵连", text)
        self.assertIn("2. 中断", text)
        self.assertIn("偏差 · 暴食贪囤", text)
        self.assertIn("全局变量", text)
        self.assertIn("超宏衰减", text)
        self.assertIn("永久损伤", text)
        self.assertIn("剩余", text)
        # 旧文案里的「个人修正」说法不再出现
        self.assertNotIn("个人修正", text)
        # 不应残留英文原名
        for english in ("Hostile Security", "Void Aberration", "Anti Guard"):
            self.assertNotIn(english, text)

    def test_empty(self):
        reply = present.research_reply([], "深层科研")
        self.assertIn("没有可用", reply.text)


if __name__ == "__main__":
    unittest.main()
