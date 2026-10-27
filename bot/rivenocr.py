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
4. **符号读反**——`+99.9%` 偶尔读成 `-99.9%`。校验阶段会看这个词条在
   这把武器上是不是只可能是正面（或只可能负面），落错边就纠正过来。
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

from .rivencalc import ALIASES, RivenAnalysis

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
    """一个识别出来的词条；同一个词条可能有多个读数（不同变体读出来的）。"""

    name: str
    options: List[Tuple[float, int]] = field(default_factory=list)
    sample: str = ""

    @property
    def value(self) -> float:
        return self.options[0][0] if self.options else 0.0

    @property
    def votes(self) -> int:
        return self.options[0][1] if self.options else 0

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
    r"(?P<mult>[x×X]\s*(?P<mnum>\d+(?:[.,]\d+)?))"
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
        raw = match.group("mnum")
        return _Token(value=(float(raw.replace(",", ".")) - 1.0) * 100.0,
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


def _line_rolls(line: str) -> List[Tuple[str, float, float]]:
    """从一行里提取 [(标准名, 数值, 权重)]。"""
    stats = _find_stats(line)
    stats = stats + _fuzzy_stats(line, stats)
    stats.sort(key=lambda item: item[0])

    entries: List[Tuple[int, int, int, str, float, float]] = []
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
        if not token.multiplier and sign and sign in _NEG_CHARS and value > 0:
            value = -value
        if abs(value) > 999:
            continue
        # 带明确正负号的读数比「光秃秃一个数字」可信（符号更容易被漏读）
        if sign or token.multiplier:
            weight += 1.0
        entries.append((token.start, order, distance, canonical, weight,
                        _quantize(value, token.raw)))

    # 同一个数字只认离它最近的词条名：`x1.5 对 Grineer 的伤害` 里「Grineer」
    # 和尾部的「伤害」都会命中，但只能算一条对派系伤害。
    best: Dict[int, Tuple[int, int, int, str, float, float]] = {}
    for entry in entries:
        previous = best.get(entry[0])
        if previous is None or entry[2] < previous[2]:
            best[entry[0]] = entry

    result: List[Tuple[str, float, float]] = []
    seen = set()
    for _start, _order, _distance, canonical, weight, value in sorted(
            best.values(), key=lambda item: item[1]):
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append((canonical, value, weight))

    # 顺带给「多读一位」的读数记一票（票力三分之一），
    # 494.1 / 143 1 这类偏差太大的，范围校验会把它挑掉
    extra: List[Tuple[str, float, float]] = []
    for canonical, value, weight in result:
        stripped = _leading_variant(value)
        if stripped is not None:
            extra.append((canonical, stripped, max(1.0, weight / 3.0)))
    return result + extra


# ---------------------------------------------------------------------- 投票
def read_texts(texts: Sequence[str]) -> List[Candidate]:
    """多份 OCR 文本合并投票，得到去重后的词条列表。"""
    votes: Dict[str, Counter] = defaultdict(Counter)
    samples: Dict[str, Dict[str, str]] = defaultdict(dict)
    order: List[str] = []

    for text in texts:
        seen: Dict[str, set] = defaultdict(set)
        for line in _lines(text):
            try:
                rolls = _line_rolls(line)
            except Exception:  # pragma: no cover - 识别异常不该拖垮整条消息
                continue
            for name, value, weight in rolls:
                key = "%.1f" % value
                if key in seen[name]:
                    continue
                seen[name].add(key)
                votes[name][key] += int(round(weight))
                samples[name].setdefault(key, line[:44])
                if name not in order:
                    order.append(name)

    candidates: List[Candidate] = []
    for name in order:
        counter = votes.get(name) or Counter()
        if not counter:
            continue
        options = sorted(((float(key), count) for key, count in counter.items()),
                         key=lambda item: (-item[1], -abs(item[0])))
        best_sample = samples[name].get("%.1f" % options[0][0], "")
        candidates.append(Candidate(name=name, options=options,
                                    sample=best_sample))
    return candidates


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
def _distance(value: float, span: Tuple[float, float]) -> float:
    low, high = span
    if low > high:
        low, high = high, low
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
    """负面词条在数据表里存的是负数（如 -89.1 ~ -72.9），这里统一取绝对值，
    好和从图上读到的「数值大小」直接比距离。"""
    result: Dict[str, Tuple[float, float]] = {}
    for stat in group or []:
        low, high = abs(stat.low), abs(stat.high)
        if low > high:
            low, high = high, low
        result[stat.name] = (low, high)
    return result


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
    """校验结果：最终词条 + 提示 + 命中的配置。"""

    pairs: List[Tuple[str, float]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    config: str = ""


def _pick(candidate: Candidate, config: _Config):
    """在这套配置的范围里挑一个读数；返回 (成本, 数值, 是否负面) 或 None。"""
    best = None
    for value, votes in candidate.options:
        magnitude = abs(value)
        choices = []
        if candidate.name in config.positives:
            choices.append((_distance(magnitude, config.positives[candidate.name]),
                            False))
        if candidate.name in config.negatives:
            choices.append((_distance(magnitude, config.negatives[candidate.name]),
                            True))
        if not choices:
            return None
        for gap, negative in choices:
            # 同样贴近时：票多的优先，其次保持图上读到的正负
            mismatch = 1 if (value < 0) != negative else 0
            score = (gap, -votes, mismatch)
            if best is None or score < best[0]:
                best = (score, magnitude, negative)
    return best


_MAX_CANDIDATES = 8

# 「数值落在范围里」允许的误差：绝对 2.0，或者数值的 5%（取大的那个）。
# 只用来吸收数据表与游戏内的四舍五入，读错一位（几十个百分点）不会混进来。
_FIT_SLACK = 2.0


def resolve(candidates: Sequence[Candidate], analysis: RivenAnalysis) -> Resolution:
    """按这把武器的词条表与数值范围，挑读数、纠符号、丢掉多认的词条。

    四种配置（2+ / 2+1 / 3+ / 3+1）各自枚举「用哪几条候选」，数值全都贴得上
    的那套胜出；配置也顺带决定了哪个词条该是负的——比单看符号可靠得多。
    多认出来的词条（OCR 把杂质读成词条名）会因为凑不进任何配置而被丢掉。
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
        usable.sort(key=lambda item: -sum(count for _value, count in item.options))
        del usable[_MAX_CANDIDATES:]

    total = len(usable)
    best = None
    for config in configs:
        size = config.want_positive + config.want_negative
        if size > total:
            continue
        for subset in combinations(range(total), size):
            picks = []
            cost = 0.0
            votes = 0
            unfit = 0
            mismatch = 0
            positives = negatives = 0
            failed = False
            for index in subset:
                candidate = usable[index]
                picked = _pick(candidate, config)
                if picked is None:
                    failed = True
                    break
                score, magnitude, negative = picked
                gap = score[0]
                cost += gap
                votes += -score[1]
                mismatch += score[2]
                # 「贴合」要求数值真的落在该配置的范围里（只容忍几分的误差）。
                # 差得远的说明这一套配置根本不是这张卡的配置。
                if gap > max(_FIT_SLACK, magnitude * 0.05):
                    unfit += 1
                if negative:
                    negatives += 1
                else:
                    positives += 1
                picks.append((candidate.name, magnitude, negative, gap))
            if failed:
                continue
            if (positives, negatives) != (config.want_positive, config.want_negative):
                continue
            # 宁可弃权也不硬凑：只要有一条数值对不上范围，这套配置就不算数，
            # 免得为了凑数量把负数读成正数、或者把读错的数值当真。
            if unfit:
                continue
            # 先尽量少丢词条（漏读一条不该反过来把别的词条挤掉），再比贴合程度、
            # 保留的票数、以及正负符是否和图上一致。
            key = (total - size, round(cost, 1), -votes, mismatch)
            if best is None or key < best[0]:
                dropped = [usable[i] for i in range(total) if i not in subset]
                best = (key, config, picks, dropped)

    if best is None:
        # 没有哪套配置能全部对上：按每行票最多的读数直出，并说明需要人工核对
        notes.append("没找到能对上的 2+/2+1/3+/3+1 配置，下面按图上读到的列出，"
                     "请核对原图")
        return Resolution(pairs=[(item.name, item.value) for item in usable],
                          notes=notes)

    _key, config, picks, dropped = best
    pairs: List[Tuple[str, float]] = []
    for name, magnitude, negative, gap in picks:
        value = -magnitude if negative else magnitude
        if gap > 1.0:
            notes.append("%s %.1f%% 略超 %s 配置范围，请核对原图"
                         % (name, value, config.label))
        pairs.append((name, value))
    for candidate in dropped:
        notes.append("%s 与 %s 的词条数量对不上，已忽略" % (candidate.name, config.label))
    return Resolution(pairs=pairs, notes=notes, config=config.label)


def finalize(candidates: Sequence[Candidate],
             analysis: RivenAnalysis) -> Tuple[List[Tuple[str, float]], List[str]]:
    """`resolve` 的简写版：只要最终词条和提示。"""
    result = resolve(candidates, analysis)
    return result.pairs, result.notes
