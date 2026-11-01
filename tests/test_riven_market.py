# -*- coding: utf-8 -*-
"""#wr 紫卡市场：中文词条筛选、排序、完整卡面字段。"""

import io
import os
import unittest

from bot.riven import (RivenAttribute, RivenAuction, RivenClient, RivenQuery,
                       attribute_label, parse_riven_query, parse_riven_filters,
                       resolve_attribute)


def _item(price, attrs, name="crita-visican", rolls=12, rank=8, mr=12):
    return {
        "buyout_price": price,
        "starting_price": price,
        "closed": False,
        "owner": {"ingame_name": "Seller%d" % price, "status": "ingame"},
        "item": {
            "mod_rank": rank,
            "re_rolls": rolls,
            "name": name,
            "polarity": "naramon",
            "mastery_level": mr,
            "weapon_url_name": "okina",
            "attributes": attrs,
        },
    }


AUCTIONS = {"payload": {"auctions": [
    # 300p：攻速 + 范围 + 爆伤 + 负面 → 完全符合
    _item(300, [
        {"url_name": "critical_damage", "value": 94.8, "positive": True},
        {"url_name": "fire_rate_/_attack_speed", "value": 79.2, "positive": True},
        {"url_name": "range", "value": 66.9, "positive": True},
        {"url_name": "puncture_damage", "value": 55.1, "positive": False},
    ]),
    # 120p：最便宜，但攻速是负的 → 不符合「攻速」
    _item(120, [
        {"url_name": "critical_damage", "value": 100.0, "positive": True},
        {"url_name": "range", "value": 60.0, "positive": True},
        {"url_name": "fire_rate_/_attack_speed", "value": 40.0, "positive": False},
    ]),
    # 150p：没有范围词条 → 不符合
    _item(150, [
        {"url_name": "critical_damage", "value": 90.0, "positive": True},
        {"url_name": "fire_rate_/_attack_speed", "value": 70.0, "positive": True},
        {"url_name": "impact_damage", "value": 50.0, "positive": False},
    ]),
    # 200p：三正 + 负 → 符合
    _item(200, [
        {"url_name": "critical_damage", "value": 88.0, "positive": True},
        {"url_name": "fire_rate_/_attack_speed", "value": 77.0, "positive": True},
        {"url_name": "range", "value": 55.0, "positive": True},
        {"url_name": "zoom", "value": 30.0, "positive": False},
    ], name="laci-acricron"),
    # 已关闭的挂单不计
    {"buyout_price": 10, "closed": True, "owner": {"ingame_name": "Gone"}},
]}}

WEAPONS = {"data": [
    {"id": "1", "slug": "okina", "group": "melee", "rivenType": "melee",
     "disposition": 1.4, "reqMasteryRank": 5,
     "i18n": {"en": {"name": "Okina"}, "zh-hans": {"name": "翁",
                                                   "thumb": "items/x/okina.png"}}},
]}

ATTRIBUTES = {"data": [
    {"slug": "critical_damage", "i18n": {"zh-hans": {"name": "暴击伤害"},
                                        "en": {"name": "Critical Damage"}}},
    {"slug": "critical_chance", "i18n": {"zh-hans": {"name": "暴击率"},
                                        "en": {"name": "Critical Chance"}}},
    {"slug": "range", "i18n": {"zh-hans": {"name": "攻击范围"},
                               "en": {"name": "Range"}}},
]}


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, headers=None):
        return self.payload


class FakeMarket:
    base_url = "http://example.invalid"
    language = "zh-hans"

    def __init__(self):
        self.http = FakeHttp(AUCTIONS)

    def _get(self, path):
        if path.endswith("/attributes"):
            return ATTRIBUTES
        return WEAPONS

    def _headers(self, platform=None):
        return {"Language": "zh-hans"}


