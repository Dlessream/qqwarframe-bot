"""赏金目标名：官方 jobType → 中文目标。"""

import json
import os
import tempfile
import unittest

from bot import bounties


class KeyTest(unittest.TestCase):
    def test_key_of(self):
        self.assertEqual(
            bounties.key_of("/Lotus/Types/Gameplay/Eidolon/Jobs/AttritionBountyExt"),
            "eidolon/jobs/attritionbountyext")
        self.assertEqual(
            bounties.key_of("/Lotus/Types/Gameplay/Eidolon/Jobs/Narmer/"
                            "AssassinateBountyAss"),
            "eidolon/jobs/narmer/assassinatebountyass")
        self.assertEqual(bounties.key_of(""), "")
        self.assertEqual(bounties.key_of(None), "")

    def test_key_without_prefix(self):
        self.assertEqual(bounties.key_of("Eidolon/Jobs/AttritionBountyExt"),
                         "eidolon/jobs/attritionbountyext")


class TableTest(unittest.TestCase):
    def test_real_entries(self):
        self.assertEqual(bounties.objective_zh(
            "/Lotus/Types/Gameplay/Eidolon/Jobs/AttritionBountyExt"), "宰杀敌人")
        self.assertEqual(bounties.objective_zh(
            "/Lotus/Types/Gameplay/Eidolon/Jobs/CaptureBountyCapTwo"), "间谍捕手")
        self.assertEqual(bounties.objective_zh(
            "/Lotus/Types/Gameplay/Eidolon/Jobs/ReclamationBountyTheft"),
            "取回被盗的器物")
        self.assertEqual(bounties.objective_zh(
            "/Lotus/Types/Gameplay/Eidolon/Jobs/Narmer/AttritionBountyExt"),
            "为了乌努母神（合一众）")
        self.assertEqual(bounties.objective_en(
            "/Lotus/Types/Gameplay/Eidolon/Jobs/AttritionBountyExt"),
            "Cull the Enemy")

    def test_unknown(self):
        self.assertEqual(bounties.objective_zh("/Lotus/Types/Gameplay/X/Jobs/Nope"),
                         "")

    def test_label(self):
        job = "/Lotus/Types/Gameplay/Eidolon/Jobs/AttritionBountyExt"
        self.assertEqual(bounties.label(job, "歼灭"), "歼灭 · 宰杀敌人")
        self.assertEqual(bounties.label(job, ""), "宰杀敌人")
        # 目标名里已经含任务类型时不要写两遍
        self.assertEqual(
            bounties.label("/Lotus/Types/Gameplay/Eidolon/Jobs/RescueBountyResc",
                           "救援"), "搜索与救援")
        # 类型没识别出来时不要写「赏金 · xxx」
        self.assertEqual(
            bounties.label("/Lotus/Types/Gameplay/Venus/Jobs/VenusArtifactJobAmbush",
                           "赏金"), "信使伏击")
        # 完全查不到的时候只回任务类型
        self.assertEqual(bounties.label("NopeJob", "赏金"), "赏金")

    def test_custom_file(self):
        folder = tempfile.mkdtemp(prefix="bounty_")
        path = os.path.join(folder, "table.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"jobs": {"a/b/jobs/x": {"en": "X", "zh": "叉"}}}, handle)
        self.assertEqual(bounties.objective_zh("A/B/Jobs/X", path), "叉")
        self.assertEqual(bounties.label("A/B/Jobs/X", "歼灭", path), "歼灭 · 叉")


if __name__ == "__main__":
    unittest.main()
