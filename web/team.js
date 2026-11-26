/* Warframe 组队面板：QQ 内打开，token 换会话，全部动作走 /api/team/*。 */
(function () {
  "use strict";

  var CAT_COLORS = {
    fissure: "#6ea8fe",
    arbitration: "#d9b26a",
    railjack: "#7fd1c6",
    tridolon: "#b28bf0",
    bounty: "#e08a5b",
    teams: "#d9b26a"
  };
  var CAT_ORDER = ["fissure", "arbitration", "railjack", "tridolon", "bounty"];

  var state = {
    me: null,
    cats: [],
    teams: [],
    mine: "",
    size: 4,
    tab: "teams",
    wantTab: "",
    missions: {},
    busy: false,
    timer: 0
  };

  var view = document.getElementById("view");
  var tabs = document.getElementById("tabs");
  var sheet = document.getElementById("sheet");
  var toastEl = document.getElementById("toast");
  var whoBtn = document.getElementById("whoBtn");

  /* ------------------------------------------------------------ 工具 */
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function catLabel(key) {
    for (var i = 0; i < state.cats.length; i += 1) {
      if (state.cats[i].key === key) { return state.cats[i].label; }
    }
    return "组队";
  }

  function catNote(key) {
    for (var i = 0; i < state.cats.length; i += 1) {
      if (state.cats[i].key === key) { return state.cats[i].note; }
    }
    return "";
  }

  function toast(text, bad) {
    toastEl.textContent = text;
    toastEl.className = "toast" + (bad ? " bad" : "");
    toastEl.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { toastEl.hidden = true; }, 2600);
  }

  async function api(path, body) {
    var options = { headers: {} };
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body || {});
      options.credentials = "same-origin";
    }
    var resp = await fetch(path, options);
    var data = null;
    try { data = await resp.json(); } catch (e) { data = { ok: false, message: "服务没有响应" }; }
    if (resp.status === 401) {
      state.me = null;
      renderLogin();
      throw new Error("need_login");
    }
    if (!data.ok) { throw new Error(data.message || "操作失败"); }
    return data;
  }

  /* ------------------------------------------------------------ 登录 */
  function profileLabel() {
    if (!state.me) { return "未认证"; }
    return "QQ 已认证 · " + state.me.name;
  }

  function renderLogin() {
    tabs.innerHTML = "";
    whoBtn.textContent = "未认证";
    whoBtn.className = "who";
    view.innerHTML =
      '<div class="login">' +
        '<h2>需要 QQ 认证</h2>' +
        "这个面板只认本群成员本人。<br>" +
        "请在群里发送 <code>#组队</code>，点机器人回复里的<b>「打开组队面板」</b>按钮进来。" +
        "<br><br>入口链接 30 分钟有效，且只能用一次。" +
      "</div>";
  }

  async function boot() {
    var params = new URLSearchParams(location.search);
    var token = params.get("t");
    state.wantTab = params.get("tab") || "";
    if (token) {
      try {
        var result = await api("/api/team/login", { token: token });
        state.me = result.profile;
        history.replaceState(null, "", "/team");
      } catch (err) {
        if (err.message !== "need_login") {
          renderLogin();
          toast(err.message, true);
          return;
        }
      }
    }
    try {
      var me = await api("/api/team/me");
      state.me = me.profile;
    } catch (err) {
      return;
    }
    if (!state.me) { renderLogin(); return; }
    await refreshAll(true);
    if (!state.me.named) { openName(true); }
  }

  /* ------------------------------------------------------------ 刷新 */
  async function refreshAll(first) {
    var data = await api("/api/team/state");
    state.teams = data.teams || [];
    state.mine = data.mine || "";
    state.size = data.size || 4;
    if (data.categories && data.categories.length) { state.cats = data.categories; }
    whoBtn.textContent = profileLabel();
    whoBtn.className = "who ok";
    if (first) {
      state.tab = state.mine ? "teams" : "fissure";
      if (state.wantTab === "teams" ||
          CAT_ORDER.indexOf(state.wantTab) >= 0) {
        state.tab = state.wantTab;
      }
    }
    if (state.tab !== "teams" && !state.missions[state.tab]) {
      await loadMissions(state.tab);
    }
    renderTabs();
    render();
  }

  async function loadMissions(key) {
    var data = await api("/api/team/missions?category=" + encodeURIComponent(key));
    state.missions[key] = data;
    return data;
  }

  function renderTabs() {
    var html = "";
    var teamCount = state.teams.length;
    html += tabHtml("teams", "我的车队", teamCount);
    CAT_ORDER.forEach(function (key) {
      var data = state.missions[key];
      var count = data && data.items ? data.items.length : 0;
      html += tabHtml(key, catLabel(key), count || "");
    });
    tabs.innerHTML = html;
  }

  function tabHtml(key, label, count) {
    var active = state.tab === key ? " active" : "";
    var n = count ? '<span class="n">' + esc(count) + "</span>" : "";
    return '<button class="tab' + active + '" data-tab="' + esc(key) + '" type="button">' +
      esc(label) + n + "</button>";
  }

  function render() {
    if (state.tab === "teams") { renderTeams(); return; }
    renderMissions(state.tab);
  }

  /* ------------------------------------------------------------ 任务列表 */
  function renderMissions(key) {
    var data = state.missions[key];
    var color = CAT_COLORS[key] || "#d9b26a";
    if (!data) {
      view.innerHTML = '<div class="loading">正在取' + esc(catLabel(key)) + "…</div>";
      return;
    }
    var items = data.items || [];
    var html = '<div class="section-head"><h2>' + esc(data.label) + "</h2><span>" +
      items.length + " 条实时</span></div>" +
      '<p class="note">' + esc(data.note || catNote(key)) + "</p>";
    if (key === "fissure" || key === "railjack") {
      html += '<div style="margin:0 0 12px"><button class="btn ghost" data-watch="' +
        esc(key) + '" type="button">＋ 设条件等下一波</button></div>';
    }
    if (!items.length) {
      html += '<div class="empty"><strong>暂时没有可开的' + esc(catLabel(key)) + "</strong>" +
        "数据刚刷新过，过一会儿再看看。</div>";
    } else {
      html += '<div class="list">';
      items.forEach(function (item) {
        html += '<article class="mission" style="--cat:' + color + '">' +
          '<div class="m-main">' +
            '<div class="m-title"><span class="cut">' + esc(item.title) + "</span></div>" +
            '<div class="m-sub">' + esc(item.subtitle) + "</div>" +
            '<div class="m-meta">' +
              (item.badge ? '<span class="chip ' + (item.badge === "钢铁" ? "hot" : "cool") + '">' + esc(item.badge) + "</span>" : "") +
              (item.enemy ? '<span class="chip">' + esc(item.enemy) + "</span>" : "") +
              '<span class="chip">' + esc(item.meta) + "</span>" +
            "</div>" +
          "</div>" +
          '<button class="btn primary" data-drive="' + esc(item.key) + '" data-cat="' + esc(key) + '" type="button">开车</button>' +
        "</article>";
      });
      html += "</div>";
    }
    view.innerHTML = html;
  }

  /* ------------------------------------------------------------ 车队 */
  function renderTeams() {
    var html = '<div class="section-head"><h2>本群车队</h2><span>' +
      state.teams.length + " 支进行中</span></div>";
    if (!state.teams.length) {
      html += '<div class="empty"><strong>还没有人开车</strong>' +
        "去「裂缝 / 仲裁 / 九重天 / 三傻 / 赏金」里挑一条任务，点开车就是车头。</div>";
      view.innerHTML = html;
      return;
    }
    html += '<div class="list">';
    state.teams.forEach(function (team) {
      html += teamHtml(team);
    });
    html += "</div>";
    view.innerHTML = html;
  }

  function teamHtml(team) {
    var color = CAT_COLORS[team.category] || "#d9b26a";
    var cls = "team";
    if (team.joined) { cls += " mine"; }
    if (team.full) { cls += " full"; }
    var slots = "";
    for (var i = 0; i < team.size; i += 1) {
      var member = team.members[i];
      if (member) {
        var slotCls = "slot filled";
        if (member.host) { slotCls += " host"; }
        if (member.you) { slotCls += " you"; }
        // 星号会占掉一格里的宽度，把「虚空老司机」挤成「虚空…」；
        // 车头靠金色描边 + 下方「车头 某某」标识，格子只放昵称
        slots += '<div class="' + slotCls + '">' + esc(member.name) + "</div>";
      } else {
        slots += '<div class="slot empty-slot">空位</div>';
      }
    }
    var info = "车头 <b>" + esc((team.members.filter(function (m) { return m.host; })[0] || {}).name || "—") +
      "</b> ｜ " + team.count + "/" + team.size;
    if (team.left) { info += " ｜ " + esc(team.left); }
    if (team.start_text) { info += " ｜ " + esc(team.start_text) + " 开始"; }
    var actions = "";
    if (team.host) {
      actions = '<button class="btn danger" data-close="' + esc(team.id) + '" type="button">解散</button>';
    } else if (team.joined) {
      actions = '<button class="btn ghost" data-leave="' + esc(team.id) + '" type="button">退出</button>';
    } else if (team.full) {
      actions = '<button class="btn" disabled type="button">已满</button>';
    } else if (!state.mine) {
      actions = '<button class="btn primary" data-join="' + esc(team.id) + '" type="button">进队</button>';
    } else {
      actions = '<button class="btn" disabled type="button">已有队</button>';
    }
    return '<article class="' + cls + '" style="--cat:' + color + '">' +
      '<div class="t-top">' +
        '<span class="t-kicker"><i></i>' + esc(team.label) + "</span>" +
        '<span class="t-state' + (team.full ? " full" : "") + '">' +
          (team.full ? "已满员" : "可加入") + "</span>" +
      "</div>" +
      '<div class="t-title">' + esc(team.title) + "</div>" +
      '<div class="t-target">' + esc(team.target) + "</div>" +
      (team.note ? '<div class="t-note">' + esc(team.note) + "</div>" : "") +
      '<div class="slots">' + slots + "</div>" +
      '<div class="t-foot"><div class="t-info">' + info + "</div>" +
        '<div class="t-actions">' + actions + "</div></div>" +
    "</article>";
  }

  /* ------------------------------------------------------------ 开车面板 */
  function findItem(cat, key) {
    var data = state.missions[cat];
    if (!data) { return null; }
    for (var i = 0; i < data.items.length; i += 1) {
      if (data.items[i].key === key) { return data.items[i]; }
    }
    return null;
  }

  function openDrive(cat, key) {
    var item = findItem(cat, key);
    if (!item) { toast("这条任务刚结束了，刷新看看", true); return; }
    var sizes = "";
    [2, 3, 4].forEach(function (n) {
      sizes += '<button type="button" data-size="' + n + '" class="' + (n === 4 ? "on" : "") + '">' + n + " 人</button>";
    });
    var subtitle = item.target.label || item.subtitle || "";
    if (item.meta && subtitle.indexOf(item.meta) < 0) {
      subtitle += " ｜ " + item.meta;
    }
    openSheet(
      "<h3>开车 · " + esc(catLabel(cat)) + "</h3>" +
      '<p class="sheet-sub">' + esc(subtitle) + "</p>" +
      '<div class="field"><label>人数上限</label><div class="seg" id="sizeSeg">' + sizes + "</div></div>" +
      '<div class="field"><label>备注（可选）</label>' +
        '<input id="noteInput" maxlength="20" placeholder="比如：只带萌新 / 打 3 轮"></div>' +
      '<div class="sheet-actions">' +
        '<button class="btn ghost" data-cancel="1" type="button">取消</button>' +
        '<button class="btn primary" id="driveOk" type="button">确认开车</button>' +
      "</div>"
    );
    var picked = 4;
    var seg = document.getElementById("sizeSeg");
    seg.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-size]");
      if (!btn) { return; }
      picked = parseInt(btn.dataset.size, 10);
      Array.prototype.forEach.call(seg.children, function (node) {
        node.className = node === btn ? "on" : "";
      });
    });
    document.getElementById("driveOk").addEventListener("click", async function () {
      if (state.busy) { return; }
      state.busy = true;
      try {
        var data = await api("/api/team/create", {
          category: cat, mission_key: key, size: picked,
          note: document.getElementById("noteInput").value
        });
        closeSheet();
        state.teams = data.state.teams;
        state.mine = data.state.mine;
        state.tab = "teams";
        renderTabs();
        render();
        toast("已开车，等队友进队");
      } catch (err) {
        toast(err.message, true);
      } finally {
        state.busy = false;
      }
    });
  }

  function openWatch(cat) {
    var data = state.missions[cat] || { items: [] };
    var missions = {}, tiers = {};
    data.items.forEach(function (item) {
      if (item.mission) { missions[item.mission] = 1; }
      if (item.tier) { tiers[item.tier] = 1; }
    });
    function options(map, all) {
      var html = '<option value="">' + all + "</option>";
      Object.keys(map).forEach(function (key) {
        html += '<option value="' + esc(key) + '">' + esc(key) + "</option>";
      });
      return html;
    }
    var sizes = "";
    [2, 3, 4].forEach(function (n) {
      sizes += '<button type="button" data-size="' + n + '" class="' + (n === 4 ? "on" : "") + '">' + n + " 人</button>";
    });
    openSheet(
      "<h3>等下一波 · " + esc(catLabel(cat)) + "</h3>" +
      '<p class="sheet-sub">任务一出现就在群里 @ 全队；同一波已经在场上的不算。</p>' +
      '<div class="field"><label>难度</label><select id="watchHard">' +
        '<option value="">任意</option><option value="0">普通</option><option value="1">钢铁</option>' +
      "</select></div>" +
      '<div class="field"><label>任务类型</label><select id="watchMission">' +
        options(missions, "任意") + "</select></div>" +
      (cat === "fissure"
        ? '<div class="field"><label>遗物纪元</label><select id="watchTier">' +
          options(tiers, "任意") + "</select></div>"
        : "") +
      '<div class="field"><label>人数上限</label><div class="seg" id="sizeSeg">' + sizes + "</div></div>" +
      '<div class="sheet-actions">' +
        '<button class="btn ghost" data-cancel="1" type="button">取消</button>' +
        '<button class="btn primary" id="watchOk" type="button">开始等</button>' +
      "</div>"
    );
    var picked = 4;
    var seg = document.getElementById("sizeSeg");
    seg.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-size]");
      if (!btn) { return; }
      picked = parseInt(btn.dataset.size, 10);
      Array.prototype.forEach.call(seg.children, function (node) {
        node.className = node === btn ? "on" : "";
      });
    });
    document.getElementById("watchOk").addEventListener("click", async function () {
      if (state.busy) { return; }
      var hardRaw = document.getElementById("watchHard").value;
      var payload = {
        category: cat, size: picked,
        watch: {
          mission: document.getElementById("watchMission").value,
          tier: document.getElementById("watchTier")
            ? document.getElementById("watchTier").value : "",
          hard: hardRaw === "" ? null : hardRaw === "1"
        }
      };
      state.busy = true;
      try {
        var result = await api("/api/team/create", payload);
        closeSheet();
        state.teams = result.state.teams;
        state.mine = result.state.mine;
        state.tab = "teams";
        renderTabs();
        render();
        toast("已在等任务，出现就提醒");
      } catch (err) {
        toast(err.message, true);
      } finally {
        state.busy = false;
      }
    });
  }

  function openName(force) {
    var hint = force
      ? "第一次进来，先留个名字，队友才知道是谁。"
      : "改个名字，队友看到的就是这个。";
    openSheet(
      "<h3>你的昵称</h3>" +
      '<p class="sheet-sub">' + hint + "最多 12 个字。</p>" +
      '<div class="field"><label>昵称</label>' +
        '<input id="nameInput" maxlength="12" value="' + esc(state.me ? state.me.name : "") + '"></div>' +
      '<div class="sheet-actions">' +
        (force ? "" : '<button class="btn ghost" data-cancel="1" type="button">取消</button>') +
        '<button class="btn primary" id="nameOk" type="button">保存</button>' +
      "</div>"
    );
    document.getElementById("nameOk").addEventListener("click", async function () {
      var name = document.getElementById("nameInput").value.trim();
      if (!name) { toast("昵称不能为空", true); return; }
      try {
        var data = await api("/api/team/name", { name: name });
        state.me = data.profile;
        whoBtn.textContent = profileLabel();
        whoBtn.className = "who ok";
        closeSheet();
        toast("已保存");
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  /* ------------------------------------------------------------ 面板控制 */
  function openSheet(html) {
    sheet.innerHTML = '<div class="sheet-card">' + html + "</div>";
    sheet.hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeSheet() {
    sheet.hidden = true;
    sheet.innerHTML = "";
    document.body.style.overflow = "";
  }

  /* ------------------------------------------------------------ 事件 */
  tabs.addEventListener("click", async function (event) {
    var btn = event.target.closest("[data-tab]");
    if (!btn) { return; }
    var key = btn.dataset.tab;
    state.tab = key;
    tabs.querySelectorAll(".tab").forEach(function (node) {
      node.classList.toggle("active", node === btn);
    });
    if (key !== "teams" && !state.missions[key]) {
      view.innerHTML = '<div class="loading">正在取' + esc(catLabel(key)) + "…</div>";
      try { await loadMissions(key); } catch (err) { toast(err.message, true); return; }
    }
    renderTabs();
    render();
  });

  view.addEventListener("click", async function (event) {
    var drive = event.target.closest("[data-drive]");
    if (drive) { openDrive(drive.dataset.cat, drive.dataset.drive); return; }
    var watch = event.target.closest("[data-watch]");
    if (watch) { openWatch(watch.dataset.watch); return; }
    var join = event.target.closest("[data-join]");
    if (join) { await act("/api/team/join", join.dataset.join, "已进队"); return; }
    var leave = event.target.closest("[data-leave]");
    if (leave) { await act("/api/team/leave", leave.dataset.leave, "已退出"); return; }
    var close = event.target.closest("[data-close]");
    if (close) {
      if (!confirm("解散这支车队？")) { return; }
      await act("/api/team/close", close.dataset.close, "已解散");
    }
  });

  async function act(path, id, okText) {
    if (state.busy) { return; }
    state.busy = true;
    try {
      var data = await api(path, { id: id });
      state.teams = data.state.teams;
      state.mine = data.state.mine;
      renderTabs();
      render();
      toast(okText);
    } catch (err) {
      toast(err.message, true);
    } finally {
      state.busy = false;
    }
  }

  sheet.addEventListener("click", function (event) {
    if (event.target === sheet || event.target.closest("[data-cancel]")) {
      closeSheet();
    }
  });

  whoBtn.addEventListener("click", function () {
    if (state.me) { openName(false); }
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && state.me) { refreshAll(false).catch(function () {}); }
  });

  setInterval(function () {
    if (!state.me || document.hidden) { return; }
    refreshAll(false).catch(function () {});
  }, 25000);

  boot().catch(function (err) {
    if (err.message !== "need_login") {
      view.innerHTML = '<div class="login"><h2>打不开面板</h2>' + esc(err.message) + "</div>";
    }
  });
})();
