import unittest

from bot import present
from bot.riven import (GROUP_LABELS, RivenClient, RivenWeapon, parse_group,
                       parse_group_detail, weapon_category, weapon_group)
from bot.rivencalc import calculate_group, category_for


def weapon(slug, group, riven_type, name="测试"):
    return RivenWeapon(slug=slug, name=name, name_en=slug, riven_type=riven_type,
                       group=group, disposition=1.2, mastery_rank=0)


ROWS = [
    weapon("rubico_prime", "primary", "rifle", "绝路 Prime"),
    weapon("tigris_prime", "primary", "shotgun", "猛虎 Prime"),
    weapon("akstiletto_prime", "secondary", "pistol", "双子蝰蛇 Prime"),
    weapon("catchmoon", "kitgun", "kitgun", "捕月"),
    weapon("galatine_prime", "melee", "melee", "迦伦提恩 Prime"),
    weapon("docan", "zaw", "zaw", "多坎"),
    weapon("velocitus", "archgun", "archgun", "速射炮"),
    weapon("onorix", "archgun", "melee", "奥诺里克斯"),
    weapon("deth_machine_rifle", "sentinel", "rifle", "死亡机枪"),
]


class FakeRiven(RivenClient):
    def __init__(self, rows):
        super().__init__(market=None, disposition_override=1.2)
        self._rows = {item.slug: item for item in rows}

    def weapons(self, ttl=43200):
        return self._rows


class GroupMappingTest(unittest.TestCase):
    def test_group_of_kitgun_is_secondary(self):
        """自制枪（Kitgun）除近战外都按副武器算。"""
        self.assertEqual(weapon_group(weapon("c", "kitgun", "kitgun")), "secondary")
        self.assertEqual(weapon_category(weapon("c", "kitgun", "kitgun")), "pistol")

    def test_group_of_zaw_is_melee(self):
        self.assertEqual(weapon_group(weapon("d", "zaw", "zaw")), "melee")
        self.assertEqual(weapon_category(weapon("d", "zaw", "zaw")), "melee")

    def test_archgun_includes_archwing_melee(self):
        self.assertEqual(weapon_group(weapon("o", "archgun", "melee")), "archgun")

    def test_sentinel_is_companion(self):
        self.assertEqual(weapon_group(weapon("s", "sentinel", "rifle")), "sentinel")

    def test_parse_alias(self):
        cases = {"主武器": "primary", "副武器": "secondary", "近战": "melee",
                 "空战": "archgun", "同伴": "sentinel", "pistol": "secondary",
                 "archgun": "archgun", "哨兵": "sentinel", "kitgun": "secondary"}
        for text, expect in cases.items():
            self.assertEqual(parse_group(text), expect, text)

    def test_parse_group_detail_rifle(self):
        self.assertEqual(parse_group_detail("步枪"), ("primary", "rifle"))
        self.assertEqual(parse_group_detail("霰弹"), ("primary", "shotgun"))
        self.assertEqual(parse_group_detail("主武器"), ("primary", None))


class GroupLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.riven = FakeRiven(ROWS)

    def test_counts(self):
        counts = self.riven.group_counts()
        self.assertEqual(counts["primary"], 2)
        self.assertEqual(counts["secondary"], 2)
        self.assertEqual(counts["melee"], 2)
        self.assertEqual(counts["archgun"], 2)
        self.assertEqual(counts["sentinel"], 1)

    def test_primary_splits_rifle_and_shotgun(self):
        buckets = self.riven.group_weapons("primary")
        self.assertEqual(set(buckets), {"rifle", "shotgun"})
        self.assertEqual(buckets["rifle"][0].name, "绝路 Prime")

    def test_secondary_contains_kitgun(self):
        slugs = [item.slug for item in self.riven.group_weapons("secondary")["pistol"]]
        self.assertIn("catchmoon", slugs)

    def test_melee_contains_zaw(self):
        slugs = [item.slug for item in self.riven.group_weapons("melee")["melee"]]
        self.assertIn("docan", slugs)


class GroupRangeTest(unittest.TestCase):
    def test_calculate_group_uses_disposition(self):
        analysis = calculate_group("主武器", "rifle", 1.2)
        self.assertAlmostEqual(analysis.disposition, 1.2)
        self.assertEqual(analysis.category, "rifle")
        self.assertTrue(analysis.two_neg and analysis.negatives_two)
        for stat in analysis.two_neg:
            self.assertLess(stat.minimum, stat.maximum)

    def test_reply_text_and_image(self):
        analysis = calculate_group("主武器", "rifle", 1.2)
        reply = present.riven_group_reply(
            "主武器", [("rifle", analysis)], {"rifle": 3}, 1.2)
        self.assertIn("【主武器】紫卡词条范围", reply.text)
        self.assertIn("倾向按 1.20 计算", reply.text)
        self.assertIn("步枪（3 把）", reply.text)
        self.assertIn("#紫卡", reply.text)

    def test_menu(self):
        reply = present.riven_group_menu({"primary": 2, "secondary": 1})
        self.assertIn("#紫卡分析", reply.text)
        for label in GROUP_LABELS.values():
            self.assertIn(label, reply.text)


class CategoryCompatTest(unittest.TestCase):
    def test_category_for_still_works(self):
        self.assertEqual(category_for(weapon("c", "kitgun", "kitgun")), "pistol")
        self.assertEqual(category_for(weapon("r", "primary", "rifle")), "rifle")
        self.assertEqual(category_for(weapon("t", "primary", "shotgun")), "shotgun")


if __name__ == "__main__":
    unittest.main()
