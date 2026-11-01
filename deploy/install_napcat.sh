#!/usr/bin/env bash
# 安装 NapCat（QQ 协议端）的 Shell 版 —— 不需要 Docker。
# 官方文档：https://napneko.github.io/guide/boot/Shell
#
# 用法：
#   ./deploy/install_napcat.sh
#   NAPCAT_INSTALL_URL=... ./deploy/install_napcat.sh   # 自定义安装脚本地址
#
# 装好后需要手动做两件事（脚本会提示）：
#   1) 浏览器打开 WebUI 扫码登录 QQ 小号
#   2) 「网络配置」里添加 WebSocket 服务器，端口 3001
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

OFFICIAL="${NAPCAT_INSTALL_URL:-https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh}"
MIRROR="https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh"

if ! command -v curl >/dev/null 2>&1; then
    echo "需要 curl，请先安装（Debian/Ubuntu: sudo apt install -y curl）" >&2
    exit 1
fi

echo "=============================================="
echo " 安装 NapCat（Shell 版，非 Docker）"
echo "=============================================="

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if curl -fsSL --connect-timeout 20 --max-time 300 -o "$TMP/napcat.sh" "$OFFICIAL"; then
    echo "已下载官方安装脚本"
elif curl -fsSL --connect-timeout 20 --max-time 300 -o "$TMP/napcat.sh" "$MIRROR"; then
    echo "官方源不通，已使用国内镜像"
else
    echo "两个源都下载失败，请手动执行："
    echo "  curl -fL -o napcat.sh $OFFICIAL && bash napcat.sh --docker n --cli y"
    exit 1
fi

echo ""
echo "开始安装（安装到 ~/Napcat，过程有交互提示）…"
echo "----------------------------------------------"
( cd "$TMP" && bash napcat.sh --docker n --cli y --github-proxy 0 ) \
    || { echo "NapCat 安装未成功完成，可稍后重跑本脚本。" >&2; }

IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -n "${IP:-}" ] || IP="服务器IP"

cat <<EOF

----------------------------------------------
 接下来（在 NapCat 侧）：
   1) 浏览器打开 http://127.0.0.1:6099/webui
      远程服务器：ssh -L 6099:127.0.0.1:6099 root@$IP
   2) 扫码登录 QQ 小号
   3) 「网络配置」→ 添加 WebSocket 服务器，端口 3001
   4) 若设置了 WS Token，写进机器人配置：
      cd $ROOT && python3 deploy/setup_config.py --config config.json --token 你的token
      sudo systemctl restart qqwarframe-bot     # systemd 方式
      # 或：./deploy/daemon.sh restart           # 守护脚本方式
   5) 群里发 #帮助
----------------------------------------------
EOF
