"""网络适配：按当前机器/容器的网络环境，自动挑一条能用的代理。

为什么要这层：
    代理地址在每台机器上都不一样——本机开发是 `127.0.0.1:10809`（Clash），
    服务器 Docker 里是 `http://xray:10809`，有些机器干脆直连就能通。
    写死一个地址，换台机器 / 代理容器没起来就会全线超时。

所以这里做三件事：
    1. 收集候选：模块自己配的代理 -> network.proxies -> 本机常见代理端口 -> 直连
    2. 逐条探活：只读取少量字节，记录通不通、耗时多少、是不是被拦（403/429）
    3. 选出能用的：写回各数据源的 proxy，其余候选留作兜底链；失败会自动降级

结果会缓存到 `data/net_probe.json`（默认 30 分钟），不会每轮都探。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from .net import HttpClient, global_fallbacks, set_global_fallbacks

LOGGER = logging.getLogger("qqwarframe.netadapt")

# 各数据源的探活地址：要足够轻，又要能反映真实链路。
# config 指向该数据源代理在配置里的位置，便于把结果写回去。
TARGETS: Dict[str, Dict[str, Any]] = {
    "market": {
        "label": "市场报价",
        "url": "https://api.warframe.market/v1/items",
        # 市场按请求头分流，不带会被 Cloudflare 直接 403，探出来的结果就没参考价值
        "headers": {"Platform": "pc", "Language": "zh-hans",
                    "Accept": "application/json"},
        "config": ("warframe_market", "proxy"),
    },
    "worldstate": {
        "label": "世界状态",
        "url": "https://api.warframestat.us/pc?only=timestamp",
        "config": ("worldstate", "proxy"),
    },
    "wiki": {
        "label": "英文维基",
        "url": ("https://wiki.warframe.com/api.php"
                "?action=query&meta=siteinfo&format=json"),
        "config": ("wiki", "proxy"),
    },
    "arbi": {
        "label": "仲裁评级",
        "url": "https://arbi.wf.wiki/",
        "config": ("arbi", "proxy"),
    },
    "rotation": {
        "label": "轮换商店",
        # 灰机 wiki 对非浏览器请求一律 403，实际抓取走 r.jina.ai 中转，
        # 所以这里探中转链路而不是 wiki 本身
        "url": "https://r.jina.ai/",
        "config": ("rotation", "proxy"),
    },
    "update": {
        "label": "GitHub 更新",
        "url": "https://api.github.com/repos/Dlessream/qqwarframe-bot",
        "config": ("web", "update", "proxy"),
    },
}

# 本机常见代理端口（Clash / v2rayN / Xray 默认端口都在里面）
DEFAULT_LOCAL_PORTS = (7890, 10809, 10808, 20171, 1080, 8889)

DIRECT = "none"
SYSTEM = ""


def _dedupe(values: Any) -> List[str]:
    """去重并保持顺序；空串代表「系统代理」，是合法选项，不丢。"""
    result: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text.lower() in ("none", "direct", "off"):
            text = DIRECT
        if text not in result:
            result.append(text)
    return result


def describe(proxy: str) -> str:
    if not proxy:
        return "系统代理"
    if proxy == DIRECT:
        return "直连"
    return proxy


# --------------------------------------------------------------------- 候选
def _port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    """本机这个端口有没有在监听（用来发现 Clash / v2rayN 之类）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_proxy_candidates(cfg: Dict[str, Any]) -> List[str]:
    """探测本机常见代理端口，返回真正在监听的那些。"""
    network = cfg.get("network") or {}
    if not network.get("discover_local", True):
        return []
    hosts = [str(network.get("discover_host") or "127.0.0.1")]
    ports = [int(port) for port in (network.get("local_ports")
                                    or DEFAULT_LOCAL_PORTS)]
    found: List[str] = []
    for host in hosts:
        for port in ports:
            if _port_open(host, port):
                found.append("http://%s:%d" % (host, port))
    return found


def collect_proxies(cfg: Dict[str, Any]) -> List[str]:
    """全局候选代理（不含各模块自己的配置）。"""
    network = cfg.get("network") or {}
    raw = network.get("proxies")
    if isinstance(raw, str):
        raw = [item for item in raw.replace(";", ",").split(",") if item.strip()]
    result = _dedupe(raw)
    result.extend(local_proxy_candidates(cfg))
    if network.get("allow_direct", True) and DIRECT not in result:
        result.append(DIRECT)
    return _dedupe(result)


