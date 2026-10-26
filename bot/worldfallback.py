"""官方世界状态兜底数据源。

`api.warframestat.us` 对数据中心 IP 会返回 Cloudflare 403，节点不可用时
世界状态（奸商 / 突击 / 平原 / 赏金 / 科研 …）就全废。这里改成用两个
直连可用的数据源拼出与 warframestat 完全相同的结构，上层渲染不用改：

  * https://api.warframe.com/cdn/worldState.php   官方 CDN
  * https://oracle.browse.wf/weekly               周常（深层 / 时光科研）

周期类数据官方接口不提供，按 WFCD `warframe-worldstate-parser` 的算法本地算：

  * 希图斯：由 CetusSyndicate 赏金到期时间反推（白天 100 分钟 / 夜晚 50 分钟）
  * 英择谛：与希图斯同步（法斯 / 沃姆）
  * 福尔图娜：固定 1600 秒循环（温暖 400 秒 + 寒冷 1200 秒）

官方接口没有的字段（钢铁侵袭 / 午夜电波 / 无尽回廊 / 执刑官 / 新闻 / 活动）
不在此伪造，取用时由调用方给出明确提示。
"""

from __future__ import annotations

import calendar
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import terms

LOGGER = logging.getLogger("qqwarframe.worldfallback")

DEFAULT_OFFICIAL_URL = "https://api.warframe.com/cdn/worldState.php"
DEFAULT_ORACLE_URL = "https://oracle.browse.wf"

OFFICIAL_MISSIONS = {
    "MT_EXTERMINATION": "Extermination", "MT_DEFENSE": "Defense",
    "MT_SURVIVAL": "Survival", "MT_RESCUE": "Rescue",
    "MT_TERRITORY": "Interception", "MT_ARTIFACT": "Excavation",
    "MT_SABOTAGE": "Sabotage", "MT_CAPTURE": "Capture",
    "MT_SPY": "Spy", "MT_MOBILE_DEFENSE": "Mobile Defense",
    "MT_ASSASSINATION": "Assassination", "MT_EXCAVATE": "Excavation",
    "MT_HIJACK": "Hijack", "MT_INTEL": "Spy",
    "MT_ALCHEMY": "Alchemy", "MT_VOID_CASCADE": "Void Cascade",
    "MT_ASCENSION": "Ascension", "MT_DISRUPTION": "Disruption",
    "MT_VAULT": "Vault", "MT_HIVE": "Hive", "MT_DEFECTION": "Defection",
    "MT_PURSUIT": "Pursuit", "MT_ORPHIX": "Orphix",
}

VOID_TIERS = {
    "VoidT1": "Lith", "VoidT2": "Meso", "VoidT3": "Neo",
    "VoidT4": "Axi", "VoidT5": "Requiem", "VoidT6": "Omnia",
}

# 虚空风暴按遗物等级排序（与 present.storms_reply 的 tierNum 对应）
VOID_TIER_ORDER = {
    "Lith": 1, "Meso": 2, "Neo": 3, "Axi": 4, "Requiem": 5, "Omnia": 6,
}

FACTIONS = {
    "FC_GRINEER": "Grineer", "FC_CORPUS": "Corpus",
    "FC_INFESTATION": "Infested", "FC_OROKIN": "Corrupted",
    "FC_SENTIENT": "Sentient", "FC_NARMER": "Narmer",
    "FC_MURMUR": "Murmur", "FC_SCALDRA": "Scaldra",
    "FC_TECHROT": "Techrot", "FC_CORRUPTED": "Corrupted",
}

SORTIE_BOSSES = {
    "SORTIE_BOSS_RUK": "Sargas Ruk", "SORTIE_BOSS_KRIL": "Lech Kril",
    "SORTIE_BOSS_VAY_HEK": "Vay Hek", "SORTIE_BOSS_TYL": "Tyl Regor",
    "SORTIE_BOSS_KELA": "Kela De Thaym", "SORTIE_BOSS_ALAD": "Alad V",
    "SORTIE_BOSS_AMBULAS": "Ambulas", "SORTIE_BOSS_PHORID": "Phorid",
    "SORTIE_BOSS_LEPHANTIS": "Lephantis", "SORTIE_BOSS_JORDAS": "Jordas Golem",
    "SORTIE_BOSS_RAPTOR": "Raptor", "SORTIE_BOSS_HYENA": "Hyena",
}

