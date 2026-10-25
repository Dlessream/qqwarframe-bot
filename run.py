#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQ Warframe 查询机器人启动入口。

用法：
    python run.py                  # 读取 config.json 并启动
    python run.py --check          # 只做联网自检，不连接 QQ
    python run.py --config my.json # 指定配置文件
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from bot import __version__  # noqa: E402
from bot.app import BotApp  # noqa: E402
from bot.config import ConfigError, ensure_config, load_config, redact  # noqa: E402
from bot.log import setup_logging  # noqa: E402

BANNER = """============================================
 QQ Warframe 查询机器人 v{version}
 OneBot : {ws_url}
 Token  : {token}
 平台   : {platform} | 语言 {language}
 代理   : {proxy}
 图片   : {image}
 前缀   : {prefixes}
============================================"""


def _proxy_listening(proxy_url: str) -> bool:
    """本机代理端口是否在监听（只用于启动提示，不阻断运行）。"""
    try:
        parsed = urllib.parse.urlsplit(proxy_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        sock = socket.create_connection((host, port), timeout=0.4)
        sock.close()
        return True
    except OSError:
        return False


def _print_config_hint(path: str) -> None:
    print("已生成配置文件：%s" % path)
    print("请先用记事本打开它，确认 OneBot 的 WebSocket 地址和 token，然后重新运行：")
    print("    python run.py")
    print("")
    print("如果还没有 QQ 机器人框架，推荐 NapCatQQ：")
    print("    1. 安装并登录 NapCat（QQ 机器人框架）")
    print("    2. 打开「网络配置」，新增 WebSocket 服务器，端口 3001")
    print("    3. 把上面的 ws_url / access_token 填进 config.json")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QQ Warframe 查询机器人")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--check", action="store_true", help="联网自检后退出")
    parser.add_argument("--version", action="store_true", help="显示版本号")
    args = parser.parse_args(argv)

    if args.version:
        print("QQ Warframe Bot %s" % __version__)
        return 0

    config_path = args.config
    if not os.path.isabs(config_path):
        # 相对路径按项目目录解析，保证从任何位置启动都能找到同一份配置
        config_path = os.path.abspath(os.path.join(BASE_DIR, config_path))

    os.chdir(BASE_DIR)
    example = os.path.join(BASE_DIR, "config.example.json")

    if args.check:
        from selftest import run_selftest
        return run_selftest(config_path)

    try:
        if ensure_config(config_path, example):
            _print_config_hint(config_path)
            return 0
        config = load_config(config_path)
    except ConfigError as exc:
        print("配置有误：%s" % exc)
        print("可以对照 config.example.json 检查，或删掉 config.json 重新生成。")
        return 2

    logger = setup_logging(config["log"]["level"], config["log"]["file"])
    logger.info("QQ Warframe Bot v%s 启动", __version__)
    from bot import imagecard

    image_cfg = config.get("image", {})
    imagecard.configure(str(image_cfg.get("font", "")),
                        str(image_cfg.get("font_bold", "")))
    proxy = config["warframe_market"].get("proxy") or "(系统代理)"
    print(BANNER.format(
        version=__version__,
        ws_url=config["onebot"]["ws_url"],
        token=redact(config["onebot"]["access_token"]),
        platform=config["warframe_market"]["platform"],
        language=config["warframe_market"]["language"],
        proxy=proxy,
        image="开启" if imagecard.available() else "关闭（缺少 Pillow/字体，将发文字）",
        prefixes=" ".join(config["behavior"]["prefixes"]),
    ))
    if proxy.startswith("http://127.0.0.1") or proxy.startswith("http://localhost"):
        if not _proxy_listening(proxy):
            logger.warning("代理 %s 端口没有在监听：请启动 v2rayN，"
                           "或在 config.json 里修改 proxy（也可填 none 直连）", proxy)
    logger.info("提示：在群里发送「%s帮助」查看指令", config["behavior"]["prefixes"][0])

    app = BotApp(config, logger=logger)
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
