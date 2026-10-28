"""轮换商店：言录使（Acrithis）、埃尔戈·格拉斯（Ergo Glast）、埃莉诺（Eleanor）。

三个商店官方都没有接口，数据分散在 wiki 的原始 wikitext 里：

- 言录使：`Acrithis/Current Offerings` 子页面里的 `AcrithisObserved` + Item1..5，
  由玩家每周上报；本周轮换是**随机**的，算不出来。
- 埃尔戈·格拉斯：`Tenet Weapons` 里 5 把信条近战的元素与加成，同样是玩家上报的
  数值；兑换武器固定是那 5 把，只有加成每 4 天刷新。
- 埃莉诺：终幕武器分 A / B 两批，批次由 4 天周期**可以算出来**（锚点 2025-03-18），
  每把武器的元素与加成是玩家上报的。

国内服务器直连 wiki.warframe.com 会被 Cloudflare 403，所以直连失败时改走
`r.jina.ai` 中转；两边都不通就继续用上次的快照，命令照常能回。
"""

from __future__ import annotations

import calendar
import copy
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .net import HttpClient, HttpError
from .wiki import BROWSER_UA, wiki_proxy_candidates

LOGGER = logging.getLogger("qqwarframe.rotation")

_PAGE = "https://wiki.warframe.com/index.php?title=%s&action=raw"
PAGES = {
    "acrithis": _PAGE % "Acrithis/Current_Offerings",
    "glast": _PAGE % "Tenet_Weapons",
    "coda": _PAGE % "Coda_Weapons",
}


def _utc(year: int, month: int, day: int) -> float:
    return float(calendar.timegm((year, month, day, 0, 0, 0, 0, 0, 0)))


# 三个商店的刷新锚点，都能在 wiki 的 {{Countdown}} 里对上：
# 格拉斯 December 3, 2015 00:00 UTC（4 天一轮）
# 埃莉诺 March 18, 2025 00:00 UTC（A 4 天 + B 4 天）
# 言录使 December 21, 2015 00:00 UTC（7 天一轮，周一 00:00 UTC）
GLAST_ANCHOR = _utc(2015, 12, 3)
GLAST_PERIOD = 4 * 86400
ELEANOR_ANCHOR = _utc(2025, 3, 18)
ELEANOR_BATCH = 4 * 86400
ACRITHIS_ANCHOR = _utc(2015, 12, 21)
ACRITHIS_PERIOD = 7 * 86400

ELEMENT_CN = {
    "impact": "冲击", "puncture": "穿刺", "slash": "切割",
    "heat": "火焰", "cold": "冰冻", "electricity": "电击", "toxin": "毒素",
    "magnetic": "磁力", "radiation": "辐射", "corrosive": "腐蚀",
    "viral": "病毒", "gas": "毒气", "blast": "爆炸",
}

# 格拉斯固定卖这 5 把（wiki 表格顺序），每 4 天换一次元素与数值
TENET_MELEE: Tuple[Tuple[str, str], ...] = (
    ("Tenet Ferrox", "信条·铁晶磁轨炮"),
    ("Tenet Grigori", "信条·格里高利"),
    ("Tenet Livia", "信条·莉薇娅"),
    ("Tenet Exec", "信条·枢密"),
    ("Tenet Agendus", "信条·集议"),
)

# 埃莉诺的 A / B 批（顺序同 wiki：主武器 → 副武器 → 近战）
CODA_BATCHES: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "A": (
        ("Coda Hema", "终幕·血肢"),
        ("Coda Sporothrix", "终幕·孢丝感染枪"),
        ("Coda Catabolyst", "终幕·异化者"),
        ("Coda Pox", "终幕·脓痘"),
        ("Dual Coda Torxica", "终幕·孢蚀双枪"),
        ("Coda Mire", "终幕·米尔"),
        ("Coda Motovore", "终幕·噬轮"),
    ),
    "B": (
        ("Coda Bassocyst", "终幕·低音爆囊"),
        ("Coda Bubonico", "终幕·横痃重炮"),
        ("Coda Synapse", "终幕·突触生化枪"),
        ("Coda Tysis", "终幕·啐沫者"),
        ("Coda Caustacyst", "终幕·灼蚀变体镰"),
        ("Coda Hirudo", "终幕·蚂蟥"),
        ("Coda Pathocyst", "终幕·病囊飞刃"),
    ),
}