def module_proxy(cfg: Dict[str, Any], module: str) -> str:
    """某个数据源当前配置的代理。"""
    path = TARGETS.get(module, {}).get("config") or ()
    node: Any = cfg
    for key in path:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return str(node or "").strip()


def candidates(cfg: Dict[str, Any], module: str) -> List[str]:
    """这个数据源按优先级该试哪些代理。"""
    own = module_proxy(cfg, module)
    # 没配代理时用「系统代理」（读 HTTP(S)_PROXY 环境变量）
    return _dedupe([own or SYSTEM] + collect_proxies(cfg))


def _set_module_proxy(cfg: Dict[str, Any], module: str, value: str) -> bool:
    path = TARGETS.get(module, {}).get("config") or ()
    if not path:
        return False
    node: Any = cfg
    for key in path[:-1]:
        if not isinstance(node, dict):
            return False
        nxt = node.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            node[key] = nxt
        node = nxt
    node[path[-1]] = value
    return True


# --------------------------------------------------------------------- 探活
def _client_for(cfg: Dict[str, Any], proxy: str, timeout: float,
                user_agent: Optional[str] = None) -> HttpClient:
    return HttpClient(
        user_agent=user_agent or "QQWarframeBot/1.0 (netcheck)",
        timeout=timeout,
        proxy=proxy or SYSTEM,
        retries=0,
        auto_switch=False,
        logger=LOGGER,
    )


# 阶段一用市场接口当「外网通不通」的共同参照，避免每个模块都重测一遍代理
COMMON_MODULE = "market"


def _probe_one(cfg: Dict[str, Any], proxy: str, url: str,
               timeout: float,
               headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    client = _client_for(cfg, proxy, timeout,
                         user_agent=(cfg.get("warframe_market") or {})
                         .get("user_agent"))
    return client.probe(url, headers=headers)


def probe(cfg: Dict[str, Any], modules: Optional[List[str]] = None,
          timeout: Optional[float] = None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """探活：`{模块: {代理: 结果}}`。

    分两步，省时间：
      1. 先并发测每条代理对共同目标的连通性，得到「哪些代理本身是活的」
      2. 每个模块只拿活着的代理（外加它自己配的那条）去试，命中就停
    代理压根连不上时不会浪费一整个 HTTP 超时。
    """
    network = cfg.get("network") or {}
    limit = float(timeout or network.get("probe_timeout_seconds") or 6)
    names = [name for name in (modules or list(TARGETS)) if name in TARGETS]
    result: Dict[str, Dict[str, Dict[str, Any]]] = {name: {} for name in names}
    if not names:
        return result

    shared = collect_proxies(cfg)
    common = TARGETS[COMMON_MODULE]
    common_url = str(common["url"])
    common_headers = common.get("headers")

    # ---- 阶段一：代理本身活不活
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(shared)))) as pool:
        shared_outcome = dict(zip(
            shared,
            pool.map(lambda item: _probe_one(cfg, item, common_url, limit,
                                              common_headers), shared)))

    def _order(module: str) -> List[str]:
        """这个模块该按什么顺序试：自己配的优先，然后是活着的代理。"""
        own = module_proxy(cfg, module)
        alive = [item for item in shared
                 if shared_outcome.get(item, {}).get("ok")
                 and not shared_outcome.get(item, {}).get("blocked")]
        return _dedupe([own] + alive + shared)

    def _run(module: str) -> Tuple[str, Dict[str, Dict[str, Any]]]:
        url = str(TARGETS[module]["url"])
        headers = TARGETS[module].get("headers")
        per_proxy: Dict[str, Dict[str, Any]] = {}
        # 只试到第一条能用的为止；整条链都不通才跑满
        tried = 0
        for proxy in _order(module):
            if proxy in per_proxy:
                continue
            if tried >= 3 and any(item.get("ok") and not item.get("blocked")
                                  for item in per_proxy.values()):
                break
            tried += 1
            outcome = _probe_one(cfg, proxy, url, limit, headers)
            outcome["label"] = TARGETS[module]["label"]
            per_proxy[proxy] = outcome
            if outcome.get("ok") and not outcome.get("blocked"):
                break
        # 没试到的代理沿用阶段一结论，报告里能看全貌
        for proxy in _order(module):
            if proxy not in per_proxy:
                outcome = dict(shared_outcome.get(proxy) or
                               {"ok": False, "status": 0, "blocked": False,
                                "error": "未探测"})
                outcome.setdefault("ms", 0)
                outcome["label"] = TARGETS[module]["label"]
                outcome["note"] = "沿用共同目标结论"
                outcome["inherited"] = True
                per_proxy[proxy] = outcome
        return module, per_proxy

    with ThreadPoolExecutor(max_workers=max(1, min(8, len(names)))) as pool:
        for module, per_proxy in pool.map(_run, names):
            result[module] = per_proxy
    return result


