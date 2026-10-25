#!/usr/bin/env bash
# 一键部署：Xray（你的 VLESS 节点）+ NapCat + 机器人
# 用法：sudo ./deploy/oneclick.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "请用 root 运行：sudo ./deploy/oneclick.sh" >&2
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
        echo "无法自动安装 Docker，请手动安装后重试。" >&2
        exit 1
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
    echo "Docker 不可用，请检查 systemctl status docker" >&2
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
else
    echo "缺少 docker compose，请安装 docker-compose-plugin。" >&2
    exit 1
fi

# ------------------------------------------------------------------ 目录
mkdir -p config data logs napcat/qq napcat/config napcat/plugins xray

# ------------------------------------------------------------------ .env
if [ ! -f .env ]; then
    echo "[2/5] 生成 .env（WebUI 密码等）"
    if command -v openssl >/dev/null 2>&1; then
        WEBUI_TOKEN="$(openssl rand -hex 12)"
        WEB_ADMIN_TOKEN="$(openssl rand -hex 12)"
    else
        WEBUI_TOKEN="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        WEB_ADMIN_TOKEN="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    BUILD_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
    cat > .env <<EOF
NAPCAT_WEBUI_TOKEN=$WEBUI_TOKEN
NAPCAT_WS_TOKEN=
WEB_ADMIN_TOKEN=${WEB_ADMIN_TOKEN:-}
WEB_ADMIN_BIND=${WEB_ADMIN_BIND:-127.0.0.1}
WEB_ADMIN_PORT=${WEB_ADMIN_PORT:-8080}
BUILD_COMMIT=${BUILD_COMMIT:-}
WEBUI_BIND=${WEBUI_BIND:-127.0.0.1}
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

: "${NAPCAT_WEBUI_TOKEN:? .env 缺少 NAPCAT_WEBUI_TOKEN，请删除 .env 后重新运行}"

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
 1) 打开 NapCat WebUI：
    本机浏览器： http://127.0.0.1:6099/webui
    远程服务器： ssh -L 6099:127.0.0.1:6099 root@$IP
                然后浏览器打开 http://127.0.0.1:6099/webui

    WebUI 密码（Token）：$NAPCAT_WEBUI_TOKEN

 2) 扫码登录 QQ 小号。

 3) 确认 NapCat 的 WebSocket 服务器已监听 3001。
    如果 WebUI 里设置了 WS Token，把它填到 .env：
        NAPCAT_WS_TOKEN=你的token
    然后重启机器人：
        ${COMPOSE[*]} -f docker-compose.full.yml up -d bot

 4) 查看机器人日志：
        ${COMPOSE[*]} -f docker-compose.full.yml logs -f bot

 5) 在群里发 #帮助 测试。

 代理：xray 容器使用你的 VLESS 节点，机器人通过 http://xray:10809 访问市场/维基。
 节点配置在 xray/config.json（权限 600），请勿把整个目录分享给别人。
==============================================
EOF
