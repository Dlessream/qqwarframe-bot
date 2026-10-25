#!/usr/bin/env bash
# 带版本号构建，保证 Web「检查更新」能识别本地提交。
set -euo pipefail
cd "$(dirname "$0")/.."

if git rev-parse HEAD >/dev/null 2>&1; then
    BUILD_COMMIT="$(git rev-parse HEAD)"
elif [ -s .build_commit ]; then
    BUILD_COMMIT="$(tr -d '[:space:]' < .build_commit)"
else
    BUILD_COMMIT="unknown"
fi
export BUILD_COMMIT

echo "构建版本：${BUILD_COMMIT}"
exec docker compose -f docker-compose.full.yml build "$@"
