#!/usr/bin/env bash
# 拉取源码后重新构建并重启；没有 .git 的发布包会跳过 pull。
set -euo pipefail
cd "$(dirname "$0")/.."

if git rev-parse HEAD >/dev/null 2>&1; then
    echo "更新源码…"
    git pull --ff-only
else
    echo "未检测到 .git，跳过 git pull（发布包模式）。"
fi

./deploy/build.sh
docker compose -f docker-compose.full.yml up -d
echo "完成。"
