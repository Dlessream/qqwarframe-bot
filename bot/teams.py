"""组队（车队）：QQ 认证入口、队伍存储与任务到场提醒。

QQ 官方机器人给不了「网页小窗」，但按钮可以带 http 链接，QQ 内会用自己的
内置浏览器打开，效果就是群里点一下弹出一个面板。身份认证也走同一条链路：

1. 群里发 ``#组队``，机器人回一张带「打开组队面板」链接按钮的卡片；
   链接里的 token 由 HMAC 签名，绑定了 openid 与群 openid，30 分钟过期、只能换一次。
2. 面板打开时用 token 换一个 HttpOnly 会话 cookie（30 天）。
   拿不到 token 就打不开面板 —— 这就是「必须 QQ 认证」：入口只发给本群成员本人。
3. 车头在面板里选任务开车，其他人点进队，满 4 人自动锁队。
4. 后台线程每 30 秒比对一次任务数据，任务到场（或人齐）时在群里 @ 全队。

队伍数据落 ``data/teams.json``，重启不丢；任务数据实时取世界状态与仲裁轮换表。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import terms

LOGGER = logging.getLogger(__name__)

CATEGORIES: Dict[str, str] = {
    "fissure": "裂缝",
    "arbitration": "仲裁",
    "railjack": "九重天",
    "tridolon": "三傻",
    "bounty": "赏金",
}

CATEGORY_NOTES: Dict[str, str] = {
    "fissure": "实时裂缝；点一条直接开车，或设条件等下一波。",
    "arbitration": "当前这一场与下一场；选下一场会在开场时提醒。",
    "railjack": "九重天虚空风暴；点一条直接开车。",
    "tridolon": "夜灵平原三傻；夜晚开始时提醒整队。",
    "bounty": "各据点当前赏金；选一条直接开车。",
}

# 赏金据点（与 #赏金 卡片的叫法保持一致）
BOUNTY_AREAS: Tuple[Tuple[str, str], ...] = (
    ("Ostrons", "希图斯"),
    ("Solaris United", "福尔图娜"),
    ("Entrati", "坎比昂"),
    ("The Holdfasts", "扎里曼"),
    ("Cavia", "解剖圣所"),
    ("The Hex", "霍瓦尼亚"),
)

BOUNTY_NUMBERS = ["一", "二", "三", "四", "五", "六", "七", "八"]

ACTIVE_STATUS = ("open", "full")
DEFAULT_SIZE = 4
MIN_SIZE = 2
MAX_SIZE = 4
WATCH_TTL = 6 * 3600
TEAM_TTL_AFTER_END = 180
CLOSED_KEEP = 3 * 3600


class TeamError(ValueError):
    """可以直接显示给用户的组队错误。"""


# ---------------------------------------------------------------------- 工具
def _now() -> float:
    return time.time()


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _parse_ts(value: Any) -> float:
    from .worldstate import parse_timestamp

    try:
        return float(parse_timestamp(value))
    except Exception:  # noqa: BLE001 - 时间格式不认识就当没有
        return 0.0


def _clock(ts: float) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError, OverflowError):
        return "—"


def _day_clock(ts: float) -> str:
    try:
        local = time.localtime(float(ts))
    except (TypeError, ValueError, OSError, OverflowError):
        return "—"
    today = time.localtime()
    if (local.tm_year, local.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", local)
    return time.strftime("%m-%d %H:%M", local)


def left_text(ts: float, now: Optional[float] = None) -> str:
    """「剩 23 分」「剩 1 小时 5 分」「已开始」。"""
    now = _now() if now is None else now
    delta = float(ts or 0) - now
    if delta <= 0:
        return "已开始"
    minutes = int(delta // 60)
    if minutes < 60:
        return "剩 %d 分" % max(1, minutes)
    hours, rest = divmod(minutes, 60)
    if rest:
        return "剩 %d 小时 %d 分" % (hours, rest)
    return "剩 %d 小时" % hours


def _cn_number(value: int) -> str:
    if 0 < value <= len(BOUNTY_NUMBERS):
        return BOUNTY_NUMBERS[value - 1]
    return str(value)


def _default_name(openid: str) -> str:
    tail = "".join(ch for ch in str(openid or "") if ch.isalnum())[-4:]
    return "队员%s" % (tail.upper() or "0000")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


# ---------------------------------------------------------------------- 存储
class TeamStore:
    """data/teams.json 的读写；结构 teams / profiles / sessions。"""

    def __init__(self, path: str, logger: Optional[logging.Logger] = None):
        self.path = str(path)
        self.log = logger or LOGGER
        self.lock = threading.RLock()
        self.data: Dict[str, Any] = {"teams": [], "profiles": {}, "sessions": {}}
        self._load()

    # ---------------------------------------------------------------- 文件
    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001 - 坏文件不阻断启动
            self.log.warning("读取组队数据失败（%s）：%s", self.path, exc)
            return
        if not isinstance(raw, dict):
            return
        teams = raw.get("teams")
        profiles = raw.get("profiles")
        sessions = raw.get("sessions")
        self.data = {
            "teams": [item for item in teams if isinstance(item, dict)]
            if isinstance(teams, list) else [],
            "profiles": profiles if isinstance(profiles, dict) else {},
            "sessions": sessions if isinstance(sessions, dict) else {},
        }

    def save(self) -> None:
        with self.lock:
            directory = os.path.dirname(os.path.abspath(self.path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            temp = "%s.tmp" % self.path
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=1)
            os.replace(temp, self.path)

    # ---------------------------------------------------------------- 队伍
    def teams(self, group: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.lock:
            rows = [team for team in self.data["teams"]
                    if isinstance(team, dict)]
            if group is not None:
                rows = [team for team in rows if team.get("group") == group]
            return rows

    def get(self, team_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            for team in self.data["teams"]:
                if isinstance(team, dict) and team.get("id") == team_id:
                    return team
        return None

    def add(self, team: Dict[str, Any]) -> None:
        with self.lock:
            self.data["teams"].append(team)

    def drop(self, team_id: str) -> None:
        with self.lock:
            self.data["teams"] = [
                team for team in self.data["teams"]
                if not (isinstance(team, dict) and team.get("id") == team_id)]

    # ---------------------------------------------------------------- 昵称
    def profile(self, openid: str) -> str:
        with self.lock:
            return _text(self.data["profiles"].get(str(openid))).strip()

    def set_profile(self, openid: str, name: str) -> str:
        name = " ".join(str(name or "").split())[:12]
        if not name:
            raise TeamError("昵称不能为空")
        with self.lock:
            self.data["profiles"][str(openid)] = name
        return name

    def name_of(self, openid: str) -> str:
        return self.profile(openid) or _default_name(openid)

    # ---------------------------------------------------------------- 会话
    def session(self, sid: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            item = self.data["sessions"].get(str(sid))
            return dict(item) if isinstance(item, dict) else None

    def put_session(self, sid: str, group: str, openid: str,
                    days: int = 30) -> None:
        with self.lock:
            self.data["sessions"][str(sid)] = {
                "g": str(group), "u": str(openid),
                "ts": _now(), "exp": _now() + max(1, int(days)) * 86400,
            }

    def prune(self, now: Optional[float] = None) -> None:
        now = _now() if now is None else now
        with self.lock:
            sessions = self.data["sessions"]
            for sid in [key for key, item in sessions.items()
                        if not isinstance(item, dict)
                        or float(item.get("exp") or 0) <= now]:
                sessions.pop(sid, None)
            keep: List[Dict[str, Any]] = []
            for team in self.data["teams"]:
                if not isinstance(team, dict):
                    continue
                closed_at = float(team.get("closed_at") or 0)
                if team.get("status") == "closed" and closed_at:
                    if now - closed_at > CLOSED_KEEP:
                        continue
                keep.append(team)
            self.data["teams"] = keep


# ---------------------------------------------------------------------- 服务
class TeamService:
    """组队业务：认证入口、任务数据、队伍操作与到场检测。"""

    def __init__(self, store: TeamStore, worldstate=None, arbi=None,
                 logger: Optional[logging.Logger] = None,
                 size: int = DEFAULT_SIZE, public_url: str = "",
                 secret: str = "", token_seconds: int = 1800,
                 session_days: int = 30):
        self.store = store
        self.worldstate = worldstate
        self.arbi = arbi
        self.log = logger or LOGGER
        self.size = max(MIN_SIZE, min(MAX_SIZE, int(size or DEFAULT_SIZE)))
        self.public_url = str(public_url or "").rstrip("/")
        self.secret = str(secret or "") or uuid.uuid4().hex
        self.token_seconds = max(60, int(token_seconds or 1800))
        self.session_days = max(1, int(session_days or 30))
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._used_tokens: Dict[str, float] = {}
        self._token_lock = threading.Lock()

    # -------------------------------------------------------------- 认证
    def _sign(self, body: str) -> str:
        return hmac.new(self.secret.encode("utf-8"), body.encode("ascii"),
                        hashlib.sha256).hexdigest()[:40]

    def issue_token(self, group: str, openid: str) -> Dict[str, Any]:
        """给某个群成员发一个一次性登录 token。"""
        group = _text(group)
        openid = _text(openid)
        if not group or not openid:
            raise TeamError("只能在本群内获取组队入口")
        payload = {
            "g": group, "u": openid, "n": uuid.uuid4().hex[:12],
            "e": int(_now() + self.token_seconds),
        }
        body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        token = "%s.%s" % (body, self._sign(body))
        return {"token": token, "expires": payload["e"],
                "seconds": self.token_seconds}

    def entry_url(self, group: str, openid: str) -> str:
        info = self.issue_token(group, openid)
        if not self.public_url:
            return ""
        return "%s/team?t=%s" % (self.public_url, info["token"])

    def redeem(self, token: str) -> Dict[str, Any]:
        """校验 token 并换成会话 id；同一个 token 只能换一次。"""
        raw = _text(token).strip()
        if "." not in raw:
            raise TeamError("入口链接不完整，请在群里重新发 #组队")
        body, signature = raw.rsplit(".", 1)
        if not hmac.compare_digest(signature, self._sign(body)):
            raise TeamError("入口链接签名不对，请在群里重新发 #组队")
        try:
            payload = json.loads(_unb64(body).decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise TeamError("入口链接已损坏，请重新获取")
        expire = float(payload.get("e") or 0)
        now = _now()
        if expire <= now:
            raise TeamError("入口已过期，请在群里重新发 #组队")
        nonce = _text(payload.get("n"))
        with self._token_lock:
            used = self._used_tokens.get(nonce)
            if used and used > now:
                raise TeamError("这个入口已经用过了，请重新发 #组队")
            self._used_tokens[nonce] = expire
            for key in [k for k, ts in self._used_tokens.items() if ts <= now]:
                self._used_tokens.pop(key, None)
        group = _text(payload.get("g"))
        openid = _text(payload.get("u"))
        if not group or not openid:
            raise TeamError("入口缺少群信息，请重新获取")
        sid = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        self.store.put_session(sid, group, openid, self.session_days)
        self.store.save()
        return {"sid": sid, "group": group, "openid": openid}

    def profile(self, sid: str) -> Optional[Dict[str, Any]]:
        item = self.store.session(sid)
        if item is None:
            return None
        if float(item.get("exp") or 0) <= _now():
            return None
        openid = _text(item.get("u"))
        return {
            "openid": openid,
            "group": _text(item.get("g")),
            "name": self.store.name_of(openid),
            "named": bool(self.store.profile(openid)),
        }

    def rename(self, sid: str, name: str) -> Dict[str, Any]:
        item = self.profile(sid)
        if item is None:
            raise TeamError("登录已过期，请重新打开面板")
        self.store.set_profile(item["openid"], name)
        self.store.save()
        result = self.profile(sid)
        return result or item

    # -------------------------------------------------------------- 任务数据
    def _cached(self, key: str, ttl: float, loader):
        now = _now()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = loader()
        self._cache[key] = (now, value)
        return value

    def _fissure_rows(self, storms: bool = False) -> List[Dict[str, Any]]:
        if self.worldstate is None:
            return []

        def load():
            try:
                rows = (self.worldstate.void_storms() if storms
                        else self.worldstate.fissures()) or []
            except Exception as exc:  # noqa: BLE001
                self.log.warning("组队取%s失败：%s",
                                 "虚空风暴" if storms else "裂缝", exc)
                return []
            return [row for row in rows if isinstance(row, dict)]

        return self._cached("storms" if storms else "fissures", 20, load)

    def _fissure_items(self, storms: bool = False) -> List[Dict[str, Any]]:
        now = _now()
        items: List[Dict[str, Any]] = []
        for row in self._fissure_rows(storms):
            is_storm = bool(row.get("isStorm"))
            if storms and not is_storm:
                continue
            if not storms and is_storm:
                continue
            end = _parse_ts(row.get("expiry"))
            if end <= now:
                continue
            node = _text(row.get("node"))
            node = (terms.localize_storm_node(node) if is_storm
                    else terms.localize_node(node))
            mission = terms.localize_mission(_text(row.get("missionType")))
            tier = terms.localize_relic_tier(_text(row.get("tier")))
            hard = bool(row.get("isHard"))
            enemy = (terms.localize_storm_enemy(_text(row.get("enemy")))
                     if is_storm else terms.localize_faction(
                         _text(row.get("enemy"))))
            key = "%s:%s|%s|%s|%s|%d" % (
                "storm" if is_storm else "fissure",
                _text(row.get("id") or node), mission, tier, hard, int(end))
            items.append({
                "key": key,
                "category": "railjack" if is_storm else "fissure",
                "badge": "钢铁" if hard else "普通",
                "mission": mission,
                "tier": tier,
                "node": node,
                "enemy": enemy,
                "hard": hard,
                "start": 0.0,
                "end": end,
                "title": "%s · %s" % (mission, tier) if tier else mission,
                "subtitle": node,
                "meta": left_text(end, now),
                "target": {
                    "label": " · ".join([part for part in (
                        "钢铁" if hard else "普通", tier, mission, node) if part]),
                    "mission": mission, "tier": tier, "hard": hard,
                    "node": node, "key": key, "start": 0.0, "end": end,
                },
            })
        items.sort(key=lambda item: item["end"])
        return items

    def _arbitration_items(self) -> List[Dict[str, Any]]:
        if self.arbi is None:
            return []
        now = _now()

        def load():
            try:
                entries = self.arbi.schedule_entries(now, now + 4 * 3600)
            except Exception as exc:  # noqa: BLE001
                self.log.warning("组队取仲裁失败：%s", exc)
                return []
            return entries

        entries = self._cached("arbitration", 60, load)
        items: List[Dict[str, Any]] = []
        for start, end, node, tier in entries[:2]:
            if end <= now:
                continue
            mission = terms.localize_mission(
                _text(getattr(node, "mission_type", "") or
                      getattr(node, "mission", "")))
            level = _text(getattr(node, "level_text", ""))
            label = " · ".join([part for part in (
                getattr(node, "display_name", ""), mission,
                ("评级 %s" % tier) if tier else "") if part])
            items.append({
                "key": "arbi:%s:%d" % (getattr(node, "key", ""), int(start)),
                "category": "arbitration",
                "badge": "当前" if start <= now + 60 else "下一场",
                "mission": mission,
                "tier": tier,
                "node": getattr(node, "display_name", ""),
                "enemy": terms.localize_faction(
                    _text(getattr(node, "faction", ""))),
                "hard": False,
                "start": float(start),
                "end": float(end),
                "title": getattr(node, "display_name", ""),
                "subtitle": " · ".join([part for part in (
                    mission, level, ("★ %s" % tier) if tier else "") if part]),
                "meta": ("剩 %s" % left_text(end, now).replace("剩 ", "")
                         if start <= now + 60 else
                         "%s 开始" % _day_clock(start)),
                "target": {
                    "label": label,
                    "mission": mission,
                    "node": getattr(node, "display_name", ""),
                    "node_key": getattr(node, "key", ""),
                    "start": float(start), "end": float(end),
                    "key": "arbi:%s:%d" % (getattr(node, "key", ""), int(start)),
                },
            })
        return items

    def _tridolon_item(self) -> Optional[Dict[str, Any]]:
        if self.worldstate is None:
            return None
        now = _now()

        def load():
            try:
                cycle = self.worldstate.fetch("cetusCycle")
            except Exception as exc:  # noqa: BLE001
                self.log.warning("组队取夜灵周期失败：%s", exc)
                return None
            if isinstance(cycle, dict) and isinstance(
                    cycle.get("cetusCycle"), dict):
                cycle = cycle["cetusCycle"]
            return cycle if isinstance(cycle, dict) else None

        cycle = self._cached("cetus", 60, load)
        if not cycle:
            return None
        expiry = _parse_ts(cycle.get("expiry"))
        if expiry <= 0:
            return None
        is_day = cycle.get("isDay")
        if is_day is None:
            is_day = _text(cycle.get("state")).lower() == "day"
        if bool(is_day):
            start, end, meta = expiry, 0.0, "%s 入夜" % _day_clock(expiry)
        else:
            start, end, meta = now, expiry, "夜晚中 · %s" % left_text(expiry, now)
        key = "tridolon:%d" % int(start)
        return {
            "key": key,
            "category": "tridolon",
            "badge": "夜晚" if not is_day else "待入夜",
            "mission": "三傻",
            "tier": "",
            "node": "夜灵平原 · 希图斯",
            "enemy": "灵煞",
            "hard": False,
            "start": float(start),
            "end": float(end),
            "title": "夜灵平原 · 三傻",
            "subtitle": "泰拉棱镜 / 加泽尔 / 沃姆 连战",
            "meta": meta,
            "target": {
                "label": "夜灵平原 · 三傻（%s）" % (
                    "入夜开打" if is_day else "当前夜晚"),
                "mission": "三傻", "node": "夜灵平原",
                "start": float(start), "end": float(end), "key": key,
            },
        }

    def _bounty_items(self) -> List[Dict[str, Any]]:
        if self.worldstate is None:
            return []
        now = _now()

        def load():
            try:
                rows = self.worldstate.fetch("syndicateMissions")
            except Exception as exc:  # noqa: BLE001
                self.log.warning("组队取赏金失败：%s", exc)
                return []
            return rows if isinstance(rows, list) else []

        rows = self._cached("bounties", 60, load)
        by_name = {_text(row.get("syndicate")): row for row in rows
                   if isinstance(row, dict)}
        items: List[Dict[str, Any]] = []
        for syndicate, area in BOUNTY_AREAS:
            row = by_name.get(syndicate)
            if not row:
                continue
            jobs = [job for job in (row.get("jobs") or [])
                    if isinstance(job, dict)]
            for index, job in enumerate(jobs):
                end = _parse_ts(job.get("expiry") or row.get("expiry"))
                if end <= now:
                    continue
                objective = terms.localize_bounty(_text(job.get("type")))
                title = "%s · 赏金%s" % (area, _cn_number(index + 1))
                label = "%s %s（%s）" % (title, objective, left_text(end, now))
                items.append({
                    "key": "bounty:%s:%d:%s" % (syndicate, index, objective),
                    "category": "bounty",
                    "badge": area,
                    "mission": objective,
                    "tier": "",
                    "node": area,
                    "enemy": terms.localize_syndicate(syndicate),
                    "hard": False,
                    "start": 0.0,
                    "end": end,
                    "title": title,
                    "subtitle": objective,
                    "meta": left_text(end, now),
                    "target": {
                        "label": label, "mission": objective, "node": area,
                        "syndicate": syndicate, "start": 0.0, "end": end,
                        "key": "bounty:%s:%d" % (syndicate, index),
                    },
                })
        items.sort(key=lambda item: (item["badge"], item["title"]))
        return items

    def missions(self, category: str) -> Dict[str, Any]:
        category = _text(category)
        if category not in CATEGORIES:
            raise TeamError("未知的组队类型")
        if category == "fissure":
            items = self._fissure_items(False)
        elif category == "railjack":
            items = self._fissure_items(True)
        elif category == "arbitration":
            items = self._arbitration_items()
        elif category == "tridolon":
            single = self._tridolon_item()
            items = [single] if single else []
        else:
            items = self._bounty_items()
        return {
            "category": category,
            "label": CATEGORIES[category],
            "note": CATEGORY_NOTES[category],
            "items": items,
            "updated": _now(),
        }

    # -------------------------------------------------------------- 队伍操作
    def _active_team_of(self, group: str, openid: str
                        ) -> Optional[Dict[str, Any]]:
        for team in self.store.teams(group):
            if team.get("status") not in ACTIVE_STATUS:
                continue
            for member in team.get("members") or []:
                if member.get("id") == openid:
                    return team
        return None

    def _find_item(self, category: str, key: str) -> Optional[Dict[str, Any]]:
        for item in self.missions(category)["items"]:
            if item.get("key") == key:
                return item
        return None

    def create(self, group: str, openid: str,
               payload: Dict[str, Any]) -> Dict[str, Any]:
        category = _text(payload.get("category"))
        if category not in CATEGORIES:
            raise TeamError("先选一个组队类型")
        note = " ".join(_text(payload.get("note")).split())[:40]
        try:
            size = int(payload.get("size") or self.size)
        except (TypeError, ValueError):
            size = self.size
        size = max(MIN_SIZE, min(MAX_SIZE, size))
        now = _now()
        with self.store.lock:
            if self._active_team_of(group, openid) is not None:
                raise TeamError("你已经在本群的一支车队里，先退出再开新的")
            watch = payload.get("watch")
            if isinstance(watch, dict):
                if category not in ("fissure", "railjack"):
                    raise TeamError("只有裂缝 / 九重天 支持等任务")
                mission = _text(watch.get("mission"))
                tier = _text(watch.get("tier"))
                hard = watch.get("hard")
                hard = None if hard is None else bool(hard)
                parts = [("钢铁" if hard else "普通") if hard is not None else "裂缝"]
                if tier:
                    parts.append(tier)
                if mission:
                    parts.append(mission)
                label = "等 " + " · ".join(parts)
                target = {
                    "label": label, "watch": {
                        "mission": mission, "tier": tier, "hard": hard},
                    "mission": mission, "tier": tier,
                    "start": 0.0, "end": now + WATCH_TTL,
                }
                mode = "watch"
            else:
                key = _text(payload.get("mission_key"))
                item = self._find_item(category, key)
                if item is None:
                    raise TeamError("这条任务刚刚结束了，刷新一下再选")
                target = dict(item["target"])
                target.update({
                    "label": item["target"].get("label") or item["title"],
                    "badge": item.get("badge"), "title": item.get("title"),
                    "subtitle": item.get("subtitle"), "end": item.get("end"),
                })
                mode = ("time" if float(item.get("start") or 0) > now + 60
                        else "now")
            end = float(target.get("end") or 0)
            if end and end <= now and mode != "watch":
                raise TeamError("这条任务已经开始了，换一条")
            expire = (end + TEAM_TTL_AFTER_END) if end else (now + WATCH_TTL)
            if mode == "time" and float(target.get("start") or 0) > now:
                expire = max(expire, float(target["start"]) + WATCH_TTL)
            team = {
                "id": "T%s" % uuid.uuid4().hex[:8],
                "group": group,
                "category": category,
                "mode": mode,
                "target": target,
                "size": size,
                "host": openid,
                "members": [{"id": openid,
                             "name": self.store.name_of(openid),
                             "at": now}],
                "status": "open",
                "created": now,
                "expire": expire,
                "note": note,
                "notified": {"fill": 0, "arrive": ""},
                "closed_at": 0,
            }
            if mode == "watch":
                seed = self._watch_match(team, now)
                team["notified"]["arrive"] = _text(seed.get("key")) if seed else ""
            if size <= 1:
                team["status"] = "full"
            self.store.add(team)
            self.store.save()
            return self._view(team, openid)

    def join(self, group: str, openid: str, team_id: str) -> Dict[str, Any]:
        now = _now()
        with self.store.lock:
            team = self.store.get(team_id)
            if team is None or team.get("group") != group:
                raise TeamError("这支车队不在了")
            if team.get("status") not in ACTIVE_STATUS:
                raise TeamError("这支车队已经满了或解散了")
            members = team.get("members") or []
            if any(member.get("id") == openid for member in members):
                return self._view(team, openid)
            if len(members) >= int(team.get("size") or self.size):
                raise TeamError("车队已经满了")
            if float(team.get("expire") or 0) <= now:
                raise TeamError("这支车队已经过期了")
            if self._active_team_of(group, openid) is not None:
                raise TeamError("你已经在本群的一支车队里")
            members.append({"id": openid, "name": self.store.name_of(openid),
                            "at": now})
            team["members"] = members
            if len(members) >= int(team.get("size") or self.size):
                team["status"] = "full"
            self.store.save()
            return self._view(team, openid)

    def leave(self, group: str, openid: str, team_id: str) -> Dict[str, Any]:
        with self.store.lock:
            team = self.store.get(team_id)
            if team is None or team.get("group") != group:
                raise TeamError("这支车队不在了")
            if team.get("host") == openid:
                return self.close(group, openid, team_id)
            members = [member for member in (team.get("members") or [])
                       if member.get("id") != openid]
            team["members"] = members
            if not members:
                team["status"] = "closed"
                team["closed_at"] = _now()
            elif team.get("status") == "full":
                team["status"] = "open"
            self.store.save()
            return self._view(team, openid)

    def close(self, group: str, openid: str, team_id: str) -> Dict[str, Any]:
        with self.store.lock:
            team = self.store.get(team_id)
            if team is None or team.get("group") != group:
                raise TeamError("这支车队不在了")
            if team.get("host") != openid:
                raise TeamError("只有车头能解散")
            team["status"] = "closed"
            team["closed_at"] = _now()
            self.store.save()
            return self._view(team, openid)

    # -------------------------------------------------------------- 视图
    def _view(self, team: Dict[str, Any], openid: str = "") -> Dict[str, Any]:
        now = _now()
        members = []
        for member in team.get("members") or []:
            member_id = _text(member.get("id"))
            members.append({
                # 昵称以 profile 为准：队里会显示改名后的新名字
                "name": (self.store.name_of(member_id) if member_id
                         else "") or _text(member.get("name"))
                        or _default_name(member_id),
                "host": member_id == team.get("host"),
                "you": member_id == openid,
            })
        target = team.get("target") or {}
        start = float(target.get("start") or 0)
        end = float(target.get("end") or 0)
        return {
            "id": team.get("id"),
            "category": team.get("category"),
            "label": CATEGORIES.get(_text(team.get("category")), "组队"),
            "mode": team.get("mode"),
            "title": target.get("title") or target.get("label"),
            "subtitle": target.get("subtitle") or "",
            "badge": target.get("badge") or "",
            "target": target.get("label") or "",
            "note": team.get("note") or "",
            "size": int(team.get("size") or self.size),
            "members": members,
            "count": len(members),
            "full": len(members) >= int(team.get("size") or self.size),
            "host": team.get("host") == openid,
            "joined": any(member.get("id") == openid
                          for member in team.get("members") or []),
            "status": team.get("status"),
            "start": start,
            "end": end,
            "start_text": _day_clock(start) if start > now + 60 else "",
            "left": left_text(end, now) if end else "",
            "created": float(team.get("created") or 0),
        }

    def state(self, group: str, openid: str = "") -> Dict[str, Any]:
        now = _now()
        teams = []
        for team in self.store.teams(group):
            if team.get("status") not in ACTIVE_STATUS:
                continue
            if float(team.get("expire") or 0) <= now:
                continue
            teams.append(self._view(team, openid))
        teams.sort(key=lambda item: (item["status"] != "full",
                                     item["created"]), reverse=False)
        mine = next((item["id"] for item in teams if item["joined"]), "")
        return {
            "group_tail": _text(group)[-6:],
            "teams": teams,
            "mine": mine,
            "size": self.size,
            "categories": [{"key": key, "label": value,
                            "note": CATEGORY_NOTES[key]}
                           for key, value in CATEGORIES.items()],
            "server_time": now,
        }

    # -------------------------------------------------------------- 到场检测
    def _watch_match(self, team: Dict[str, Any], now: Optional[float] = None,
                     skip_key: str = "") -> Optional[Dict[str, Any]]:
        target = team.get("target") or {}
        watch = target.get("watch") or {}
        category = _text(team.get("category"))
        now = _now() if now is None else now
        skip_key = _text(skip_key)
        try:
            items = self.missions(category)["items"]
        except Exception as exc:  # noqa: BLE001
            self.log.warning("组队比对任务失败：%s", exc)
            return None
        for item in items:
            if float(item.get("end") or 0) <= now:
                continue
            if watch.get("mission") and item["mission"] != watch["mission"]:
                continue
            if watch.get("tier") and item["tier"] != watch["tier"]:
                continue
            if watch.get("hard") is not None and bool(item["hard"]) != bool(
                    watch["hard"]):
                continue
            if skip_key and _text(item.get("key")) == skip_key:
                continue
            return item
        return None

    def _arrival(self, team: Dict[str, Any],
                 now: float) -> Optional[Dict[str, Any]]:
        """任务「到了」就返回一条命中记录；now 模式创建时任务已在，不重复提醒。"""
        mode = _text(team.get("mode"))
        target = team.get("target") or {}
        if mode == "watch":
            return self._watch_match(team, now)
        if mode == "time":
            start = float(target.get("start") or 0)
            if start and now >= start - 30:
                return {"key": "time:%d" % int(start),
                        "label": target.get("label") or "任务"}
        return None

    def poll(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """检查一轮，返回需要推送的提醒（群里 @ 全队）。"""
        now = _now() if now is None else now
        out: List[Dict[str, Any]] = []
        changed = False
        with self.store.lock:
            teams = self.store.teams()
            for team in teams:
                if team.get("status") not in ACTIVE_STATUS:
                    continue
                expire = float(team.get("expire") or 0)
                if expire and now > expire:
                    team["status"] = "closed"
                    team["closed_at"] = now
                    changed = True
                    continue
                members = team.get("members") or []
                size = int(team.get("size") or self.size)
                if team.get("status") == "open" and len(members) >= size:
                    team["status"] = "full"
                    changed = True
                notified = team.setdefault("notified", {"fill": 0, "arrive": ""})
                if not notified.get("fill") and len(members) >= size:
                    notified["fill"] = now
                    changed = True
                    out.append({"kind": "fill", "group": team.get("group"),
                                "team": dict(team), "text": self._notify_text(
                                    "fill", team, None, now)})
                    continue
                mode = _text(team.get("mode"))
                hit = None
                if mode == "watch":
                    # 等任务：上一次已经报过的那一波要跳过，等真正新的一波
                    hit = self._watch_match(
                        team, now, skip_key=_text(notified.get("arrive")))
                elif mode == "time" and not notified.get("arrive"):
                    hit = self._arrival(team, now)
                if hit is not None:
                    notified["arrive"] = _text(hit.get("key")) or "hit"
                    changed = True
                    out.append({"kind": "arrive", "group": team.get("group"),
                                "team": dict(team),
                                "text": self._notify_text(
                                    "arrive", team, hit, now)})
            if changed:
                self.store.save()
        return out

    def _notify_text(self, kind: str, team: Dict[str, Any],
                     hit: Optional[Dict[str, Any]], now: float) -> str:
        category = CATEGORIES.get(_text(team.get("category")), "组队")
        target = team.get("target") or {}
        hit_target = (hit or {}).get("target") or {}
        label = _text((hit or {}).get("label") or hit_target.get("label")
                      or target.get("label") or "任务")
        members = team.get("members") or []
        mentions = " ".join("<@%s>" % member.get("id") for member in members
                            if member.get("id"))
        host_id = team.get("host")
        host = (self.store.name_of(host_id) if host_id else "") or "车头"
        count = "%d/%d" % (len(members), int(team.get("size") or self.size))
        lines: List[str] = []
        if kind == "fill":
            lines.append("【车队满员 · %s】%s" % (category, label))
            lines.append("车头 %s ｜ %s" % (host, count))
            if _text(team.get("mode")) == "now":
                end = float(target.get("end") or 0)
                lines.append("任务已在，可以出发%s。" % (
                    "（%s）" % left_text(end, now) if end else ""))
            else:
                start = float(target.get("start") or 0)
                lines.append("到点会再提醒一次%s。" % (
                    "，%s 开始" % _day_clock(start) if start else ""))
        else:
            lines.append("【任务来了 · %s】%s" % (category, label))
            end = float((hit or {}).get("end") or target.get("end") or 0)
            if end:
                lines.append("%s ｜ 车头 %s" % (left_text(end, now), host))
            else:
                lines.append("车头 %s ｜ %s" % (host, count))
            if len(members) < int(team.get("size") or self.size):
                lines.append("还差 %d 人，面板里可以直接喊人。" % (
                    int(team.get("size") or self.size) - len(members)))
        if team.get("note"):
            lines.append("备注：%s" % team.get("note"))
        if mentions:
            lines.append(mentions)
        return "\n".join(lines)

    # -------------------------------------------------------------- 快照
    def stats(self) -> Dict[str, Any]:
        now = _now()
        teams = [team for team in self.store.teams()
                 if team.get("status") in ACTIVE_STATUS
                 and float(team.get("expire") or 0) > now]
        return {"active_teams": len(teams),
                "groups": len({team.get("group") for team in teams})}
