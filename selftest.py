#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联网自检：不连接 QQ，只验证 warframe.market 与中文维基是否可用。

    python selftest.py
    python run.py --check
"""

from __future__ import annotations

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from bot.config import ConfigError, ensure_config, load_config  # noqa: E402
from bot.log import setup_logging  # noqa: E402
from bot.market import MarketClient, MarketError  # noqa: E402
from bot import imagecard, present, render  # noqa: E402
from bot.arbi import ArbiClient  # noqa: E402
from bot.riven import RivenClient  # noqa: E402
from bot.rivencalc import calculate  # noqa: E402
from bot.wiki import WikiClient, WikiError  # noqa: E402
from bot.worldstate import WorldStateClient, WorldStateError  # noqa: E402

SAMPLES = ["绝路", "牛甲", "Mesa Prime", "分裂膛室"]
WIKI_SAMPLES = ["高斯", "Excalibur"]


class Report:
    def __init__(self) -> None:
        self.rows = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, ok, detail))
        mark = "OK  " if ok else "FAIL"
        print("[%s] %s%s" % (mark, name, ("  " + detail) if detail else ""))

    def warn(self, name: str, detail: str = "") -> None:
        self.rows.append((name, None, detail))
        print("[WARN] %s%s" % (name, ("  " + detail) if detail else ""))

    @property
    def ok(self) -> bool:
        return all(row[1] is not False for row in self.rows)


def run_selftest(config_path: str = "config.json") -> int:
    os.chdir(BASE_DIR)
    if not os.path.isabs(config_path):
        config_path = os.path.abspath(config_path)

    if not os.path.isfile(config_path):
        if ensure_config(config_path, os.path.join(BASE_DIR, "config.example.json")):
            print("已生成配置文件 config.json，请填写 OneBot 信息后重新运行自检。")
            return 2

    setup_logging("INFO", "")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print("[FAIL] 配置：%s" % exc)
        return 2

    print("=" * 46)
    print(" Warframe 机器人联网自检")
    print(" 市场接口: %s" % config["warframe_market"]["base_url"])
    print(" 维基接口: %s" % config["wiki"]["api_url"])
    print(" 世界状态: %s" % config.get("worldstate", {}).get("base_url", "-"))
    proxy = config["warframe_market"].get("proxy") or "(系统代理)"
    print(" 代理    : %s" % proxy)
    print("=" * 46)

    report = Report()

    # ---------------------------------------------------------------- 网络适配
    from bot import netadapt

    # load_config 启动时已经探过一遍（结果缓存在 data/net_probe.json），直接用
    net_result = netadapt.load_cache(config) or netadapt.probe(config)
    net_best = netadapt.suggest(config, net_result)
    reachable = [name for name, value in net_best.items() if value]
    if reachable:
        detail = "、".join(
            "%s=%s" % (netadapt.TARGETS[name]["label"],
                       netadapt.describe(net_best[name]))
            for name in reachable)
        report.add("网络适配", True,
                   "%d/%d 个数据源有可用链路：%s"
                   % (len(reachable), len(net_result), detail))
    else:
        report.add("网络适配", False,
                   "没有一条代理能通：检查代理是否开启，或在 network.proxies 里填上")

    market = MarketClient(config["warframe_market"])
    wiki = WikiClient(config["wiki"])

    image_cfg = config.get("image", {})
    imagecard.configure(str(image_cfg.get("font", "")),
                        str(image_cfg.get("font_bold", "")))
    image_ok = imagecard.available()
    report.add("图片渲染", image_ok,
               "Pillow + 中文字体可用" if image_ok else
               "缺少 Pillow 或中文字体；Linux 安装 fonts-noto-cjk，或设置 image.font")

    # ---------------------------------------------------------------- 物品索引
    started = time.time()
    index = None
    try:
        index = market.load_items(force=True)
        elapsed = time.time() - started
        localized = sum(1 for item in index.items if item.name and item.name != item.name_en)
        report.add("物品索引", True, "%d 条，中文名 %d 条，%.1fs" %
                   (len(index), localized, elapsed))
        if localized == 0 and config["warframe_market"]["language"].startswith("zh"):
            print("       ⚠ 接口没有返回中文名，中文搜索会退化为英文匹配。")
            print("         请检查网络，或把 warframe_market.language 改成 en 后重试。")
    except MarketError as exc:
        report.add("物品索引", False, str(exc))
        print("       ⚠ 如果是 403/超时：请检查网络，必要时在 config.json 里设置代理。")

    # ---------------------------------------------------------------- 搜索
    if index is not None:
        search_ok = True
        for query in SAMPLES:
            hits = index.search(query, limit=3)
            if not hits:
                search_ok = False
                print("       搜索“%s”：无结果" % query)
                continue
            preview = " / ".join("%s(%s)" % (hit.item.name, hit.item.kind_label)
                                 for hit in hits[:2])
            print("       搜索“%s”：%s" % (query, preview))
        report.add("中文搜索", search_ok)

    # ---------------------------------------------------------------- 报价
    detail = sell = buy = stats = None
    if index is not None:
        hits = index.search("绝路", limit=1) or index.search("Mesa Prime", limit=1)
        if not hits:
            report.add("报价与行情", False, "找不到用于测试的物品")
        else:
            item = hits[0].item
            try:
                detail = market.item_detail(item.slug) or item
                sell, buy = market.top_orders(item.slug, limit=3)
                stats = market.statistics(item.slug)
                report.add("报价与行情", True, "%s 在售 %d / 收购 %d 条" %
                           (detail.name, len(sell), len(buy)))
                card = render.render_price(detail, sell, buy, stats, order_limit=3)
                print("-" * 46)
                print(card)
                print("-" * 46)
            except MarketError as exc:
                report.add("报价与行情", False, str(exc))

    # ---------------------------------------------------------------- 图片
    if image_ok and detail is not None:
        try:
            png = present.item_reply(detail, sell or [], buy or [], stats, "pc").image()
            report.add("图片卡片", png.startswith(b"\x89PNG"),
                       "%d × %d，%d KB" % (
                           int.from_bytes(png[16:20], "big"),
                           int.from_bytes(png[20:24], "big"),
                           len(png) // 1024))
        except Exception as exc:  # pragma: no cover - 仅自检
            report.add("图片卡片", False, str(exc))

    # ---------------------------------------------------------------- 紫卡
    try:
        weapons = RivenClient(
            market,
            disposition_override=config.get("riven", {}).get("disposition_override"),
        ).weapons()
        report.add("紫卡数据", bool(weapons), "%d 把可上紫卡的武器" % len(weapons))
        sample = weapons.get("rubico") or next(iter(weapons.values()))
        analysis = calculate(sample)
        report.add("紫卡词条范围", True, "%s 倾向 %.2f，%d 条正向词条" % (
            analysis.name, analysis.disposition, len(analysis.two_neg)))
    except Exception as exc:  # pragma: no cover - 仅自检
        report.add("紫卡数据", False, str(exc))

    # ---------------------------------------------------------------- 世界状态
    if config.get("worldstate", {}).get("enabled", True):
        try:
            trader = WorldStateClient(config["worldstate"]).void_trader()
            report.add("世界状态", True, "奸商库存 %d 件" % len(trader.inventory))
        except WorldStateError as exc:
            report.add("世界状态", False, str(exc))

    if config.get("arbi", {}).get("enabled", True):
        try:
            arbi_data = ArbiClient(config["arbi"]).data()
            report.add("仲裁评级", True, "%d 个已评级节点" % arbi_data.rated_count)
        except Exception as exc:  # pragma: no cover - 仅自检
            report.warn("仲裁评级", "arbi.wf.wiki 不可用：%s" % exc)

    # ---------------------------------------------------------------- 维基
    wiki_found = False
    wiki_error = ""
    for query in WIKI_SAMPLES:
        started = time.time()
        try:
            page, hits = wiki.lookup(query, limit=3)
            elapsed = time.time() - started
            if page:
                wiki_found = True
                print("       维基“%s”：%s（%.1fs）" % (query, page.title, elapsed))
                print("         %s" % page.extract[:80].replace("\n", " "))
            elif hits:
                wiki_found = True
                print("       维基“%s”：仅搜索到 %s" % (query, hits[0].title))
            else:
                print("       维基“%s”：无结果" % query)
        except WikiError as exc:
            wiki_error = str(exc)
            print("       维基“%s”失败：%s" % (query, exc))

    if wiki_found:
        report.add("中文维基", True)
    elif wiki_error:
        report.warn("中文维基", "请求被 WAF/Cloudflare 拦截（403/567）或接口异常")
        print("       浏览器能打开 https://warframe.huijiwiki.com/wiki/高斯 就说明是"
              "机器人被拦截，可换代理，或改用 B 站 WIKI。")
    else:
        report.warn("中文维基", "没有搜索到词条")

    print("=" * 46)
    passed = sum(1 for row in report.rows if row[1] is True)
    warned = sum(1 for row in report.rows if row[1] is None)
    print(" 结果：%d/%d 项通过%s" % (
        passed, len(report.rows), ("，%d 项警告" % warned) if warned else ""))
    if report.ok:
        print(" 核心功能正常，可以运行 python run.py 启动机器人了。")
        if warned:
            print(" 警告项不影响启动，按上面的提示处理即可。")
    else:
        print(" 存在失败项：请先解决上面的问题，再启动机器人。")
    print("=" * 46)
    return 0 if report.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Warframe 机器人联网自检")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    args = parser.parse_args()
    return run_selftest(args.config)


if __name__ == "__main__":
    sys.exit(main())
