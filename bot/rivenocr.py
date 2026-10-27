"""紫卡词条识别：把 OCR 文本变成「词条 + 数值」，再用该武器的范围交叉校验。

之前那版识别错的主要原因，以及这里的对策：

1. **数值和词条对不上**——旧代码「一行里第一个数字就算这个词条的」，
   于是 `（重击时 x2）` 的 `x2`、上一行剩下的数字都可能被算进来。
   现在只在词条名前面（或紧跟其后）**最近**的那个数字上取值。
2. **凭空多出词条**——词条别名里 `as`（攻击速度）这种 2 个字母的英文缩写
   会命中任意乱码（`BKB IAS` 就被读成攻击速度）。现在英文别名至少要 4 个
   字符才算命中，而且校验阶段还会核对「这把武器到底有没有这个词条」。
3. **数值读错一位**——`-79.3%` 有几次被读成 `-19.3%`。同一张图会跑 6 套
   预处理各识别一次，同一个词条的多个读数按票数排序，再交给数值范围裁决。
4. **符号读反**——`+99.9%` 偶尔读成 `-99.9%`，反过来 `-4.7%` 也会被翻成正的。
   现在**以卡面上的符号为准**：`+` 就是正向、`-` 就是负向，数值范围不能推翻它。
   只有符号压根没读到，或者符号和这条词条能对上的正负明显冲突（比如
   「火焰伤害」不可能为负），才让范围出面定正负。
   例外两处：后坐力这类属性有益方向本身就是负值（正面也写 `-`）；对派系伤害
   （歧视）压根没有符号位，卡面写成倍率 `x1.5` / `x0.6`，1.x 是正面、0.x 是负面。
5. **多读一位小数**——紫卡数值都是 1 位小数，`92.19` 其实是 `92.1%` 多吃了
   一个 9，截断后与正确读数合并成一票。
6. **兜不住就直说**——数值怎么都对不上范围时只提示、不硬猜，宁可不给结论，
   也不输出一个看起来正确其实错的数字。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .rivencalc import ALIASES, RivenAnalysis, format_roll, is_multiplier_stat

# ---------------------------------------------------------------------- 词条别名
_ASCII_RE = re.compile(r"^[a-z0-9 ]+$")


def _is_ascii(text: str) -> bool:
    return bool(_ASCII_RE.match(text))


def _alias_table() -> List[Tuple[str, str]]:
    """(别名小写, 标准名)，长别名优先。

    英文别名太短会误伤：`as`（攻击速度）能匹配上任意乱码里的 a/s，
    之前就是这样凭空多出一条「攻击速度」。所以英文只保留 4 个字符以上。
    """
    table: List[Tuple[str, str]] = []
    for canonical, aliases in ALIASES.items():
        for alias in (canonical,) + tuple(aliases):
            text = str(alias).strip().lower()
            if not text:
                continue
            if _is_ascii(text):
                if len(text) < 4 or " " in text:
                    continue
            elif len(text) < 2:
                continue
            table.append((text, canonical))
    table.sort(key=lambda item: (-len(item[0]), item[0]))
    seen = set()
    result: List[Tuple[str, str]] = []
    for alias, canonical in table:
        if (alias, canonical) in seen:
            continue
        seen.add((alias, canonical))
        result.append((alias, canonical))
    return result


ALIAS_TABLE = _alias_table()


# ---------------------------------------------------------------------- 结构
@dataclass
class Candidate:
    """一个识别出来的词条；同一个词条可能有多个读数（不同变体读出来的）。

    `signs` 记的是「这个读数在图上带的是 + 还是 -」：键是数值，值是
    (读到 + 的票数, 读到 - 的票数)。卡面上 `+` 就是正向、`-` 就是负向，
    这是最硬的一条依据——比拿范围反推可靠得多，所以校验时优先听它的。
    """

    name: str
    options: List[Tuple[float, int]] = field(default_factory=list)
    sample: str = ""
    signs: Dict[str, Tuple[int, int]] = field(default_factory=dict)

    @property
    def value(self) -> float:
        return self.options[0][0] if self.options else 0.0

    @property
    def votes(self) -> int:
        return self.options[0][1] if self.options else 0

    @property
    def votes_total(self) -> int:
        return sum(count for _value, count in self.options)

    def sign_of(self, value: float) -> Tuple[int, int]:
        """某个读数在图上被读到几次 +、几次 -。"""
        return self.signs.get("%.1f" % value, (0, 0))

    def polarity(self, value: float) -> str:
        """图上的符号：`+` / `-`，都没读到或两种都读到时返回空串。"""
        plus, minus = self.sign_of(value)
        if plus and not minus:
            return "+"
        if minus and not plus:
            return "-"
        return ""

    def __str__(self) -> str:  # pragma: no cover - 调试用
        detail = "、".join("%+.1f×%d" % (value, votes)
                          for value, votes in self.options)
        return "%s：%s" % (self.name, detail)


@dataclass
class ReadResult:
    candidates: List[Candidate] = field(default_factory=list)
    texts: List[str] = field(default_factory=list)
    tilt: float = 0.0


# ---------------------------------------------------------------------- 数值
_TOKEN_RE = re.compile(
    r"(?P<mult>[x×X]\s*(?P<mnum>[.,]?\d+(?:[.,]\d+)?))"
    r"|(?P<sign>[+\-−–—－﹣])?\s*(?P<num>\d{1,3}(?:[.,]\d{1,2})?)\s*(?P<pct>[%％])?")
_SIGN_CHARS = "+-−–—－﹣"
_NEG_CHARS = "-−–—－﹣"


def _quantize(value: float, raw: str = "") -> float:
    """保留 1 位小数；OCR 多读出来的小数位直接截断（92.19 → 92.1）。"""
    negative = value < 0
    magnitude = abs(value)
    decimals = 0
    if raw and ("." in raw or "," in raw):
        decimals = len(re.split(r"[.,]", raw)[1])
    if decimals >= 2:
        whole, frac = ("%.2f" % magnitude).split(".")
        magnitude = float("%s.%s" % (whole, frac[:1]))
    return round(-magnitude if negative else magnitude, 1)


@dataclass
class _Token:
    value: float
    raw: str
    start: int
    end: int
    sign: str
    multiplier: bool
    percent: bool


def _token_from_match(match) -> _Token:
    if match.group("mult"):
        raw = match.group("mnum").replace(",", ".")
        # OCR 偶尔把 `x0.6` 读成 `x.6`，补回整数位
        if raw.startswith("."):
            raw = "0" + raw
        return _Token(value=(float(raw) - 1.0) * 100.0,
                      raw=raw, start=match.start(), end=match.end(),
                      sign="", multiplier=True, percent=False)
    raw = match.group("num")
    return _Token(value=float(raw.replace(",", ".")), raw=raw,
                  start=match.start(), end=match.end(),
                  sign=match.group("sign") or "", multiplier=False,
                  percent=bool(match.group("pct")))


_SPLIT_DECIMAL_RE = re.compile(r"(?<=\d)[.．,]\s+(?=\d)")
# `143 1%`：小数点干脆没读出来，只剩个空格。只在「数字 + 空格 + 一两位数字 + %」
# 时才补小数点，避免把 `段位 13 210` 这种并成一个数。
_SPLIT_PERCENT_RE = re.compile(r"(?<=\d)\s+(?=\d{1,2}\s*[%％])")


def _tokens(chunk: str) -> List[_Token]:
    tokens: List[_Token] = []
    for match in _TOKEN_RE.finditer(chunk):
        token = _token_from_match(match)
        # `92.1%6`、`79.396` 这种：紧跟在前一个数字（或 %）后面的
        # 那一截是同一个数被多读出来的，不算独立数值
        if not token.multiplier and token.start > 0:
            previous = chunk[token.start - 1]
            if previous in "%％" or previous.isdigit():
                continue
        tokens.append(token)
    return tokens


def _last_token(chunk: str, limit: int) -> Optional[_Token]:
    """chunk 里最靠后的数字（`x1.5` 倍率也算）；离词条名太远就不认。"""
    tokens = _tokens(chunk)
    if not tokens:
        return None
    token = tokens[-1]
    if len(chunk) - token.end > limit:
        return None
    return token


def _first_token(chunk: str, limit: int) -> Optional[_Token]:
    """兼容「暴击几率 89.2%」这种词条在前、数值在后的写法。"""
    tokens = _tokens(chunk)
    if not tokens or tokens[0].start > limit:
        return None
    return tokens[0]


def _sign_before(line: str, token: _Token) -> str:
    """数字前面紧邻（3 个字符内）的符号，兜住 OCR 把符号读丢的情况。"""
    head = line[max(0, token.start - 3):token.start]
    for char in reversed(head):
        if char in _SIGN_CHARS:
            return char
    return ""


def _sign_flag(token: _Token, sign: str) -> str:
    """把这个读数在图上带的是 + 还是 - 记下来。

    卡面上 `+` 就是正向、`-` 就是负向，这是判定正负的第一依据。
    但对派系伤害（歧视）是例外：它写成倍率 `x1.5` / `x0.5`，**没有符号位**，
    1.x 是正面（加伤）、0.x 才是负面（减伤）——正负由倍率本身决定。
    """
    if token.multiplier:
        return "+" if token.value >= 0 else "-"
    if not sign:
        return ""
    return "-" if sign in _NEG_CHARS else "+"


# ---------------------------------------------------------------------- 词条名
def _boundary_ok(text: str, start: int, end: int, alias: str) -> bool:
    if not _is_ascii(alias):
        return True
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return not (before.isalnum() and before.isascii()) and \
        not (after.isalnum() and after.isascii())


def _find_stats(line: str) -> List[Tuple[int, int, str, float]]:
    """返回 [(start, end, 标准名, 权重)]，长别名优先、互不重叠。

    权重按别名长度给：`暴击伤害`（4 字）比 `暴击`（2 字）可信，
    乱码里蹭到的 2 字别名只有一半票力。
    """
    lower = line.lower()
    taken = [False] * len(lower)
    found: List[Tuple[int, int, str, float]] = []
    for alias, canonical in ALIAS_TABLE:
        start = lower.find(alias)
        while start != -1:
            end = start + len(alias)
            if not any(taken[start:end]) and _boundary_ok(lower, start, end, alias):
                for index in range(start, end):
                    taken[index] = True
                weight = 2.0 if len(alias) >= 3 else 1.0
                found.append((start, end, canonical, weight))
            start = lower.find(alias, start + 1)
    found.sort()
    return found


_FUZZY_MIN = 0.75


def _fuzzy_stats(line: str,
                 taken: Sequence[Tuple[int, int, str, float]]
                 ) -> List[Tuple[int, int, str, float]]:
    """OCR 偶尔认错词条里的一个字（触发→触友）。按 75% 相似度补回来，票力减半。"""
    lower = line.lower()
    spans = [(start, end) for start, end, _name, _weight in taken]
    extra: List[Tuple[int, int, str, float]] = []
    for alias, canonical in ALIAS_TABLE:
        if _is_ascii(alias) or len(alias) < 3:
            continue
        size = len(alias)
        for start in range(0, max(0, len(lower) - size + 1)):
            end = start + size
            if any(start < span_end and end > span_start
                   for span_start, span_end in spans):
                continue
            window = lower[start:end]
            if " " in window:
                continue
            hit = sum(1 for a, b in zip(window, alias) if a == b)
            if hit < size and hit / float(size) >= _FUZZY_MIN:
                extra.append((start, end, canonical, 1.0))
                spans.append((start, end))
                break
    return extra


# ---------------------------------------------------------------------- 行解析
def _lines(text: str) -> List[str]:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    # `102. 9%`、`143 1%` 这种被断在小数点上的，先合回去，否则只会读到后面那截
    return [_SPLIT_PERCENT_RE.sub(".", _SPLIT_DECIMAL_RE.sub(".", line))
            for line in lines]


def _leading_variant(value: float):
    """`492.1` 往往是 `92.1` 前面多读了一个 4，去掉首位给一个低票候选。

    只在整数部分有三位以上时才给，交给范围校验去判断哪个读数合规——
    单独看数字没法知道哪个才是真的。
    """
    text = "%.1f" % abs(value)
    whole, fraction = text.split(".")
    if len(whole) < 3:
        return None
    number = float("%s.%s" % (whole[1:], fraction))
    return -number if value < 0 else number


def _line_rolls(line: str) -> List[Tuple[str, float, float, str]]:
    """从一行里提取 [(标准名, 数值, 权重, 图上符号)]。"""
    stats = _find_stats(line)
    stats = stats + _fuzzy_stats(line, stats)
    stats.sort(key=lambda item: item[0])

    entries: List[Tuple[int, int, int, str, float, float, str]] = []
    for order, (start, end, canonical, weight) in enumerate(stats):
        chunk = line[:start]
        token = _last_token(chunk, 18)
        distance = (len(chunk) - token.end) if token is not None else 0
        if token is None:
            chunk = line[end:]
            token = _first_token(chunk, 10)
            distance = token.start if token is not None else 0
        if token is None:
            continue
        sign = token.sign or _sign_before(chunk, token)
        value = token.value
        flag = _sign_flag(token, sign)
        if not token.multiplier and sign and sign in _NEG_CHARS and value > 0:
            value = -value
        # 对派系伤害是倍率写法（`x1.5` / `x0.5`）：OCR 把 `x` 读丢时只剩 `1.5`，
        # 这里按倍率解释。不然 `1.5%` 永远套不进 ~45% 的范围，而且正负也没法判
        # ——它的正负就看倍率本身：1.x 是加伤、0.x 是减伤。
        if not token.multiplier and is_multiplier_stat(canonical) and \
                0 < abs(value) <= 3:
            value = round((abs(value) - 1.0) * 100.0, 1)
            flag = "+" if value >= 0 else "-"
        if abs(value) > 999:
            continue
        # 带明确正负号的读数比「光秃秃一个数字」可信（符号更容易被漏读）
        if sign or token.multiplier or flag:
            weight += 1.0
        entries.append((token.start, order, distance, canonical, weight,
                        _quantize(value, token.raw), flag))

    # 同一个数字只认离它最近的词条名：`x1.5 对 Grineer 的伤害` 里「Grineer」
    # 和尾部的「伤害」都会命中，但只能算一条对派系伤害。
    best: Dict[int, Tuple[int, int, int, str, float, float, str]] = {}
    for entry in entries:
        previous = best.get(entry[0])
        if previous is None or entry[2] < previous[2]:
            best[entry[0]] = entry

    result: List[Tuple[str, float, float, str]] = []
    seen = set()
    for _start, _order, _distance, canonical, weight, value, flag in sorted(
            best.values(), key=lambda item: item[1]):
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append((canonical, value, weight, flag))

    # 顺带给「多读一位」的读数记一票（票力三分之一），
    # 494.1 / 143 1 这类偏差太大的，范围校验会把它挑掉
    extra: List[Tuple[str, float, float, str]] = []
    for canonical, value, weight, flag in result:
        stripped = _leading_variant(value)
        if stripped is not None:
            extra.append((canonical, stripped, max(1.0, weight / 3.0), flag))
    return result + extra


# ---------------------------------------------------------------------- 投票
def read_texts(texts: Sequence[str]) -> List[Candidate]:
    """多份 OCR 文本合并投票，得到去重后的词条列表。

    除了「数值是多少」投票，还会单独记「这个数值在图上带的是 + 还是 -」的票数，
    校验阶段据此定正负。

    **顺序要跟卡面一致**：每份文本都记下某条词条落在第几行（按总行数归一化），
    取中位数排序。单份文本被倾斜、个别行漏读都不会把顺序带歪——紫卡词条在卡面上
    的先后是固定的，输出顺序也得跟着它，不能按票数或字母重排。
    """
    votes: Dict[str, Counter] = defaultdict(Counter)
    signs: Dict[str, Dict[str, Counter]] = defaultdict(
        lambda: defaultdict(Counter))
    samples: Dict[str, Dict[str, str]] = defaultdict(dict)
    order: List[str] = []
    rows_at: Dict[str, List[float]] = defaultdict(list)

    for text in texts:
        seen: Dict[str, set] = defaultdict(set)
        stats_order: List[str] = []
        for line in _lines(text):
            try:
                rolls = _line_rolls(line)
            except Exception:  # pragma: no cover - 识别异常不该拖垮整条消息
                continue
            for name, value, weight, flag in rolls:
                key = "%.1f" % value
                if key in seen[name]:
                    continue
                seen[name].add(key)
                votes[name][key] += int(round(weight))
                if flag:
                    signs[name][key][flag] += 1
                samples[name].setdefault(key, line[:44])
                if name not in order:
                    order.append(name)
                if name not in stats_order:
                    stats_order.append(name)
        # 记「这条词条在这份文本里排第几个」，只看词条之间的相对次序，
        # 不受工具顶部的武器名、底部段位行数不同影响
        for index, name in enumerate(stats_order):
            rows_at[name].append(float(index))

    candidates: List[Candidate] = []
    for name in sorted(order, key=lambda item: (_row_key(rows_at.get(item)),)):
        counter = votes.get(name) or Counter()
        if not counter:
            continue
        options = sorted(((float(key), count) for key, count in counter.items()),
                         key=lambda item: (-item[1], -abs(item[0])))
        best_sample = samples[name].get("%.1f" % options[0][0], "")
        candidate_signs = {
            key: (flag["+"], flag["-"])
            for key, flag in signs.get(name, {}).items()
        }
        candidates.append(Candidate(name=name, options=options,
                                    sample=best_sample,
                                    signs=candidate_signs))
    return candidates


def _row_key(rows: Optional[Sequence[float]]) -> float:
    """一条词条在卡面上的相对行位置（取中位数，抗漏读与倾斜）。"""
    values = sorted(rows or [])
    if not values:
        return 1.0
    size = len(values)
    if size % 2:
        return values[size // 2]
    return (values[size // 2 - 1] + values[size // 2]) / 2.0


def parse_text(text: str) -> List[Tuple[str, float]]:
    """单份文本 → [(词条, 数值)]，保留首次出现顺序（旧接口）。"""
    return [(item.name, item.value) for item in read_texts([text])]


# ---------------------------------------------------------------------- 识别入口
def read(png: bytes) -> ReadResult:
    """截图 → 候选词条。

    先花几十毫秒测一下文字歪了多少度：歪得明显就直接把图转正再识别，
    省得在歪图上白跑一遍。转正后还是几乎什么都没认出来时，再补几组角度
    重来——歪图本来就难认，多花几秒总比回一句「没认出来」有用。
    """
    from . import ocr

    image = ocr.load(png)
    hint = ocr.deskew_angle(image)
    primary = [round(hint, 1)] if abs(hint) >= 2.0 else []
    texts = ocr.extract_variants(png, angles=primary)
    candidates = read_texts(texts)
    if len(candidates) < 4:
        extra = [angle for angle in ocr.retry_angles(image)
                 if angle not in primary]
        if extra:
            texts = texts + ocr.extract_variants(png, angles=extra)
            candidates = read_texts(texts)
    return ReadResult(candidates=candidates, texts=texts, tilt=hint)


# ---------------------------------------------------------------------- 范围校验
def _distance(value: float, span: Tuple[float, float],
              slack: float = 0.0) -> float:
    """数值到范围的距离。

    `slack` 是这个读数本身的精度误差：倍率词条（`x1.5`）只保留一位小数，
    反推成百分数会落在 ±5 个百分点里（x1.5 可能是 +45.0 也可能是 +55.0），
    所以拿 [value-slack, value+slack] 这个区间去和范围比，别因为这点偏差
    就把本来合规的读数判成「套不上」。
    """
    low, high = span
    if low > high:
        low, high = high, low
    if slack:
        if value - slack <= high and value + slack >= low:
            return 0.0
        return min(abs(value - slack - high), abs(low - value - slack))
    if low <= value <= high:
        return 0.0
    return min(abs(value - low), abs(value - high))


@dataclass
class _Config:
    key: str
    label: str
    positives: Dict[str, Tuple[float, float]]
    negatives: Dict[str, Tuple[float, float]]
    want_positive: int
    want_negative: int


def _span_map(group: Sequence) -> Dict[str, Tuple[float, float]]:
    """词条范围，**保留符号**。

    不能取绝对值：有些属性有益方向本身就是负的（后坐力，wiki 表里正面值写的就是
    -90%），同一属性在正面槽位是负数、在负面槽位是正数。取绝对值会把这两种情况
    压成一样，导致 `-100% 后坐力` 被当成负面词条。
    """
    result: Dict[str, Tuple[float, float]] = {}
    for stat in group or []:
        low, high = stat.low, stat.high
        if low > high:
            low, high = high, low
        result[stat.name] = (low, high)
    return result


def _expected_sign(span: Tuple[float, float]) -> str:
    """这条槽位在卡面上应该带什么号。

    范围整体是负数 → 卡面写 `-`（后坐力作正面词条、普通词条作负面都是这种）；
    整体是正数 → 卡面写 `+`；跨过 0（比如穿透 1.8m、连击时间）→ 不强求。
    """
    low, high = span
    if low > high:
        low, high = high, low
    if high < 0:
        return "-"
    if low > 0:
        return "+"
    return ""


def _configs(analysis: RivenAnalysis) -> List[_Config]:
    return [
        _Config("two", "2+", _span_map(analysis.two), {}, 2, 0),
        _Config("two_neg", "2+1", _span_map(analysis.two_neg),
                _span_map(analysis.negatives_two), 2, 1),
        _Config("three", "3+", _span_map(analysis.three), {}, 3, 0),
        _Config("three_neg", "3+1", _span_map(analysis.three_neg),
                _span_map(analysis.negatives_three), 3, 1),
    ]


@dataclass
class Resolution:
    """校验结果：最终词条 + 提示 + 命中的配置 + 卡面等级换算系数。"""

    pairs: List[Tuple[str, float]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    config: str = ""
    scale: float = 1.0


# 「卡面上的符号没读到」时允许按范围把方向补回来，但要付一点代价：
# 读过 `-` 的读数绝不翻转（那是卡面写明的），只有符号彻底丢了才试另一个方向。
_SIGN_FIX_PENALTY = 1.5

# 倍率词条（`x1.5`）的精度：卡面只有一位小数，1.5 可能是 +45.0 也可能是 +55.0，
# 反推成百分数时留 ±5 个百分点的不确定度。
_MULTIPLIER_SLACK = 5.0


def _pick(candidate: Candidate, config: _Config, strict: bool = True,
          scale: float = 1.0):
    """在这套配置的范围里挑一个读数；返回 (评分, 满级数值, 是否负面, 卡面数值) 或 None。

    `strict` 为真时先听图上读到的符号：卡面符号要和这条槽位「本该有的号」一致
    （`_expected_sign`）。注意「本该有的号」不等于「正面就是 +」——后坐力作正面
    词条时卡面写的正是 `-`，所以这里比的是槽位，不是正负。
    `scale` 是「卡面数值 / 满级数值」：没升满的卡要先除掉它再比范围。
    """
    best = None
    # 倍率词条（`x1.5`）只有一位小数，反推成百分数有 ±5 的不确定度
    slack = _MULTIPLIER_SLACK if is_multiplier_stat(candidate.name) else 0.0
    for value, votes in candidate.options:
        scaled = value / (scale or 1.0)
        prefer = candidate.polarity(value)  # 卡面上读到的是 + 还是 -
        slots: List[Tuple[Tuple[float, float], bool]] = []
        if candidate.name in config.positives:
            slots.append((config.positives[candidate.name], False))
        if candidate.name in config.negatives:
            slots.append((config.negatives[candidate.name], True))
        if not slots:
            return None
        ordered = slots
        if strict and prefer:
            matched = [slot for slot in slots
                       if _expected_sign(slot[0]) in ("", prefer)]
            if matched:
                ordered = matched
        for span, negative in ordered:
            # 输出用槽位该有的号：卡面写的本来就该是这个号
            sign = _expected_sign(span) or prefer or ("-" if value < 0 else "+")
            plain = _distance(scaled, span, slack)
            gap, flipped = plain, False
            if not prefer:
                # 符号没读到：`79.3` 可能是 `-79.3` 少读了减号，两个方向都试，
                # 翻转要付代价，免得把真·正数硬翻成负数
                mirrored = _distance(-scaled, span, slack) + _SIGN_FIX_PENALTY
                if mirrored < gap:
                    gap, flipped = mirrored, True
            signed = abs(value) * (-1.0 if sign == "-" else 1.0)
            mismatch = 0 if (not prefer or sign == prefer) else 1
            score = (gap, -votes, mismatch)
            if best is None or score < best[0]:
                best = (score, scaled, negative, signed, flipped)
    return best


_MAX_CANDIDATES = 8

# 紫卡没升满时，卡面上显示的是**当前等级**的数值，不是满级（Lv.8）的。
# 实测：一张未升级的紫卡，四条数值正好都是满级范围的 1/9，说明等级 0~8
# 对应 1/9 ~ 9/9 线性递增。不换算的话，这类卡的数值怎么都套不进 Lv.8 范围，
# 只能走兜底分支——之前「弹匣容量 -4.7%」被当成 +4.7%、还多出一条词条，
# 根子就在这里。
RIVEN_MAX_RANK = 8
RANK_SCALES = [(rank + 1) / 9.0 for rank in range(RIVEN_MAX_RANK + 1)]
# 先按满级算；只有满级套不上，才往低等级试（从高到低）
_SCALE_ORDER = [1.0] + [scale for scale in reversed(RANK_SCALES) if scale < 1.0]


def rank_for_scale(scale: float) -> int:
    """换算系数 → 紫卡等级（1/9 是 0 级，9/9 是 8 级）。"""
    return max(0, min(RIVEN_MAX_RANK, int(round(scale * 9.0)) - 1))


def _family(name: str) -> str:
    """词条名家族：中文名取前两字。

    `暴击伤害` / `暴击几率` 同属「暴击」，`弹药最大值` / `弹药效率` 同属「弹药」。
    OCR 把同族的两个词条读混时，同一条数值会投出两个词条——靠这个把它们标成互斥。
    """
    return str(name or "")[:2]


def _family_key(candidate: Candidate) -> Tuple[str, str]:
    return ("%.1f" % abs(candidate.value), _family(candidate.name))


def _conflicts(candidates: Sequence[Candidate]) -> List[List[int]]:
    """互相排斥的候选下标：同一条数值（卡上不可能重复）+ 同一个词族。

    例如 6 份预处理里 3 份读成「暴击伤害 11.2」、2 份读成「暴击几率 11.2」，
    两个都会进候选；不标互斥的话，结果里就会平白多出一条。
    """
    groups: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        if candidate.options:
            groups[_family_key(candidate)].append(index)
    return [indices for indices in groups.values() if len(indices) > 1]

# 「数值落在范围里」允许的误差：绝对 2.0，或者数值的 5%（取大的那个）。
# 只用来吸收数据表与游戏内的四舍五入，读错一位（几十个百分点）不会混进来。
_FIT_SLACK = 2.0


def _search(usable: Sequence[Candidate], configs: Sequence[_Config],
            conflicts: Sequence[Sequence[int]], strict: bool,
            allow_unfit: bool = False, rank: int = 0, scale: float = 1.0):
    """枚举「用哪几条候选 + 哪套配置」，返回最贴合的一组。

    `rank` 是这一轮的可信度（0 最可信），只在「保留的词条一样多」时才拿来分胜负：
    先保证不漏词条，再看是不是听了卡面的符号、是不是满级的数值。
    `allow_unfit` 用于兜底轮：数值和理论范围对不上，但只要卡面上明明白白写着
    `+` / `-`，就照卡面给，宁可提示「请核对」也不擅自改符号。
    `scale` 是「卡面数值 / 满级数值」，用来认没升满的紫卡。
    """
    total = len(usable)
    scale_penalty = 0 if scale >= 1.0 else 1
    best = None
    for config in configs:
        size = config.want_positive + config.want_negative
        if size > total:
            continue
        for subset in combinations(range(total), size):
            chosen = set(subset)
            # 同一条数值 + 同一个词族只能留一个：OCR 读混出来的重影不能都要
            if any(sum(1 for index in group if index in chosen) > 1
                   for group in conflicts):
                continue
            picks = []
            cost = 0.0
            votes = 0
            unfit = 0
            mismatch = 0
            positives = negatives = 0
            failed = False
            for index in subset:
                candidate = usable[index]
                picked = _pick(candidate, config, strict=strict, scale=scale)
                if picked is None:
                    failed = True
                    break
                score, scaled, negative, signed, flipped = picked
                # 只把「真的超范围」算进贴合度；「补符号」的代价只参与排序
                gap = score[0] - (_SIGN_FIX_PENALTY if flipped else 0.0)
                cost += score[0]
                votes += -score[1]
                mismatch += score[2]
                # 「贴合」要求数值真的落在该配置的范围里（只容忍几分的误差）。
                # 差得远的说明这一套配置根本不是这张卡的配置。
                if gap > max(_FIT_SLACK, abs(scaled) * 0.05):
                    unfit += 1
                if negative:
                    negatives += 1
                else:
                    positives += 1
                picks.append((candidate.name, signed, negative, gap))
            if failed:
                continue
            if (positives, negatives) != (config.want_positive, config.want_negative):
                continue
            # 宁可弃权也不硬凑：只要有一条数值对不上范围，这套配置就不算数，
            # 免得为了凑数量把负数读成正数、或者把读错的数值当真。
            if unfit and not allow_unfit:
                continue
            # 先尽量少丢词条（漏读一条不该反过来把别的词条挤掉），再看这一轮
            # 可不可信（听没听卡面的符号、是不是满级），然后才比贴合程度与票数。
            key = (total - size, rank, scale_penalty, round(cost, 1),
                   -votes, mismatch)
            if best is None or key < best[0]:
                dropped = [usable[i] for i in range(total) if i not in subset]
                best = (key, config, picks, dropped)
    return best


def resolve(candidates: Sequence[Candidate], analysis: RivenAnalysis) -> Resolution:
    """按这把武器的词条表与数值范围，挑读数、定正负、丢掉多认的词条。

    正负**以图上的符号为准**：`+` 是正向、`-` 是负向，这是卡面直接写明的，
    比拿范围反推可靠。范围只用来做两件事——在多个读数里挑出真正合规的那个
    （`-19.3` / `79.3` 里挑 `-79.3`），以及把凑不进任何配置的词条丢掉。
    符号全都没读到时，才退回用范围判断正负。
    """
    notes: List[str] = []
    usable: List[Candidate] = []
    configs = _configs(analysis)
    for candidate in candidates:
        if not candidate.options:
            continue
        if not any(candidate.name in config.positives or
                   candidate.name in config.negatives for config in configs):
            notes.append("%s 不是 %s 的词条，已忽略" % (candidate.name, analysis.name))
            continue
        usable.append(candidate)

    if not usable:
        return Resolution(notes=notes)

    if len(usable) > _MAX_CANDIDATES:
        # 只留票最多的几条，但**保留卡面顺序**：候选数过多时要剪枝，
        # 不能顺手把词条位置重排了
        keep = sorted(range(len(usable)),
                      key=lambda index: -usable[index].votes_total)[:_MAX_CANDIDATES]
        usable = [usable[index] for index in sorted(keep)]

    conflicts = _conflicts(usable)
    has_signs = any(candidate.polarity(value)
                    for candidate in usable
                    for value, _count in candidate.options)
    # 四轮按可信度排序，取「保留词条最多 + 最可信」的那一组：
    #   0 听卡面符号，数值也要贴范围（正常情况）
    #   1 听卡面符号，数值对不上也照给（`-` 就是负的，只提示核对）
    #   2 符号没法用，退回按范围定正负
    #   3 符号和范围都不靠谱，按范围定正负且放宽数值
    passes = [(True, False, 0), (True, True, 1), (False, False, 2),
              (False, True, 3)]

    def run(scale: float):
        found = None
        for strict, allow_unfit, rank in passes:
            if allow_unfit and not has_signs:
                continue  # 连符号都没读到，就不该放宽数值要求
            result = _search(usable, configs, conflicts, strict=strict,
                             allow_unfit=allow_unfit, rank=rank, scale=scale)
            if result is not None and (found is None or result[0] < found[0]):
                found = result
        return found

    best = run(1.0)
    best_scale = 1.0 if best is not None else 0.0
    # 满级套不上时，再按「这张卡没升满」试一遍：等级 0~8 对应 1/9~9/9
    if best is None or best[0][0] or best[0][1]:
        for scale in _SCALE_ORDER[1:]:
            found = run(scale)
            if found is not None and (best is None or found[0] < best[0]):
                best, best_scale = found, scale

    if best is not None:
        rank = best[0][1]
        if rank == 1:
            notes.append("数值与理论范围对不上，已按卡面符号列出，请核对原图")
        elif rank >= 2:
            notes.append("正负号没和卡面对上，已按词条范围判断，请核对原图")

    if best is None:
        # 没有哪套配置能全部对上：按每行票最多的读数直出，并说明需要人工核对
        notes.append("没找到能对上的 2+/2+1/3+/3+1 配置，下面按图上读到的列出，"
                     "请核对原图")
        pairs: List[Tuple[str, float]] = []
        taken = set()
        for candidate in usable:
            key = _family_key(candidate)
            if key in taken:
                continue
            taken.add(key)
            pairs.append((candidate.name, candidate.value))
        return Resolution(pairs=pairs, notes=notes)

    _key, config, picks, dropped = best
    scale = best_scale or 1.0
    pairs: List[Tuple[str, float]] = []
    for name, value, negative, gap in picks:
        if gap > 1.0:
            notes.append("%s %s 略超 %s 配置范围，请核对原图"
                         % (name, format_roll(None, name, value), config.label))
        pairs.append((name, value))
    for candidate in dropped:
        notes.append("%s 与 %s 的词条数量对不上，已忽略" % (candidate.name, config.label))
    if scale < 1.0:
        notes.insert(0, "这张紫卡还没升满（约 Lv.%d）：数值按卡面显示，"
                        "评分已换算到 Lv.8 比较" % rank_for_scale(scale))
    return Resolution(pairs=pairs, notes=notes, config=config.label,
                      scale=scale)


def finalize(candidates: Sequence[Candidate],
             analysis: RivenAnalysis) -> Tuple[List[Tuple[str, float]], List[str]]:
    """`resolve` 的简写版：只要最终词条和提示。"""
    result = resolve(candidates, analysis)
    return result.pairs, result.notes
