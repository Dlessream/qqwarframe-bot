#!/usr/bin/env bash
# 裸机一键部署：不需要 Docker，也不需要 screen / tmux。
#
# 用法（普通用户或 root 都行，装系统包和注册 systemd 才需要 root）：
#   ./deploy/baremetal.sh                      安装到当前目录并注册常驻
#   sudo ./deploy/baremetal.sh --dir /opt/qqwarframe-bot
#   ./deploy/baremetal.sh --ws-url ws://127.0.0.1:3001 --token 你的token
#   ./deploy/baremetal.sh --proxy http://127.0.0.1:7890   # 用已有代理
#   ./deploy/baremetal.sh --proxy none                    # 全部直连
#   ./deploy/baremetal.sh --with-xray                     # 顺带装 xray-core 做本地代理
#   ./deploy/baremetal.sh --with-napcat                   # 顺带装 NapCat（Shell 版）
#   ./deploy/baremetal.sh --no-service                    # 只装依赖，不注册常驻
#   ./deploy/baremetal.sh --no-start                      # 注册但不立即启动
#
# 常用环境变量：
#   PIP_INDEX_URL       pip 源（默认清华）
#   XRAY_INSTALL_URL    xray-core 安装脚本地址
#   NAPCAT_INSTALL_URL  NapCat 安装脚本地址
#   RUN_USER            运行机器人服务的系统用户
set -uo pipefail

cd "$(dirname "$0")/.."
SRC="$(pwd)"

# ------------------------------------------------------------------ 参数
DIR="$SRC"
WS_URL=""
WS_TOKEN=""
PROXY=""
PROXY_ALL=0
WITH_XRAY=0
WITH_NAPCAT=0
NO_SERVICE=0
NO_START=0
VLESS_FILE=""
WEB_PORT="8080"
RUN_USER="${RUN_USER:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)            DIR="${2:?--dir 需要路径}"; shift 2 ;;
        --ws-url)         WS_URL="${2:?--ws-url 需要地址}"; shift 2 ;;
        --token)          WS_TOKEN="$2"; shift 2 ;;
        --proxy)          PROXY="$2"; shift 2 ;;
        --proxy-all)      PROXY_ALL=1; shift ;;
        --with-xray)      WITH_XRAY=1; shift ;;
        --vless-file)     VLESS_FILE="${2:?--vless-file 需要路径}"; shift 2 ;;
        --with-napcat)    WITH_NAPCAT=1; shift ;;
        --no-service)     NO_SERVICE=1; shift ;;
        --no-start)       NO_START=1; shift ;;
        --web-port)       WEB_PORT="${2:?--web-port 需要端口}"; shift 2 ;;
        --user)           RUN_USER="${2:?--user 需要用户名}"; shift 2 ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "未知参数：$1（--help 查看用法）" >&2; exit 1 ;;
    esac
done

PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1

echo "=============================================="
echo " QQ Warframe 机器人 · 裸机部署（无 Docker）"
echo " 目录：$DIR"
echo "=============================================="

# ------------------------------------------------------------------ 目录
if [ "$DIR" != "$SRC" ]; then
    echo "[1/6] 复制文件到 $DIR"
    mkdir -p "$DIR"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --exclude '.git' --exclude '.venv' --exclude 'run/' \
            "$SRC"/ "$DIR"/
    else
        (cd "$SRC" && tar --exclude=.git --exclude=.venv --exclude=run -cf - .) \
            | (cd "$DIR" && tar -xf -)
    fi
    cd "$DIR"
fi
ROOT="$(pwd)"
mkdir -p data logs run

