(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    token: localStorage.getItem("qqwf_admin_token") || "",
    status: null,
    dismissedRemote: "",
    dismissedLocal: false,
    autoPrompted: false,
    groupsDirty: false,
    aliasesLoaded: false,
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
    setText("groupPolicy", "白 " + Number((groups.whitelist || []).length) +
      " / 黑 " + Number((groups.blacklist || []).length));
    renderGroups(groups);
    if (!state.aliasesLoaded) loadAliases();
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

  function renderGroups(groups) {
    const whitelist = Array.isArray(groups.whitelist) ? groups.whitelist : [];
    const blacklist = Array.isArray(groups.blacklist) ? groups.blacklist : [];
    if (!state.groupsDirty) {
      $("whitelistInput").value = whitelist.join("\n");
      $("blacklistInput").value = blacklist.join("\n");
    }
    const mode = whitelist.length
      ? "白名单模式：仅 " + whitelist.length + " 个群可用；黑名单优先"
      : "黑名单模式：除 " + blacklist.length + " 个黑名单群外，其他群都可用";
    setText("groupMode", "当前：" + mode);
  }

  function parseGroupIds(value) {
    return String(value || "")
      .split(/[\s,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  async function saveGroups() {
    const button = $("groupSave");
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const result = await api("/api/groups", {
        method: "POST",
        body: {
          whitelist: parseGroupIds($("whitelistInput").value),
          blacklist: parseGroupIds($("blacklistInput").value),
        },
      });
      state.groupsDirty = false;
      renderGroups(result);
      toast("群策略已保存，立即生效");
      refresh();
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("保存失败：" + exc.message);
    } finally {
      button.disabled = false;
      button.textContent = "保存名单";
    }
  }

  function formatAge(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value < 0) return "尚未加载";
    if (value < 90) return Math.round(value) + " 秒前";
    if (value < 5400) return Math.round(value / 60) + " 分钟前";
    return (value / 3600).toFixed(1) + " 小时前";
  }

  function renderAliasGroups(data) {
    const source = data.source || {};
    const groups = data.groups || [];
    const parts = [];
    parts.push("warframe.market");
    parts.push("<b>" + Number(source.items || 0) + "</b> 物品");
    parts.push("上次刷新 " + formatAge(source.last_refresh_seconds));
    if (source.error) parts.push("物品源：" + source.error);
    $("sourceInfo").innerHTML = parts.join(" · ");
    const total = groups.reduce((sum, group) => sum + (group.aliases || []).length, 0);
    setText("aliasCount", groups.length + " 个目标 / " + total + " 个别名");

    const list = $("aliasGroups");
    list.innerHTML = "";
    if (!groups.length) {
      list.innerHTML = '<div class="alias-empty">还没有别名。</div>';
      return;
    }
    groups.forEach((group) => {
      const row = document.createElement("div");
      row.className = "alias-group";

      const slots = document.createElement("div");
      slots.className = "alias-slots";
      const builtin = new Set(group.builtin || []);
      const values = group.aliases || [];
      const count = Math.max(4, values.length);
      for (let index = 0; index < count; index += 1) {
        const input = document.createElement("input");
        input.className = "input";
        input.type = "text";
        input.maxLength = 40;
        input.placeholder = "别名 " + (index + 1);
        input.value = values[index] || "";
        if (builtin.has(input.value)) {
          input.disabled = true;
          input.title = "内置别名不可删除";
        }
        slots.appendChild(input);
      }

      const target = document.createElement("div");
      target.className = "alias-target";
      const name = document.createElement("b");
      name.textContent = group.name || (group.missing ? "目标不存在" : group.slug);
      name.title = name.textContent;
      const slug = document.createElement("small");
      slug.textContent = group.slug;
      target.append(name, slug);

      const save = document.createElement("button");
      save.type = "button";
      save.className = "btn btn-ghost btn-sm";
      save.textContent = "保存";
      save.addEventListener("click", async () => {
        const aliases = Array.from(slots.querySelectorAll("input"))
          .filter((input) => !input.disabled)
          .map((input) => input.value.trim())
          .filter(Boolean);
        save.disabled = true;
        save.textContent = "保存中…";
        try {
          await api("/api/alias-groups", {
            method: "POST", body: { slug: group.slug, aliases },
          });
          toast("已更新 " + (group.name || group.slug));
          await loadAliases();
        } catch (exc) {
          if (exc.message !== "UNAUTHORIZED") toast("保存失败：" + exc.message);
        } finally {
          save.disabled = false;
          save.textContent = "保存";
        }
      });
      row.append(slots, target, save);
      list.appendChild(row);
    });
  }

  async function loadAliases() {
    try {
      const data = await api("/api/alias-groups");
      state.aliasesLoaded = true;
      renderAliasGroups(data);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") {
        setText("sourceInfo", "读取数据源失败：" + exc.message);
      }
    }
  }

  async function searchItems() {
    const query = $("aliasTarget").value.trim();
    if (!query) {
      toast("先输入要搜索的物品名");
      return;
    }
    const box = $("aliasResults");
    box.hidden = false;
    box.innerHTML = '<span class="alias-empty">搜索中…</span>';
    try {
      const data = await api("/api/items?q=" + encodeURIComponent(query));
      const items = data.items || [];
      box.innerHTML = "";
      if (!items.length) {
        box.innerHTML = '<span class="alias-empty">没有找到物品，试试英文名或 slug。</span>';
        return;
      }
      items.forEach((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "alias-candidate";
        button.textContent = item.name || item.slug;
        const small = document.createElement("small");
        small.textContent = (item.name_en ? item.name_en + " · " : "") + item.slug;
        button.appendChild(small);
        button.addEventListener("click", () => {
          $("aliasTarget").value = item.slug;
          $("aliasResults").hidden = true;
          $("aliasTranslation").hidden = true;
        });
        box.appendChild(button);
      });
    } catch (exc) {
      box.innerHTML = '<span class="alias-empty">搜索失败：' + exc.message + "</span>";
    }
  }

  async function translateTarget() {
    const query = $("aliasTarget").value.trim();
    if (!query) {
      toast("先输入要翻译的物品名");
      return;
    }
    const box = $("aliasTranslation");
    box.hidden = false;
    box.textContent = "翻译中…";
    try {
      const data = await api("/api/translate?q=" + encodeURIComponent(query));
      box.innerHTML = "";
      const zh = document.createElement("div");
      zh.innerHTML = "中文：<b></b>";
      zh.querySelector("b").textContent = data.zh || "—";
      const en = document.createElement("div");
      en.innerHTML = "英文：<b></b>";
      en.querySelector("b").textContent = data.en || "—";
      const src = document.createElement("div");
      src.textContent = "来源：" + (data.source || "—");
      box.append(zh, en, src);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") box.textContent = "翻译失败：" + exc.message;
    }
  }

  async function saveAlias() {
    const fields = ["aliasName", "aliasName2", "aliasName3", "aliasName4"];
    const aliases = fields.map((id) => $(id).value.trim()).filter(Boolean);
    const target = $("aliasTarget").value.trim();
    if (!aliases.length || !target) {
      toast("至少填一个别名和目标");
      return;
    }
    const button = $("aliasSave");
    button.disabled = true;
    button.textContent = "保存中…";
    try {
      const result = await api("/api/aliases", {
        method: "POST", body: { aliases, target },
      });
      fields.forEach((id) => { $(id).value = ""; });
      $("aliasTarget").value = "";
      $("aliasResults").hidden = true;
      toast("已保存 " + Number((result.aliases || aliases).length) + " 个别名，wm 立即生效");
      await loadAliases();
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("保存失败：" + exc.message);
    } finally {
      button.disabled = false;
      button.textContent = "保存别名";
    }
  }

  async function deleteAlias(alias) {
    if (!window.confirm("删除别名「" + alias + "」？")) return;
    try {
      await api("/api/aliases/delete", { method: "POST", body: { alias } });
      toast("别名已删除");
      await loadAliases();
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("删除失败：" + exc.message);
    }
  }

  async function refreshData() {
    const button = $("dataRefresh");
    button.disabled = true;
    button.textContent = "刷新中…";
    try {
      const data = await api("/api/data/refresh", { method: "POST", body: {} });
      toast("数据源已刷新：" + Number((data.source || {}).items || 0) + " 条物品");
      await loadAliases();
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("刷新失败：" + exc.message);
    } finally {
      button.disabled = false;
      button.textContent = "刷新数据源";
    }
  }

  function bind() {
    $("refreshBtn").addEventListener("click", refresh);
    $("checkBtn").addEventListener("click", checkUpdate);
    $("restartBtn").addEventListener("click", () => openModal("restart"));
    $("logRefresh").addEventListener("click", refreshLogs);
    $("groupSave").addEventListener("click", saveGroups);
    $("aliasSearch").addEventListener("click", searchItems);
    $("aliasTranslate").addEventListener("click", translateTarget);
    $("aliasSave").addEventListener("click", saveAlias);
    $("dataRefresh").addEventListener("click", refreshData);
    $("aliasTarget").addEventListener("keydown", (event) => {
      if (event.key === "Enter") searchItems();
    });
    ["whitelistInput", "blacklistInput"].forEach((id) => {
      $(id).addEventListener("input", () => { state.groupsDirty = true; });
    });
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