SORTIE_MODIFIERS = {
    "SORTIE_MODIFIER_ARMOR": "强化敌人护甲",
    "SORTIE_MODIFIER_SHIELDS": "强化敌人护盾",
    "SORTIE_MODIFIER_HEALTH": "强化敌人生命",
    "SORTIE_MODIFIER_IMPACT": "物理强化：冲击",
    "SORTIE_MODIFIER_PUNCTURE": "物理强化：穿刺",
    "SORTIE_MODIFIER_SLASH": "物理强化：切割",
    "SORTIE_MODIFIER_FIRE": "元素强化：火焰",
    "SORTIE_MODIFIER_FREEZE": "元素强化：冰冻",
    "SORTIE_MODIFIER_ELECTRICITY": "元素强化：电击",
    "SORTIE_MODIFIER_TOXIN": "元素强化：毒素",
    "SORTIE_MODIFIER_GAS": "元素强化：毒气",
    "SORTIE_MODIFIER_MAGNETIC": "元素强化：磁力",
    "SORTIE_MODIFIER_RADIATION": "元素强化：辐射",
    "SORTIE_MODIFIER_VIRAL": "元素强化：病毒",
    "SORTIE_MODIFIER_CORROSIVE": "元素强化：腐蚀",
    "SORTIE_MODIFIER_BLAST": "元素强化：爆炸",
    "SORTIE_MODIFIER_HAZARD_RADIATION": "环境：辐射危害",
    "SORTIE_MODIFIER_HAZARD_FOG": "环境：浓雾",
    "SORTIE_MODIFIER_HAZARD_FIRE": "环境：火灾",
    "SORTIE_MODIFIER_HAZARD_ICE": "环境：冰冻",
    "SORTIE_MODIFIER_HAZARD_COLD": "环境：极寒",
    "SORTIE_MODIFIER_HAZARD_MAGNETIC": "环境：磁暴",
    "SORTIE_MODIFIER_LOW_ENERGY": "能量上限降低",
    "SORTIE_MODIFIER_EXIMUS": "精英敌人强化",
    "SORTIE_MODIFIER_NO_SHIELDS": "敌人无护盾",
}

# 官方 Node 字段里少数中继站 ID 不在 sol_nodes.json 内。
RELAY_NODES = {
    "MercuryHUB": "Larunda Relay (Mercury)",
    "EarthHUB": "Strata Relay (Earth)",
    "SaturnHUB": "Kronia Relay (Saturn)",
    "PlutoHUB": "Orcus Relay (Pluto)",
    "VenusHUB": "Vesper Relay (Venus)",
    "ErisHUB": "Kuiper Relay (Eris)",
}

# 官方 Syndicate Tag → warframestat 的仙族名 + 赏金标题
BOUNTY_SYNDICATES = {
    "CetusSyndicate": ("Ostrons", "希图斯赏金"),
    "SolarisSyndicate": ("Solaris United", "福尔图娜赏金"),
    "EntratiSyndicate": ("Entrati", "英择谛赏金"),
    "EntratiLabSyndicate": ("Cavia", "解剖圣所赏金"),
    "HexSyndicate": ("The Hex", "霍瓦尼亚赏金"),
    "ZarimanSyndicate": ("The Holdfasts", "扎里曼赏金"),
    "NecraloidSyndicate": ("Necraloid", "死灵锁赏金"),
}

# 赏金任务类型：官方 jobType 路径末段关键字 → 中文
BOUNTY_KINDS = [
    ("exterminat", "歼灭"), ("bountyext", "歼灭"), ("surviv", "生存"),
    ("excav", "挖掘"), ("assassin", "刺杀"), ("sabotage", "破坏"),
    ("bountysab", "破坏"), ("bountycap", "捕获"), ("capture", "捕获"),
    ("spy", "间谍"), ("defen", "防御"), ("rescue", "救援"),
    ("theft", "盗取"), ("resource", "采集"), ("cache", "寻回缓存"),
    ("keypieces", "钥匙碎片"), ("lib", "解放"),
]