# 从压缩包/git 检出时执行位可能丢失，这里统一补上
chmod +x "$ROOT"/deploy/*.sh "$ROOT"/install.sh "$ROOT"/start.sh \
    "$ROOT"/check.sh 2>/dev/null || true

# ------------------------------------------------------------------ 系统依赖
detect_pkg() {
    if command -v apt-get >/dev/null 2>&1; then echo apt
    elif command -v dnf >/dev/null 2>&1; then echo dnf
    elif command -v yum >/dev/null 2>&1; then echo yum
    elif command -v pacman >/dev/null 2>&1; then echo pacman
    elif command -v apk >/dev/null 2>&1; then echo apk
    else echo none; fi
}

install_system_deps() {
    local pm
    pm="$(detect_pkg)"
    [ "$pm" = none ] && { echo "未识别的包管理器，请手动安装 python3 / pip / 中文字体"; return 0; }
    echo "  包管理器：$pm"
    if [ "$IS_ROOT" -ne 1 ]; then
        echo "  非 root，跳过系统包安装（缺什么请自行安装）"
        return 0
    fi
    case "$pm" in
        apt)
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq || true
            apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates git \
                fonts-noto-cjk || apt-get install -y python3 python3-pip curl git || true
            ;;
        dnf)
            dnf install -y python3 python3-pip curl git google-noto-sans-cjk-fonts \
                || dnf install -y python3 python3-pip curl git || true
            ;;
        yum)
            yum install -y python3 python3-pip curl git || true
            ;;
        pacman)
            pacman -Sy --noconfirm python python-pip curl git noto-fonts-cjk \
                || pacman -Sy --noconfirm python python-pip curl git || true
            ;;
        apk)
            apk add --no-cache python3 py3-pip curl git font-noto-cjk || true
            ;;
    esac
}

echo "[1/6] 检查系统依赖"
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    echo "  未找到 python3，尝试安装…"
    install_system_deps
    command -v python3 >/dev/null 2>&1 && PY=python3
fi
[ -n "$PY" ] || { echo "仍然没有 python3，请先安装 Python 3.8+。" >&2; exit 1; }

ver="$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0.0)"
major="${ver%%.*}"
minor="${ver##*.}"
if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 8 ]; }; then
    echo "Python 版本过低（$ver），需要 3.8+。" >&2
    exit 1
fi
echo "  $PY $ver"

if command -v fc-list >/dev/null 2>&1; then
    if ! fc-list :lang=zh 2>/dev/null | grep -q .; then
        echo "  未检测到中文字体，卡片会退回文字模式，尝试安装…"
        if [ "$IS_ROOT" -eq 1 ]; then
            case "$(detect_pkg)" in
                apt)    apt-get install -y -qq fonts-noto-cjk || true ;;
                dnf)    dnf install -y google-noto-sans-cjk-fonts || true ;;
                pacman) pacman -Sy --noconfirm noto-fonts-cjk || true ;;
                apk)    apk add --no-cache font-noto-cjk || true ;;
            esac
        else
            echo "  请自行安装中文字体（Debian/Ubuntu: sudo apt install fonts-noto-cjk）"
        fi
    fi
fi

# ------------------------------------------------------------------ 虚拟环境
echo "[2/6] 准备 Python 环境"
USE_VENV=1
if [ ! -x "$ROOT/.venv/bin/python" ]; then
    if "$PY" -m venv "$ROOT/.venv" 2>/dev/null; then
        echo "  已创建虚拟环境 .venv"
    else
        echo "  创建虚拟环境失败（Debian/Ubuntu 需要 python3-venv），改用系统 Python"
        USE_VENV=0
    fi
else
    echo "  复用已有 .venv"
fi
if [ "$USE_VENV" -eq 1 ]; then
    PYBIN="$ROOT/.venv/bin/python"
else
    PYBIN="$(command -v "$PY")"
fi

echo "[3/6] 安装 Python 依赖"
if ! "$PYBIN" -m pip install --upgrade pip -i "$PIP_INDEX_URL" >/dev/null 2>&1; then
    "$PYBIN" -m pip install --upgrade pip >/dev/null 2>&1 || true
fi
if "$PYBIN" -m pip install -i "$PIP_INDEX_URL" -r requirements.txt; then
    echo "  依赖安装完成（源：$PIP_INDEX_URL）"
else
    echo "  清华源失败，回退官方源…"
    "$PYBIN" -m pip install -r requirements.txt \
        || "$PYBIN" -m pip install --break-system-packages -r requirements.txt \
        || { echo "依赖安装失败，请检查网络或 pip 源。" >&2; exit 1; }
fi

# ------------------------------------------------------------------ 代理
PROXY_VALUE="$PROXY"
if [ "$WITH_XRAY" -eq 1 ]; then
    echo "[4/6] 安装 xray-core（本地代理）"
    VLESS_FILE="${VLESS_FILE:-$ROOT/deploy/vless.txt}"
    if [ ! -f "$VLESS_FILE" ]; then
        echo "  未找到 $VLESS_FILE，跳过（可把 vless:// 链接写进该文件后重跑）"
    else
        if ! command -v xray >/dev/null 2>&1; then
            XRAY_URL="${XRAY_INSTALL_URL:-https://github.com/XTLS/Xray-install/raw/main/install-release.sh}"
            echo "  下载官方安装脚本：$XRAY_URL"
            if bash -c "$(curl -fsSL "$XRAY_URL")" @ install; then
                echo "  xray-core 安装完成"
            else
                echo "  xray-core 安装失败，可稍后手动安装："
                echo "    bash -c \"\$(curl -L $XRAY_URL)\" @ install"
            fi
        else
            echo "  已存在 xray: $(command -v xray)"
        fi
        "$PYBIN" deploy/vless_to_xray.py --file "$VLESS_FILE" \
            --listen 127.0.0.1 --output "$ROOT/xray-config.json" || {
            echo "  VLESS 解析失败，跳过代理配置" >&2
        }
        if [ -f "$ROOT/xray-config.json" ]; then
            chmod 600 "$ROOT/xray-config.json"
            if [ -d /usr/local/etc/xray ] && [ "$IS_ROOT" -eq 1 ]; then
                [ -f /usr/local/etc/xray/config.json ] \
                    && cp /usr/local/etc/xray/config.json /usr/local/etc/xray/config.json.bak
                cp "$ROOT/xray-config.json" /usr/local/etc/xray/config.json
                chmod 640 /usr/local/etc/xray/config.json || true
                if command -v systemctl >/dev/null 2>&1; then
                    systemctl restart xray >/dev/null 2>&1 || true
                    systemctl enable xray >/dev/null 2>&1 || true
                fi
                echo "  已写入 /usr/local/etc/xray/config.json 并重启 xray"
            else
                cp "$ROOT/xray-config.json" "${HOME}/xray-config.json" 2>/dev/null || true
                echo "  配置已生成：$ROOT/xray-config.json（非 root 请自行放到 xray 配置目录）"
            fi
            PROXY_VALUE="http://127.0.0.1:10809"
        fi
    fi
fi

# ------------------------------------------------------------------ 配置
echo "[5/6] 写入配置 config.json"
CFG_ARGS=(--config "$ROOT/config.json")
[ -n "$WS_URL" ] && CFG_ARGS+=(--ws-url "$WS_URL")
[ -n "$WS_TOKEN" ] && CFG_ARGS+=(--token "$WS_TOKEN")
if [ -n "$PROXY_VALUE" ]; then
    CFG_ARGS+=(--proxy "$PROXY_VALUE")
    [ "$PROXY_ALL" -eq 1 ] && CFG_ARGS+=(--proxy-all)
fi

WEB_TOKEN=""
if command -v openssl >/dev/null 2>&1; then
    WEB_TOKEN="$(openssl rand -hex 12 2>/dev/null || true)"
fi
[ -n "$WEB_TOKEN" ] || WEB_TOKEN="$($PYBIN -c 'import secrets;print(secrets.token_hex(12))' 2>/dev/null || true)"
CFG_ARGS+=(--web-enable 1 --web-token "$WEB_TOKEN" --web-port "$WEB_PORT" --web-bind 127.0.0.1)

"$PYBIN" deploy/setup_config.py "${CFG_ARGS[@]}" || {
    echo "配置写入失败。" >&2; exit 1; }
if [ -n "$PROXY_VALUE" ]; then
    "$PYBIN" deploy/setup_config.py --config "$ROOT/config.json" \
        --manual-command "" >/dev/null || true
fi

# ------------------------------------------------------------------ 常驻
echo "[6/6] 配置常驻方式"
HAVE_SYSTEMD=0
command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files >/dev/null 2>&1 && HAVE_SYSTEMD=1

SERVICE_MODE="daemon"
UNIT="/etc/systemd/system/qqwarframe-bot.service"

if [ "$NO_SERVICE" -eq 1 ]; then
    SERVICE_MODE="none"
    echo "  已跳过常驻配置（--no-service）"
elif [ "$HAVE_SYSTEMD" -eq 1 ] && [ "$IS_ROOT" -eq 1 ]; then
    if [ -z "$RUN_USER" ]; then
        RUN_USER="${SUDO_USER:-}"
        [ -n "$RUN_USER" ] && [ "$RUN_USER" = root ] && RUN_USER=""
    fi
    if [ -z "$RUN_USER" ]; then
        RUN_USER=qqwarframe
        if ! id -u qqwarframe >/dev/null 2>&1; then
            useradd -r -s /usr/sbin/nologin qqwarframe || true
        fi
        chown -R qqwarframe:qqwarframe "$ROOT" 2>/dev/null || true
    fi
    cat > "$UNIT" <<EOF
[Unit]
Description=QQ Warframe Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONIOENCODING=utf-8
ExecStart=$PYBIN $ROOT/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable qqwarframe-bot >/dev/null 2>&1 || true
    SERVICE_MODE="systemd"
    "$PYBIN" deploy/setup_config.py --config "$ROOT/config.json" \
        --manual-command "sudo systemctl restart qqwarframe-bot" >/dev/null || true
    echo "  已写入并启用 systemd 服务：$UNIT"
else
    if [ "$HAVE_SYSTEMD" -eq 1 ]; then
        echo "  非 root，跳过 systemd（可用 --user 级服务或下面的 daemon 方式）"
    else
        echo "  未检测到 systemd，改用自带守护脚本（不依赖 screen/tmux）"
    fi
    "$PYBIN" deploy/setup_config.py --config "$ROOT/config.json" \
        --manual-command "cd $ROOT && ./deploy/daemon.sh restart" >/dev/null || true
    echo "  守护脚本：$ROOT/deploy/daemon.sh"
fi

if [ "$SERVICE_MODE" = systemd ] && [ "$NO_START" -ne 1 ]; then
    systemctl restart qqwarframe-bot || systemctl start qqwarframe-bot
    sleep 2
    systemctl --no-pager status qqwarframe-bot | head -n 8 || true
elif [ "$SERVICE_MODE" = daemon ] && [ "$NO_START" -ne 1 ]; then
    "$ROOT/deploy/daemon.sh" start || true
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
[ -n "${IP:-}" ] || IP="服务器IP"

cat <<EOF

==============================================
 裸机部署完成（目录：$ROOT）
==============================================
EOF

case "$SERVICE_MODE" in
    systemd)
        cat <<EOF
 服务管理（systemd）：
   sudo systemctl status  qqwarframe-bot
   sudo systemctl restart qqwarframe-bot
   sudo journalctl -u qqwarframe-bot -f
EOF
        ;;
    daemon)
        cat <<EOF
 服务管理（自带守护脚本，无需 screen）：
   $ROOT/deploy/daemon.sh status
   $ROOT/deploy/daemon.sh restart
   $ROOT/deploy/daemon.sh logs
EOF
        ;;
    none)
        cat <<EOF
 手动启动：
   cd $ROOT && ./start.sh
 或后台常驻：
   cd $ROOT && ./deploy/daemon.sh start
EOF
        ;;
esac

cat <<EOF

 Web 控制台： http://127.0.0.1:$WEB_PORT
   令牌：$WEB_TOKEN
   远程服务器：ssh -L $WEB_PORT:127.0.0.1:$WEB_PORT root@$IP
   登录后在「控制台」改配置、看日志、重启服务。

 自检（不连 QQ）：
   cd $ROOT && ./check.sh

 配置文件： $ROOT/config.json
   需要确认 onebot.ws_url / access_token；代理当前为：
   ${PROXY_VALUE:-（未设置，启动时由网络适配自动探测）}

 手动更新：
   cd $ROOT && git pull && ./deploy/update.sh --bare
EOF

if [ "$WITH_NAPCAT" -eq 1 ]; then
    "$ROOT/deploy/install_napcat.sh" || true
else
    cat <<EOF

 还没有 NapCat（QQ 协议端）？两个办法：
   1) 裸机装 Shell 版：./deploy/baremetal.sh --with-napcat
   2) 手动：把 config.json 的 onebot.ws_url 指向你已有的 OneBot 实现
      （NapCat / Lagrange / go-cqhttp 均可，端口与 token 对上就行）
EOF
fi

echo ""
echo "完成。"
