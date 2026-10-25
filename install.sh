#!/usr/bin/env bash
# 一键安装（创建虚拟环境 + 安装 Pillow + 生成配置）
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if ! command -v "$PY" >/dev/null 2>&1; then
    echo "未找到 $PY，请先安装 Python 3.8+" >&2
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "创建虚拟环境 .venv ..."
    if ! "$PY" -m venv .venv; then
        echo "创建虚拟环境失败。Debian/Ubuntu 请先安装：sudo apt install python3-venv" >&2
        exit 1
    fi
fi

# shellcheck disable=SC1091
. .venv/bin/activate
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
python -m pip install --upgrade pip -i "$PIP_INDEX_URL" >/dev/null
echo "安装 Pillow ..."
pip install -i "$PIP_INDEX_URL" -r requirements.txt

if command -v fc-list >/dev/null 2>&1; then
    if ! fc-list :lang=zh 2>/dev/null | grep -q .; then
        echo "提示：未检测到中文字体，图片会退回文字。"
        echo "      Debian/Ubuntu: sudo apt install -y fonts-noto-cjk"
        echo "      Fedora:        sudo dnf install -y google-noto-sans-cjk-fonts"
    fi
fi

if [ ! -f "config.json" ]; then
    cp config.example.json config.json
    echo "已生成 config.json，请先填写 OneBot 的 ws_url / access_token。"
fi

echo ""
echo "安装完成。下一步："
echo "  1. 编辑 config.json（ws_url、access_token、proxy）"
echo "  2. ./check.sh    # 联网自检"
echo "  3. ./start.sh    # 启动机器人"