CETUS_DAY_SECONDS = 6000       # 白天 100 分钟
CETUS_NIGHT_SECONDS = 3000     # 夜晚 50 分钟
CETUS_CYCLE_SECONDS = CETUS_DAY_SECONDS + CETUS_NIGHT_SECONDS

VALLIS_LOOP_MS = 1600000
VALLIS_WARM_MS = 400000
# 2026-02-04T19:46:48Z（WFCD 解析器里的当前锚点）
VALLIS_START_MS = int(calendar.timegm((2026, 2, 4, 19, 46, 48, 0, 0, 0)) * 1000)


class FallbackError(Exception):
    """官方兜底数据也不可用。"""


# ---------------------------------------------------------------------- 工具
def _ms(value: Any) -> float:
    """官方时间字段：{"$date":{"$numberLong":"1793018506448"}} 等 → 毫秒。"""
    raw = value
    if isinstance(raw, dict):
        raw = raw.get("$date", raw)
        if isinstance(raw, dict):
            raw = (raw.get("$numberLong") or raw.get("$numberDouble")
                   or raw.get("$numberInt") or 0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _iso(seconds: float) -> str:
    if not seconds:
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _delta(seconds: float) -> str:
    """3700 → "1h 1m 40s"（与 warframestat 的 timeLeft 同格式）。"""
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append("%dd" % days)
    if hours:
        parts.append("%dh" % hours)
    if minutes:
        parts.append("%dm" % minutes)
    parts.append("%ds" % seconds)
    return " ".join(parts)


def _humanize(text: Any) -> str:
    """HostileSecurity / DualDefense → "Hostile Security" / "Dual Defense"。"""
    value = str(text or "").strip()
    if not value:
        return ""
    value = value.replace("_", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _item_name(path: Any) -> str:
    """官方资源路径末段 → 可读名：.../SnipetronVandalStock → Snipetron Vandal Stock。"""
    tail = str(path or "").split("/")[-1]
    return _humanize(tail)


def _node_name(nodes: Dict[str, Dict[str, Any]], key: str) -> str:
    row = nodes.get(key) or {}
    name = str(row.get("name") or "").strip()
    if name:
        return name
    return RELAY_NODES.get(key, key)


def _faction(code: Any) -> str:
    return FACTIONS.get(str(code or "").upper(), "")


def _mission(code: Any) -> str:
    raw = str(code or "")
    return OFFICIAL_MISSIONS.get(raw, _humanize(raw.replace("MT_", "")))


def _bounty_kind(job_type: Any) -> str:
    tail = str(job_type or "").split("/")[-1].lower()
    for token, label in BOUNTY_KINDS:
        if token in tail:
            return label
    return "赏金"


_challenge_title_cache: Dict[str, str] = {}


def _challenge_title(path: Any) -> str:
    """把 /Lotus/Types/Challenges/Seasons/Daily/SeasonDailyTwoForOne 还原成可读标题。"""
    raw = str(path or "").strip()
    cached = _challenge_title_cache.get(raw)
    if cached is not None:
        return cached
    tail = raw.split("/")[-1] or "挑战"
    for prefix in ("SeasonNightwave", "SeasonElite", "SeasonDaily",
                   "SeasonWeekly", "Season"):
        if tail.startswith(prefix):
            tail = tail[len(prefix):]
            break
    title = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", tail).strip() or tail or "挑战"
    _challenge_title_cache[raw] = title
    return title


def _bounty_label(job: Dict[str, Any]) -> str:
    """不同档位的赏金任务类型相同，补上敌人等级以便区分。"""
    kind = _bounty_kind(job.get("jobType"))
    low = int(job.get("minEnemyLevel") or 0)
    high = int(job.get("maxEnemyLevel") or 0)
    if not high:
        return kind
    if low and low != high:
        return "%s %d-%d" % (kind, low, high)
    return "%s %d" % (kind, high)


def _reward(reward: Any) -> Dict[str, Any]:
    """官方 missionReward / AttackerReward → warframestat 的 reward 结构。"""
    data = reward if isinstance(reward, dict) else {}
    items = []
    for row in data.get("countedItems") or []:
        if not isinstance(row, dict):
            continue
        items.append({
            "type": _item_name(row.get("ItemType")),
            "count": int(row.get("ItemCount") or 1),
        })
    return {"items": [], "countedItems": items,
            "credits": int(data.get("credits") or 0)}


# ---------------------------------------------------------------------- 周期
def cetus_cycle(now: float, bounties_end: float) -> Optional[Dict[str, Any]]:
    """按 CetusSyndicate 赏金到期时间反推希图斯昼夜。"""
    if bounties_end <= 0:
        return None
    left = int(bounties_end) - now
    if left < 0:
        # 表过期时按 150 分钟一个循环回卷，避免显示「已结束」
        left %= CETUS_CYCLE_SECONDS
    is_day = left > CETUS_NIGHT_SECONDS
    remaining = left - CETUS_NIGHT_SECONDS if is_day else left
    expiry = int(round((now + remaining) / 60.0)) * 60
    start = expiry - (CETUS_DAY_SECONDS if is_day else CETUS_NIGHT_SECONDS)
    return {
        "state": "day" if is_day else "night",
        "isDay": is_day,
        "timeLeft": _delta(remaining),
        "expiry": _iso(expiry),
        "activation": _iso(start),
    }


def vallis_cycle(now: float) -> Dict[str, Any]:
    """福尔图娜：1600 秒固定循环，温暖 400 秒 + 寒冷 1200 秒。"""
    now_ms = now * 1000.0
    since = (now_ms - VALLIS_START_MS) % VALLIS_LOOP_MS
    to_full = VALLIS_LOOP_MS - since
    cold_ms = VALLIS_LOOP_MS - VALLIS_WARM_MS
    state = "warm" if to_full > cold_ms else "cold"
    to_minor = to_full if to_full < cold_ms else to_full - cold_ms
    return {
        "state": state,
        "isWarm": state == "warm",
        "timeLeft": _delta(to_minor / 1000.0),
        "expiry": _iso((now_ms + to_minor) / 1000.0),
    }


def cambion_cycle(cetus: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """英择谛与希图斯同步，白天=法斯、夜晚=沃姆。"""
    if not cetus:
        return None
    return {
        "state": "fass" if cetus.get("isDay") else "vome",
        "timeLeft": cetus.get("timeLeft", ""),
        "expiry": cetus.get("expiry", ""),
        "activation": cetus.get("activation", ""),
    }


# ---------------------------------------------------------------------- 裂缝
def official_fissures(data: Dict[str, Any],
                      nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ActiveMissions → warframestat fissures 结构（含钢铁裂缝）。"""
    rows = data.get("ActiveMissions") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    result = []
    now = time.time()
    for row in rows:
        if not isinstance(row, dict):
            continue
        end = _ms(row.get("Expiry")) / 1000.0
        if end <= now:
            continue
        node_id = str(row.get("Node") or "")
        node = nodes.get(node_id) or {}
        node_name = terms.localize_node(str(node.get("name") or _node_name(nodes, node_id)))
        result.append({
            "id": node_id,
            "node": node_name,
            "nodeKey": node_name,
            "missionType": _mission(row.get("MissionType")),
            "enemy": terms.localize_faction(str(node.get("enemy") or "")),
            "tier": VOID_TIERS.get(str(row.get("Modifier") or ""), "—"),
            "expiry": _iso(end),
            "isHard": bool(row.get("Hard")),
            "isStorm": False,
            "source": "official",
        })
    result.sort(key=lambda item: (item["isHard"], item["expiry"]))
    return result


def official_void_storms(data: Dict[str, Any],
                         nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """VoidStorms → warframestat storms 结构（九重天风暴裂缝）。

    官方条目只有节点和光体等级，任务类型取自节点表（Skirmish/Volatile/Orphix）。
    """
    rows = data.get("VoidStorms") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    result = []
    now = time.time()
    for row in rows:
        if not isinstance(row, dict):
            continue
        end = _ms(row.get("Expiry")) / 1000.0
        if end <= now:
            continue
        node_id = str(row.get("Node") or "")
        node = nodes.get(node_id) or {}
        node_name = str(node.get("name") or _node_name(nodes, node_id))
        tier = VOID_TIERS.get(str(row.get("ActiveMissionTier") or ""), "—")
        result.append({
            "id": str((row.get("_id") or {}).get("$oid") or node_id),
            "node": node_name,
            "nodeKey": node_name,
            "missionType": str(node.get("type") or "Skirmish"),
            # 保留英文阵营名，交给 present 做「G系 / C系」风格本地化
            "enemy": str(node.get("enemy") or ""),
            "tier": tier,
            "tierNum": VOID_TIER_ORDER.get(tier, 9),
            "expiry": _iso(end),
            "isHard": False,
            "isStorm": True,
            "source": "official",
        })
    result.sort(key=lambda item: (item["tierNum"], item["expiry"]))
    return result


# ---------------------------------------------------------------------- 主体
class OfficialWorldState:
    """把官方 CDN 数据整理成 warframestat 的文档结构（带短缓存）。"""

    def __init__(self, http, nodes: Optional[Dict[str, Dict[str, Any]]] = None,
                 oracle_http=None, official_url: str = DEFAULT_OFFICIAL_URL,
                 oracle_url: str = DEFAULT_ORACLE_URL, ttl: int = 30,
                 logger: Optional[logging.Logger] = None) -> None:
        self.http = http
        self.oracle_http = oracle_http if oracle_http is not None else http
        self.official_url = official_url
        self.oracle_url = str(oracle_url or DEFAULT_ORACLE_URL).rstrip("/")
        self.nodes = nodes or {}
        self.ttl = max(5, int(ttl))
        self.log = logger or LOGGER
        self._lock = threading.Lock()
        self._state: Optional[Dict[str, Any]] = None
        self._at = 0.0
        self._oracle_at = 0.0
        self._weekly: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ 缓存
    def state(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            if (not force and self._state is not None
                    and time.time() - self._at < self.ttl):
                return self._state
        try:
            data = self.http.get_json(self.official_url)
        except Exception as exc:
            raise FallbackError("官方世界状态接口不可用：%s" % exc)
        if not isinstance(data, dict):
            raise FallbackError("官方世界状态接口返回格式异常。")
        built = self._build(data, time.time())
        with self._lock:
            self._state = built
            self._at = time.time()
        return built

    def _weekly_data(self) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._weekly is not None and now - self._oracle_at < \
                    (600 if self._weekly else 60):
                return self._weekly
        data: Any = {}
        for _ in range(2):
            try:
                data = self.oracle_http.get_json(self.oracle_url + "/weekly")
                break
            except Exception as exc:
                self.log.warning("科研周常数据不可用：%s", exc)
                data = {}
        if not isinstance(data, dict):
            data = {}
        with self._lock:
            self._weekly = data
            self._oracle_at = time.time()
        return data

    # ------------------------------------------------------------------ 构建
    def _build(self, data: Dict[str, Any], now: float) -> Dict[str, Any]:
        nodes = self.nodes
        syndicates = data.get("SyndicateMissions") or []
        cetus = self._cetus(syndicates, now)
        state: Dict[str, Any] = {
            "timestamp": _iso(now),
            "source": "official",
            "cetusCycle": cetus,
            "vallisCycle": vallis_cycle(now),
            "cambionCycle": cambion_cycle(cetus),
            "voidTrader": self._trader(data, nodes),
            "sortie": self._sortie(data, nodes),
            "alerts": self._alerts(data, nodes),
            "invasions": self._invasions(data, nodes),
            "syndicateMissions": self._bounties(syndicates),
            "fissures": official_fissures(data, nodes),
            "voidStorms": official_void_storms(data, nodes),
            "nightwave": self._nightwave(data),
            "archimedeas": self._archimedeas(),
            # 官方接口没有仲裁字段；留空让上层按 arbi.wf.wiki 轮换兜底
            "arbitration": {
                "node": "", "nodeKey": "SolNode000", "type": "",
                "enemy": "", "expiry": "", "expired": True,
            },
        }
        # 官方接口没有的模块：显式置 None，总览里显示「暂不可用」而不是 0
        for key in ("steelPath", "duviriCycle", "archonHunt", "news",
                    "events", "dailyDeals"):
            state[key] = None
        return state

    def _nightwave(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """SeasonInfo → warframestat nightwave 结构。

        官方只给挑战内部路径（如 SeasonDailyTwoForOne），这里还原成可读标题；
        声望数值官方不给，保留 0，中转数据可用时由上层覆盖。
        """
        info = data.get("SeasonInfo") if isinstance(data, dict) else None
        if not isinstance(info, dict) or not info.get("Season"):
            return None
        now = time.time()
        challenges = []
        for row in info.get("ActiveChallenges") or []:
            if not isinstance(row, dict):
                continue
            end = _ms(row.get("Expiry")) / 1000.0
            if end and end <= now:
                continue
            path = str(row.get("Challenge") or "")
            challenges.append({
                "id": str((row.get("_id") or {}).get("$oid") or ""),
                "title": _challenge_title(path),
                "desc": "",
                "reputation": int(row.get("Reputation") or 0),
                "isDaily": bool(row.get("Daily")),
                "isElite": "Elite" in path,
                "expiry": _iso(end),
            })
        challenges.sort(key=lambda item: (not item["isDaily"], item["expiry"]))
        return {
            "season": int(info.get("Season") or 0),
            "tag": str(info.get("AffiliationTag") or ""),
            "phase": info.get("Phase"),
            "expiry": _iso(_ms(info.get("Expiry")) / 1000.0),
            "activeChallenges": challenges,
        }

    def _cetus(self, syndicates: List[Any], now: float) -> Optional[Dict[str, Any]]:
        for row in syndicates:
            if not isinstance(row, dict):
                continue
            if str(row.get("Tag") or "") != "CetusSyndicate":
                continue
            end = _ms(row.get("Expiry")) / 1000.0
            return cetus_cycle(now, end)
        return None

    def _trader(self, data: Dict[str, Any],
                nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        rows = data.get("VoidTraders") or []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        start = _ms(row.get("Activation")) / 1000.0
        end = _ms(row.get("Expiry")) / 1000.0
        character = str(row.get("Character") or "")
        if not character or "Baro" in character:
            character = "Baro Ki'Teer"
        return {
            "active": bool(start and end and start <= time.time() <= end),
            "character": character,
            "location": _node_name(nodes, str(row.get("Node") or "")),
            "activation": _iso(start),
            "expiry": _iso(end),
            # 官方接口不含库存，奸商卡片会显示「未到访 / 无库存」
            "inventory": [],
        }

    def _sortie(self, data: Dict[str, Any],
                nodes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        rows = data.get("Sorties") or []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
        variants = []
        faction = ""
        for item in row.get("Variants") or []:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node") or "")
            node = nodes.get(node_id) or {}
            if not faction:
                faction = str(node.get("enemy") or "")
            modifier = str(item.get("modifierType") or "")
            variants.append({
                "missionType": _mission(item.get("missionType")),
                "node": _node_name(nodes, node_id),
                "modifier": modifier,
                "modifierDescription": SORTIE_MODIFIERS.get(
                    modifier, _humanize(modifier.replace("SORTIE_MODIFIER_", ""))),
            })
        boss_key = str(row.get("Boss") or "")
        return {
            "boss": SORTIE_BOSSES.get(
                boss_key, _humanize(boss_key.replace("SORTIE_BOSS_", ""))),
            "faction": faction,
            "expiry": _iso(_ms(row.get("Expiry")) / 1000.0),
            "activation": _iso(_ms(row.get("Activation")) / 1000.0),
            "variants": variants,
        }

    def _alerts(self, data: Dict[str, Any],
                nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for row in data.get("Alerts") or []:
            if not isinstance(row, dict):
                continue
            info = row.get("MissionInfo") or {}
            reward = info.get("missionReward") or {}
            result.append({
                "id": str((row.get("_id") or {}).get("$oid") or ""),
                "activation": _iso(_ms(row.get("Activation")) / 1000.0),
                "expiry": _iso(_ms(row.get("Expiry")) / 1000.0),
                "rewardTypes": [],
                "mission": {
                    "node": _node_name(nodes, str(info.get("location") or "")),
                    "type": _mission(info.get("missionType")),
                    "faction": _faction(info.get("faction")),
                    "reward": _reward(reward),
                },
            })
        return result

    def _invasions(self, data: Dict[str, Any],
                   nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for row in data.get("Invasions") or []:
            if not isinstance(row, dict):
                continue
            if row.get("Completed"):
                continue
            goal = float(row.get("Goal") or 0) or 1.0
            count = float(row.get("Count") or 0)
            result.append({
                "id": str((row.get("_id") or {}).get("$oid") or ""),
                "node": _node_name(nodes, str(row.get("Node") or "")),
                "desc": "",
                "completed": bool(row.get("Completed")),
                "completion": round(min(100.0, abs(count) / goal * 100.0), 1),
                "attacker": {
                    "faction": _faction(row.get("Faction")),
                    "reward": _reward(row.get("AttackerReward") or {}),
                },
                "defender": {
                    "faction": _faction(row.get("DefenderFaction")),
                    "reward": _reward(row.get("DefenderReward") or {}),
                },
            })
        return result

    def _bounties(self, syndicates: List[Any]) -> List[Dict[str, Any]]:
        # 官方数据里同一 Tag 可能重复下发（不同平台条目），按 Tag 去重，
        # 保留任务最多的那一条。
        picked: Dict[str, Dict[str, Any]] = {}
        for row in syndicates:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("Tag") or "")
            if tag not in BOUNTY_SYNDICATES:
                continue
            jobs = row.get("Jobs") or []
            current = picked.get(tag)
            if current is None or len(jobs) > len(current.get("Jobs") or []):
                picked[tag] = row
        result = []
        for tag, row in picked.items():
            name = BOUNTY_SYNDICATES[tag][0]
            expiry = _iso(_ms(row.get("Expiry")) / 1000.0)
            jobs = []
            for job in row.get("Jobs") or []:
                if not isinstance(job, dict):
                    continue
                jobs.append({
                    "type": _bounty_label(job),
                    "expiry": expiry,
                    "enemyLevels": [int(job.get("minEnemyLevel") or 0),
                                    int(job.get("maxEnemyLevel") or 0)],
                    "rewardPool": [],
                    "rewardPoolDrops": [],
                })
            result.append({
                "id": tag,
                "syndicate": name,
                "syndicateKey": tag,
                "expiry": expiry,
                "activation": _iso(_ms(row.get("Activation")) / 1000.0),
                "jobs": jobs,
            })
        return result

    def _archimedeas(self) -> List[Dict[str, Any]]:
        weekly = self._weekly_data()
        if not weekly:
            return []
        expiry = _iso(float(weekly.get("expiry") or 0))
        result = []
        for key, type_key, variables, kind in (
                ("labConquestMissions", "C T_ L A B",
                 "labConquestFrameVariables", "LabConquest"),
                ("hexConquestMissions", "C T_ H E X",
                 "hexConquestFrameVariables", "HexConquest")):
            missions = weekly.get(key) or []
            if not isinstance(missions, list) or not missions:
                continue
            rows = []
            for mission in missions:
                if not isinstance(mission, dict):
                    continue
                variant = str(mission.get("variant") or "")
                conditions = [str(value) for value in
                              mission.get("conditions") or []]
                rows.append({
                    "missionType": _humanize(mission.get("type")),
                    "missionTypeKey": str(mission.get("type") or ""),
                    "faction": "",
                    # 内部代号（HostileSecurity 之类）与游戏内名字并不一致，
                    # 展示层统一用它查官方中文名与说明。
                    "variantKey": variant,
                    "deviation": {"name": _humanize(variant)},
                    "conditionKeys": conditions,
                    "risks": [{"name": _humanize(value)} for value in conditions],
                })
            personal = [str(value) for value in weekly.get(variables) or []]
            result.append({
                "typeKey": type_key,
                "kind": kind,
                "expiry": expiry,
                "missions": rows,
                "personalKeys": personal,
                "personalModifiers": [{"name": _humanize(value)}
                                      for value in personal],
            })
        return result
