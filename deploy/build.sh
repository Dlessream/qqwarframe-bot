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

# 同步写一份到 .build_commit：这样即使直接 `docker compose up -d --build`
# （不经过本脚本、不传 BUILD_COMMIT），镜像里也带着版本号。
if [ "$BUILD_COMMIT" != "unknown" ]; then
    printf '%s\n' "$BUILD_COMMIT" > .build_commit
fi

echo "构建版本：${BUILD_COMMIT}"
exec docker compose -f docker-compose.full.yml build "$@"