class AttributeQueryTest(unittest.TestCase):
    def test_chinese_filters(self):
        query = parse_riven_query("翁 攻击速度 范围 暴击伤害 负任意")
        self.assertEqual(query.weapon, "翁")
        self.assertEqual([item.word for item in query.filters],
                         ["攻击速度", "范围", "暴击伤害"])
        self.assertTrue(query.need_negative)
        self.assertEqual(query.unknown, [])

    def test_aliases(self):
        for word, attr in (("攻速", "fire_rate_/_attack_speed"), ("爆伤", "critical_damage"),
                           ("爆率", "critical_chance"), ("多重", "multishot"),
                           ("触发", "status_chance"), ("范围", "range")):
            group = resolve_attribute(word)
            self.assertIsNotNone(group, word)
            self.assertIn(attr.replace("/", "").replace("_", ""), group, word)

    def test_unknown_word(self):
        query = parse_riven_filters(["完全不是词条"])
        self.assertEqual(query.unknown, ["完全不是词条"])
        self.assertFalse(query.filters)

    def test_positive_only_default(self):
        # 没写负任意时，有负面也照样匹配（不强制）
        query = parse_riven_filters(["暴击伤害"])
        auction = RivenAuction(price=1, seller="s", rank=8, re_rolls=0,
                               attributes=[], detail=[
                                   RivenAttribute("critical_damage", "暴击伤害", 90, True),
                                   RivenAttribute("zoom", "变焦", 30, False)])
        self.assertTrue(query.match(auction.detail))

    def test_match_requires_listed_attributes_positive(self):
        query = parse_riven_filters(["攻击速度"])
        positive = [RivenAttribute("fire_rate_/_attack_speed", "攻击速度", 80, True)]
        negative = [RivenAttribute("fire_rate_/_attack_speed", "攻击速度", 80, False)]
        self.assertTrue(query.match(positive))
        self.assertFalse(query.match(negative))

    def test_need_negative(self):
        query = parse_riven_filters(["负任意"])
        self.assertTrue(query.need_negative)
        with_neg = [RivenAttribute("zoom", "变焦", 30, False)]
        without = [RivenAttribute("zoom", "变焦", 30, True)]
        self.assertTrue(query.match(with_neg))
        self.assertFalse(query.match(without))

    def test_forbid_negative(self):
        query = parse_riven_filters(["无负"])
        self.assertTrue(query.forbid_negative)
        self.assertFalse(query.match([RivenAttribute("zoom", "变焦", 30, False)]))


class AuctionFilterTest(unittest.TestCase):
    def setUp(self):
        self.client = RivenClient(FakeMarket())

    def test_parse_detail_fields(self):
        auctions, note = self.client.auctions("okina", limit=10)
        self.assertEqual(note, "")
        self.assertEqual([item.price for item in auctions], [120, 150, 200, 300])
        cheapest = auctions[0]
        self.assertEqual(cheapest.name, "crita-visican")
        self.assertEqual(cheapest.polarity, "naramon")
        self.assertEqual(cheapest.mastery_level, 12)
        self.assertEqual(cheapest.weapon_slug, "okina")
        self.assertEqual(cheapest.status, "ingame")
        self.assertEqual(len(cheapest.detail), 3)
        labels = {attr.label: attr for attr in cheapest.detail}
        # 官方词条表优先：critical_damage → 暴击伤害
        self.assertEqual(labels["暴击伤害"].text, "+100.0%")
        self.assertEqual(labels["攻击范围"].text, "+60.0%")
        # 负面排在后面
        self.assertFalse(cheapest.detail[-1].positive)

    def test_filter_by_chinese_attributes(self):
        query = parse_riven_filters(["攻击速度", "范围", "暴击伤害", "负任意"])
        auctions, note = self.client.auctions("okina", limit=5, query=query)
        self.assertEqual([item.price for item in auctions], [200, 300])
        self.assertEqual(note, "")

    def test_filter_without_match_keeps_note(self):
        query = parse_riven_filters(["多重射击"])
        auctions, note = self.client.auctions("okina", limit=2, query=query)
        # 没有一张有多重射击：退回最便宜的两张，并给出说明
        self.assertEqual([item.price for item in auctions], [120, 150])
        self.assertIn("没有符合这些词条", note)

    def test_cache_key_varies_by_filter(self):
        auctions, _ = self.client.auctions("okina", limit=5)
        filtered, _ = self.client.auctions(
            "okina", limit=5, query=parse_riven_filters(["范围"]))
        self.assertEqual(len(auctions), 4)
        # 120 也有正面「范围」，所以三个都在
        self.assertEqual([item.price for item in filtered], [120, 200, 300])


class AttributeLabelTest(unittest.TestCase):
    def test_official_table_first(self):
        names = {"critical_damage": "暴击伤害"}
        self.assertEqual(attribute_label("critical_damage", names), "暴击伤害")

    def test_auction_variants(self):
        self.assertEqual(attribute_label("fire_rate_/_attack_speed"), "攻击速度")
        self.assertEqual(attribute_label("base_damage_/_melee_damage"), "伤害")
        self.assertEqual(attribute_label("electric_damage"), "电击伤害")

    def test_unknown_falls_back(self):
        self.assertEqual(attribute_label("some_new_stat"), "Some New Stat")


class WeaponIconTest(unittest.TestCase):
    def test_icon_url(self):
        weapon = RivenClient(FakeMarket()).weapons()["okina"]
        self.assertEqual(weapon.name, "翁")
        self.assertTrue(weapon.icon_url.startswith("https://warframe.market/static/assets/"))
        self.assertTrue(weapon.icon_url.endswith("okina.png"))