def pick(result: Dict[str, Dict[str, Any]], prefer: str = "order") -> Optional[str]:
    """从一个模块的探活结果里挑一条能用的代理。"""
    usable = [(proxy, item) for proxy, item in result.items()
              if item.get("ok") and not item.get("blocked")]
    if not usable:
        return None
    if prefer == "fastest":
        def _ms(pair: Tuple[str, Dict[str, Any]]) -> int:
            item = pair[1]
            # 没实测（沿用共同目标结论）的不参与「最快」比较
            if item.get("inherited"):
                return 999999
            return int(item.get("ms") or 999999)

        usable.sort(key=_ms)
    return usable[0][0]


def suggest(cfg: Dict[str, Any],
            result: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
            prefer: Optional[str] = None) -> Dict[str, str]:
    """每个数据源推荐用哪条代理。

    原则：**现在配的那条能用就不动它**——自动适配是给「配的这条路不通」
    兜底的，不是替用户做主。只有当前代理探不通（或被拦）才换。
    """
    network = cfg.get("network") or {}
    mode = str(prefer or network.get("prefer") or "order").lower()
    outcome = result if result is not None else probe(cfg)
    suggestion: Dict[str, str] = {}
    for module, per_proxy in outcome.items():
        own = module_proxy(cfg, module) or SYSTEM
        current_item = per_proxy.get(own)
        if current_item and current_item.get("ok") and not current_item.get("blocked"):
            continue
        best = pick(per_proxy, mode)
        if best is not None:
            suggestion[module] = best
    return suggestion


def effective(cfg: Dict[str, Any],
              result: Dict[str, Dict[str, Dict[str, Any]]],
              suggestion: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """每个数据源**最终**会走哪条链路。

    `suggest()` 只给出「需要改」的那部分；当前配的代理本来就通时它不说话，
    所以看结果要用这里——否则会出现「直连明明可用，却报无可用链路」。
    """
    suggestion = suggestion if suggestion is not None else suggest(cfg, result)
    final: Dict[str, str] = {}
    for module, per_proxy in result.items():
        own = module_proxy(cfg, module) or SYSTEM
        item = per_proxy.get(own)
        if item and item.get("ok") and not item.get("blocked"):
            final[module] = own
        elif module in suggestion:
            final[module] = suggestion[module]
    return final


def apply_suggestion(cfg: Dict[str, Any], suggestion: Dict[str, str],
                     set_fallbacks: bool = True) -> List[str]:
    """把推荐结果写回配置；返回改动说明。"""
    changes: List[str] = []
    for module, proxy in (suggestion or {}).items():
        old = module_proxy(cfg, module) or SYSTEM
        if old == proxy:
            continue
        if _set_module_proxy(cfg, module, proxy):
            changes.append("%s：%s -> %s" % (TARGETS[module]["label"],
                                             describe(old), describe(proxy)))
    if set_fallbacks:
        set_global_fallbacks(collect_proxies(cfg))
    return changes


# --------------------------------------------------------------------- 缓存
def cache_path(cfg: Dict[str, Any]) -> str:
    network = cfg.get("network") or {}
    return str(network.get("probe_cache_file") or "data/net_probe.json")


def load_cache(cfg: Dict[str, Any]) -> Optional[Dict[str, Dict[str, Dict[str, Any]]]]:
    network = cfg.get("network") or {}
    ttl = float(network.get("probe_ttl_seconds") or 1800)
    path = cache_path(cfg)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    stamp = float(payload.get("ts") or 0)
    if ttl > 0 and time.time() - stamp > ttl:
        return None
    data = payload.get("result")
    return data if isinstance(data, dict) else None


def save_cache(cfg: Dict[str, Any],
               result: Dict[str, Dict[str, Dict[str, Any]]]) -> bool:
    path = cache_path(cfg)
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"ts": time.time(), "result": result}, handle,
                      ensure_ascii=False, indent=2)
        return True
    except OSError as exc:
        LOGGER.debug("网络探测缓存写入失败：%s", exc)
        return False


