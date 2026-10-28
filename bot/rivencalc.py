"""本地紫卡数值计算（数据来自 Warframe Wiki 的 Riven Mods 页面）。

公式：
    最终值 = 基础值 × random(0.90, 1.10) × 紫卡倾向 × 权重

权重：
    2 正向          0.99
    2 正向 + 1 负面  正向 1.2375，负面 -0.495
    3 正向          0.75
    3 正向 + 1 负面  正向 0.9375，负面 -0.75

基础值按武器类别（步枪/霰弹/手枪/空战/近战）取自 Wiki 的
"Attributes and their base value" 表。全部本地计算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .riven import RivenWeapon

WIKI_URL = "https://wiki.warframe.com/w/Riven_Mods"

CATEGORIES = ("rifle", "shotgun", "pistol", "archgun", "melee")

WEIGHTS = {
    "two": 0.99,
    "two_neg": 1.2375,
    "three": 0.75,
    "three_neg": 0.9375,
}
MALUS = {
    "two_neg": -0.495,
    "three_neg": -0.75,
}


# 卡面上写成倍率的词条：对派系伤害（歧视）。它的数值不是 `+45.5%` 而是 `x1.5`，
# 正负也不是看 `+` / `-` 号，而是看倍率本身——1.x 是正面（加伤），0.x 是负面（减伤）。
# 内部一律用百分数存（+50 / -50），只在识别与显示时换算。
MULTIPLIER_STATS = {"对派系伤害"}


def is_multiplier_stat(name: str) -> bool:
    """这个词条在卡面上是不是倍率写法（`x1.5`）。"""
    text = str(name or "").strip()
    if text in MULTIPLIER_STATS:
        return True
    canonical = canonical_name(text) if text else ""
    return canonical in MULTIPLIER_STATS


def multiplier_text(value: float) -> str:
    """百分数 → 卡面倍率写法：+50 → `x1.5`，-34 → `x0.66`。

    卡面上倍率一般只保留一位小数（`x1.5`），但偶尔会多一位（`x0.66`），
    所以这里按两位格式化、末尾是 0 就去掉——读进来是什么，输出还是什么。
    """
    number = "%.2f" % (1.0 + value / 100.0)
    if number.endswith("0"):
        number = number[:-1]
    return "x" + number


def format_roll(stat: Optional["RivenStat"], name: str, value: float) -> str:
    """按卡面写法输出数值；拿不到词条定义时按名字判断是不是倍率词条。"""
    if stat is not None:
        return stat.format(value)
    if is_multiplier_stat(name):
        return multiplier_text(value)
    return "%+g" % value


@dataclass
class RivenStat:
    name: str
    minimum: float
    maximum: float
    unit: str = "%"
    negative: bool = False
    # 卡面上写成倍率的词条（对派系伤害：`x1.5` 是正面、`x0.5` 是负面）。
    # 内部数值仍旧用百分数（+50 / -50），只在显示与识别时换算。
    multiplier: bool = False

    @property
    def low(self) -> float:
        return min(self.minimum, self.maximum)

    @property
    def high(self) -> float:
        return max(self.minimum, self.maximum)

    def format(self, value: float) -> str:
        """按卡面的写法输出一条词条的数值。"""
        if self.multiplier:
            return multiplier_text(value)
        return "%+g%s" % (value, self.unit)

    def range_text(self, scale: float = 1.0) -> str:
        """`scale` 用来把满级范围换算到当前等级（没升满的紫卡显示的是当前等级数值）。"""
        ratio = 1.0 if not scale else scale
        if self.multiplier:
            return "%s ~ %s" % (multiplier_text(self.minimum * ratio),
                                multiplier_text(self.maximum * ratio))
        if abs(ratio - 1.0) < 1e-9:
            return "%s ~ %s%s" % (_fmt(self.minimum), _fmt(self.maximum), self.unit)
        return "%s ~ %s%s" % (_fmt(self.minimum * ratio),
                              _fmt(self.maximum * ratio), self.unit)


@dataclass
class RollEvaluation:
    name: str
    value: float
    stat: Optional[RivenStat]
    percentile: float
    grade: str
    note: str = ""
    negative: bool = False
    scale: float = 1.0


@dataclass
class RivenAnalysis:
    slug: str
    name: str
    name_en: str
    disposition: float
    dots: str
    two: List[RivenStat]
    two_neg: List[RivenStat]
    three: List[RivenStat]
    three_neg: List[RivenStat]
    negatives_two: List[RivenStat]
    negatives_three: List[RivenStat]
    history: List[Tuple[str, str]]
    source_url: str
    category: str = "rifle"
    weapon_type: str = ""

    @property
    def disposition_label(self) -> str:
        if self.disposition >= 1.3:
            return "高"
        if self.disposition >= 1.0:
            return "中"
        return "低"


@dataclass(frozen=True)
class BaseStat:
    key: str
    name: str
    values: Dict[str, Optional[float]]
    unit: str = "%"
    positive_only: bool = False
    invert: bool = False
    multiplier: bool = False
    # 紫卡里其实是同一条词条的几个变体（四种单元素、六种复合元素），
    # 数值完全一样；group 非空时表里合并成一行，行名用 group。
    group: str = ""


# 单元素（火焰 / 冰冻 / 电击 / 毒素）在紫卡上是同一个「元素伤害」词条，
# 只多了个随机出来的元素类型；复合元素（毒气 / 腐蚀 / 病毒 / 辐射 / 爆炸 / 磁力）同理。
# 基础值一模一样，所以表里各占一行，不用把同样的区间列十遍。
ELEMENT_GROUP = "元素伤害"
COMBINED_GROUP = "复合元素"


# 数值来自 Wiki 的 Attributes and their base value 表
BASE_STATS: List[BaseStat] = [
    BaseStat("damage", "伤害", {"rifle": 165, "shotgun": 164.7, "pistol": 219.6,
                                "archgun": 99.9, "melee": 164.7}),
    BaseStat("physical", "物理伤害", {"rifle": 119.97, "shotgun": 119.97,
                                      "pistol": 119.97, "archgun": 90, "melee": 119.7}),
    BaseStat("critical_chance", "暴击几率", {"rifle": 149.99, "shotgun": 90,
                                             "pistol": 149.99, "archgun": 99.9,
                                             "melee": 180}),
    BaseStat("critical_damage", "暴击伤害", {"rifle": 120, "shotgun": 90,
                                             "pistol": 90, "archgun": 80.1,
                                             "melee": 90}),
    BaseStat("multishot", "多重射击", {"rifle": 90, "shotgun": 119.7,
                                        "pistol": 119.7, "archgun": 60.3}),
    BaseStat("status_chance", "触发几率", {"rifle": 90, "shotgun": 90,
                                           "pistol": 90, "archgun": 60.3, "melee": 90}),
    BaseStat("status_duration", "状态持续时间", {"rifle": 99.99, "shotgun": 99,
                                                 "pistol": 99.99, "archgun": 99.99,
                                                 "melee": 99}),
    BaseStat("fire_rate", "射速", {"rifle": 60.03, "shotgun": 89.1,
                                   "pistol": 74.7, "archgun": 60.03}),
    BaseStat("attack_speed", "攻击速度", {"melee": 54.9}),
    BaseStat("heat", "火焰伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                  "archgun": 119.7, "melee": 90},
             positive_only=True, group=ELEMENT_GROUP),
    BaseStat("cold", "冰冻伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                  "archgun": 119.7, "melee": 90},
             positive_only=True, group=ELEMENT_GROUP),
    BaseStat("electricity", "电击伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                         "archgun": 119.7, "melee": 90},
             positive_only=True, group=ELEMENT_GROUP),
    BaseStat("toxin", "毒素伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                   "archgun": 119.7, "melee": 90},
             positive_only=True, group=ELEMENT_GROUP),
    BaseStat("status_damage", "异常状态伤害", {"rifle": 90, "shotgun": 90,
                                               "pistol": 90, "archgun": 90,
                                               "melee": 90}),
    BaseStat("gas", "毒气伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                 "archgun": 90}, positive_only=True,
             group=COMBINED_GROUP),
    BaseStat("corrosive", "腐蚀伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                       "archgun": 90}, positive_only=True,
             group=COMBINED_GROUP),
    BaseStat("viral", "病毒伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                   "archgun": 90}, positive_only=True,
             group=COMBINED_GROUP),
    BaseStat("radiation", "辐射伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                       "archgun": 90}, positive_only=True,
             group=COMBINED_GROUP),
    BaseStat("blast", "爆炸伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                   "archgun": 90}, positive_only=True,
             group=COMBINED_GROUP),
    BaseStat("magnetic", "磁力伤害", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                      "archgun": 90}, positive_only=True,
             group=COMBINED_GROUP),
    BaseStat("faction", "对派系伤害", {"rifle": 45, "shotgun": 45, "pistol": 45,
                                       "archgun": 45, "melee": 45},
             multiplier=True),
    BaseStat("magazine", "弹匣容量", {"rifle": 50, "shotgun": 50, "pistol": 50,
                                      "archgun": 60.3}),
    BaseStat("ammo", "弹药最大值", {"rifle": 49.95, "shotgun": 90,
                                    "pistol": 90, "archgun": 99.9}),
    BaseStat("ammo_efficiency", "弹药效率", {"rifle": 45, "shotgun": 45,
                                             "pistol": 45, "archgun": 45}),
    BaseStat("reload", "装填速度", {"rifle": 50, "shotgun": 49.45,
                                    "pistol": 50, "archgun": 99.9}),
    BaseStat("projectile", "投射物速度", {"rifle": 90, "shotgun": 89.1, "pistol": 90}),
    BaseStat("punch", "穿透", {"rifle": 2.7, "shotgun": 2.7, "pistol": 2.7,
                               "archgun": 2.7},
             unit="", positive_only=True),
    BaseStat("recoil", "后坐力", {"rifle": 90, "shotgun": 90, "pistol": 90,
                                  "archgun": 90}, invert=True),
    BaseStat("zoom", "变焦", {"rifle": 59.99, "pistol": 80.1, "archgun": 59.99}),
    BaseStat("heavy_efficiency", "重击效率", {"melee": 73.44}),
    BaseStat("combo_duration", "连击持续时间", {"melee": 8.1}, unit=""),
    BaseStat("initial_combo", "初始连击", {"melee": 24.5}, unit=""),
    BaseStat("finisher", "处决伤害", {"melee": 119.7}),
    BaseStat("slide_crit", "滑铲暴击几率", {"melee": 120}),
    BaseStat("range", "攻击范围", {"melee": 1.94}, unit=""),
    BaseStat("extra_combo", "额外连击数几率", {"melee": 58.77}),
    BaseStat("no_combo", "不获得连击数几率", {"melee": 104.85}),
]

ALIASES = {
    "伤害": ("伤害", "基伤", "基础伤害", "近战伤害", "dmg", "damage", "melee"),
    "物理伤害": ("物理", "物理伤害", "冲击", "穿刺", "切割", "impact",
                 "puncture", "slash", "physical"),
    "暴击几率": ("暴击", "暴率", "暴击率", "暴击几率", "cc", "crit"),
    "暴击伤害": ("暴伤", "暴击伤害", "cd", "critdmg"),
    "多重射击": ("多重", "多重射击", "ms", "multishot"),
    "触发几率": ("触发", "触发率", "触发几率", "状态", "状态几率", "sc", "status"),
    "状态持续时间": ("状态时间", "持续时间", "状态持续时间", "触发时间"),
    "射速": ("射速", "fr", "firerate"),
    "攻击速度": ("攻速", "攻击速度", "as", "attackspeed"),
    "火焰伤害": ("火焰", "火", "heat"),
    "冰冻伤害": ("冰冻", "冰", "cold"),
    "电击伤害": ("电击", "电", "electric", "electricity"),
    "毒素伤害": ("毒素", "毒", "toxin"),
    "异常状态伤害": ("异常状态", "状态伤害", "异况", "statusdamage", "dot"),
    "毒气伤害": ("毒气", "毒气伤害", "gas"),
    "腐蚀伤害": ("腐蚀", "腐蚀伤害", "corrosive"),
    "病毒伤害": ("病毒", "病毒伤害", "viral"),
    "辐射伤害": ("辐射", "辐射伤害", "radiation"),
    "爆炸伤害": ("爆炸", "爆炸伤害", "blast"),
    "磁力伤害": ("磁力", "磁力伤害", "magnetic"),
    "对派系伤害": ("派系", "对派系", "歧视", "bane", "grineer", "corpus",
                   "infested", "corrupted", "orokin", "sentient", "narmer",
                   "murmur"),
    "弹匣容量": ("弹匣", "弹匣容量", "mag", "magazine"),
    "弹药最大值": ("弹药", "弹药上限", "弹药最大值", "ammo"),
    "弹药效率": ("弹效", "弹药效率", "ammoefficiency", "ammo efficiency"),
    "装填速度": ("装填", "装填速度", "reload"),
    "投射物速度": ("投射物", "飞行速度", "投射物速度", "projspeed"),
    "穿透": ("穿透", "punch", "punchthrough"),
    "后坐力": ("后坐力", "后坐", "recoil"),
    "变焦": ("变焦", "zoom"),
    "重击效率": ("重击效率", "重击", "heavy"),
    "连击持续时间": ("连击", "连击时间", "连击持续时间", "combo"),
    "初始连击": ("初始连击", "initialcombo"),
    "处决伤害": ("处决", "处决伤害", "finisher"),
    "滑铲暴击几率": ("滑铲", "滑铲暴击", "slide"),
    "攻击范围": ("范围", "攻击范围", "reach", "range"),
    "额外连击数几率": ("额外连击", "额外连击几率", "extragain"),
    "不获得连击数几率": ("不获得连击", "不获得连击几率", "nocombo"),
}


def _fmt(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return ("%.1f" % value).rstrip("0").rstrip(".")


def _round1(value: float) -> float:
    if abs(value) < 1e-9:
        return 0.0
    sign = -1.0 if value < 0 else 1.0
    return sign * math.floor(abs(value) * 10 + 0.5) / 10


def disposition_dots(disposition: float) -> str:
    if disposition >= 1.31:
        return "●●●●●"
    if disposition >= 1.11:
        return "●●●●○"
    if disposition >= 0.9:
        return "●●●○○"
    if disposition >= 0.7:
        return "●●○○○"
    return "●○○○○"


def category_for(weapon: RivenWeapon) -> str:
    riven_type = (weapon.riven_type or "").lower()
    group = (weapon.group or "").lower()
    if riven_type == "archgun" or group == "archgun":
        return "archgun"
    if riven_type in CATEGORIES:
        return riven_type
    if riven_type == "zaw" or group == "zaw":
        return "melee"
    if riven_type == "kitgun" or group == "kitgun":
        return "pistol"
    if group == "melee":
        return "melee"
    if group == "secondary":
        return "pistol"
    if group == "primary":
        return "rifle"
    return "rifle"


def _positive(base: BaseStat, category: str, weight: float,
              disposition: float) -> Optional[RivenStat]:
    raw = base.values.get(category)
    if raw is None:
        return None
    nominal = raw * weight * disposition
    low, high = nominal * 0.9, nominal * 1.1
    if base.invert:
        low, high = -high, -low
    return RivenStat(base.name, _round1(low), _round1(high), base.unit,
                     multiplier=base.multiplier)


def _negative(base: BaseStat, category: str, malus: float,
              disposition: float) -> Optional[RivenStat]:
    raw = base.values.get(category)
    if raw is None or base.positive_only:
        return None
    nominal = raw * malus * disposition
    low, high = nominal * 0.9, nominal * 1.1
    if base.invert:
        low, high = -high, -low
    minimum, maximum = min(low, high), max(low, high)
    return RivenStat(base.name, _round1(minimum), _round1(maximum),
                     base.unit, negative=True, multiplier=base.multiplier)


def calculate(weapon: RivenWeapon) -> RivenAnalysis:
    category = category_for(weapon)
    disposition = float(weapon.disposition or 1.0)

    def merge(stat_base: BaseStat, stat: RivenStat, seen: set) -> bool:
        """同一族的词条（元素伤害 / 复合元素）只保留第一条，行名换成族名。"""
        if not stat_base.group:
            return True
        if stat_base.group in seen:
            return False
        seen.add(stat_base.group)
        stat.name = stat_base.group
        return True

    def positives(weight: float) -> List[RivenStat]:
        stats = []
        seen = set()
        for base in BASE_STATS:
            stat = _positive(base, category, weight, disposition)
            if stat is not None and merge(base, stat, seen):
                stats.append(stat)
        return stats

    def negatives(malus: float) -> List[RivenStat]:
        stats = []
        seen = set()
        for base in BASE_STATS:
            stat = _negative(base, category, malus, disposition)
            if stat is not None and merge(base, stat, seen):
                stats.append(stat)
        return stats

    return RivenAnalysis(
        slug=weapon.slug,
        name=weapon.name,
        name_en=weapon.name_en,
        disposition=disposition,
        dots=disposition_dots(disposition),
        two=positives(WEIGHTS["two"]),
        two_neg=positives(WEIGHTS["two_neg"]),
        three=positives(WEIGHTS["three"]),
        three_neg=positives(WEIGHTS["three_neg"]),
        negatives_two=negatives(MALUS["two_neg"]),
        negatives_three=negatives(MALUS["three_neg"]),
        history=[],
        source_url=WIKI_URL,
        category=category,
        weapon_type=weapon.riven_type,
    )


# ---------------------------------------------------------------------- 词条匹配
def canonical_name(raw: str) -> str:
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        return ""
    for canonical, aliases in ALIASES.items():
        for candidate in (canonical,) + aliases:
            if text == candidate.lower().replace(" ", ""):
                return canonical
    candidates: List[Tuple[int, str]] = []
    for canonical, aliases in ALIASES.items():
        for candidate in (canonical,) + aliases:
            normalized = candidate.lower().replace(" ", "")
            if len(normalized) >= 2 and normalized in text:
                candidates.append((len(normalized), canonical))
    if candidates:
        return max(candidates)[1]
    return (raw or "").strip()


def looks_like_stat(raw: str) -> bool:
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        return False
    for canonical, aliases in ALIASES.items():
        for candidate in (canonical,) + aliases:
            if text == candidate.lower().replace(" ", ""):
                return True
    return False


def find_stat(stats: Sequence[RivenStat], canonical: str) -> Optional[RivenStat]:
    for stat in stats:
        if stat.name == canonical:
            return stat
    if not canonical:
        return None
    for stat in stats:
        if canonical.startswith(stat.name) or stat.name.startswith(canonical):
            return stat
    return None


def is_negative_effect(name: str, value: float) -> bool:
    canonical = canonical_name(name)
    if canonical == "后坐力":
        return value > 0
    return value < 0


def infer_config_label(evaluations: Sequence[RollEvaluation]) -> str:
    positives = sum(1 for item in evaluations if not item.negative)
    has_negative = any(item.negative for item in evaluations)
    if positives >= 3:
        return "3+1" if has_negative else "3+"
    if positives == 2:
        return "2+1" if has_negative else "2+"
    return "参考"


def evaluate_roll(analysis: RivenAnalysis,
                  pairs: Sequence[Tuple[str, float]],
                  scale: float = 1.0) -> List[RollEvaluation]:
    """`scale` 是「卡面数值 / 满级数值」：没升满的紫卡要先换算到满级再评分，
    但 `value` 仍保留卡面上的数字——用户要的是和图片一模一样的结果。
    """
    if not pairs:
        return []
    if not scale:
        scale = 1.0

    normalized: List[Tuple[str, float]] = []
    for name, value in pairs:
        # 手输/OCR 漏掉 `x` 时，对派系伤害会写成 `1.5`（意思是 x1.5，即 +50%）。
        # 它不可能是 1.5%，所以这里按倍率换算回来。
        if is_multiplier_stat(name) and 0 < abs(value) <= 3:
            value = round((abs(value) - 1.0) * 100.0, 1)
        normalized.append((name, value))

    classified = [(name, value, is_negative_effect(name, value))
                  for name, value in normalized]
    positives = [item for item in classified if not item[2]]
    has_negative = any(item[2] for item in classified)

    if len(positives) == 2:
        positive_stats = analysis.two_neg if has_negative else analysis.two
    elif len(positives) >= 3:
        positive_stats = analysis.three_neg if has_negative else analysis.three
    else:
        positive_stats = analysis.three_neg or analysis.three or analysis.two_neg

    negative_stats = (analysis.negatives_three if len(positives) >= 3
                      else analysis.negatives_two)
    if not negative_stats:
        negative_stats = analysis.negatives_three

    results: List[RollEvaluation] = []
    for raw_name, value, negative in classified:
        canonical = canonical_name(raw_name)
        stats = negative_stats if negative else positive_stats
        stat = find_stat(stats, canonical) if stats else None
        item = _evaluate_one(canonical or raw_name, value / scale, stat, negative)
        results.append(RollEvaluation(item.name, value, item.stat, item.percentile,
                                      item.grade, item.note, item.negative, scale))
    return results


def _evaluate_one(name: str, value: float, stat: Optional[RivenStat],
                  negative: bool) -> RollEvaluation:
    if stat is None:
        return RollEvaluation(name, value, None, 0.0, "未识别",
                              "该武器没有这个词条，或名称没对上", negative)

    if negative:
        magnitude = abs(value)
        low = min(abs(stat.low), abs(stat.high))
        high = max(abs(stat.low), abs(stat.high))
        if high <= low:
            percentile = 0.5
        else:
            percentile = (magnitude - low) / (high - low)
        if magnitude > high:
            percentile = 1.0
            grade = "负面过大"
        elif magnitude < low:
            percentile = 0.0
            grade = "轻微"
        elif percentile >= 0.75:
            grade = "负面偏大"
        elif percentile >= 0.4:
            grade = "中等"
        else:
            grade = "轻微"
        return RollEvaluation(name, value, stat, percentile, grade,
                              "负面数值越小越好", True)

    low, high = stat.low, stat.high
    if high < 0:
        magnitude = abs(value)
        low, high = abs(stat.high), abs(stat.low)
        if high <= low:
            percentile = 0.5
        else:
            percentile = (magnitude - low) / (high - low)
        if magnitude > high:
            percentile = 1.0
            grade = "超上限"
            note = "超过该配置理论上限，检查是否输错或倾向不符"
        elif magnitude < low:
            percentile = 0.0
            grade = "低于下限"
            note = "低于该配置理论下限"
        else:
            note = "位于可能范围的 %d%%" % int(round(percentile * 100))
            grade = ("接近满值" if percentile >= 0.9 else
                     "优秀" if percentile >= 0.7 else
                     "中等" if percentile >= 0.4 else "偏低")
        return RollEvaluation(name, value, stat, percentile, grade, note, False)

    if high <= low:
        percentile = 0.5
    else:
        percentile = (value - low) / (high - low)
    if value > high:
        percentile = 1.0
        grade = "超上限"
        note = "超过该配置理论上限，检查是否输错或倾向不符"
    elif value < low:
        percentile = 0.0
        grade = "低于下限"
        note = "低于该配置理论下限"
    else:
        note = "位于可能范围的 %d%%" % int(round(percentile * 100))
        if percentile >= 0.9:
            grade = "接近满值"
        elif percentile >= 0.7:
            grade = "优秀"
        elif percentile >= 0.4:
            grade = "中等"
        else:
            grade = "偏低"
    return RollEvaluation(name, value, stat, percentile, grade, note, False)
