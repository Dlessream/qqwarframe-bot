#!/usr/bin/env bash
# 一键部署：Xray（你的 VLESS 节点）+ QQ 官方机器人
# 用法：
#   sudo ./deploy/oneclick.sh                 # Docker 方式（官方机器人 + xray）
#   sudo ./deploy/oneclick.sh --bare          # 裸机方式（不用 Docker，也不用 screen）
# 装不上 Docker 时会自动降级为裸机方式。
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# ------------------------------------------------------------ 模式选择
BARE=0
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --bare|--baremetal|--no-docker) BARE=1; shift ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
if [ "$BARE" -eq 1 ]; then
    echo "已指定 --bare：改用裸机部署（无 Docker / 无 screen）。"
    exec "$ROOT/deploy/baremetal.sh" --dir "$ROOT" "${ARGS[@]}"
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 运行：sudo ./deploy/oneclick.sh" >&2
    echo "（不用 root 也可以跑裸机方式：./deploy/baremetal.sh）" >&2
    exit 1
fi

echo "=============================================="
echo " QQ Warframe 机器人 · 一键部署"
echo " 目录：$ROOT"
echo "=============================================="

# ------------------------------------------------------------------ Docker
if ! command -v docker >/dev/null 2>&1; then
    echo "[1/5] 未检测到 Docker，尝试安装..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y docker.io docker-compose-v2 \
            || apt-get install -y docker.io docker-compose-plugin \
            || apt-get install -y docker.io docker-compose || true
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y docker docker-compose-plugin || dnf install -y docker docker-compose || true
    elif command -v yum >/dev/null 2>&1; then
        yum install -y docker docker-compose-plugin || yum install -y docker docker-compose || true
    else
        echo "无法自动安装 Docker，自动降级为裸机部署（无 Docker / 无 screen）…"
        exec "$ROOT/deploy/baremetal.sh" --dir "$ROOT" "${ARGS[@]}"
    fi
    systemctl enable --now docker >/dev/null 2>&1 || true
else
    echo "[1/5] Docker 已安装"
fi

if ! docker info >/dev/null 2>&1; then
    echo "Docker 没有运行，尝试启动..."
    systemctl start docker >/dev/null 2>&1 || true
fi
if ! docker info >/dev/null 2>&1; then
    echo "Docker 守护进程不可用，自动降级为裸机部署（无 Docker / 无 screen）…"
    exec "$ROOT/deploy/baremetal.sh" --dir "$ROOT" "${ARGS[@]}"
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    echo "缺少 docker compose，自动降级为裸机部署（无 Docker / 无 screen）…"
    exec "$ROOT/deploy/baremetal.sh" --dir "$ROOT" "${ARGS[@]}"
fi

# ------------------------------------------------------------------ 目录
mkdir -p config data logs xray

# ------------------------------------------------------------------ .env
if [ ! -f .env ]; then
    echo "[2/5] 生成 .env（官方机器人票据与 Web 控制台令牌）"
    if command -v openssl >/dev/null 2>&1; then
        WEB_ADMIN_TOKEN="$(openssl rand -hex 12)"
    else
        WEB_ADMIN_TOKEN="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    BUILD_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
    cat > .env <<EOF