# --------------------------------------------------------------------- 入口
def adapt(cfg: Dict[str, Any], force: bool = False,
          log: Optional[logging.Logger] = None) -> Dict[str, str]:
    """按当前网络环境自动适配各数据源的代理。

    - 关掉 `network.auto_adapt` 就直接返回空结果（行为跟以前一样）
    - 有未过期的缓存就复用，不重复探测
    - 探测本身出错不影响启动
    """
    log = log or LOGGER
    network = cfg.get("network") or {}
    if not network.get("auto_adapt", True):
        return {}
    result = None if force else load_cache(cfg)
    if result is None:
        try:
            result = probe(cfg)
        except Exception as exc:  # noqa: BLE001 - 探测失败不该拖垮启动
            log.warning("网络探测失败，沿用配置里的代理：%s", exc)
            return {}
        save_cache(cfg, result)
    suggestion = suggest(cfg, result)
    changes = apply_suggestion(cfg, suggestion)
    for line in changes:
        log.info("网络适配：%s", line)
    if not changes:
        log.info("网络适配：各数据源代理无需调整（%d 条候选）",
                 len(collect_proxies(cfg)))
    return suggestion


def current(cfg: Dict[str, Any]) -> Dict[str, str]:
    """各数据源当前生效的代理（给自检/控制台看）。"""
    return {module: (module_proxy(cfg, module) or SYSTEM)
            for module in TARGETS}


# --------------------------------------------------------------------- 报告
def _status_text(item: Dict[str, Any]) -> str:
    if item.get("blocked"):
        return "被拦 %s" % (item.get("status") or "")
    if item.get("ok"):
        status = int(item.get("status") or 0)
        if status and status != 200:
            return "通 %sms（HTTP %d）" % (item.get("ms"), status)
        return "可用 %sms" % item.get("ms")
    if item.get("status"):
        return "HTTP %s" % item.get("status")
    return "不通（%s）" % (str(item.get("error") or "")[:36] or "超时")


def report(cfg: Dict[str, Any],
           result: Dict[str, Dict[str, Dict[str, Any]]],
           suggestion: Optional[Dict[str, str]] = None) -> str:
    """人能读的探活报告（自检 / CLI 用）。"""
    suggestion = suggestion if suggestion is not None else suggest(cfg, result)
    final = effective(cfg, result, suggestion)
    lines: List[str] = []
    lines.append("候选代理：%s" % "、".join(describe(item)
                                          for item in collect_proxies(cfg)))
    for module, per_proxy in result.items():
        label = TARGETS[module]["label"]
        # 注意：空串代表「系统代理」，是有效值，不能用真值判断
        best = final.get(module)
        mark = "" if module in suggestion else "（沿用配置）"
        lines.append("")
        lines.append("[%s] %s%s" % (
            label,
            describe(best) if best is not None else "无可用链路",
            mark if best is not None else ""))
        for proxy, item in per_proxy.items():
            mark = " => 采用" if best is not None and proxy == best else ""
            lines.append("    %-24s %s%s"
                         % (describe(proxy), _status_text(item), mark))
    return "\n".join(lines)


def summary(cfg: Dict[str, Any],
            result: Dict[str, Dict[str, Dict[str, Any]]]) -> str:
    """一行式摘要，塞进启动横幅。"""
    final = effective(cfg, result)
    return "，".join("%s=%s" % (TARGETS[module]["label"],
                                describe(final[module])
                                if module in final else "不可用")
                    for module in result)
