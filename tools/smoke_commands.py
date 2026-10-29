"""全命令冒烟测试：逐条跑机器人指令，检查是否都有完整输出。

用法：
    python tools/smoke_commands.py                 # 用 config.json（含网络适配）
    python tools/smoke_commands.py --config X.json
    python tools/smoke_commands.py --only riven,baro   # 只跑指定命令
    python tools/smoke_commands.py --verbose            # 打印完整文本

判定标准：
    OK    有文字或有图片
    EMPTY 命令被识别但没有输出（说明功能挂了）
    FAIL  抛异常
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from typing import List, Tuple

sys.path.insert(0, ".")

from bot import netadapt  # noqa: E402
from bot.app import BotApp  # noqa: E402
from bot.commands import Context  # noqa: E402
from bot.config import load_config  # noqa: E402

# （命令名，示例指令）
CASES: List[Tuple[str, str]] = [
    ("help", "#帮助"),
    ("query", "#查 绝路"),
    ("price", "#价格 绝路"),
    ("warframe", "#战甲"),
    ("warframe_named", "#战甲 Mesa"),
    ("weapon", "#武器 绝路"),
    ("mod", "#mod 分裂膛室"),
    ("set", "#拆件 Mesa Prime"),
    ("riven", "#紫卡 绝路"),
    ("riven_rank", "#紫卡榜"),
    ("frame_rank", "#战甲榜"),
    ("junk_gold", "#金垃圾"),
    ("junk_silver", "#银垃圾"),
    ("junk_bronze", "#铜垃圾"),
    ("junk_fake_gold", "#假金垃圾"),
    ("junk_fake_silver", "#假银垃圾"),
    ("junk_overview", "#垃圾行情"),
    ("rotation", "#轮换"),
    ("rotation_acrithis", "#言录使"),
    ("rotation_glast", "#格拉斯"),
    ("rotation_eleanor", "#埃莉诺"),
    ("data_status", "#数据状态"),
    ("baro", "#奸商"),
    ("sortie", "#突击"),
    ("arbitration", "#仲裁"),
    ("arbi_tiers", "#仲裁评级"),
    ("arbi_schedule", "#仲裁表"),
    ("world_summary", "#世界状态"),
    ("world_alerts", "#警报"),
    ("world_invasions", "#入侵"),
    ("steel_path", "#钢铁侵袭"),
    ("nightwave", "#电波"),
    ("cycles", "#平原"),
    ("deals", "#特惠"),
    ("news", "#新闻"),
    ("events", "#活动"),
    ("circuit", "#回廊"),
    ("archon", "#执刑官"),
    ("fissures", "#裂缝"),
    ("steel_fissures", "#钢铁裂缝"),
    ("storms", "#虚空风暴"),
    ("bounties", "#赏金"),
    ("bounty_cavia", "#解剖圣所"),
    ("bounty_hex", "#霍瓦尼亚"),
    ("bounty_zariman", "#扎里曼"),
    ("cetus", "#希图斯"),
    ("today_arbitration", "#今日仲裁"),
    ("effective_arbitration", "#高效仲裁"),
    ("deep_research", "#深层科研"),
    ("temporal_research", "#时光科研"),
    ("research", "#科研"),
    ("alerts", "#提醒列表"),
    ("subs", "#订阅列表"),
    ("wiki", "#wiki 高斯"),
    ("translate", "#翻译 Mesa Prime"),
    ("unknown_is_query", "#绝路"),
]

# 这些命令依赖外部数据，输出「暂无/失败」也算跑通，只要不是空或异常
SOFT = {"news", "events", "deals", "storms", "world_alerts"}


def main() -> int:
    parser = argparse.ArgumentParser(description="全命令冒烟测试")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--only", default="", help="逗号分隔的命令名，只跑这些")
    parser.add_argument("--verbose", action="store_true", help="打印完整输出")
    parser.add_argument("--timeout", default="", help="覆盖 HTTP 超时秒数")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("smoke")

    cfg = load_config(args.config)
    if args.timeout:
        for section in ("warframe_market", "wiki", "worldstate", "arbi", "rotation"):
            part = cfg.setdefault(section, {})
            part["timeout"] = float(args.timeout)
    # 注意：adapt() 返回的是「需要调整的模块」，看链路要用探活结果本身
    probed = netadapt.load_cache(cfg) or netadapt.probe(cfg, log=log)
    print("网络适配：%s" % netadapt.summary(cfg, probed))

    app = BotApp(cfg, logger=log, config_path=args.config)
    ctx = Context(user_id="10001", group_id="20002", is_group=True, prefix="#")

    only = {item.strip() for item in args.only.split(",") if item.strip()}
    cases = [c for c in CASES if not only or c[0] in only]

    ok = empty = fail = 0
    failures: List[str] = []
    for name, text in cases:
        started = time.time()
        try:
            replies = app.router.handle(text, ctx)
        except Exception:  # noqa: BLE001
            fail += 1
            failures.append("%s（%s）异常：\n%s" % (name, text, traceback.format_exc()))
            print("[FAIL] %-22s %s  %.1fs" % (name, text, time.time() - started))
            continue
        if not replies:
            status, detail = "EMPTY", "（无回复）"
        else:
            parts = []
            image_bytes = 0
            for reply in replies:
                parts.append(reply.text or "")
                if reply.image is not None:
                    try:
                        image_bytes += len(reply.image())
                    except Exception as exc:  # noqa: BLE001
                        failures.append("%s 图片渲染异常：%r" % (name, exc))
            body = "\n".join(p for p in parts if p).strip()
            if not body and not image_bytes:
                status, detail = "EMPTY", "（空文本且无图）"
            else:
                status = "OK"
                detail = "%d 字" % len(body)
                if image_bytes:
                    detail += " + 图片 %.0fKB" % (image_bytes / 1024.0)
        if status == "OK":
            ok += 1
        else:
            empty += 1
            failures.append("%s（%s）：%s" % (name, text, detail))
        print("[%-5s] %-22s %-16s %s  %.1fs"
              % (status, name, text, detail, time.time() - started))
        if args.verbose:
            for reply in replies:
                if reply.text:
                    print("        " + reply.text.replace("\n", "\n        "))

    print("\n合计 %d 条：OK %d / EMPTY %d / FAIL %d" % (len(cases), ok, empty, fail))
    if failures:
        print("\n--- 问题明细 ---")
        for line in failures:
            print(line)
    return 0 if (empty == 0 and fail == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