# 言录使周轮换候选池：英文名 -> (中文名, 简称, 苦栓价格)
ACRITHIS_ITEMS: Dict[str, Tuple[str, str, int]] = {
    "Orokin Reactor": ("奥罗金反应堆蓝图", "金土豆", 20),
    "Orokin Catalyst": ("奥罗金催化剂蓝图", "银土豆", 20),
    "Exilus Warframe Adapter": ("战甲特殊功能槽连接器", "战甲特殊槽", 20),
    "Exilus Weapon Adapter": ("武器特殊功能槽连接器", "武器特殊槽", 20),
    "Primary Arcane Adapter": ("主要武器赋能连接器", "主武器赋能槽", 25),
    "Secondary Arcane Adapter": ("次要武器赋能连接器", "副武器赋能槽", 25),
    "Forma": ("福马蓝图", "福马", 10),
    "Melee Riven Mod": ("近战裂罅 MOD", "近战裂罅", 15),
    "Pistol Riven Mod": ("手枪裂罅 MOD", "手枪裂罅", 15),
    "Rifle Riven Mod": ("步枪裂罅 MOD", "步枪裂罅", 15),
    "Kitgun Riven Mod": ("组合枪裂罅 MOD", "组合枪裂罅", 15),
    "Zaw Riven Mod": ("Zaw 裂罅 MOD", "Zaw 裂罅", 15),
    "Shotgun Riven Mod": ("霰弹枪裂罅 MOD", "霰弹裂罅", 15),
    "Companion Weapon Riven Mod": ("同伴武器裂罅 MOD", "同伴裂罅", 20),
    "10,000 Kuva": ("10,000 赤毒", "1 万赤毒", 10),
}

ACRITHIS_DAILY_HINT = "每日赋能 / Captura 每天 08:00（北京时间）换，wiki 没有上报，得进游戏看。"


