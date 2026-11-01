#!/usr/bin/env python3
"""按部署方式写入 config.json（供裸机部署脚本调用）。

不用 sed 硬替换，避免破坏 JSON 结构与中文。只改传入的项，其余保留。

用法示例：
    python3 deploy/setup_config.py --config config.json \
        --ws-url ws://127.0.0.1:3001 --token abc \
        --proxy http://127.0.0.1:10809 \
        --web-token xyz --web-port 8080 \
        --manual-command "systemctl restart qqwarframe-bot"
"""

import argparse
import json
import os
import shutil
import sys
from typing import Any, Dict, List, Optional

# 需要跟随 --proxy 一起改的代理字段（section, key）
PROXY_FIELDS = [
    ("warframe_market", "proxy"),
    ("wiki", "proxy"),
    ("worldstate", "proxy"),
]

# 这些默认就是 "none"，除非显式给 --proxy-all 才一起改
OPTIONAL_PROXY_FIELDS = [
    ("worldstate", "official_proxy"),
    ("worldstate", "oracle_proxy"),
    ("arbitration", "proxy"),
]


def load(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(os.path.abspath(path)),
                               "config.example.json")
        parent = os.path.dirname(os.path.abspath(path))
        example = os.path.join(parent, "config.example.json")
        if not os.path.exists(example):
            raise SystemExit("找不到 %s，也没有 config.example.json 可复制" % path)
        shutil.copyfile(example, path)
        print("已从 config.example.json 生成 %s" % path)
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit("解析 %s 失败：%s（请修复或删除后重跑）" % (path, exc))


def save(path: str, data: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def ensure_section(data: Dict[str, Any], name: str) -> Dict[str, Any]:
    section = data.get(name)
    if not isinstance(section, dict):
        section = {}
        data[name] = section
    return section


def apply(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    changed: List[str] = []

    if args.ws_url:
        onebot = ensure_section(data, "onebot")
        onebot["ws_url"] = args.ws_url
        changed.append("onebot.ws_url=%s" % args.ws_url)

    if args.token is not None:
        onebot = ensure_section(data, "onebot")
        onebot["access_token"] = args.token
        changed.append("onebot.access_token=%s" % ("(已设置)" if args.token else "(空)"))

    if args.proxy is not None:
        fields = list(PROXY_FIELDS)
        if args.proxy_all:
            fields += OPTIONAL_PROXY_FIELDS
        for section_name, key in fields:
            section = data.get(section_name)
            if not isinstance(section, dict):
                continue  # 配置里本来就没有这一段，不凭空造
            if key not in section:
                continue
            section[key] = args.proxy
            changed.append("%s.%s=%s" % (section_name, key, args.proxy or "(空=系统代理)"))

    if args.web_token is not None or args.web_enable is not None or args.web_port:
        web = ensure_section(data, "web")
        if args.web_enable is not None:
            web["enabled"] = bool(args.web_enable)
            changed.append("web.enabled=%s" % web["enabled"])
        if args.web_token is not None:
            web["token"] = args.web_token
            changed.append("web.token=%s" % ("(已设置)" if args.web_token else "(空)"))
        if args.web_port:
            web["port"] = int(args.web_port)
            changed.append("web.port=%s" % web["port"])
        if args.web_bind:
            web["host"] = args.web_bind
            changed.append("web.host=%s" % args.web_bind)

    if args.manual_command is not None:
        web = ensure_section(data, "web")
        update = web.get("update")
        if not isinstance(update, dict):
            update = {}
            web["update"] = update
        update["manual_command"] = args.manual_command
        changed.append("web.update.manual_command=%s" % (args.manual_command or "(空)"))

    if args.log_file:
        log = ensure_section(data, "log")
        log["file"] = args.log_file
        changed.append("log.file=%s" % args.log_file)

    return changed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="写入机器人配置")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument("--ws-url", dest="ws_url", default="", help="OneBot WebSocket 地址")
    parser.add_argument("--token", default=None, help="OneBot access_token")
    parser.add_argument("--proxy", default=None,
                        help="HTTP 代理；none 表示直连，空串表示跟随系统代理")
    parser.add_argument("--proxy-all", action="store_true",
                        help="连 worldstate.official_proxy / oracle_proxy / arbitration.proxy 一起改")
    parser.add_argument("--web-enable", dest="web_enable", default=None,
                        choices=["0", "1"], help="是否启用 Web 控制台")
    parser.add_argument("--web-token", dest="web_token", default=None, help="Web 控制台令牌")
    parser.add_argument("--web-port", dest="web_port", default="", help="Web 控制台端口")
    parser.add_argument("--web-bind", dest="web_bind", default="", help="Web 控制台监听地址")
    parser.add_argument("--manual-command", dest="manual_command", default=None,
                        help="控制台提示的手动更新/重启命令")
    parser.add_argument("--log-file", dest="log_file", default="", help="日志文件路径")
    args = parser.parse_args(argv)

    if args.web_enable is not None:
        args.web_enable = int(args.web_enable)

    data = load(args.config)
    changed = apply(data, args)
    if not changed:
        print("没有需要修改的配置项。")
        return 0
    save(args.config, data)
    for line in changed:
        print("  %s" % line)
    print("已写入 %s（共 %d 项）" % (args.config, len(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
