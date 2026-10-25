# Linux 部署速查

四种方式任选一种。完整说明（配置项、指令、常见问题）见 [README.md](README.md)。

---

## 0. 一键部署（NapCat + Xray + 机器人，推荐）

要求：全新 Linux 服务器（Debian/Ubuntu/Fedora 等）、root、能拉 Docker 镜像。

```bash
tar -xzf qqwarframe-bot-linux.tar.gz
cd qqwarframe-bot
sudo ./deploy/oneclick.sh
```

脚本会自动：

1. 安装/启动 Docker（已装则跳过）
2. 生成 `.env`（NapCat WebUI 随机密码）
3. 配置 Docker 国内镜像加速（daocloud / 1ms）
4. 用 `deploy/vless.txt` 里的 VLESS 节点生成 `xray/config.json`
5. 构建并启动 **napcat + xray + bot** 三个容器
6. 打印 WebUI 地址、密码和后续步骤

完成后：

- 打开 `http://127.0.0.1:6099/webui`（远程服务器用 SSH 隧道：
  `ssh -L 6099:127.0.0.1:6099 root@服务器IP`），输入脚本打印的密码。
- 扫码登录 QQ 小号。
- 确认「网络配置」里的 WebSocket 服务器监听 `3001`；若设置了 Token，
  把 Token 填到 `.env` 的 `NAPCAT_WS_TOKEN=`，再
  `docker compose -f docker-compose.full.yml up -d bot`。
- 看日志：`docker compose -f docker-compose.full.yml logs -f bot`。
- 打开 Web 控制台 `http://127.0.0.1:8080`，令牌在 `.env` 的
  `WEB_ADMIN_TOKEN`；远程服务器用 `ssh -L 8080:127.0.0.1:8080 root@服务器IP`。
- 控制台检测到 GitHub 新提交或本地 `bot/*.py` 变化时，会弹窗询问
  “是否更新/重启”，不会自动执行。
- 群里发 `#帮助`。

节点配置在 `xray/config.json`（权限 600），机器人通过 `http://xray:10809`
访问市场/维基/世界状态。**这个文件和 `deploy/vless.txt` 含节点凭据，
不要把整个目录或压缩包分享给别人。**

国内加速变量在 `.env`：

```text
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
DOCKER_MIRRORS=https://docker.m.daocloud.io,https://docker.1ms.run
```

想恢复官方源，分别改成 `https://pypi.org/simple`、`deb.debian.org`，并把
`DOCKER_MIRRORS` 留空后重建 `bot` 镜像。

换节点：

```bash
nano deploy/vless.txt          # 粘贴新的 vless:// 链接
python3 deploy/vless_to_xray.py --file deploy/vless.txt --output xray/config.json
docker compose -f docker-compose.full.yml restart xray
```

---

## A. 直接运行（不用 Docker）

```bash
# Debian / Ubuntu
sudo apt update
sudo apt install -y python3 python3-venv fonts-noto-cjk \
    tesseract-ocr tesseract-ocr-chi-sim

# Fedora
# sudo dnf install python3 python3-pip google-noto-sans-cjk-fonts \
#     tesseract tesseract-langpack-chi_sim
# Arch
# sudo pacman -S python python-pip noto-fonts-cjk

cd qqwarframe-bot
chmod +x install.sh start.sh check.sh
./install.sh
nano config.json        # 填 onebot.ws_url / access_token，确认 proxy
./check.sh
./start.sh
```

没有虚拟环境时也可以直接：

```bash
python3 -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple Pillow
python3 run.py
```

---

## B. Docker（推荐长期挂机）

```bash
cd qqwarframe-bot
mkdir -p config data logs
docker compose up -d --build
docker compose logs -f
```

更新版本：

```bash
docker compose -f docker-compose.full.yml build
docker compose -f docker-compose.full.yml up -d
```

首次启动会生成 `config/config.json`，在宿主机编辑后：

```bash
docker compose restart
```

默认 `network_mode: host`，所以容器里的 `127.0.0.1` 就是宿主机：
NapCat 的 `ws://127.0.0.1:3001` 和代理 `http://127.0.0.1:10809` 都不用改。

也可以不用配置文件，直接用环境变量：

```yaml
environment:
  QQWF_WS_URL: ws://127.0.0.1:3001
  QQWF_ACCESS_TOKEN: "你的token"
  QQWF_PROXY: http://127.0.0.1:10809
  QQWF_PLATFORM: pc
```

支持：`QQWF_WS_URL`、`QQWF_ACCESS_TOKEN`、`QQWF_PROXY`、`QQWF_WIKI_PROXY`、
`QQWF_WORLDSTATE_PROXY`、`QQWF_PLATFORM`、`QQWF_LANGUAGE`、`QQWF_IMAGE`、
`QQWF_ALERTS`、`QQWF_WORLDSTATE`、`QQWF_AUTO_UPDATE`、`QQWF_CACHE`、
`QQWF_RIVEN_DISPOSITION`、`QQWF_ARBI_PROXY`、`QQWF_FONT`、`QQWF_FONT_BOLD`、
`QQWF_LOG_LEVEL`。

---

## C. systemd（原生常驻）

```bash
sudo useradd -r -s /usr/sbin/nologin qqwarframe
sudo mkdir -p /opt/qqwarframe-bot
sudo cp -r . /opt/qqwarframe-bot
sudo chown -R qqwarframe:qqwarframe /opt/qqwarframe-bot

cd /opt/qqwarframe-bot
sudo -u qqwarframe ./install.sh
sudo -u qqwarframe nano config.json

sudo cp deploy/qqwarframe-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now qqwarframe-bot
sudo journalctl -u qqwarframe-bot -f
```

服务文件默认使用 `/opt/qqwarframe-bot/.venv/bin/python`；路径不同就改 `ExecStart`。

---

## NapCat

NapCat 官方提供 Linux / Docker 版本。Docker 常用：

```bash
docker run -d --name napcat --restart unless-stopped \
  --network host \
  -v "$PWD/napcat/config:/app/napcat/config" \
  mlikiowa/napcat-docker:latest
```

（镜像名和挂载路径以 NapCat 官方文档为准。）WebUI 默认 `6099`，登录后到
「网络配置」新建 **WebSocket 服务器**，端口 `3001`，Token 与 `config.json` 一致。

如果 NapCat 在另一台机器：`ws_url` 改成那台机器的 IP，并确保 3001 端口可达。

---

## 代理与字体

- 代理端口不是 10809 时，改 `config.json` 里三处 `proxy`（市场 / 维基 / 世界状态）。
- Clash 常见 `http://127.0.0.1:7890`；只给市场走代理时，其余填 `"none"`。
- 图片卡片需要中文字体：`fonts-noto-cjk`（Debian/Ubuntu）或
  `google-noto-sans-cjk-fonts`（Fedora）。Docker 镜像已内置。
- 紫卡截图 OCR 需要 `tesseract-ocr` + `tesseract-ocr-chi-sim`；
  Docker 镜像已内置，直接运行需要手动安装。
- 字体缺失时机器人不会崩，会自动退回文字；`check.sh` 会显示“图片渲染 FAIL”。

---

## 验证

```bash
./check.sh                              # 联网自检
python3 -m unittest discover -s tests -t .   # 303 个离线测试
```

自检里维基可能显示 `WARN`（Cloudflare/WAF 拦截），不影响市场、图片、提醒等功能。
































