(function () {
  "use strict";

  const state = {
    token: sessionStorage.getItem("wfdb_token") || "",
    view: "search",
  };

  const $ = (id) => document.getElementById(id);
  const authError = new Error("unauthorized");

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmt(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") return value.toLocaleString("zh-CN");
    return String(value);
  }

  function showError(message) {
    const box = $("errorBox");
    if (!message) {
      box.classList.add("hidden");
      box.textContent = "";
      return;
    }
    box.textContent = message;
    box.classList.remove("hidden");
  }

  function showToken(show) {
    $("tokenPanel").classList.toggle("hidden", !show);
    if (show) $("tokenInput").focus();
  }

  async function api(path) {
    const headers = {};
    if (state.token) headers["X-API-Token"] = state.token;
    const response = await fetch(path, { headers });
    let payload = null;
    try {
      payload = await response.json();
    } catch (err) {
      throw new Error("服务返回了非 JSON 响应");
    }
    if (response.status === 401) {
      showToken(true);
      throw authError;
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || payload.message || ("HTTP " + response.status));
    }
    return payload;
  }

  function table(headers, rows, options) {
    if (!rows || !rows.length) return el("div", "empty", "（无结果）");
    const wrap = el("div");
    const t = el("table");
    const thead = el("thead");
    const tr = el("tr");
    headers.forEach((item) => tr.appendChild(el("th", "", item.label)));
    thead.appendChild(tr);
    t.appendChild(thead);
    const tbody = el("tbody");
    rows.forEach((row) => {
      const line = el("tr", options && options.clickable ? "clickable" : "");
      headers.forEach((item) => {
        const cell = el("td", item.className || "");
        cell.textContent = item.format ? item.format(row[item.key], row) : fmt(row[item.key]);
        line.appendChild(cell);
      });
      if (options && options.onClick) {
        line.addEventListener("click", () => options.onClick(row));
      }
      tbody.appendChild(line);
    });
    t.appendChild(tbody);
    wrap.appendChild(t);
    return wrap;
  }

  function renderStats(data) {
    $("statRows").textContent = fmt(data.total_rows);
    $("statItems").textContent = fmt((data.key || {}).items);
    $("statRelations").textContent = fmt((data.key || {}).item_relations);
    $("statSchema").textContent = fmt((data.meta || {}).schema_version);
    $("statBuilt").textContent = fmt((data.meta || {}).built_at);
  }

  async function loadHealth() {
    try {
      const response = await fetch("/health");
      const data = await response.json();
      const pill = $("health");
      if (data.ok) {
        pill.textContent = "数据库在线";
        pill.className = "pill ok";
      } else {
        pill.textContent = "数据库不可用";
        pill.className = "pill bad";
        showError(data.message || "数据库不可用");
      }
    } catch (err) {
      const pill = $("health");
      pill.textContent = "服务不可达";
      pill.className = "pill bad";
      showError("无法连接数据库服务");
    }
  }

  async function loadStats() {
    try {
      const data = await api("/stats");
      renderStats(data);
    } catch (err) {
      if (err !== authError) showError(err.message);
    }
  }

  async function search() {
    const q = $("searchInput").value.trim();
    if (!q) return;
    showError("");
    $("itemDetail").classList.add("hidden");
    const box = $("searchResult");
    box.replaceChildren(el("div", "empty", "搜索中…"));
    try {
      const data = await api("/search?q=" + encodeURIComponent(q) + "&limit=30");
      box.replaceChildren(table([
        { key: "slug", label: "SLUG", className: "slug" },
        { key: "name_zh", label: "中文名" },
        { key: "name_en", label: "英文名" },
        { key: "ducats", label: "杜卡德", className: "num" },
        { key: "mastery_rank", label: "MR", className: "num" },
      ], data.items, { clickable: true, onClick: (row) => showItem(row.slug) }));
    } catch (err) {
      if (err !== authError) box.replaceChildren(el("div", "empty", err.message));
    }
  }

  async function showItem(slug) {
    const panel = $("itemDetail");
    panel.classList.remove("hidden");
    panel.replaceChildren(el("div", "empty", "加载中…"));
    try {
      const data = await api("/item?q=" + encodeURIComponent(slug));
      const item = data.item;
      const frag = document.createDocumentFragment();
      frag.appendChild(el("h3", "", item.name_zh || item.name_en || item.slug));
      frag.appendChild(el("div", "sub", item.slug));
      const meta = el("div", "meta");
      [["英文名", item.name_en], ["杜卡德", item.ducats], ["MR", item.mastery_rank],
       ["可交易", item.tradable]].forEach(([label, value]) => {
        const box = el("div", "", label);
        box.appendChild(el("strong", "", fmt(value)));
        meta.appendChild(box);
      });
      frag.appendChild(meta);
      if (item.tags) {
        const chips = el("div", "chips");
        String(item.tags).split(",").forEach((tag) => chips.appendChild(el("span", "chip", tag)));
        frag.appendChild(chips);
      }
      if (item.aliases) {
        frag.appendChild(el("div", "hint", "别名：" + item.aliases));
      }
      if (item.parts && item.parts.length) {
        frag.appendChild(el("div", "hint", "套装部件（" + item.parts.length + "）："));
        const chips = el("div", "chips");
        item.parts.forEach((part) => chips.appendChild(
          el("span", "chip part", part.child_name_zh || part.child_slug)));
        frag.appendChild(chips);
      }
      if (item.price_quotes && item.price_quotes.length) {
        const prices = item.price_quotes.map((p) =>
          p.kind + " " + fmt(p.price) + "p / " + fmt(p.orders) + " 单").join(" · ");
        frag.appendChild(el("div", "hint", "行情：" + prices));
      }
      panel.replaceChildren(frag);
    } catch (err) {
      if (err !== authError) panel.replaceChildren(el("div", "empty", err.message));
    }
  }

  async function queryParts() {
    const q = $("partsInput").value.trim();
    if (!q) return;
    showError("");
    const box = $("partsResult");
    box.replaceChildren(el("div", "empty", "查询中…"));
    try {
      const data = await api("/parts?q=" + encodeURIComponent(q));
      const head = el("div", "detail");
      head.appendChild(el("h3", "", data.name_zh || data.name_en || data.slug));
      head.appendChild(el("div", "sub", data.slug));
      const chips = el("div", "chips");
      (data.parts || []).forEach((part) => chips.appendChild(
        el("span", "chip part", (part.child_name_zh || part.child_slug))));
      head.appendChild(chips);
      box.replaceChildren(head);
    } catch (err) {
      if (err !== authError) box.replaceChildren(el("div", "empty", err.message));
    }
  }

  async function validate() {
    const box = $("validateResult");
    box.replaceChildren(el("div", "empty", "校验中…"));
    try {
      const data = await api("/validate");
      const frag = document.createDocumentFragment();
      const pill = el("div", "chips");
      pill.appendChild(el("span", data.ok ? "chip" : "chip part",
        data.ok ? "完整性通过" : "校验失败"));
      pill.appendChild(el("span", "chip", "integrity=" + fmt(data.integrity)));
      frag.appendChild(pill);
      if (data.errors && data.errors.length) {
        frag.appendChild(el("div", "hint", "错误：" + data.errors.join("；")));
      }
      if (data.warnings && data.warnings.length) {
        frag.appendChild(el("div", "hint", "警告：" + data.warnings.join("；")));
      }
      box.replaceChildren(frag);
    } catch (err) {
      if (err !== authError) box.replaceChildren(el("div", "empty", err.message));
    }
  }

  async function loadSources() {
    const box = $("sourcesResult");
    box.replaceChildren(el("div", "empty", "加载中…"));
    try {
      const data = await api("/sources");
      box.replaceChildren(table([
        { key: "name", label: "来源" },
        { key: "kind", label: "类型" },
        { key: "rows", label: "行数", className: "num" },
        { key: "path", label: "路径", className: "slug" },
        { key: "note", label: "说明" },
      ], data.sources));
    } catch (err) {
      if (err !== authError) box.replaceChildren(el("div", "empty", err.message));
    }
  }

  function switchView(name) {
    state.view = name;
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.view === name);
    });
    ["search", "parts", "validate", "sources"].forEach((key) => {
      $("view-" + key).classList.toggle("hidden", key !== name);
    });
    if (name === "sources") loadSources();
  }

  function bind() {
    $("searchBtn").addEventListener("click", search);
    $("searchInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") search();
    });
    $("partsBtn").addEventListener("click", queryParts);
    $("partsInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") queryParts();
    });
    $("validateBtn").addEventListener("click", validate);
    $("sourcesBtn").addEventListener("click", loadSources);
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => switchView(tab.dataset.view));
    });
    $("tokenBtn").addEventListener("click", () => {
      const panel = $("tokenPanel");
      const hidden = panel.classList.contains("hidden");
      showToken(hidden);
    });
    $("tokenSave").addEventListener("click", () => {
      state.token = $("tokenInput").value.trim();
      sessionStorage.setItem("wfdb_token", state.token);
      showToken(false);
      showError("");
      loadStats();
    });
    $("tokenClear").addEventListener("click", () => {
      state.token = "";
      sessionStorage.removeItem("wfdb_token");
      $("tokenInput").value = "";
      showToken(false);
      loadStats();
    });
    $("endpoint").textContent = location.origin;
    $("tokenInput").value = state.token;
  }

  bind();
  loadHealth();
  loadStats();
})();
