"""应用装配：过滤消息、限流、路由、图片/文字发送、降价提醒。"""

from __future__ import annotations

import base64
import collections
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

from . import __version__, imagecard, ocr, present
from .alerts import AlertStore
from .aliases import AliasStore
from .arbi import ArbiClient
from .commands import CommandRouter, Context, strip_cq
from .market import Item, MarketClient, MarketError
from .net import HttpClient, HttpError
from .onebot import OneBotClient, OneBotError, escape_cq
from .replies import Reply
from .render import chunk_text
from .riven import RivenClient
from .update import UpdateChecker
from .updater import DataUpdater
from .web import AdminServer
from .wiki import WikiClient, WikiError
from .worldstate import WorldStateClient, WorldStateError

LOGGER = logging.getLogger("qqwarframe.app")


class BotApp:
    def __init__(self, config: Dict[str, Any], logger: Optional[logging.Logger] = None,
                 config_path: Optional[str] = None):
        self.cfg = config
        self.config_path = config_path
        self.log = logger or LOGGER
        self.behavior = config.get("behavior", {})
        self.allow_bare = not bool(self.behavior.get("require_prefix", False))
        self.prefixes: List[str] = list(self.behavior.get("prefixes") or ["#"])
        self.self_id = ""
        self._stop = threading.Event()

        image_cfg = config.get("image", {})
        self.image_enabled = bool(image_cfg.get("enabled", True))
        if self.image_enabled:
            imagecard.configure(str(image_cfg.get("font", "")),
                                str(image_cfg.get("font_bold", "")))
            if not imagecard.available():
                self.image_enabled = False
                self.log.warning(
                    "Pillow 或中文字体不可用，图片将回退为文字。"
                    "Linux 可安装 fonts-noto-cjk，或在 image.font / QQWF_FONT 指定字体路径")

        self.market = MarketClient(config.get("warframe_market", {}), logger=self.log,
                                   cache_config=config.get("cache", {}))
        self.alias_store = AliasStore(self.market.alias_file)
        self.alias_store.ensure_frame_defaults()
        self.wiki = WikiClient(config.get("wiki", {}), logger=self.log,
                               cache_config=config.get("cache", {}))
        self.riven = RivenClient(
            self.market, self.log,
            cache_config=config.get("cache", {}),
            disposition_override=config.get("riven", {}).get("disposition_override"))

        arbi_cfg = config.get("arbi", {})
        self.arbi = (ArbiClient(arbi_cfg, logger=self.log)
                     if arbi_cfg.get("enabled", True) else None)

        world_cfg = config.get("worldstate", {})
        self.worldstate = (WorldStateClient(world_cfg, cache_config=config.get("cache", {}))
                           if world_cfg.get("enabled", True) else None)

        caches = [self.market.cache, self.wiki.cache, self.riven.cache]
        if self.worldstate is not None:
            caches.append(self.worldstate.cache)
        self.updater = DataUpdater(
            self.market, self.riven,
            config.get("auto_update", {}), logger=self.log,
            cache_config=config.get("cache", {}), caches=caches,
            arbi=self.arbi)

        alert_cfg = config.get("alerts", {})
        self.alerts: Optional[AlertStore] = None
        if alert_cfg.get("enabled", True):
            self.alerts = AlertStore(str(alert_cfg.get("file", "data/alerts.json")),
                                     int(alert_cfg.get("max_per_chat", 20)))
        self.alert_interval = max(60, int(alert_cfg.get("check_seconds", 300)))

        self.router = CommandRouter(
            self.market, self.wiki, config, logger=self.log,
            riven=self.riven, worldstate=self.worldstate, alerts=self.alerts,
            arbi=self.arbi, updater=self.updater)
        self.onebot = OneBotClient(config.get("onebot", {}), self.on_event, logger=self.log)
        self.image_http = HttpClient(timeout=15, retries=0, proxy="none",
                                     logger=self.log)
        self.image_proxy_http = HttpClient(
            timeout=15, retries=0,
            proxy=config.get("warframe_market", {}).get("proxy", ""),
            logger=self.log)

        self._last_reply: Dict[str, float] = {}
        self._chat_bucket: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()
        self._config_lock = threading.Lock()
        self._alert_thread: Optional[threading.Thread] = None
        self.started_at = time.time()
        self._restarting = False
        self.web_config = dict(config.get("web", {}))
        update_cfg = dict(self.web_config.get("update", {}))
        self.update_checker = UpdateChecker(
            update_cfg,
            proxy=(update_cfg.get("proxy")
                   or config.get("warframe_market", {}).get("proxy", "")),
            logger=self.log,
            on_notify=self._notify_update)
        self.web_server = AdminServer(
            self, self.web_config, logger=self.log)

    # ------------------------------------------------------------------ 启动
    def start(self) -> None:
        self.started_at = time.time()
        self.update_checker.start()
        self.web_server.start()
        if self.behavior.get("preload_items", True):
            thread = threading.Thread(target=self._prewarm, name="prewarm", daemon=True)
            thread.start()
        if self.updater is not None:
            self.updater.start()
        if self.alerts is not None:
            self._alert_thread = threading.Thread(target=self._alert_loop,
                                                  name="alerts", daemon=True)
            self._alert_thread.start()

    def run(self) -> None:
        self.start()
        try:
            self.onebot.run_forever(self._stop)
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        self.web_server.stop()
        self.update_checker.stop()
        if self.updater is not None:
            self.updater.stop()
        self.onebot.close()
        self.log.info("机器人已停止")

    def _prewarm(self) -> None:
        started = time.time()
        try:
            index = self.market.load_items()
            self.log.info("物品索引预热完成：%d 条，用时 %.1f 秒", len(index), time.time() - started)
        except Exception as exc:
            self.log.warning("物品索引预热失败（首次查询时会重试）：%s", exc)

    # ------------------------------------------------------------------ Web/更新
    def status_snapshot(self) -> Dict[str, Any]:
        try:
            alert_count = len(self.alerts.active()) if self.alerts is not None else 0
        except Exception:
            alert_count = 0
        update = self.update_checker.snapshot() if self.update_checker else {}
        whitelist = {str(item).strip() for item in
                     (self.behavior.get("whitelist_groups") or [])}
        blacklist = {str(item).strip() for item in
                     (self.behavior.get("blacklist_groups") or [])}
        return {
            "version": __version__,
            "uptime": max(0.0, time.time() - self.started_at),
            "onebot": {
                "connected": self.onebot.connected,
                "url": getattr(self.onebot, "url", ""),
            },
            "image": self.image_enabled,
            "font": imagecard.available(),
            "ocr": ocr.available(),
            "market": {
                "items": self.market.item_count(),
                "cache_age": self.market.cache_age(),
            },
            "riven": {
                "weapons": self.riven.weapon_count() if self.riven is not None else 0,
            },
            "updater": {
                "last_run": self.updater.last_run if self.updater else 0,
                "last_duration": self.updater.last_duration if self.updater else 0,
                "results": self.updater.last_results if self.updater else {},
            },
            "alerts": alert_count,
            "groups": {
                "whitelist": sorted(whitelist),
                "blacklist": sorted(blacklist),
            },
            "update": update,
            "web": {
                "host": self.web_config.get("host", "127.0.0.1"),
                "port": self.web_server.actual_port,
            },
        }

    def apply_update(self) -> Dict[str, Any]:
        if self.update_checker is None:
            return {"ok": False, "message": "更新检查未启用。"}
        result = self.update_checker.apply_update()
        if result.get("ok") and result.get("restart"):
            self._schedule_restart()
        return result

    def request_restart(self) -> None:
        self._schedule_restart()

    def _schedule_restart(self) -> None:
        if self._restarting:
            return
        self._restarting = True
        timer = threading.Timer(0.6, self._restart_process)
        timer.daemon = True
        timer.start()

    def _restart_process(self) -> None:
        self.log.info("收到重启请求，正在重新启动进程…")
        try:
            args = [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]
            if os.name == "nt":
                subprocess.Popen(args, cwd=os.getcwd(), close_fds=True)
                os._exit(0)
            os.execv(sys.executable, args)
        except Exception:
            self.log.exception("重启失败，进程退出")
            os._exit(1)

    def _web_url(self) -> str:
        public = str(self.web_config.get("public_url") or "").strip()
        if public:
            return public.rstrip("/")
        host = str(self.web_config.get("host") or "127.0.0.1")
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return "http://%s:%s" % (host, self.web_server.actual_port)

    def _notify_update(self, info: Dict[str, Any], kind: str) -> None:
        update_cfg = self.web_config.get("update", {})
        target_group = str(update_cfg.get("notify_group") or "").strip()
        target_user = str(update_cfg.get("notify_user") or "").strip()
        if not target_group and not target_user:
            return
        if kind == "local":
            text = (
                "【后端文件更新】检测到机器人代码文件发生变化。\n"
                "是否重启加载？打开 %s 确认。" % self._web_url())
        else:
            text = (
                "【后端新版本】本地 %s → 远端 %s\n%s\n是否更新？打开 %s 确认。"
                % (str(info.get("local_short") or "?"),
                   str(info.get("remote_short") or "?"),
                   str(info.get("remote_message") or ""),
                   self._web_url()))
        try:
            if target_group:
                if not self._group_allowed(target_group):
                    self.log.info("更新提示目标群 %s 被群策略拒绝，跳过", target_group)
                    return
                self.onebot.send_group_text(target_group, text)
            elif target_user:
                self.onebot.send_private_text(target_user, text)
        except Exception as exc:
            self.log.warning("发送更新提示失败：%s", exc)

    # ------------------------------------------------------------------ 事件
    def on_event(self, event: Dict[str, Any]) -> None:
        if event.get("post_type") != "message":
            return
        message_type = str(event.get("message_type") or "")
        if message_type not in ("group", "private"):
            return

        self.self_id = str(event.get("self_id") or self.self_id)
        user_id = str(event.get("user_id") or "")
        group_id = str(event.get("group_id") or "")
        if not user_id or (self.self_id and user_id == self.self_id):
            return

        is_group = message_type == "group"
        if is_group and not self._group_allowed(group_id):
            return
        if not is_group and not self.behavior.get("allow_private", True):
            return

        text, at_me = self._extract_text(event)
        text = text.strip()
        if not text:
            return

        parsed = self.router.parse(text, allow_bare=self.allow_bare)
        if parsed is None:
            if at_me and self.behavior.get("respond_to_at", True):
                text = self.prefixes[0] + text
                parsed = self.router.parse(text)
            if parsed is None:
                return

        chat_key = "g:%s" % group_id if is_group else "p:%s" % user_id
        user_key = "%s/u:%s" % (chat_key, user_id)
        if not self._allow(chat_key, user_key):
            self.log.debug("触发限流：%s", user_key)
            return

        image = (self._event_image(event)
                 if parsed[0] == "riven_analysis" else None)
        context = Context(
            user_id=user_id,
            group_id=group_id,
            is_group=is_group,
            self_id=self.self_id,
            prefix=self.prefixes[0],
            image=image,
            allow_bare=self.allow_bare,
        )

        try:
            replies = self.router.handle(text, context)
        except Exception as exc:  # pragma: no cover - 防御
            self.log.exception("处理命令失败：%s", exc)
            replies = [present.error_reply(
                "查询出错了，请稍后再试。若持续失败，请运行 python selftest.py 自检。")]

        if replies:
            self._reply(replies, is_group, group_id, user_id)

    def _group_allowed(self, group_id: str) -> bool:
        group_id = str(group_id or "").strip()
        whitelist = {str(item).strip() for item in
                     (self.behavior.get("whitelist_groups") or [])}
        blacklist = {str(item).strip() for item in
                     (self.behavior.get("blacklist_groups") or [])}
        if whitelist:
            return group_id in whitelist and group_id not in blacklist
        return group_id not in blacklist

    def group_policy_snapshot(self) -> Dict[str, List[str]]:
        return {
            "whitelist": sorted({str(item).strip() for item in
                                 (self.behavior.get("whitelist_groups") or [])
                                 if str(item).strip()}),
            "blacklist": sorted({str(item).strip() for item in
                                 (self.behavior.get("blacklist_groups") or [])
                                 if str(item).strip()}),
        }

    def _normalize_group_ids(self, values, label: str) -> List[str]:
        if not isinstance(values, (list, tuple)):
            raise ValueError("%s 必须是数组" % label)
        if len(values) > 2000:
            raise ValueError("%s 数量过多（最多 2000 个）" % label)
        result: List[str] = []
        for raw in values:
            for item in re.split(r"[\s,，;；]+", str(raw or "").strip()):
                if not item:
                    continue
                if not re.fullmatch(r"\d{5,20}", item):
                    raise ValueError("%s 含非法群号：%s" % (label, item))
                if item not in result:
                    result.append(item)
        return sorted(result)

    def update_group_policy(self, whitelist, blacklist) -> Dict[str, Any]:
        """更新群策略，并原子写回 config.json；无需重启立即生效。"""
        whitelist_ids = self._normalize_group_ids(whitelist, "白名单")
        blacklist_ids = self._normalize_group_ids(blacklist, "黑名单")
        if not self.config_path:
            raise ValueError("当前启动方式没有配置文件路径，无法保存名单。")
        path = os.path.abspath(self.config_path)
        if not os.path.isfile(path):
            raise ValueError("找不到配置文件：%s" % path)
        with self._config_lock:
            try:
                with open(path, encoding="utf-8") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    raise ValueError("配置文件根节点不是 JSON 对象。")
                behavior = data.setdefault("behavior", {})
                behavior["whitelist_groups"] = whitelist_ids
                behavior["blacklist_groups"] = blacklist_ids
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                try:
                    shutil.copymode(path, tmp)
                except OSError:
                    pass
                os.replace(tmp, path)
            except Exception as exc:
                try:
                    os.remove(path + ".tmp")
                except OSError:
                    pass
                raise ValueError("保存配置失败：%s" % exc)
            self.behavior["whitelist_groups"] = whitelist_ids
            self.behavior["blacklist_groups"] = blacklist_ids
        self.log.info("群策略已更新：白名单 %d，黑名单 %d",
                      len(whitelist_ids), len(blacklist_ids))
        return {"ok": True, **self.group_policy_snapshot()}

    # ------------------------------------------------------------------ 别名/数据源
    def _market_source(self, index=None) -> Dict[str, Any]:
        count = len(index) if index is not None else self.market.item_count()
        return {
            "name": "warframe.market",
            "base_url": str(self.market.cfg.get("base_url") or ""),
            "language": str(self.market.cfg.get("language") or ""),
            "items": count,
            "last_refresh_seconds": round(self.market.cache_age(), 1),
            "alias_file": self.market.alias_file,
            "aliases": len(self.alias_store.load()),
        }

    def alias_snapshot(self) -> Dict[str, Any]:
        index = None
        error = ""
        try:
            index = self.market.index
        except MarketError as exc:
            error = str(exc)
        entries = []
        for entry in self.alias_store.entries():
            item = index.get(entry["slug"]) if index is not None else None
            entry["name"] = item.name if item else ""
            entry["name_en"] = item.name_en if item else ""
            entry["missing"] = item is None
            entries.append(entry)
        source = self._market_source(index)
        if error:
            source["error"] = error
        return {"source": source, "aliases": entries}

    def alias_groups(self) -> Dict[str, Any]:
        index = None
        error = ""
        try:
            index = self.market.index
        except MarketError as exc:
            error = str(exc)
        groups: Dict[str, Dict[str, Any]] = {}
        for entry in self.alias_store.entries():
            slug = entry["slug"]
            item = index.get(slug) if index is not None else None
            group = groups.setdefault(slug, {
                "slug": slug,
                "name": item.name if item else "",
                "name_en": item.name_en if item else "",
                "missing": item is None,
                "aliases": [],
                "builtin": [],
            })
            group["aliases"].append(entry["alias"])
            if entry.get("builtin"):
                group["builtin"].append(entry["alias"])
        if index is not None:
            for group in groups.values():
                slug = group["slug"]
                if not slug.endswith("_prime_blueprint"):
                    continue
                stem = slug[:-len("_blueprint")]
                parts = []
                for label, part in (("头部", "neuroptics"),
                                    ("机体", "chassis"),
                                    ("系统", "systems")):
                    component = "%s_%s_blueprint" % (stem, part)
                    if component not in index.by_slug:
                        continue
                    aliases = sorted(alias for alias, target in index.aliases.items()
                                     if target == component)
                    item = index.get(component)
                    parts.append({
                        "label": label,
                        "slug": component,
                        "name": item.name if item else "",
                        "aliases": aliases,
                    })
                group["parts"] = parts
        source = self._market_source(index)
        if error:
            source["error"] = error
        values = sorted(groups.values(),
                        key=lambda item: (item["missing"], item["name"] or item["slug"]))
        return {"source": source, "groups": values}

    def save_alias_group(self, slug: str, aliases) -> Dict[str, Any]:
        slug = str(slug or "").strip()
        if not slug:
            raise ValueError("缺少目标 slug。")
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [str(alias or "").strip() for alias in (aliases or [])
                   if str(alias or "").strip()]
        if slug not in self.market.index.by_slug:
            raise ValueError("目标站内不存在：%s" % slug)
        self.alias_store.replace_for_slug(slug, aliases)
        self.market.reload_aliases()
        group = next((item for item in self.alias_groups()["groups"]
                      if item["slug"] == slug), None)
        self.log.info("别名组已保存：%s -> %s", "、".join(aliases), slug)
        return {"ok": True, "group": group}

    def search_market_items(self, query: str) -> List[Dict[str, str]]:
        query = str(query or "").strip()
        if not query:
            return []
        hits = self.market.lookup(query, limit=8)
        return [{"slug": hit.item.slug, "name": hit.item.name,
                 "name_en": hit.item.name_en} for hit in hits]

    def translate_item(self, query: str) -> Dict[str, str]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("请填写要翻译的物品名。")
        item = None
        try:
            hits = self.market.lookup(query, limit=3)
            if hits:
                item = hits[0].item
        except MarketError:
            item = None
        if item is not None:
            zh = item.name or item.name_en
            en = item.name_en or item.name
            if re.search(r"[\u4e00-\u9fff]", zh):
                return {"query": query, "zh": zh, "en": en,
                        "source": "warframe.market", "url": item.url}
        try:
            page, _hits = self.wiki.lookup(query, limit=1)
        except WikiError:
            page = None
        if page is not None:
            return {"query": query, "zh": page.title,
                    "en": item.name_en if item is not None else query,
                    "source": "中文维基", "url": page.url}
        if item is not None:
            zh = item.name or item.name_en
            en = item.name_en or item.name
            return {"query": query, "zh": zh, "en": en,
                    "source": "warframe.market", "url": item.url}
        raise ValueError("没有找到“%s”的可靠中英译名。" % query)

    def _resolve_alias_target(self, target: str) -> str:
        target = str(target or "").strip()
        if not target:
            raise ValueError("请填写目标物品名称或 slug。")
        index = self.market.index
        if target in index.by_slug:
            return target
        hits = self.market.lookup(target, limit=8)
        if not hits:
            raise ValueError("找不到目标物品：%s" % target)
        compact = re.sub(r"[\s_\-]+", "", target.lower())
        exact = [hit.item for hit in hits
                 if compact in (
                     re.sub(r"[\s_\-]+", "", (hit.item.name or "").lower()),
                     re.sub(r"[\s_\-]+", "", (hit.item.name_en or "").lower()),
                     hit.item.slug)]
        if len(exact) == 1:
            return exact[0].slug
        if len(hits) == 1:
            return hits[0].item.slug
        examples = "、".join("%s(%s)" % (hit.item.name, hit.item.slug)
                             for hit in hits[:5])
        raise ValueError("目标不唯一，请改用更精确的名称或 slug：%s" % examples)

    def save_alias(self, alias: str, target: str) -> Dict[str, Any]:
        return self.save_aliases([alias], target)

    def save_aliases(self, aliases, target: str) -> Dict[str, Any]:
        names = [str(alias or "").strip() for alias in aliases
                 if str(alias or "").strip()]
        if not names:
            raise ValueError("至少填一个别名。")
        slug = self._resolve_alias_target(target)
        saved = []
        for alias in names:
            self.alias_store.save(alias, slug)
            saved.append(alias)
        self.market.reload_aliases()
        self.log.info("别名已保存：%s -> %s", "、".join(saved), slug)
        return {"ok": True, "aliases": saved,
                "entry": {"alias": saved[0], "slug": slug}}

    def delete_alias(self, alias: str) -> Dict[str, Any]:
        removed = self.alias_store.delete(alias)
        self.market.reload_aliases()
        self.log.info("别名已删除：%s（%s）", alias, removed)
        return {"ok": True, "removed": removed, "alias": alias}

    def refresh_market_data(self) -> Dict[str, Any]:
        count = self.market.refresh()
        self.market.reload_aliases()
        self.log.info("warframe.market 物品索引已刷新：%d 条", count)
        return {"ok": True, "source": self._market_source()}

    def _extract_text(self, event: Dict[str, Any]) -> Tuple[str, bool]:
        segments = event.get("message")
        at_me = False
        if isinstance(segments, list):
            parts: List[str] = []
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                kind = segment.get("type")
                data = segment.get("data") or {}
                if kind == "text":
                    parts.append(str(data.get("text") or ""))
                elif kind == "at":
                    if self.self_id and str(data.get("qq")) == self.self_id:
                        at_me = True
            text = "".join(parts)
            if text.strip():
                return text, at_me

        raw_source = str(event.get("raw_message") or event.get("message") or "")
        raw = strip_cq(raw_source)
        if self.self_id and ("[CQ:at,qq=%s]" % self.self_id) in raw_source:
            at_me = True
        return raw, at_me

    def _download_image(self, url: str) -> Optional[bytes]:
        try:
            raw = self.image_http.request(url)
            if raw:
                return raw
        except HttpError as exc:
            self.log.info("图片直连失败，尝试代理：%s", exc)
        try:
            raw = self.image_proxy_http.request(url)
            if raw:
                return raw
        except HttpError as exc:
            self.log.warning("图片下载失败：%s", exc)
        return None

    def _event_image(self, event: Dict[str, Any]) -> Optional[bytes]:
        """只取消息里的第一张图片；支持 URL / base64 / 本地 file://。"""
        segments = event.get("message")
        if not isinstance(segments, list):
            return None
        for segment in segments:
            if not isinstance(segment, dict) or segment.get("type") != "image":
                continue
            data = segment.get("data") or {}
            url = str(data.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                raw = self._download_image(url)
                if raw and len(raw) <= 8 * 1024 * 1024:
                    return raw
                continue
            file_value = str(data.get("file") or "").strip()
            if file_value.startswith("base64://"):
                try:
                    payload = file_value[len("base64://"):]
                    payload += "=" * (-len(payload) % 4)
                    raw = base64.b64decode(payload)
                    if len(raw) <= 8 * 1024 * 1024:
                        return raw
                except Exception as exc:
                    self.log.warning("图片 base64 解码失败：%s", exc)
            elif file_value.startswith("file://"):
                path = file_value[len("file://"):]
                if os.path.isfile(path) and os.path.getsize(path) <= 8 * 1024 * 1024:
                    with open(path, "rb") as handle:
                        return handle.read()
        return None

    # ------------------------------------------------------------------ 限流
    def _allow(self, chat_key: str, user_key: str) -> bool:
        now = time.time()
        cooldown = float(self.behavior.get("cooldown_seconds", 3))
        limit = int(self.behavior.get("max_replies_per_minute", 20))

        with self._lock:
            last = self._last_reply.get(user_key, 0.0)
            if cooldown > 0 and now - last < cooldown:
                return False

            bucket = self._chat_bucket.setdefault(chat_key, collections.deque())
            while bucket and now - bucket[0] > 60:
                bucket.popleft()
            if len(bucket) >= limit:
                return False

            self._last_reply[user_key] = now
            bucket.append(now)
            if len(self._last_reply) > 5000:
                cutoff = now - 3600
                self._last_reply = {
                    key: value for key, value in self._last_reply.items() if value > cutoff}
            return True

    # ------------------------------------------------------------------ 发送
    def _reply(self, replies: List[Reply], is_group: bool,
               group_id: str, user_id: str) -> None:
        first = True
        for reply in replies:
            at_user = user_id if (is_group and first
                                  and self.behavior.get("at_sender", True)) else ""
            sent = self._deliver(is_group, group_id if is_group else user_id,
                                 reply, at_user=at_user)
            if sent:
                first = False

    def _deliver(self, is_group: bool, chat_id: str, reply: Reply,
                 at_user: str = "") -> bool:
        prefix = "[CQ:at,qq=%s] " % at_user if at_user else ""
        if self.image_enabled and reply.image is not None:
            try:
                png = reply.image()
            except Exception as exc:
                self.log.warning("图片生成失败，回退文字：%s", exc)
                png = None
            if png:
                try:
                    if is_group:
                        self.onebot.send_group_image(chat_id, png, prefix)
                    else:
                        self.onebot.send_private_image(chat_id, png, prefix)
                    return True
                except OneBotError as exc:
                    self.log.warning("图片发送失败，回退文字：%s", exc)

        if not reply.text:
            return False

        max_chars = int(self.behavior.get("max_reply_chars", 1200))
        chunks = chunk_text(reply.text, max_chars)
        for index, chunk in enumerate(chunks):
            text = escape_cq(chunk)
            if len(chunks) > 1:
                text = "(%d/%d) %s" % (index + 1, len(chunks), text)
            if prefix and index == 0:
                text = prefix + text
            try:
                if is_group:
                    self.onebot.send_group_text(chat_id, text)
                else:
                    self.onebot.send_private_text(chat_id, text)
            except OneBotError as exc:
                self.log.warning("发送消息失败：%s", exc)
                return False
            if len(chunks) > 1:
                time.sleep(0.35)
        return True

    # ------------------------------------------------------------------ 提醒
    def _alert_loop(self) -> None:
        if self._stop.wait(min(60, self.alert_interval)):
            return
        while not self._stop.is_set():
            try:
                self._check_alerts()
            except Exception as exc:  # pragma: no cover - 防御
                self.log.exception("检查提醒时出错：%s", exc)
            if self._stop.wait(self.alert_interval):
                return

    def _check_alerts(self) -> None:
        assert self.alerts is not None
        pending = self.alerts.active()[:50]
        if not pending:
            return
        self.log.info("检查 %d 条降价提醒…", len(pending))
        for alert in pending:
            if self._stop.is_set():
                return
            try:
                sell, _ = self.market.top_orders(alert.slug, limit=1,
                                                 platform=alert.platform)
            except MarketError as exc:
                self.log.warning("提醒查询失败 %s：%s", alert.slug, exc)
                continue

            price = sell[0].platinum if sell else None
            if price is not None and price != alert.last_price:
                self.alerts.set_price(alert, price)
            if price is None or price > alert.target:
                continue

            item = None
            try:
                item = self.market.index.get(alert.slug)
            except Exception:
                item = None
            item = item or Item(slug=alert.slug, name=alert.name)
            reply = present.alert_fired_reply(item, sell[0], alert)
            if alert.chat_type == "group" and not self._group_allowed(alert.chat_id):
                # 群被拉黑或不在白名单时保持提醒数据，但不发送。
                continue
            chat_id = alert.chat_id
            self._deliver(alert.chat_type == "group", chat_id, reply)
            alert.active = False
            self.alerts.update(alert)
            self.log.info("提醒 #%s 已触发：%s %dp <= %dp",
                          alert.id, alert.name, price, alert.target)
            time.sleep(0.4)