class AttributeTextTest(unittest.TestCase):
    """数值显示：普通词条百分比，对派系伤害是倍率。"""

    def test_percent_attributes(self):
        self.assertEqual(RivenAttribute("critical_damage", "暴击伤害", 94.8).text,
                         "+94.8%")
        self.assertEqual(RivenAttribute("range", "攻击范围", 55.1, False).text,
                         "-55.1%")

    def test_faction_damage_is_multiplier(self):
        # 卡面上写的是 x1.4 对 Corpus 的伤害，不是 +1.4%
        self.assertEqual(RivenAttribute("damage_to_corpus", "对 Corpus 的伤害",
                                        1.4).text, "x1.40")
        self.assertEqual(RivenAttribute("damage_vs_grineer", "对克隆尼伤害",
                                        -0.9, False).text, "x0.90")


class WeaponTypeLabelTest(unittest.TestCase):
    """卡面/文案里类别用中文，别直接把 melee 这种原文吐出来。"""

    def make(self, riven_type, group=""):
        from bot.riven import RivenWeapon
        return RivenWeapon(slug="x", name="测试", name_en="Test",
                           riven_type=riven_type, group=group,
                           disposition=1.0, mastery_rank=0)

    def test_known_types(self):
        self.assertEqual(self.make("melee").type_label, "近战")
        self.assertEqual(self.make("rifle").type_label, "步枪")
        self.assertEqual(self.make("shotgun").type_label, "霰弹枪")
        self.assertEqual(self.make("pistol").type_label, "手枪")
        self.assertEqual(self.make("archgun").type_label, "空战")

    def test_falls_back_to_group_then_raw(self):
        self.assertEqual(self.make("", "melee").type_label, "近战")
        self.assertEqual(self.make("zaw").type_label, "zaw")

    def test_empty_is_dash(self):
        self.assertEqual(self.make("").type_label, "—")


class PolarityIconTest(unittest.TestCase):
    """极性槽用游戏里的极性图标（灰机 wiki 的白色 PNG），不写中文。"""

    KEYS = ("madurai", "naramon", "vazarin", "zenurik", "unairu",
            "penjaga", "umbra", "universal")

    def test_assets_exist(self):
        from bot import imagecard
        for key in self.KEYS:
            self.assertIn(key, imagecard.POLARITY_FILES)
            path = os.path.join(imagecard.POLARITY_DIR,
                                imagecard.POLARITY_FILES[key])
            self.assertTrue(os.path.exists(path), key)

    def test_unknown_polarity_returns_none(self):
        from bot import imagecard
        self.assertIsNone(imagecard._polarity_image("nonsense", 40))
        self.assertIsNone(imagecard._polarity_image("", 40))

    def test_icon_is_scaled_and_transparent(self):
        from bot import imagecard
        icon = imagecard._polarity_image("madurai", 40)
        self.assertIsNotNone(icon)
        self.assertEqual(icon.size, (40, 40))
        alpha = icon.getchannel("A")
        low, high = alpha.getextrema()
        self.assertEqual(low, 0)        # 四周透明，只留符号本身
        self.assertEqual(high, 255)

    def test_cache_reuses_image(self):
        from bot import imagecard
        first = imagecard._polarity_image("naramon", 36)
        second = imagecard._polarity_image("naramon", 36)
        self.assertIs(first, second)


class MarketLayoutTest(unittest.TestCase):
    """9 张卡按 3×3 排，4 张以内退回 2 列。"""

    def weapon(self):
        from bot.riven import RivenWeapon
        return RivenWeapon(slug="okina", name="翁", name_en="Okina",
                           riven_type="melee", group="melee",
                           disposition=1.2, mastery_rank=5)

    def auctions(self, count):
        return [RivenAuction(price=100 + index * 10, seller="s%d" % index,
                             rank=8, re_rolls=index, attributes=[],
                             name="crita-visican", polarity="madurai")
                for index in range(count)]

    def test_nine_cards_are_three_columns(self):
        from bot import imagecard
        if not imagecard.available():
            self.skipTest("本机没有可用中文字体")
        raw = imagecard.riven_market_card(self.weapon(), self.auctions(9))
        self.assertTrue(raw.startswith(b"\x89PNG"))
        from PIL import Image
        image = Image.open(io.BytesIO(raw))
        self.assertEqual(image.width, 3 * 340 + 2 * 20 + 2 * 34)

    def test_few_cards_use_two_columns(self):
        from bot import imagecard
        if not imagecard.available():
            self.skipTest("本机没有可用中文字体")
        raw = imagecard.riven_market_card(self.weapon(), self.auctions(4))
        from PIL import Image
        image = Image.open(io.BytesIO(raw))
        self.assertEqual(image.width, 2 * 340 + 20 + 2 * 34)


if __name__ == "__main__":
    unittest.main()
