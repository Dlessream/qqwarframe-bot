#!/usr/bin/env bash
# 无 systemd 时的常驻管理（不依赖 screen / tmux / supervisor）。
#
# 用法：
#   ./deploy/daemon.sh start     后台启动（崩溃 5 秒后自动拉起）
#   ./deploy/daemon.sh stop      停止
#   ./deploy/daemon.sh restart   重启
#   ./deploy/daemon.sh status    查看运行状态与最近日志
#   ./deploy/daemon.sh logs      实时跟随日志（Ctrl+C 退出，不会杀掉机器人）
#   ./deploy/daemon.sh run       前台运行（调试用）
#
# 环境变量：
#   PYTHON       指定解释器（默认 .venv/bin/python，其次 python3）
#   QQWF_ARGS    传给 run.py 的额外参数，例如 "--config /opt/qqwf/config.json"
#   NO_WATCH=1   启动时不带自动拉起（用于一次性调试）
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

PID_FILE="$ROOT/run/bot.pid"
LOG_FILE="$ROOT/logs/daemon.log"
RUN_ARGS="${QQWF_ARGS:-}"

if [ -x "$ROOT/.venv/bin/python" ]; then
    PY="$ROOT/.venv/bin/python"
elif [ -n "${PYTHON:-}" ] && command -v "$PYTHON" >/dev/null 2>&1; then
    PY="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    echo "未找到 python3，请先运行 ./deploy/baremetal.sh 或 ./install.sh" >&2
    exit 1
fi

mkdir -p "$ROOT/run" "$ROOT/logs"

alive() {
    [ -f "$PID_FILE" ] || return 1
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

pid_of() {
    cat "$PID_FILE" 2>/dev/null || true
}

do_start() {
    if alive; then
        echo "机器人已在运行（pid $(pid_of)）。"
        echo "  重启请用：./deploy/daemon.sh restart"
        return 0
    fi
    rm -f "$PID_FILE"
    local cmd
    if [ "${NO_WATCH:-0}" = "1" ]; then
        cmd="exec \"$PY\" run.py $RUN_ARGS"
    else
        # 崩溃自动拉起：退出后等 5 秒重新运行
        cmd="while true; do \"$PY\" run.py $RUN_ARGS; code=\$?; \
echo \"[\$(date '+%F %T')] 进程退出（码 \$code），5 秒后自动重启\" >> \"$LOG_FILE\"; \
sleep 5; done"
    fi
    # setsid 让进程脱离当前终端，关闭 SSH 也不会被杀
    setsid nohup bash -c "$cmd" >>"$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2
    if alive; then
        echo "已启动（pid $pid），日志：$LOG_FILE"
    else
        echo "启动失败，请查看日志尾部："
        tail -n 20 "$LOG_FILE" >&2 || true
        return 1
    fi
}

do_stop() {
    if ! alive; then
        echo "机器人未在运行。"
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(pid_of)"
    # setsid 后 pid 就是进程组 id，负号可一次带走子进程
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    for _ in $(seq 1 15); do
        alive || break
        sleep 1
    done
    if alive; then
        echo "未响应 TERM，发送 KILL…"
        kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        sleep 1
    fi
    rm -f "$PID_FILE"
    echo "已停止。"
}

do_status() {
    if alive; then
        echo "运行中：pid $(pid_of)，解释器 $PY"
        echo "日志尾部："
        tail -n 5 "$LOG_FILE" 2>/dev/null || true
    else
        echo "未运行。"
        [ -f "$LOG_FILE" ] && echo "最后一次日志尾部：" && tail -n 5 "$LOG_FILE"
        return 1
    fi
}

case "${1:-}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart)
        alive && do_stop || true
        do_start
        ;;
    status)  do_status ;;
    logs)
        if [ ! -f "$LOG_FILE" ]; then
            echo "还没有日志文件，先启动一次。" >&2
            exit 1
        fi
        tail -n "${2:-50}" -f "$LOG_FILE"
        ;;
    run)
        exec "$PY" run.py $RUN_ARGS
        ;;
    *)
        sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
