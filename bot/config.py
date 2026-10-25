"""配置读取与校验。

配置采用 JSON，方便直接手改。缺省字段会用内置默认值补齐，
因此升级版本后旧配置文件仍然可用。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from typing import Any, Dict, List

DEFAULTS: Dict[str, Any] = {
    "onebot": {
        "ws_url": "ws://127.0.0.1:3001",
        "access_token": "",
        "reconnect_min_seconds": 1,
        "reconnect_max_seconds": 60,
        "heartbeat_seconds": 25,
        "heartbeat_timeout_seconds": 75,
        "api_timeout_seconds": 15,
    },
    "behavior": {
        "prefixes": ["#", "＃"],
        "allow_private": True,
        "respond_to_at": True,
        "at_sender": True,
        "whitelist_groups": [],
        "blacklist_groups": [],
        "cooldown_seconds": 3,
        "max_replies_per_minute": 20,
        "max_reply_chars": 1200,
        "preload_items": True,
    },
    "warframe_market": {
        "base_url": "https://api.warframe.market",
        "language": "zh-hans",
        "platform": "pc",
        "crossplay": True,
        "timeout_seconds": 15,
        "retries": 2,
        "max_requests_per_second": 3,
        "proxy": "http://127.0.0.1:10809",
        "insecure_tls": False,
        "cache_ttl_seconds": 43200,
        "cache_file": "data/items_cache.json",
        "alias_file": "data/aliases.json",
        "top_orders": 5,
        "price_orders": 8,
        "user_agent": "QQWarframeBot/1.0 (+https://warframe.market)",
    },
    "wiki": {
        "api_url": "https://warframe.huijiwiki.com/api.php",
        "site_url": "https://warframe.huijiwiki.com/wiki/",
        "timeout_seconds": 15,
        "retries": 2,
        "proxy": "http://127.0.0.1:10809",
        "summary_chars": 280,
        "fallback_bwiki": True,
        "bwiki_base": "https://wiki.biligame.com/warframe",
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
    },
    "worldstate": {
        "enabled": True,
        "base_url": "https://api.warframestat.us",
        "language": "zh",
        "timeout_seconds": 12,
        "retries": 1,
        "proxy": "http://127.0.0.1:10809",
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
    },
    "riven": {
        "disposition_override": 1.2,
    },
    "arbi": {
        "enabled": True,
        "base_url": "https://arbi.wf.wiki",
        "timeout_seconds": 20,
        "retries": 1,
        "proxy": "http://127.0.0.1:10809",
        "cache_seconds": 21600,
        "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0.0.0 Safari/537.36"),
    },
    "image": {
        "enabled": True,
        "font": "",
        "font_bold": "",
    },
    "alerts": {
        "enabled": True,
        "file": "data/alerts.json",
        "check_seconds": 300,
        "max_per_chat": 20,
    },
    "auto_update": {
        "enabled": True,
        "interval_seconds": 21600,
        "on_start": True,
        "market": True,
        "riven": True,
        "arbi": True,
        "manual_cooldown_seconds": 600,
    },
    "web": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8080,
        "token": "",
        "public_url": "",
        "log_lines": 200,
        "update": {
            "repo": "Dlessream/qqwarframe-bot",
            "branch": "main",
            "check_seconds": 600,
            "proxy": "",
            "notify_group": "",
            "notify_user": "",
            "manual_command": "docker compose -f docker-compose.full.yml up -d --build",
        },
    },
    "cache": {
        "enabled": True,
        "item_seconds": 3600,
        "orders_seconds": 60,
        "stats_seconds": 900,
        "riven_seconds": 300,
        "worldstate_seconds": 60,
        "wiki_seconds": 600,
    },
    "log": {
        "level": "INFO",
        "file": "logs/bot.log",
    },
}

_REQUIRED_PATHS = (
    ("onebot", "ws_url"),
    ("warframe_market", "base_url"),
    ("wiki", "api_url"),
)

_ENV_OVERRIDES = {
    "QQWF_WS_URL": ("onebot", "ws_url", "str"),
    "QQWF_ACCESS_TOKEN": ("onebot", "access_token", "str"),
    "QQWF_PROXY": ("warframe_market", "proxy", "str"),
    "QQWF_WIKI_PROXY": ("wiki", "proxy", "str"),
    "QQWF_WORLDSTATE_PROXY": ("worldstate", "proxy", "str"),
    "QQWF_PLATFORM": ("warframe_market", "platform", "str"),
    "QQWF_LANGUAGE": ("warframe_market", "language", "str"),
    "QQWF_LOG_LEVEL": ("log", "level", "str"),
    "QQWF_IMAGE": ("image", "enabled", "bool"),
    "QQWF_ALERTS": ("alerts", "enabled", "bool"),
    "QQWF_WORLDSTATE": ("worldstate", "enabled", "bool"),
    "QQWF_AUTO_UPDATE": ("auto_update", "enabled", "bool"),
    "QQWF_CACHE": ("cache", "enabled", "bool"),
    "QQWF_RIVEN_DISPOSITION": ("riven", "disposition_override", "str"),
    "QQWF_ARBI_PROXY": ("arbi", "proxy", "str"),
    "QQWF_FONT": ("image", "font", "str"),
    "QQWF_FONT_BOLD": ("image", "font_bold", "str"),
    "QQWF_WEB": ("web", "enabled", "bool"),
    "QQWF_WEB_HOST": ("web", "host", "str"),
    "QQWF_WEB_PORT": ("web", "port", "str"),
    "QQWF_WEB_TOKEN": ("web", "token", "str"),
}


def _apply_env_overrides(cfg: Dict[str, Any]) -> None:
    """Docker / systemd 部署时可用环境变量覆盖关键配置。"""
    for env_name, (section, key, kind) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value is None or value.strip() == "":
            continue
        target = cfg.setdefault(section, {})
        if kind == "bool":
            target[key] = value.strip().lower() not in ("0", "false", "no", "off")
        else:
            target[key] = value.strip()


class ConfigError(Exception):
    """配置文件不合法。"""


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _as_int(value: Any, name: str, minimum: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ConfigError("配置项 %s 必须是整数，当前为 %r" % (name, value))
    if number < minimum:
        raise ConfigError("配置项 %s 不能小于 %d" % (name, minimum))
    return number


def _as_str_list(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ConfigError("配置项 %s 必须是数组" % name)
    return [str(item) for item in value]


def load_config(path: str = "config.json") -> Dict[str, Any]:
    """读取并规范化配置；文件不存在时抛出 ConfigError。"""
    if not os.path.isfile(path):
        raise ConfigError("找不到配置文件：%s" % os.path.abspath(path))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError("配置文件不是合法 JSON：第 %d 行第 %d 列 %s" % (
            exc.lineno, exc.colno, exc.msg))
    except UnicodeDecodeError:
        raise ConfigError("配置文件编码不是 UTF-8，请用记事本「另存为」并选择 UTF-8")
    except OSError as exc:
        raise ConfigError("无法读取配置文件：%s" % exc)
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层必须是一个 JSON 对象")

    cfg = _deep_merge(DEFAULTS, raw)
    _apply_env_overrides(cfg)

    for section, key in _REQUIRED_PATHS:
        if not str(cfg.get(section, {}).get(key, "")).strip():
            raise ConfigError("配置项 %s.%s 不能为空" % (section, key))

    ws_url = str(cfg["onebot"]["ws_url"]).strip()
    if not (ws_url.startswith("ws://") or ws_url.startswith("wss://")):
        raise ConfigError("onebot.ws_url 必须以 ws:// 或 wss:// 开头，当前为 %r" % ws_url)
    cfg["onebot"]["ws_url"] = ws_url

    cfg["onebot"]["reconnect_min_seconds"] = max(
        1, _as_int(cfg["onebot"]["reconnect_min_seconds"], "onebot.reconnect_min_seconds", 1))
    cfg["onebot"]["reconnect_max_seconds"] = max(
        cfg["onebot"]["reconnect_min_seconds"],
        _as_int(cfg["onebot"]["reconnect_max_seconds"], "onebot.reconnect_max_seconds", 1))
    cfg["onebot"]["heartbeat_seconds"] = max(
        5, _as_int(cfg["onebot"]["heartbeat_seconds"], "onebot.heartbeat_seconds", 5))
    cfg["onebot"]["heartbeat_timeout_seconds"] = max(
        cfg["onebot"]["heartbeat_seconds"] * 2,
        _as_int(cfg["onebot"]["heartbeat_timeout_seconds"], "onebot.heartbeat_timeout_seconds", 10))
    cfg["onebot"]["api_timeout_seconds"] = max(
        3, _as_int(cfg["onebot"]["api_timeout_seconds"], "onebot.api_timeout_seconds", 3))

    prefixes = _as_str_list(cfg["behavior"].get("prefixes"), "behavior.prefixes")
    cfg["behavior"]["prefixes"] = [p for p in prefixes if p] or ["#"]
    cfg["behavior"]["whitelist_groups"] = _as_str_list(
        cfg["behavior"].get("whitelist_groups"), "behavior.whitelist_groups")
    cfg["behavior"]["blacklist_groups"] = _as_str_list(
        cfg["behavior"].get("blacklist_groups"), "behavior.blacklist_groups")
    cfg["behavior"]["cooldown_seconds"] = max(
        0, _as_int(cfg["behavior"]["cooldown_seconds"], "behavior.cooldown_seconds", 0))
    cfg["behavior"]["max_replies_per_minute"] = max(
        1, _as_int(cfg["behavior"]["max_replies_per_minute"],
                   "behavior.max_replies_per_minute", 1))
    cfg["behavior"]["max_reply_chars"] = max(
        200, _as_int(cfg["behavior"]["max_reply_chars"], "behavior.max_reply_chars", 200))

    market = cfg["warframe_market"]
    market["base_url"] = str(market["base_url"]).strip().rstrip("/")
    market["timeout_seconds"] = max(
        3, _as_int(market["timeout_seconds"], "warframe_market.timeout_seconds", 3))
    market["retries"] = max(0, _as_int(market["retries"], "warframe_market.retries", 0))
    market["max_requests_per_second"] = max(
        0, _as_int(market.get("max_requests_per_second", 3),
                   "warframe_market.max_requests_per_second", 0))
    market["cache_ttl_seconds"] = max(
        60, _as_int(market["cache_ttl_seconds"], "warframe_market.cache_ttl_seconds", 60))
    market["top_orders"] = max(1, _as_int(market["top_orders"], "warframe_market.top_orders", 1))
    market["price_orders"] = max(1, _as_int(market["price_orders"], "warframe_market.price_orders", 1))

    wiki = cfg["wiki"]
    wiki["timeout_seconds"] = max(3, _as_int(wiki["timeout_seconds"], "wiki.timeout_seconds", 3))
    wiki["retries"] = max(0, _as_int(wiki["retries"], "wiki.retries", 0))
    wiki["summary_chars"] = max(
        80, _as_int(wiki["summary_chars"], "wiki.summary_chars", 80))

    world = cfg["worldstate"]
    world["timeout_seconds"] = max(
        3, _as_int(world["timeout_seconds"], "worldstate.timeout_seconds", 3))
    world["retries"] = max(0, _as_int(world["retries"], "worldstate.retries", 0))

    alerts = cfg["alerts"]
    alerts["check_seconds"] = max(
        60, _as_int(alerts["check_seconds"], "alerts.check_seconds", 60))
    alerts["max_per_chat"] = max(
        1, _as_int(alerts["max_per_chat"], "alerts.max_per_chat", 1))

    return cfg


def ensure_config(path: str = "config.json", example: str = "config.example.json") -> bool:
    """配置文件不存在时从示例复制一份，返回是否新建。"""
    if os.path.isfile(path):
        return False
    if not os.path.isfile(example):
        raise ConfigError("缺少 %s，无法生成配置文件" % example)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    shutil.copyfile(example, path)
    return True


def redact(value: str) -> str:
    """日志里隐藏 token。"""
    if not value:
        return "(未设置)"
    if len(value) <= 6:
        return "***"
    return value[:3] + "***" + value[-2:]