# ---------------------------------------------------------------------- 时间
def next_slot(anchor: float, period: float, now: Optional[float] = None) -> float:
    """锚点起、每 period 秒一次的轮换，下一次刷新的时间戳。"""
    now = time.time() if now is None else float(now)
    if now < anchor:
        return anchor
    return anchor + (int((now - anchor) // period) + 1) * period


def glast_next(now: Optional[float] = None) -> float:
    return next_slot(GLAST_ANCHOR, GLAST_PERIOD, now)


def acrithis_next(now: Optional[float] = None) -> float:
    return next_slot(ACRITHIS_ANCHOR, ACRITHIS_PERIOD, now)


def eleanor_state(now: Optional[float] = None) -> Tuple[str, float]:
    """返回 (当前批次 "A"/"B", 下次切换时间戳)。

    对应 wiki 的 floor(((now - 2025-03-18) mod 8d) / 4d)：
    前 4 天 A，后 4 天 B，如此交替。
    """
    now = time.time() if now is None else float(now)
    if now < ELEANOR_ANCHOR:
        return "A", ELEANOR_ANCHOR
    slot = int((now - ELEANOR_ANCHOR) // ELEANOR_BATCH)
    batch = "A" if slot % 2 == 0 else "B"
    return batch, ELEANOR_ANCHOR + (slot + 1) * ELEANOR_BATCH


# ---------------------------------------------------------------------- 解析
_OBSERVED_RE = re.compile(r"AcrithisObserved\s*\|\s*([^}\n]+)")
_ITEM_RE = re.compile(r"AcrithisItem\d\s*\|\s*([^}\n]+)")

_BONUS_ROW_RE = re.compile(
    r"<td>\s*(?:\{\{\s*Weapon\s*\|\s*([^}]+?)\s*\}\}|([^<]+?))\s*</td>\s*"
    r"<td>\s*\{\{\s*D\s*\|\s*([A-Za-z]+)\s*\}\}\s*</td>\s*"
    r"<td>\s*\{\{\s*ValenceBonusPercentageColor\s*\|\s*([\d.]+)\s*\}\}",
    re.I)


def parse_acrithis(text: str) -> Optional[Dict[str, Any]]:
    """解析 `Acrithis/Current Offerings` 的 wikitext。"""
    items = [item.strip() for item in _ITEM_RE.findall(text or "")]
    items = [item for item in items if item]
    if not items:
        return None
    observed = _OBSERVED_RE.search(text or "")
    return {
        "observed": observed.group(1).strip() if observed else "",
        "items": items[:5],
    }


def parse_bonus_rows(text: str) -> List[Tuple[str, str, float]]:
    """从 wiki 表格里抠出 (武器英文名, 元素, 加成百分比)。"""
    rows: List[Tuple[str, str, float]] = []
    for match in _BONUS_ROW_RE.finditer(text or ""):
        weapon = (match.group(1) or match.group(2) or "").strip()
        element = (match.group(3) or "").strip().title()
        try:
            percent = float(match.group(4))
        except (TypeError, ValueError):
            continue
        if not weapon:
            continue
        rows.append((weapon, element, percent))
    return rows


def parse_glast(text: str) -> List[Tuple[str, str, float]]:
    """格里斯的 5 把信条近战加成（只认已知的 5 把，防止抓到别的表）。"""
    known = {name for name, _cn in TENET_MELEE}
    rows = [row for row in parse_bonus_rows(text) if row[0] in known]
    return rows


def parse_coda(text: str) -> Dict[str, List[Tuple[str, str, float]]]:
    """埃莉诺 A / B 两批加成：以 `Weapon (Batch B)` 为界把两张表分开。"""
    text = text or ""
    known = {name for batch in CODA_BATCHES.values() for name, _cn in batch}
    split_at = text.find("Weapon (Batch B)")
    if split_at <= 0:
        # 只有一个批次表时按武器归属分开
        head, tail = text, ""
    else:
        head, tail = text[:split_at], text[split_at:]
    result = {"A": [], "B": []}
    for part, key in ((head, "A"), (tail, "B")):
        allowed = {name for name, _cn in CODA_BATCHES[key]}
        result[key] = [row for row in parse_bonus_rows(part)
                       if row[0] in allowed and row[0] in known]
    if not result["A"] and not result["B"]:
        # 兜底：整页解析后按武器所属批次归类
        for weapon, element, percent in parse_bonus_rows(text):
            for key, batch in CODA_BATCHES.items():
                if weapon in {name for name, _cn in batch}:
                    result[key].append((weapon, element, percent))
    return result


_MONTHS = {
    name.lower(): index
    for index, name in enumerate((
        "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December"), 1)
}
_OBSERVED_DATE_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})")


def format_observed(text: str) -> str:
    """把 wiki 的 "October 26, 2026" 写成「10 月 26 日」；认不出来就原样返回。"""
    match = _OBSERVED_DATE_RE.search(text or "")
    if not match:
        return (text or "").strip()
    month = _MONTHS.get(match.group(1).lower())
    if not month:
        return (text or "").strip()
    return "%d 月 %d 日" % (month, int(match.group(2)))


def strip_jina(text: str) -> str:
    """去掉 r.jina.ai 在正文前加的几行头。"""
    marker = "Markdown Content:"
    index = (text or "").find(marker)
    if index >= 0:
        return text[index + len(marker):].lstrip("\n")
    return text or ""


# ---------------------------------------------------------------------- 客户端
class RotationClient:
    """后台抓轮换数据 + 本地快照，命令只读快照。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 logger: Optional[logging.Logger] = None,
                 http_factory=None):
        config = config or {}
        self.log = logger or LOGGER
        self.enabled = bool(config.get("enabled", True))
        self.cache_file = str(config.get("cache_file", "data/rotation_cache.json"))
        self.refresh_seconds = max(300, int(config.get("refresh_seconds", 1800)))
        self.acrithis_seconds = max(300, int(config.get("acrithis_seconds",
                                                       self.refresh_seconds)))
        self.glast_seconds = max(300, int(config.get("glast_seconds",
                                                     self.refresh_seconds)))
        self.coda_seconds = max(300, int(config.get("coda_seconds",
                                                    self.refresh_seconds)))
        self.timeout = max(5.0, float(config.get("timeout_seconds", 25)))
        self.use_jina = bool(config.get("use_jina", True))
        self.jina_base = str(config.get("jina_base", "https://r.jina.ai/"))
        self.user_agent = str(config.get("user_agent") or BROWSER_UA)
        self.proxies = wiki_proxy_candidates(config) or [""]
        self.tick = max(30, int(config.get("tick_seconds", 60)))

        self._config = config
        self._http_factory = http_factory
        self._clients: Dict[str, HttpClient] = {}
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "acrithis": None, "glast": [], "coda": {"A": [], "B": []},
        }
        self._fetched: Dict[str, float] = {"acrithis": 0.0, "glast": 0.0,
                                           "coda": 0.0}
        self._strategy: Dict[str, Tuple[str, str]] = {}
        self._error = ""
        self._saved_at = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._load()

    # ------------------------------------------------------------------ 网络
    def _client(self, proxy: str) -> HttpClient:
        if self._http_factory is not None:
            return self._http_factory(proxy)
        key = proxy or "system"
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = HttpClient(user_agent=self.user_agent,
                                    timeout=self.timeout, proxy=proxy,
                                    retries=1, logger=self.log)
                self._clients[key] = client
            return client

    def _candidates(self, page: str) -> List[Tuple[str, str]]:
        """(方式, 代理) 的组合，上次成功的排最前。"""
        combos: List[Tuple[str, str]] = []
        if self.use_jina:
            combos.extend(("jina", proxy) for proxy in self.proxies)
        combos.extend(("wiki", proxy) for proxy in self.proxies)
        remembered = self._strategy.get(page)
        if remembered in combos:
            combos.remove(remembered)
            combos.insert(0, remembered)
        return combos

    def _fetch_page(self, page: str) -> str:
        url = PAGES[page]
        errors: List[str] = []
        for mode, proxy in self._candidates(page):
            target = (self.jina_base + urllib.parse.quote(url, safe="")
                      if mode == "jina" else url)
            try:
                text = self._client(proxy).get_text(target)
            except HttpError as exc:
                errors.append("%s@%s: %s" % (mode, proxy or "system", exc))
                continue
            if mode == "jina":
                text = strip_jina(text)
            if text and len(text) > 200:
                self._strategy[page] = (mode, proxy)
                return text
            errors.append("%s@%s: 内容过短" % (mode, proxy or "system"))
        raise HttpError("；".join(errors[-2:]) or "轮换数据抓取失败", url=url)

    # ------------------------------------------------------------------ 刷新
    def refresh(self, force: bool = False) -> bool:
        """按各自的 TTL 抓一遍，返回是否有更新。"""
        if not self.enabled:
            return False
        now = time.time()
        ttl = {"acrithis": self.acrithis_seconds, "glast": self.glast_seconds,
               "coda": self.coda_seconds}
        changed = False
        for page in ("acrithis", "glast", "coda"):
            with self._lock:
                if not force and now - self._fetched.get(page, 0.0) < ttl[page]:
                    continue
            try:
                text = self._fetch_page(page)
            except HttpError as exc:
                with self._lock:
                    self._error = "%s %s" % (page, exc)
                self.log.warning("轮换数据抓取失败（%s）：%s", page, exc)
                continue
            parsed = self._apply(page, text)
            if parsed:
                changed = True
                self.log.info("轮换数据已更新：%s", page)
            with self._lock:
                self._fetched[page] = time.time()
                self._error = ""
        if changed:
            self._save()
        return changed

    def _apply(self, page: str, text: str) -> bool:
        with self._lock:
            if page == "acrithis":
                data = parse_acrithis(text)
                if not data:
                    return False
                self._data["acrithis"] = data
                return True
            if page == "glast":
                rows = parse_glast(text)
                if not rows:
                    return False
                self._data["glast"] = [list(row) for row in rows]
                return True
            batches = parse_coda(text)
            if not batches["A"] and not batches["B"]:
                return False
            self._data["coda"] = {key: [list(row) for row in rows]
                                  for key, rows in batches.items()}
            return True

    # ------------------------------------------------------------------ 线程
    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="rotation",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001 - 后台线程不能死
                self.log.warning("轮换刷新异常：%s", exc)
            if self._stop.wait(self.tick):
                return

    # ------------------------------------------------------------------ 持久化
    def _load(self) -> None:
        path = self.cache_file
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            self.log.warning("轮换快照读取失败：%s", exc)
            return
        with self._lock:
            data = raw.get("data") or {}
            self._data = {
                "acrithis": data.get("acrithis") or None,
                "glast": data.get("glast") or [],
                "coda": data.get("coda") or {"A": [], "B": []},
            }
            self._fetched.update(
                {key: float(value or 0)
                 for key, value in (raw.get("fetched") or {}).items()
                 if key in self._fetched})

    def _save(self) -> None:
        path = self.cache_file
        if not path:
            return
        with self._lock:
            payload = {"version": 1, "saved": time.time(),
                       "data": copy.deepcopy(self._data),
                       "fetched": dict(self._fetched)}
        try:
            directory = os.path.dirname(path)
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp, path)
            self._saved_at = time.time()
        except OSError as exc:
            self.log.warning("轮换快照写入失败：%s", exc)

    # ------------------------------------------------------------------ 查询
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "data": copy.deepcopy(self._data),
                "fetched": dict(self._fetched),
                "error": self._error,
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "updated": max(self._fetched.values() or [0.0]),
                "error": self._error,
                "acrithis": bool(self._data.get("acrithis")),
                "glast": len(self._data.get("glast") or []),
                "coda_a": len((self._data.get("coda") or {}).get("A") or []),
                "coda_b": len((self._data.get("coda") or {}).get("B") or []),
            }

    # 以下是给表现层用的“已经翻成中文”的视图 ---------------------------
    def acrithis_view(self) -> Dict[str, Any]:
        with self._lock:
            data = copy.deepcopy(self._data.get("acrithis") or {})
            fetched = self._fetched.get("acrithis", 0.0)
        rows = []
        for name in data.get("items") or []:
            cn, short, cost = ACRITHIS_ITEMS.get(name, (name, name, 0))
            rows.append({"name": cn, "short": short, "cost": cost,
                         "en": name})
        return {"observed": format_observed(data.get("observed", "")),
                "observed_raw": data.get("observed", ""), "rows": rows,
                "fetched": fetched, "next": acrithis_next()}

    def glast_view(self) -> Dict[str, Any]:
        with self._lock:
            raw = copy.deepcopy(self._data.get("glast") or [])
            fetched = self._fetched.get("glast", 0.0)
        by_name = {}
        for weapon, element, percent in raw:
            by_name[weapon] = (element, percent)
        rows = []
        for name, cn in TENET_MELEE:
            element, percent = by_name.get(name, ("", 0.0))
            rows.append({"name": cn, "element": ELEMENT_CN.get(element.lower(),
                                                              element),
                         "percent": percent})
        return {"rows": rows, "fetched": fetched, "next": glast_next(),
                "complete": all(row["element"] for row in rows)}

    def eleanor_view(self, now: Optional[float] = None) -> Dict[str, Any]:
        batch, nxt = eleanor_state(now)
        with self._lock:
            raw = copy.deepcopy((self._data.get("coda") or {}).get(batch) or [])
            fetched = self._fetched.get("coda", 0.0)
        by_name = {}
        for weapon, element, percent in raw:
            by_name[weapon] = (element, percent)
        rows = []
        for name, cn in CODA_BATCHES.get(batch, ()):
            element, percent = by_name.get(name, ("", 0.0))
            rows.append({"name": cn, "element": ELEMENT_CN.get(element.lower(),
                                                              element),
                         "percent": percent})
        return {"batch": batch, "rows": rows, "fetched": fetched, "next": nxt,
                "complete": all(row["element"] for row in rows)}
