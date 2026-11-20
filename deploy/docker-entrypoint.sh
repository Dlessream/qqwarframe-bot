#!/bin/sh
# Docker 入口：首次启动时生成 config.json，然后启动机器人
set -e

CONFIG="${QQWF_CONFIG:-/app/config/config.json}"

mkdir -p "$(dirname "$CONFIG")" /app/data /app/logs

# 挂载宿主机 data 目录时，把镜像打包的静态数据补回去（只补缺失，不覆盖已修改的）。
# 部署时 data/ 通常不参与代码同步，新增的静态文件（遗物库、MOD 译名…）容易缺，
# 这里统一补齐；运行时 bot/datafiles.py 还有 builtin-data 兜底，双保险。
if [ -d /app/builtin-data ]; then
    for src in /app/builtin-data/*.json; do
        [ -f "$src" ] || continue
        name=$(basename "$src")
        if [ ! -f "/app/data/$name" ]; then
            cp "$src" "/app/data/$name" 2>/dev/null || true
            echo "已补回静态数据：data/$name"
        fi
    done
fi

# 兼容没有 builtin-data 的旧镜像：至少把外号表补回去
if [ ! -f /app/data/aliases.json ] && [ -f /app/default-aliases.json ]; then
    cp /app/default-aliases.json /app/data/aliases.json
fi

if [ ! -f "$CONFIG" ]; then
    cp /app/config.example.json "$CONFIG"
    echo "已生成配置文件：$CONFIG"
    echo "可以在宿主机 ./config/config.json 里修改，或用 QQWF_* 环境变量覆盖。"
fi

exec python /app/run.py --config "$CONFIG"
