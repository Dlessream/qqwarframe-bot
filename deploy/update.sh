#!/usr/bin/env bash
# 拉取源码后重启服务；没有 .git 的发布包会跳过 pull。
#
# 用法：
#   ./deploy/update.sh             自动识别部署方式（systemd / daemon / docker）
#   ./deploy/update.sh --bare      强制按裸机方式处理（顺带重装依赖）
#   ./deploy/update.sh --docker    强制按 Docker 方式处理
#   ./deploy/update.sh --no-start  只更新代码，不重启
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

MODE=auto
NO_START=0
while [ $# -gt 0 ]; do
    case "$1" in
        --bare|--baremetal) MODE=bare; shift ;;
        --docker)           MODE=docker; shift ;;
        --no-start)         NO_START=1; shift ;;
        *) shift ;;
    esac
done

if git rev-parse HEAD >/dev/null 2>&1; then
    echo "更新源码…"
    git pull --ff-only || { echo "git pull 失败，已停止（请先处理冲突）。" >&2; exit 1; }
else
    echo "未检测到 .git，跳过 git pull（发布包模式）。"
fi

# 自动识别部署方式
if [ "$MODE" = auto ]; then
    if [ -f /etc/systemd/system/qqwarframe-bot.service ] \
       && command -v systemctl >/dev/null 2>&1 \
       && systemctl list-unit-files 2>/dev/null | grep -q qqwarframe-bot; then
        MODE=systemd
    elif [ -f "$ROOT/run/bot.pid" ]; then
        MODE=daemon
    else
        MODE=docker
    fi
fi

reload_deps() {
    [ -x "$ROOT/.venv/bin/python" ] || return 0
    "$ROOT/.venv/bin/python" -m pip install -q -r requirements.txt || true
}

case "$MODE" in
    systemd)
        echo "部署方式：systemd"
        reload_deps
        if [ "$NO_START" -eq 1 ]; then echo "已跳过重启（--no-start）。"; exit 0; fi
        systemctl restart qqwarframe-bot 2>/dev/null || sudo systemctl restart qqwarframe-bot
        sleep 2
        systemctl --no-pager status qqwarframe-bot 2>/dev/null | head -n 6 || true
        ;;
    daemon)
        echo "部署方式：自带守护脚本（deploy/daemon.sh）"
        reload_deps
        if [ "$NO_START" -eq 1 ]; then echo "已跳过重启（--no-start）。"; exit 0; fi
        "$ROOT/deploy/daemon.sh" restart
        ;;
    bare)
        echo "部署方式：裸机（显式指定）"
        reload_deps
        if [ "$NO_START" -eq 1 ]; then echo "已跳过重启（--no-start）。"; exit 0; fi
        if [ -f /etc/systemd/system/qqwarframe-bot.service ] \
           && command -v systemctl >/dev/null 2>&1; then
            systemctl restart qqwarframe-bot 2>/dev/null || sudo systemctl restart qqwarframe-bot
        else
            "$ROOT/deploy/daemon.sh" restart
        fi
        ;;
    docker)
        echo "部署方式：Docker"
        ./deploy/build.sh
        docker compose -f docker-compose.full.yml up -d
        ;;
esac

echo "完成。"
