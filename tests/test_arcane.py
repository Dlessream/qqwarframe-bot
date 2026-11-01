# -*- coding: utf-8 -*-
"""赋能必须带「赋能」前缀才参与匹配（规则：赋能的话使用前缀赋能）。"""

import unittest

from bot.market import Item, ItemIndex, is_arcane, wants_arcane


def _item(slug, name, name_en, tags):
    return Item.from_dict({"slug": slug, "name": name, "name_en": name_en,
                           "tags": list(tags)})


ITEMS = [
    # 正牌「赋能·」系列：不带前缀时不该被命中
    _item("arcane_arachne", "赋能·蜘蛛", "Arcane Arachne",
          ("arcane_enhancement", "rare")),
    _item("arcane_velocity", "赋能·迅速", "Arcane Velocity",
          ("arcane_enhancement", "rare")),
    # 同属 arcane_enhancement 但玩家本就带系列名：不受前缀限制
    _item("magus_vigor", "魔导·活力", "Magus Vigor", ("arcane_enhancement",)),
    _item("virtuos_strike", "正直·打击", "Virtuos Strike", ("arcane_enhancement",)),
    # 普通物品：名字里含赋能里的字
    _item("twitch", "迅速抽换", "Twitch", ("mod",)),
    _item("threat_blocker", "威胁壁垒", "Threat Blocker", ("mod",)),
    # 秘奥头盔（arcane_helmet）不算「赋能·」系列
    _item("arcane_thrak_helmet", "震击 秘奥头盔", "Arcane Thrak Helmet",
          ("arcane_helmet", "skin")),
]


class ArcanePrefixTest(unittest.TestCase):
    def setUp(self):
        self.index = ItemIndex(ITEMS)

    # ---------------------------------------------------------------- 判定
    def test_is_arcane(self):
        self.assertTrue(is_arcane(self.index.get("arcane_arachne")))
        self.assertTrue(is_arcane(self.index.get("arcane_velocity")))
        self.assertFalse(is_arcane(self.index.get("magus_vigor")))
        self.assertFalse(is_arcane(self.index.get("arcane_thrak_helmet")))
        self.assertFalse(is_arcane(self.index.get("twitch")))

    def test_wants_arcane(self):
        self.assertTrue(wants_arcane("赋能蜘蛛"))
        self.assertTrue(wants_arcane("赋能·蜘蛛"))
        self.assertTrue(wants_arcane("赋能 蜘蛛"))
        self.assertTrue(wants_arcane("Arcane Velocity"))
        self.assertFalse(wants_arcane("蜘蛛"))
        self.assertFalse(wants_arcane("迅速"))
        self.assertFalse(wants_arcane(""))

    # ---------------------------------------------------------------- 搜索
    def test_plain_query_skips_arcane(self):
        self.assertEqual(self.index.search("蜘蛛"), [])
        self.assertEqual([hit.item.slug for hit in self.index.search("迅速")],
                         ["twitch"])

    def test_prefixed_query_finds_arcane(self):
        self.assertEqual(
            [hit.item.slug for hit in self.index.search("赋能蜘蛛")][:1],
            ["arcane_arachne"])
        self.assertEqual(
            [hit.item.slug for hit in self.index.search("赋能·蜘蛛")][:1],
            ["arcane_arachne"])
        self.assertEqual(
            [hit.item.slug for hit in self.index.search("赋能 迅速")][:1],
            ["arcane_velocity"])

    def test_allow_arcane_override(self):
        # 管理页等维护场景可以显式放开
        hits = self.index.search("蜘蛛", allow_arcane=True)
        self.assertEqual([hit.item.slug for hit in hits][:1], ["arcane_arachne"])
        self.assertEqual(self.index.search("蜘蛛", allow_arcane=False), [])

    def test_other_arcane_series_unaffected(self):
        # 魔导· / 正直· 系列玩家本来就带系列名，不需要「赋能」前缀
        self.assertEqual(
            [hit.item.slug for hit in self.index.search("魔导活力")][:1],
            ["magus_vigor"])
        self.assertEqual(
            [hit.item.slug for hit in self.index.search("正直打击")][:1],
            ["virtuos_strike"])

    def test_arcane_helmet_unaffected(self):
        self.assertEqual(
            [hit.item.slug for hit in self.index.search("震击秘奥头盔")][:1],
            ["arcane_thrak_helmet"])


if __name__ == "__main__":
    unittest.main()
