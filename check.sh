#!/usr/bin/env bash
# Linux / macOS 联网自检脚本（不连接 QQ）
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "未找到 python3，请先安装 Python 3.8+" >&2
    exit 1
fi

exec "$PY" selftest.py "$@"
