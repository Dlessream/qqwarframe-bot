#!/bin/sh
# Docker 入口：首次启动时生成 config.json，然后启动机器人
set -e

CONFIG="${QQWF_CONFIG:-/app/config/config.json}"

mkdir -p "$(dirname "$CONFIG")" /app/data /app/logs

# 挂载空 data 目录时，把镜像里的外号表补回去
if [ ! -f /app/data/aliases.json ] && [ -f /app/default-aliases.json ]; then
    cp /app/default-aliases.json /app/data/aliases.json
fi

if [ ! -f "$CONFIG" ]; then
    cp /app/config.example.json "$CONFIG"
    echo "已生成配置文件：$CONFIG"
    echo "可以在宿主机 ./config/config.json 里修改，或用 QQWF_* 环境变量覆盖。"
fi

exec python /app/run.py --config "$CONFIG"
