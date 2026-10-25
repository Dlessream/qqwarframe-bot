#!/usr/bin/env python3
"""把 VLESS 分享链接转换成 Xray 客户端配置。

支持：
  - security=reality / tls / none
  - type=tcp / ws / grpc
  - flow=xtls-rprx-vision

用法：
    python3 deploy/vless_to_xray.py "vless://..." > xray/config.json
    python3 deploy/vless_to_xray.py --file deploy/vless.txt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit

DEFAULT_HTTP_PORT = 10809
DEFAULT_SOCKS_PORT = 10808


def parse_vless(uri: str, http_port: int = DEFAULT_HTTP_PORT,
                socks_port: int = DEFAULT_SOCKS_PORT,
                listen: str = "0.0.0.0") -> dict:
    uri = (uri or "").strip()
    if not uri.startswith("vless://"):
        raise ValueError("不是 vless:// 开头的链接")

    parts = urlsplit(uri)
    if not parts.username:
        raise ValueError("链接缺少 UUID")
    host = parts.hostname
    if not host:
        raise ValueError("链接缺少服务器地址")
    port = parts.port or 443

    query = parse_qs(parts.query, keep_blank_values=True)

    def get(name: str, default: str = "") -> str:
        values = query.get(name)
        return unquote(values[0]) if values else default

    security = get("security", "none").lower()
    network = get("type", "tcp").lower()
    encryption = get("encryption", "none") or "none"
    flow = get("flow", "")
    sni = get("sni", "") or get("peer", "")
    fingerprint = get("fp", "") or "chrome"
    public_key = get("pbk", "")
    short_id = get("sid", "")
    spider_x = get("spx", "/")

    user = {"id": parts.username, "encryption": encryption}
    if flow:
        user["flow"] = flow

    stream = {"network": network, "security": security}

    if security == "reality":
        if not public_key:
            raise ValueError("REALITY 链接缺少 pbk（公钥）")
        if not sni:
            raise ValueError("REALITY 链接缺少 sni")
        stream["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fingerprint,
            "publicKey": public_key,
            "shortId": short_id,
            "spiderX": spider_x or "/",
        }
    elif security == "tls":
        tls = {
            "serverName": sni or host,
            "fingerprint": fingerprint,
            "allowInsecure": False,
        }
        alpn = get("alpn", "")
        if alpn:
            tls["alpn"] = [item for item in alpn.split(",") if item]
        stream["tlsSettings"] = tls

    if network == "ws":
        ws = {"path": get("path", "/") or "/"}
        ws_host = get("host", "")
        if ws_host:
            ws["headers"] = {"Host": ws_host}
        stream["wsSettings"] = ws
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": get("serviceName", "")}
    elif network == "tcp":
        header_type = get("headerType", "none") or "none"
        if header_type != "none":
            stream["tcpSettings"] = {"header": {"type": header_type}}

    outbound = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [{
                "address": host,
                "port": port,
                "users": [user],
            }]
        },
        "streamSettings": stream,
    }

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "http-in",
                "listen": listen,
                "port": http_port,
                "protocol": "http",
                "settings": {},
            },
            {
                "tag": "socks-in",
                "listen": listen,
                "port": socks_port,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
            },
        ],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom", "settings": {}},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {
                    "type": "field",
                    "ip": ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                           "192.168.0.0/16", "::1/128", "fc00::/7"],
                    "outboundTag": "direct",
                }
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="VLESS 链接转 Xray 客户端配置")
    parser.add_argument("uri", nargs="?", help="vless:// 链接")
    parser.add_argument("--file", help="从文件读取链接（一行）")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    parser.add_argument("--socks-port", type=int, default=DEFAULT_SOCKS_PORT)
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--output", help="写入文件（UTF-8，权限 600）")
    args = parser.parse_args()

    uri = args.uri
    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            uri = handle.read().strip()
    if not uri:
        parser.error("请提供 vless:// 链接，或用 --file 指定文件")

    try:
        config = parse_vless(uri, http_port=args.http_port,
                             socks_port=args.socks_port, listen=args.listen)
    except ValueError as exc:
        print("解析失败：%s" % exc, file=sys.stderr)
        return 2

    text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        directory = os.path.dirname(os.path.abspath(args.output))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(args.output, 0o600)
        except OSError:
            pass
        print("已写入 %s" % args.output, file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