# QQ 开放平台机器人：在 q.qq.com 创建机器人后填入 AppID / AppSecret
QQWF_APP_ID=
QQWF_APP_SECRET=
# websocket = 我方主动连官方网关（默认）；webhook = 官方推送到我方可信 HTTPS 回调
QQWF_TRANSPORT=websocket
QQWF_QQBOT_ENV=prod
QQWF_CALLBACK_PATH=/qq/callback
QQWF_SELF_ID=
WEB_ADMIN_TOKEN=${WEB_ADMIN_TOKEN:-}
WEB_ADMIN_BIND=${WEB_ADMIN_BIND:-127.0.0.1}
WEB_ADMIN_PORT=${WEB_ADMIN_PORT:-8080}
BUILD_COMMIT=${BUILD_COMMIT:-}
PIP_INDEX_URL=${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}
DOCKER_MIRRORS=${DOCKER_MIRRORS:-https://docker.m.daocloud.io,https://docker.1ms.run}
APT_MIRROR=${APT_MIRROR:-mirrors.tuna.tsinghua.edu.cn}
EOF
    chmod 600 .env
else
    echo "[2/5] 复用已有 .env"
fi

# shellcheck disable=SC1091
. ./.env

if [ -z "${WEB_ADMIN_TOKEN:-}" ]; then
    echo "警告：.env 未设置 WEB_ADMIN_TOKEN，Web 控制台的管理接口将拒绝访问。" >&2
fi
if [ -z "${QQWF_APP_ID:-}" ] || [ -z "${QQWF_APP_SECRET:-}" ]; then
    echo "警告：.env 未设置 QQWF_APP_ID / QQWF_APP_SECRET，机器人不会连接 QQ 官方网关。" >&2
    echo "      可现在编辑 .env 后重新运行本脚本，或部署后在 Web 控制台填写。" >&2
fi

# ------------------------------------------------------------------ Docker 国内镜像
configure_docker_mirror() {
    local mirrors="${DOCKER_MIRRORS:-}"
    [ -n "$mirrors" ] || return 0
    if ! command -v python3 >/dev/null 2>&1; then
        echo "未找到 python3，跳过 Docker 镜像加速配置" >&2
        return 0
    fi
    mkdir -p /etc/docker
    python3 - "$mirrors" <<'PY' || return 1
import json
import os
import sys

path = "/etc/docker/daemon.json"
data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        print("daemon.json 不是合法 JSON，跳过镜像配置：%s" % exc, file=sys.stderr)
        raise SystemExit(1)
mirrors = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
current = data.get("registry-mirrors") or []
for item in mirrors:
    if item not in current:
        current.append(item)
data["registry-mirrors"] = current
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.replace(tmp, path)
print("Docker registry-mirrors: %s" % ", ".join(current))
PY
    systemctl restart docker >/dev/null 2>&1 || true
    sleep 2
}

echo "[2.5/5] 配置 Docker 国内镜像加速"
configure_docker_mirror || echo "Docker 镜像加速配置失败，将继续使用默认源" >&2

# ------------------------------------------------------------------ Xray
if [ ! -f xray/config.json ]; then
    echo "[3/5] 生成 Xray 配置"
    if [ -f deploy/vless.txt ]; then
        URI_FILE="deploy/vless.txt"
        if ! command -v python3 >/dev/null 2>&1; then
            echo "需要 python3 来把 VLESS 链接转成 Xray 配置。" >&2
            exit 1
        fi
        python3 deploy/vless_to_xray.py --file "$URI_FILE" --output xray/config.json
    else
        read -r -p "请输入 VLESS 节点链接: " VLESS_URI
        python3 deploy/vless_to_xray.py "$VLESS_URI" --output xray/config.json
    fi
else
    echo "[3/5] 复用已有 xray/config.json"
fi
chmod 600 xray/config.json 2>/dev/null || true

# ------------------------------------------------------------------ 启动
echo "[4/5] 构建并启动容器（首次会拉取镜像，可能需要几分钟）"
"${COMPOSE[@]}" -f docker-compose.full.yml up -d --build

echo "[5/5] 等待服务就绪..."
sleep 5

IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -n "${IP:-}" ] || IP="服务器IP"

cat <<EOF

==============================================
 部署完成
==============================================
 1) 在 QQ 开放平台（https://q.qq.com/）创建机器人，拿到 AppID / AppSecret。
    写入 .env：
        QQWF_APP_ID=你的AppID
        QQWF_APP_SECRET=你的AppSecret
    默认走 WebSocket 反向连接（QQWF_TRANSPORT=websocket），无需公网回调地址。
    改完重启：
        ${COMPOSE[*]} -f docker-compose.full.yml up -d bot

 2) Webhook 模式（可选）：
    .env 里设 QQWF_TRANSPORT=webhook，回调地址为
        https://你的域名:443\${QQWF_CALLBACK_PATH:-/qq/callback}
    官方回调端口只接受 80/443/8080/8443；用反代把 443 转到本机
    \${WEB_ADMIN_PORT:-8080}，并把 WEB_ADMIN_BIND 调整为 0.0.0.0 或反代来源。

 3) 打开 Web 控制台：
    远程服务器：ssh -L 8080:127.0.0.1:8080 root@$IP
    然后浏览器打开 http://127.0.0.1:8080，令牌为 .env 的 WEB_ADMIN_TOKEN。
    「QQ 官方机器人」面板可改票据、看事件日志、调试回调、发测试消息。

 4) 查看机器人日志：
        ${COMPOSE[*]} -f docker-compose.full.yml logs -f bot

 5) 在群里 @机器人发 #帮助 测试（群聊默认只处理 @机器人的消息）。
    群主需开启「接收全部消息」才能裸词唤醒，开启「允许主动发送」才能主动推送。

 代理：xray 容器使用你的 VLESS 节点，机器人通过 http://xray:10809 访问市场/维基。
 节点配置在 xray/config.json（权限 600），请勿把整个目录分享给别人。
==============================================
EOF
