(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    token: localStorage.getItem("qqwf_admin_token") || "",
    status: null,
    dismissedRemote: "",
    dismissedLocal: false,
    autoPrompted: false,
    timer: null,
    logTimer: null,
    modalAction: null,
  };

  function setText(id, value) {
    const node = $(id);
    if (node) node.textContent = value == null || value === "" ? "—" : String(value);
  }

  function api(path, options) {
    const opts = Object.assign({ method: "GET", headers: {} }, options || {});
    opts.headers = Object.assign({ "Accept": "application/json" }, opts.headers || {});
    if (state.token) opts.headers["X-Admin-Token"] = state.token;
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(path, opts).then(async (res) => {
      if (res.status === 401) {
        showLogin();
        throw new Error("UNAUTHORIZED");
      }
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.message || ("HTTP " + res.status));
      return data;
    });
  }

  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { node.hidden = true; }, 4200);
  }

  function showLogin() {
    const login = $("login");
    if (login) login.hidden = false;
  }

  function hideLogin() {
    const login = $("login");
    if (login) login.hidden = true;
    const error = $("loginError");
    if (error) error.hidden = true;
  }

  function formatUptime(seconds) {
    seconds = Math.max(0, Number(seconds || 0));
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    if (days) return days + "天 " + hours + "小时";
    if (hours) return hours + "小时 " + mins + "分";
    return mins + " 分钟";
  }

  function formatTime(ts) {
    if (!ts) return "—";
    const date = new Date(Number(ts) * 1000);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function shortHash(value) {
    return value ? String(value).slice(0, 7) : "—";
  }

  function updateStateLabel(update) {
    if (!update) return "—";
    if (update.available) return "有新版本";
    if (update.local_changed) return "代码已变化";
    if (update.state === "up_to_date") return "已是最新";
    if (update.state === "local_ahead") return "本地领先";
    if (update.state === "local_only") return "本地未推送";
    if (update.state === "diverged") return "历史分叉";
    if (update.state === "error") return "检查失败";
    return "未知";
  }

  function render(status) {
    state.status = status;
    const conn = status.onebot || {};
    setText("uptime", formatUptime(status.uptime));
    setText("version", status.version);
    setText("footerVersion", status.version);
    setText("wsState", conn.connected ? "已连接" : "未连接");
    setText("wsUrl", conn.url || "—");
    setText("itemCount", Number(status.market && status.market.items || 0).toLocaleString("zh-CN"));
    setText("rivenCount", Number(status.riven && status.riven.weapons || 0).toLocaleString("zh-CN"));
    setText("lastUpdate", status.updater && status.updater.last_run ? formatTime(status.updater.last_run) : "尚未运行");
    setText("alertCount", status.alerts == null ? "—" : status.alerts);
    const groups = status.groups || {};
    setText("groupPolicy", "白 " + Number(groups.whitelist || 0) +
      " / 黑 " + Number(groups.blacklist || 0));
    setText("imageState", status.image ? "可用" : "关闭");
    setText("ocrState", status.ocr ? "可用" : "未启用");
    setText("fontState", status.font ? "已加载" : "缺失");
    setText("ocrEngine", status.ocr ? "Tesseract" : "未安装");

    const pill = $("connPill");
    pill.classList.remove("pill-ok", "pill-bad", "pill-muted");
    if (conn.connected) {
      pill.classList.add("pill-ok");
      pill.innerHTML = '<i class="dot"></i>在线';
      setText("statusChip", "ONLINE");
    } else {
      pill.classList.add("pill-bad");
      pill.innerHTML = '<i class="dot"></i>离线';
      setText("statusChip", "OFFLINE");
    }

    const update = status.update || {};
    setText("localCommit", shortHash(update.local_commit));
    setText("remoteCommit", shortHash(update.remote_commit));
    setText("lastChecked", update.last_checked ? formatTime(update.last_checked) : "尚未检查");
    setText("updateState", updateStateLabel(update));

    const updateChip = $("updateChip");
    updateChip.textContent = update.available ? "UPDATE" : "CHECK";
    updateChip.className = "chip " + (update.available ? "chip-gold" : "chip-blue");

    renderUpdateBanner(update);
    const remotePrompt = update.available && update.remote_commit !== state.dismissedRemote;
    const localPrompt = update.local_changed && !state.dismissedLocal;
    if ((remotePrompt || localPrompt) && !state.autoPrompted) {
      state.autoPrompted = true;
      setTimeout(() => openModal(remotePrompt ? "remote" : "local"), 450);
    }
  }

  function renderUpdateBanner(update) {
    const banner = $("updateBanner");
    if (!update) { banner.hidden = true; return; }
    const remote = update.available && update.remote_commit !== state.dismissedRemote;
    const local = update.local_changed && !state.dismissedLocal;
    if (!remote && !local) { banner.hidden = true; return; }
    banner.hidden = false;
    if (remote) {
      setText("bannerTitle", "发现后端新版本");
      setText("bannerText", "远端 " + shortHash(update.remote_commit) + " · " +
        (update.remote_message || "有新提交") + "，是否更新？");
      $("bannerAction").dataset.kind = "remote";
    } else {
      setText("bannerTitle", "检测到后端文件变化");
      setText("bannerText", "代码已修改，需要重启服务才能加载新版本。");
      $("bannerAction").dataset.kind = "local";
    }
  }

  function openModal(kind) {
    const update = (state.status && state.status.update) || {};
    const modal = $("modal");
    const command = $("modalCommand");
    const confirm = $("modalConfirm");
    command.hidden = true;
    command.textContent = "";
    confirm.disabled = false;
    state.modalAction = null;

    if (kind === "remote") {
      setText("modalTitle", "发现新版本，是否更新？");
      setText("modalText", "本地 " + shortHash(update.local_commit) + " → 远端 " +
        shortHash(update.remote_commit) + "\n" + (update.remote_message || ""));
      confirm.textContent = "立即更新";
      state.modalAction = "apply";
    } else if (kind === "local") {
      setText("modalTitle", "检测到后端文件更新");
      setText("modalText", "代码文件已变化。是否重启服务加载新版本？");
      confirm.textContent = "重启加载";
      state.modalAction = "restart";
    } else if (kind === "restart") {
      setText("modalTitle", "确认重启服务？");
      setText("modalText", "重启期间机器人会短暂离线，确认继续？");
      confirm.textContent = "确认重启";
      state.modalAction = "restart";
    } else if (kind === "manual") {
      setText("modalTitle", "需要手动更新");
      setText("modalText", update.message || "当前环境不支持自动更新，请在部署机执行：");
      command.hidden = false;
      command.textContent = update.manual_command || "docker compose -f docker-compose.full.yml up -d --build";
      confirm.textContent = "知道了";
      state.modalAction = "close";
    }
    modal.hidden = false;
  }

  function closeModal() {
    $("modal").hidden = true;
    state.modalAction = null;
  }

  async function applyRemote() {
    const confirm = $("modalConfirm");
    confirm.disabled = true;
    confirm.textContent = "更新中…";
    try {
      const result = await api("/api/update/apply", { method: "POST", body: { confirm: true } });
      if (result.manual) {
        closeModal();
        const update = Object.assign({}, state.status.update, {
          message: result.message,
          manual_command: result.command,
        });
        state.status.update = update;
        openModal("manual");
        return;
      }
      if (!result.ok) {
        toast("更新失败：" + (result.message || "未知错误"));
        confirm.disabled = false;
        confirm.textContent = "立即更新";
        return;
      }
      toast(result.message || "更新成功，正在重启");
      closeModal();
      setTimeout(() => location.reload(), 2500);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("更新失败：" + exc.message);
      confirm.disabled = false;
      confirm.textContent = "立即更新";
    }
  }

  async function restartService() {
    const confirm = $("modalConfirm");
    confirm.disabled = true;
    confirm.textContent = "重启中…";
    try {
      const result = await api("/api/restart", { method: "POST", body: { confirm: true } });
      toast(result.message || "正在重启");
      closeModal();
      setTimeout(() => location.reload(), 2500);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("重启失败：" + exc.message);
      confirm.disabled = false;
      confirm.textContent = "确认重启";
    }
  }

  async function refresh() {
    try {
      const status = await api("/api/status");
      render(status);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") {
        const pill = $("connPill");
        pill.className = "pill pill-bad";
        pill.innerHTML = '<i class="dot"></i>控制台离线';
      }
    }
  }

  async function refreshLogs() {
    try {
      const data = await api("/api/logs?lines=120");
      const node = $("logs");
      node.textContent = data.lines || "（暂无日志）";
      node.scrollTop = node.scrollHeight;
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") $("logs").textContent = "日志读取失败：" + exc.message;
    }
  }

  async function checkUpdate() {
    const button = $("checkBtn");
    button.disabled = true;
    button.textContent = "检查中…";
    try {
      const result = await api("/api/update/check", { method: "POST", body: {} });
      if (result.update) render(Object.assign({}, state.status, { update: result.update }));
      const update = result.update || {};
      let message = "当前已是最新";
      if (update.available) message = "发现新版本";
      else if (update.local_changed) message = "检测到本地代码变化";
      else if (update.state !== "up_to_date") message = update.last_error || "无法确认版本";
      toast(message);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("检查失败：" + exc.message);
    } finally {
      button.disabled = false;
      button.textContent = "检查更新";
    }
  }

  function bind() {
    $("refreshBtn").addEventListener("click", refresh);
    $("checkBtn").addEventListener("click", checkUpdate);
    $("restartBtn").addEventListener("click", () => openModal("restart"));
    $("logRefresh").addEventListener("click", refreshLogs);
    $("bannerAction").addEventListener("click", () => {
      const kind = $("bannerAction").dataset.kind || "remote";
      openModal(kind);
    });
    $("bannerDismiss").addEventListener("click", async () => {
      const kind = $("bannerAction").dataset.kind === "local" ? "local" : "remote";
      if (kind === "local") state.dismissedLocal = true;
      else state.dismissedRemote = (state.status.update || {}).remote_commit || "";
      try {
        await api("/api/update/dismiss", { method: "POST", body: { kind } });
      } catch (_) {}
      $("updateBanner").hidden = true;
    });
    $("modalCancel").addEventListener("click", closeModal);
    $("modal").addEventListener("click", (event) => {
      if (event.target === $("modal")) closeModal();
    });
    $("modalConfirm").addEventListener("click", () => {
      if (state.modalAction === "apply") applyRemote();
      else if (state.modalAction === "restart") restartService();
      else closeModal();
    });
    $("tokenSave").addEventListener("click", () => {
      const value = $("tokenInput").value.trim();
      if (!value) {
        $("loginError").textContent = "请输入令牌。";
        $("loginError").hidden = false;
        return;
      }
      state.token = value;
      localStorage.setItem("qqwf_admin_token", value);
      hideLogin();
      refresh();
    });
    $("tokenInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") $("tokenSave").click();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeModal();
    });
  }

  bind();
  refresh();
  refreshLogs();
  state.timer = setInterval(refresh, 10000);
  state.logTimer = setInterval(refreshLogs, 5000);
})();
