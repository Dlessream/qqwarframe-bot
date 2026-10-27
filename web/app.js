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
    aliasGroups: [],
    aliasCategory: "all",
    catalogCounts: {},
    catalogTotal: 0,
    catalogLimit: 40,
    catalogOffset: 0,
    catalogQuery: "",
    arbiNodes: [],
    arbiTiers: [],
    arbiQuery: "",
    arbiCurrent: "",
    arbiView: "ratings",
    arbiSchedule: null,
    missionEntries: [],
    missionColors: {},
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

  function boardText(board) {
    if (!board || board.enabled === false) return "未启用";
    const junk = board.junk || {};
    const frames = board.frames || {};
    const riven = board.riven || {};
    let text = "垃圾 " + Number(junk.ready || 0) + "/" + Number(junk.total || 0) +
      " ｜ 战甲 " + Number(frames.ready || 0) + "/" + Number(frames.total || 0) +
      " ｜ 紫卡 " + Number(riven.ready || 0) + "/" + Number(riven.total || 0);
    if (board.banned) text += "（限流 " + Math.ceil(board.banned) + "s）";
    return text;
  }

  function rotationText(rotation) {
    if (!rotation || rotation.enabled === false) return "未启用";
    const parts = [];
    parts.push("言录使 " + (rotation.acrithis ? "已获取" : "待抓取"));
    parts.push("信条 " + Number(rotation.glast || 0) + "/5");
    parts.push("终幕 " + Number(rotation.coda_a || 0) + "+" +
      Number(rotation.coda_b || 0));
    let text = parts.join(" ｜ ");
    if (rotation.error) text += "（抓取失败）";
    return text;
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
    setText("boardState", boardText(status.board));
    setText("rotationState", rotationText(status.rotation));
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
      if (!state.missionEntries.length) await loadMissionColors();
      if (!state.arbiNodes.length) await loadArbi();
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
      const local = shortHash(update.local_commit);
      const remote = shortHash(update.remote_commit);
      let message = "当前已是最新";
      if (update.available) {
        message = "发现新版本 " + (remote || "") + "，可点击更新";
      } else if (update.local_changed) {
        message = "检测到本地代码变化，重启后生效";
      } else if (update.local_missing) {
        message = remote
          ? "本地版本未知；远端最新 " + remote +
            "（重建时带 BUILD_COMMIT 或设置 QQWF_COMMIT 环境变量）"
          : "本地版本未知（重建时带 BUILD_COMMIT 或设置 QQWF_COMMIT 环境变量）";
      } else if (update.state === "up_to_date") {
        message = "当前已是最新（" + local + "）";
      } else if (update.state === "local_only") {
        message = "本地 " + local + " 还没推送到远端（远端 main 在 " + remote + "）";
      } else if (update.state === "local_ahead") {
        message = "本地领先远端：" + local + " → " + remote;
      } else if (update.state === "diverged") {
        message = "本地与远端历史分叉：" + local + " / " + remote;
      } else {
        message = update.last_error || "无法确认版本";
      }
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
    state.aliasGroups = groups;
    state.catalogCounts = data.counts || {};
    state.catalogTotal = Number(data.total || 0);
    state.catalogOffset = Number(data.offset || 0);
    state.catalogLimit = Number(data.limit || 40);
    const parts = [];
    parts.push(source.name || "warframe market");
    parts.push("<b>" + Number(source.items || 0) + "</b> 可交易物品");
    parts.push("上次刷新 " + formatAge(source.last_refresh_seconds));
    if (source.realtime) parts.push("wm 实时报价");
    if (source.error) parts.push("物品源：" + source.error);
    $("sourceInfo").innerHTML = parts.join(" · ");
    setText("aliasCount", Number(source.aliases || 0) + " 个别名");
    renderAliasTabs();
    renderAliasRows();
  }

  function renderAliasTabs() {
    const labels = {
      all: "全部", warframe: "战甲", weapon: "武器",
      relic: "遗物", mod: "模组", other: "其他",
    };
    const counts = state.catalogCounts || {};
    const tabs = $("aliasTabs");
    tabs.innerHTML = "";
    Object.keys(labels).forEach((key) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "alias-tab" + (state.aliasCategory === key ? " active" : "");
      button.textContent = labels[key] + " " + Number(counts[key] || 0);
      button.addEventListener("click", () => {
        state.aliasCategory = key;
        state.catalogOffset = 0;
        loadAliases();
      });
      tabs.appendChild(button);
    });
  }

  function renderAliasRows() {
    const list = $("aliasGroups");
    const groups = state.aliasGroups || [];
    list.innerHTML = "";
    const page = Math.floor(state.catalogOffset / state.catalogLimit) + 1;
    const pages = Math.max(1, Math.ceil(state.catalogTotal / state.catalogLimit));
    setText("aliasPage", "第 " + page + " / " + pages + " 页 · " +
      state.catalogTotal + " 条");
    $("aliasPrev").disabled = state.catalogOffset <= 0;
    $("aliasNext").disabled = state.catalogOffset + state.catalogLimit >= state.catalogTotal;
    if (!groups.length) {
      list.innerHTML = '<div class="alias-empty">这个分组没有物品。</div>';
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
      if ((group.parts || []).length) {
        const parts = document.createElement("div");
        parts.className = "alias-parts";
        group.parts.forEach((part) => {
          const line = document.createElement("div");
          const label = document.createElement("span");
          label.textContent = part.label + "：";
          const values = document.createElement("code");
          const aliases = part.aliases || [];
          values.textContent = aliases.length
            ? aliases.slice(0, 5).join(" · ") + (aliases.length > 5 ? " …" : "")
            : "（未生成）";
          values.title = aliases.join("、");
          line.append(label, values);
          parts.appendChild(line);
        });
        target.appendChild(parts);
      }

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
      const params = new URLSearchParams({
        category: state.aliasCategory,
        offset: String(state.catalogOffset),
        limit: String(state.catalogLimit),
      });
      if (state.catalogQuery) params.set("q", state.catalogQuery);
      const data = await api("/api/catalog?" + params.toString());
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

  async function loadArbi() {
    try {
      const data = await api("/api/arbi");
      state.arbiNodes = data.nodes || [];
      state.arbiTiers = data.tiers || [];
      state.arbiCurrent = data.current || "";
      renderArbi(data);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") {
        setText("arbiInfo", "读取仲裁节点失败：" + exc.message);
      }
    }
  }

  function arbiStatusLabel(node) {
    if (node.source === "community") return "社区梯队 " + node.community;
    if (node.source === "manual") return "人工评级 " + node.manual;
    return "本地推算 " + node.local;
  }

  function missionColorOf(name, fallbackTable) {
    const table = state.missionColors || {};
    const raw = String(name || "").trim();
    // 老数据里带英文前缀（INFESTED 资源回收），去掉前缀再查一次
    const keys = [raw, raw.replace(/^INFESTED\s+/i, "")];
    for (const key of keys) {
      if (key && table[key]) return table[key];
      if (key && fallbackTable && fallbackTable[key]) return fallbackTable[key];
    }
    return "#8B98AA";
  }

  function makeSpan(text, className) {
    const node = document.createElement("span");
    node.className = className;
    node.textContent = text;
    return node;
  }

  function tierColor(tier) {
    const table = {
      "S": "#E8B45A", "A+": "#E27070", "A": "#E89B5A",
      "A-": "#EBBC60", "B": "#8FB8D8", "C": "#8B98AA",
    };
    return table[tier] || "#6F7C8D";
  }

  function hexAlpha(hex, alpha) {
    const text = String(hex || "").replace("#", "");
    if (text.length !== 6) return "rgba(139,152,170," + alpha + ")";
    const r = parseInt(text.slice(0, 2), 16);
    const g = parseInt(text.slice(2, 4), 16);
    const b = parseInt(text.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  async function applyMissionColor(name, color) {
    try {
      const result = await api("/api/mission-colors", {
        method: "POST",
        body: { name, color },
      });
      state.missionColors[name] = result.color || color;
      toast("已改「" + name + "」的显示色：" + (result.color || color));
      await loadMissionColors();
      await loadArbi();
      state.arbiSchedule = null;
      if (state.arbiView === "schedule") await loadArbiSchedule(false);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("改色失败：" + exc.message);
    }
  }

  function renderArbi(data) {
    const counts = data.counts || {};
    const total = (data.nodes || []).length;
    const info = [];
    if (data.message) {
      info.push(data.message);
    } else if (data.error) {
      info.push("仲裁数据不可用：" + data.error);
    } else {
      info.push("共 <b>" + total + "</b> 个节点");
      info.push("社区 <b>" + Number(counts.community || 0) + "</b>");
      info.push("人工 <b>" + Number(counts.manual || 0) + "</b>");
      info.push("本地 <b>" + Number(counts.local || 0) + "</b>");
      if (data.ratings_file) info.push("人工评级保存在 " + data.ratings_file);
    }
    $("arbiInfo").innerHTML = info.join(" · ");
    setText("arbiCount", total ? total + " 个节点" : "");

    const list = $("arbiList");
    list.innerHTML = "";
    const query = state.arbiQuery;
    const nodes = (data.nodes || []).filter((node) => {
      if (!query) return true;
      const hay = [node.name, node.system, node.mission, node.key,
        node.community, node.manual, node.local].join(" ").toLowerCase();
      return hay.indexOf(query) >= 0;
    });
    if (!nodes.length) {
      list.innerHTML = '<div class="arbi-empty">没有匹配的节点。</div>';
      return;
    }

    // 顶部图例：这一屏里出现过哪些任务、各自是什么颜色
    const legend = $("arbiLegend");
    if (legend) {
      legend.innerHTML = "";
      const seen = new Set();
      nodes.forEach((node) => {
        const missionName = node.mission;
        if (!missionName || seen.has(missionName)) return;
        seen.add(missionName);
        const color = missionColorOf(missionName, data.mission_colors);
        const item = document.createElement("span");
        item.className = "legend-item";
        const dot = document.createElement("i");
        dot.style.background = color;
        item.append(dot, document.createTextNode(missionName));
        legend.appendChild(item);
      });
    }

    nodes.forEach((node) => {
      const row = document.createElement("div");
      row.className = "arbi-row";
      const missionName = node.mission || "—";
      const missionColor = missionColorOf(missionName, data.mission_colors);
      // 整行铺一层任务色的淡底（跟仲裁排期表一个观感），左边再压一条实色竖线
      row.style.setProperty("--mission-color", missionColor);
      row.style.background = "linear-gradient(90deg, "
        + hexAlpha(missionColor, 0.34) + " 0%, "
        + hexAlpha(missionColor, 0.16) + " 100%)";

      const main = document.createElement("div");
      main.className = "arbi-main";
      const line = document.createElement("div");
      line.className = "arbi-line";

      const mission = document.createElement("b");
      mission.className = "arbi-mission";
      mission.textContent = missionName;
      mission.style.color = missionColor;

      // 任务名后面直接跟一个取色器：点开就能改这个任务类型的显示色，改完立即保存
      const swatch = document.createElement("input");
      swatch.type = "color";
      swatch.className = "mission-swatch";
      swatch.value = /^#[0-9a-fA-F]{6}$/.test(missionColor) ? missionColor : "#8B98AA";
      swatch.title = "点这里改「" + missionName + "」的显示颜色";
      swatch.setAttribute("aria-label", "修改 " + missionName + " 的显示颜色");
      const rowTint = (hex) => "linear-gradient(90deg, " + hexAlpha(hex, 0.34)
        + " 0%, " + hexAlpha(hex, 0.16) + " 100%)";
      swatch.addEventListener("input", () => {
        const hex = swatch.value.toUpperCase();
        row.style.setProperty("--mission-color", hex);
        row.style.background = rowTint(hex);
        mission.style.color = hex;
      });
      swatch.addEventListener("change", () => {
        applyMissionColor(missionName, swatch.value.toUpperCase());
      });
      line.append(mission, swatch);

      if (node.faction) {
        line.append(makeSpan("- " + node.faction, "arbi-enemy"));
      }
      line.append(makeSpan("@ " + (node.name || node.key)
        + (node.system ? ", " + node.system : ""), "arbi-target"));

      const meta = document.createElement("div");
      meta.className = "arbi-meta";
      meta.textContent = [node.key, node.levels || "—",
        arbiStatusLabel(node)].join(" · ");
      main.append(line, meta);

      const tags = document.createElement("div");
      tags.className = "arbi-tags";
      const tier = node.tier || "未评级";
      const pill = document.createElement("span");
      pill.className = "tier-pill";
      pill.textContent = tier;
      pill.title = "最终评级 " + tier;
      pill.style.color = tierColor(tier);
      pill.style.borderColor = hexAlpha(tierColor(tier), 0.55);
      pill.style.background = hexAlpha(tierColor(tier), 0.16);
      tags.appendChild(pill);
      if (state.arbiCurrent && node.key === state.arbiCurrent) {
        const badge = document.createElement("span");
        badge.className = "arbi-tag is-current";
        badge.textContent = "轮换中";
        tags.appendChild(badge);
      }
      const badge = document.createElement("span");
      badge.className = "arbi-tag is-" + (node.source || "local");
      badge.textContent = arbiStatusLabel(node);
      tags.appendChild(badge);

      const select = document.createElement("select");
      select.className = "arbi-select";
      const options = [{ value: "", label: "跟随默认" }].concat(
        (data.tiers || []).map((tier) => ({ value: tier, label: tier })));
      options.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.value;
        option.textContent = item.label;
        if ((node.manual || "") === item.value) option.selected = true;
        select.appendChild(option);
      });
      select.disabled = !data.enabled;
      select.addEventListener("change", async () => {
        select.disabled = true;
        try {
          const result = await api("/api/arbi", {
            method: "POST",
            body: { key: node.key, tier: select.value },
          });
          toast("已保存：" + (result.text || node.name));
          await loadArbi();
        } catch (exc) {
          if (exc.message !== "UNAUTHORIZED") toast("保存失败：" + exc.message);
          select.disabled = false;
        }
      });

      row.append(main, tags, select);
      list.appendChild(row);
    });
  }

  async function loadArbiSchedule(force) {
    if (state.arbiSchedule && !force) return;
    try {
      const data = await api("/api/arbi-schedule?days=7");
      state.arbiSchedule = data;
      if (data.missions) {
        state.missionColors = Object.assign({}, state.missionColors, data.missions);
      }
      renderArbiSchedule(data);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") {
        setText("arbiScheduleInfo", "读取排期失败：" + exc.message);
      }
    }
  }

  function schedRow(entry) {
    const nowSec = Math.floor(Date.now() / 1000);
    const row = document.createElement("div");
    row.className = "sched-row";
    const color = missionColorOf(entry.mission, state.missionColors);
    row.style.setProperty("--mission-color", color);
    row.style.background = "linear-gradient(90deg, " + hexAlpha(color, 0.30)
      + " 0%, " + hexAlpha(color, 0.10) + " 100%)";

    const time = makeSpan(entry.time || "—", "sched-time");
    const node = makeSpan(entry.node || entry.key || "—", "sched-node");
    const mission = makeSpan(entry.mission || "—", "sched-mission");
    mission.style.color = color;

    const tierText = entry.tier || "未评级";
    const tier = document.createElement("span");
    tier.className = "tier-pill sched-tier";
    tier.textContent = tierText;
    tier.title = "评级 " + tierText;
    tier.style.color = tierColor(tierText);
    tier.style.borderColor = hexAlpha(tierColor(tierText), 0.55);
    tier.style.background = hexAlpha(tierColor(tierText), 0.16);

    row.append(time, node, mission);
    if (entry.start <= nowSec && entry.end > nowSec) {
      row.classList.add("is-live");
      row.appendChild(makeSpan("进行中", "sched-live"));
    }
    row.appendChild(tier);
    return row;
  }

  function renderArbiSchedule(data) {
    const box = $("arbiSchedule");
    if (!box) return;
    box.innerHTML = "";
    if (!data.enabled) {
      setText("arbiScheduleInfo", data.message || "仲裁排期未启用");
      setText("arbiScheduleCount", "");
      return;
    }
    const groups = data.groups || [];
    const total = data.total || 0;
    $("arbiScheduleInfo").innerHTML = "未来 <b>" + (data.days || 7) +
      "</b> 天 · 每小时一场 · 共 <b>" + total + "</b> 场";
    setText("arbiScheduleCount", groups.length + " 天");

    groups.forEach((group, index) => {
      const day = document.createElement("details");
      day.className = "sched-day";
      if (index === 0) day.open = true;

      const head = document.createElement("summary");
      head.className = "sched-day-head";
      head.appendChild(makeSpan(group.label || group.date, "sched-day-label"));
      const highs = document.createElement("span");
      highs.className = "sched-day-highs";
      (group.highlights || []).forEach((item) => {
        const pill = document.createElement("span");
        pill.className = "tier-pill";
        pill.textContent = item.tier + " × " + item.count;
        pill.style.color = tierColor(item.tier);
        pill.style.borderColor = hexAlpha(tierColor(item.tier), 0.55);
        pill.style.background = hexAlpha(tierColor(item.tier), 0.16);
        highs.appendChild(pill);
      });
      head.appendChild(highs);
      head.appendChild(makeSpan((group.count || 0) + " 场", "sched-day-count"));
      day.appendChild(head);

      const rows = document.createElement("div");
      rows.className = "sched-rows";
      (group.entries || []).forEach((entry) => rows.appendChild(schedRow(entry)));
      day.appendChild(rows);
      box.appendChild(day);
    });
  }

  function setArbiView(view) {
    state.arbiView = view === "schedule" ? "schedule" : "ratings";
    document.querySelectorAll("#arbiTabs .arbi-tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.arbiView === state.arbiView);
    });
    $("arbiRatingsView").hidden = state.arbiView !== "ratings";
    $("arbiScheduleView").hidden = state.arbiView !== "schedule";
    if (state.arbiView === "schedule") loadArbiSchedule(false);
  }

  async function loadMissionColors() {
    try {
      const data = await api("/api/mission-colors");
      state.missionEntries = data.entries || [];
      state.missionColors = {};
      state.missionEntries.forEach((entry) => {
        state.missionColors[entry.name] = entry.color;
      });
      renderMissionColors(data);
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") {
        setText("missionInfo", "读取任务配色失败：" + exc.message);
      }
    }
  }

  function renderMissionColors(data) {
    const entries = data.entries || [];
    const custom = entries.filter((entry) => entry.custom).length;
    const info = [];
    info.push("共 <b>" + entries.length + "</b> 种任务类型");
    info.push("已自定义 <b>" + custom + "</b>");
    if (data.file) info.push("配色保存在 " + data.file);
    $("missionInfo").innerHTML = info.join(" · ");
    setText("missionCount", entries.length ? entries.length + " 种" : "");

    const list = $("missionColors");
    list.innerHTML = "";
    if (!entries.length) {
      list.innerHTML = '<div class="arbi-empty">没有可配置的任务类型。</div>';
      return;
    }
    entries.forEach((entry) => {
      const row = document.createElement("label");
      row.className = "mission-row" + (entry.custom ? " is-custom" : "");
      const input = document.createElement("input");
      input.type = "color";
      input.value = entry.color;
      input.dataset.name = entry.name;
      input.dataset.default = entry.default;
      const name = document.createElement("span");
      name.className = "mission-name";
      name.textContent = entry.name;
      name.style.color = entry.color;
      const code = document.createElement("code");
      code.textContent = entry.color;
      input.addEventListener("input", () => {
        code.textContent = input.value.toUpperCase();
        row.classList.toggle("is-custom",
          input.value.toUpperCase() !== String(entry.default).toUpperCase());
      });
      row.title = "默认 " + entry.default;
      row.append(input, name, code);
      list.appendChild(row);
    });
  }

  async function saveMissionColors() {
    const inputs = Array.from(
      $("missionColors").querySelectorAll('input[type="color"]'));
    if (!inputs.length) {
      toast("没有可保存的任务配色。");
      return;
    }
    const colors = {};
    inputs.forEach((input) => { colors[input.dataset.name] = input.value; });
    const button = $("missionSave");
    button.disabled = true;
    try {
      const result = await api("/api/mission-colors", {
        method: "POST",
        body: { colors },
      });
      toast("任务配色已保存，下一次出图生效");
      state.missionEntries = result.entries || state.missionEntries;
      renderMissionColors(result);
      await loadArbi();
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("保存失败：" + exc.message);
    } finally {
      button.disabled = false;
    }
  }

  async function resetMissionColors() {
    const button = $("missionReset");
    button.disabled = true;
    try {
      const result = await api("/api/mission-colors", {
        method: "POST",
        body: { reset: true },
      });
      toast("已恢复默认任务配色");
      state.missionEntries = result.entries || [];
      renderMissionColors(result);
      await loadArbi();
    } catch (exc) {
      if (exc.message !== "UNAUTHORIZED") toast("恢复失败：" + exc.message);
    } finally {
      button.disabled = false;
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
    $("aliasFilterBtn").addEventListener("click", () => {
      state.catalogQuery = $("aliasFilter").value.trim();
      state.catalogOffset = 0;
      loadAliases();
    });
    $("aliasFilter").addEventListener("keydown", (event) => {
      if (event.key === "Enter") $("aliasFilterBtn").click();
    });
    $("aliasPrev").addEventListener("click", () => {
      state.catalogOffset = Math.max(0, state.catalogOffset - state.catalogLimit);
      loadAliases();
    });
    $("aliasNext").addEventListener("click", () => {
      state.catalogOffset += state.catalogLimit;
      loadAliases();
    });
    $("dataRefresh").addEventListener("click", refreshData);
    $("missionSave").addEventListener("click", saveMissionColors);
    $("missionReset").addEventListener("click", resetMissionColors);
    $("arbiRefresh").addEventListener("click", () => {
      if (state.arbiView === "schedule") loadArbiSchedule(true);
      else loadArbi();
    });
    document.querySelectorAll("#arbiTabs .arbi-tab").forEach((tab) => {
      tab.addEventListener("click", () => setArbiView(tab.dataset.arbiView));
    });
    $("arbiMissionColors").addEventListener("click", () => {
      const panel = document.querySelector(".mission-panel");
      if (!panel) return;
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
      panel.classList.remove("is-flash");
      void panel.offsetWidth;
      panel.classList.add("is-flash");
    });
    $("arbiFilterBtn").addEventListener("click", () => {
      state.arbiQuery = $("arbiFilter").value.trim().toLowerCase();
      loadArbi();
    });
    $("arbiFilter").addEventListener("keydown", (event) => {
      if (event.key === "Enter") $("arbiFilterBtn").click();
    });
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
