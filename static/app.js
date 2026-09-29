const state = {
  route: "connect",
  connections: [],
  activeId: localStorage.getItem("tm-active") || "demo",
  tms: [],
  selected: new Set(),
  checks: [],
  selectedChecks: new Set(["empty", "duplicates", "numbers_only", "symbols_only", "whitespace", "end_punct"]),
  settings: { hasKey: false, openai_model: "gpt-4o-mini", llm_limit: 200 },
  glossary: null,
  glossaries: [],
  glossaryNote: "",
  step: 1,
  promptTemplate: "",
  draft: {
    name: "",
    tagText: "",
    tags: [],
    languages: [],
    linked: [],
    mode: "",
    before: "",
    users: "",
    threshold: 80,
    caseSensitive: false,
    symbolPercent: 90,
    glossaryId: "",
    glossaryMode: "demo",
    key: "",
    model: "gpt-4o-mini",
    limit: 200,
    aiName: "",
    aiDetail: "",
    aiPrompt: "",
  },
  reportTab: 0,
  runs: [],
  current: null,
  stats: null,
  filters: { languages: [], errors: [], tags: [], includeSample: true },
  drill: null,
  lokalise: null,
  busy: false,
};

const $ = (sel) => document.querySelector(sel);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function fmt(n) {
  return Number(n || 0).toLocaleString();
}

function pct(part, whole) {
  if (!whole) return 0;
  return (part / whole) * 100;
}

function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : undefined,
    ...options,
    body: options.body && !(options.body instanceof FormData) ? JSON.stringify(options.body) : options.body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : "Request failed");
  }
  return data;
}

function captureDraft() {
  const read = (id) => document.getElementById(id);
  const name = read("run-name");
  if (name) state.draft.name = name.value;
  const tag = read("tag-text");
  if (tag) state.draft.tagText = tag.value;
  const before = read("before-date");
  if (before) state.draft.before = before.value;
  const users = read("user-list");
  if (users) state.draft.users = users.value;
  const threshold = read("threshold");
  if (threshold) state.draft.threshold = threshold.value;
  const glossaryId = read("glossary-id");
  if (glossaryId) state.draft.glossaryId = glossaryId.value;
  const caseBox = read("case-sensitive");
  if (caseBox) state.draft.caseSensitive = caseBox.checked;
  const symbolPercent = read("symbol-percent");
  if (symbolPercent) state.draft.symbolPercent = symbolPercent.value;
  const key = read("api-key");
  if (key) state.draft.key = key.value;
  const aiName = read("ai-name");
  if (aiName) state.draft.aiName = aiName.value;
  const aiDetail = read("ai-detail");
  if (aiDetail) state.draft.aiDetail = aiDetail.value;
  const aiPrompt = read("ai-prompt");
  if (aiPrompt) state.draft.aiPrompt = aiPrompt.value;
  document.querySelectorAll("[data-conn]").forEach((form) => {
    const id = form.dataset.conn;
    state.draft[id] = state.draft[id] || {};
    form.querySelectorAll("[name]").forEach((input) => {
      state.draft[id][input.name] = input.value;
    });
  });
}

function paint() {
  const activeId = document.activeElement && document.activeElement.id;
  const pos = document.activeElement && document.activeElement.selectionStart;
  captureDraft();
  const view = $("#view");
  const route = state.route;
  document.querySelectorAll(".nav a").forEach((link) => {
    const nav = route === "run" || route === "review" ? "history" : route;
    link.classList.toggle("active", link.dataset.route === nav);
  });
  const conn = state.connections.find((item) => item.id === state.activeId);
  $("#side-foot").textContent = conn ? conn.label : "No workspace selected";
  if (route === "connect") view.innerHTML = viewConnect();
  else if (route === "workspace") view.innerHTML = viewWorkspace();
  else if (route === "history") view.innerHTML = viewHistory();
  else if (route === "stats") view.innerHTML = viewStats();
  else if (route === "run") view.innerHTML = viewRun();
  else if (route === "review") view.innerHTML = viewReview();
  bind();
  if (activeId) {
    const el = document.getElementById(activeId);
    if (el) {
      el.focus();
      if (typeof pos === "number" && el.setSelectionRange) {
        try { el.setSelectionRange(pos, pos); } catch (err) { /* date inputs */ }
      }
    }
  }
}

function routeFromHash() {
  const hash = (location.hash || "#connect").slice(1);
  if (hash.startsWith("run/")) {
    state.route = "run";
    state.runId = hash.slice(4);
    loadRun(state.runId);
    return;
  }
  if (hash.startsWith("review/")) {
    state.route = "review";
    state.runId = hash.slice(7);
    loadRun(state.runId);
    return;
  }
  state.route = ["connect", "workspace", "history", "stats"].includes(hash) ? hash : "connect";
  if (state.route === "workspace") loadTms();
  if (state.route === "history") loadRuns();
  if (state.route === "stats") loadStats();
  if (state.route === "connect") paint();
}

async function boot() {
  const [connections, checks, settings, glossary] = await Promise.all([
    api("/api/connections"),
    api("/api/checks"),
    api("/api/settings"),
    api("/api/glossary"),
  ]);
  state.connections = connections.connections;
  state.checks = checks.checks;
  state.promptTemplate = checks.promptTemplate;
  state.draft.aiPrompt = state.draft.aiPrompt || checks.promptTemplate;
  state.settings = settings;
  state.draft.model = settings.openai_model;
  state.draft.limit = settings.llm_limit;
  state.glossary = glossary.glossary;
  window.addEventListener("hashchange", routeFromHash);
  if (!location.hash) location.hash = "#connect";
  else routeFromHash();
}

function viewConnect() {
  const saved = state.connections.filter((item) => !["demo", "local"].includes(item.id));
  return `
    <div class="eyebrow">Translation management</div>
    <h1>Connect a TMS</h1>
    <p class="lede">Sign in with the same credentials the download scripts use. Phrase, Smartcat, and XTM talk to their APIs. Lokalise opens the browser session from the Lokalise script, because that product has no translation-memory API.</p>
    <div class="card" style="margin-bottom:16px">
      <div class="split">
        <div>
          <h2>Try it on sample memories</h2>
          <p class="sub" style="margin:0">The demo workspace lists three TMs and needs no login. Local TMX accepts a file you already downloaded.</p>
        </div>
        <div class="row-actions">
          <button class="btn" data-open="demo" type="button">Open demo</button>
          <button class="btn secondary" data-open="local" type="button">Use a TMX file</button>
        </div>
      </div>
    </div>
    <div class="grid-providers">
      ${providerCard("phrase", "Phrase TMS", "Username and password, or an API token. The script caches the token for 24 hours.", phraseFields())}
      ${providerCard("smartcat", "Smartcat", "Account ID and API key from Settings → API. Pick the region the script calls a server.", smartcatFields())}
      ${providerCard("xtm", "XTM Cloud", "Client name, user ID, and password. The app requests an XTM-Basic token, then lists each customer’s language pairs.", xtmFields())}
      ${providerCard("lokalise", "Lokalise", "Email and password are optional. The first login uses a browser window: open Team settings → Translation memory, then continue here.", lokaliseFields())}
    </div>
    ${saved.length ? `<h2 style="margin-top:22px">Saved connections</h2><div class="grid-2">${saved.map(savedCard).join("")}</div>` : ""}
  `;
}

function providerCard(id, title, copy, fields) {
  const existing = state.connections.find((item) => item.provider === id && !["demo", "local"].includes(item.id));
  const values = state.draft[id] || {};
  const fieldHtml = fields.map((field) => {
    const value = values[field.name] ?? field.value ?? "";
    if (field.type === "select") {
      return `<label class="field"><span>${esc(field.label)}</span><select name="${field.name}">${
        field.options.map((opt) => `<option value="${esc(opt)}" ${opt === value ? "selected" : ""}>${esc(opt)}</option>`).join("")
      }</select></label>`;
    }
    return `<label class="field"><span>${esc(field.label)}</span><input name="${field.name}" type="${field.type || "text"}" value="${esc(value)}" placeholder="${esc(field.placeholder || "")}" autocomplete="off"></label>`;
  }).join("");
  return `<form class="card" data-conn="${id}">
    <div class="provider-head"><div><div class="provider-kicker">${esc(id)}</div><h2>${esc(title)}</h2></div>
      ${existing ? statusBadge(existing) : ""}</div>
    <p class="sub">${esc(copy)}</p>
    <label class="field"><span>Label</span><input name="label" type="text" value="${esc(values.label || existing?.label || title)}"></label>
    ${fieldHtml}
    ${existing ? `<input type="hidden" name="id" value="${esc(existing.id)}">` : ""}
    <div class="row-actions">
      <button class="btn" type="submit">Connect</button>
      ${id === "lokalise" && existing ? `<button class="btn secondary" type="button" data-lokalise="${esc(existing.id)}">Launch browser login</button>` : ""}
    </div>
    ${existing && existing.error ? `<p class="note">${esc(existing.error)}</p>` : ""}
    ${id === "lokalise" && state.lokalise ? `<p class="note teal">${esc(lokaliseMessage())}</p>${state.lokalise.waiting ? `<button class="btn" type="button" id="lokalise-continue">Continue</button>` : ""}` : ""}
  </form>`;
}

function phraseFields() {
  return [
    { name: "region", label: "Region", type: "select", options: ["eu", "us"], value: "eu" },
    { name: "username", label: "Username", placeholder: "PHRASE_USERNAME" },
    { name: "password", label: "Password", type: "password" },
    { name: "token", label: "API token", placeholder: "Optional, instead of a password" },
    { name: "base_url", label: "Base URL", placeholder: "Optional override" },
  ];
}
function smartcatFields() {
  return [
    { name: "server", label: "Server", type: "select", options: ["eu", "us", "ea"], value: "eu" },
    { name: "account_id", label: "Account ID" },
    { name: "api_key", label: "API key", type: "password" },
  ];
}
function xtmFields() {
  return [
    { name: "base_url", label: "Instance URL", placeholder: "https://xtm.cloud", value: "https://xtm.cloud" },
    { name: "client", label: "Client name" },
    { name: "user_id", label: "User ID" },
    { name: "password", label: "Password", type: "password" },
  ];
}
function lokaliseFields() {
  return [
    { name: "email", label: "Email", type: "email" },
    { name: "password", label: "Password", type: "password" },
  ];
}

function statusBadge(conn) {
  const kind = conn.status === "connected" ? "ok" : conn.status === "error" ? "bad" : "warn";
  return `<span class="badge ${kind}">${esc(conn.status)}</span>`;
}

function savedCard(conn) {
  return `<div class="card"><div class="split"><div><strong>${esc(conn.label)}</strong><div class="sub" style="margin:4px 0 0">${esc(conn.account || conn.provider)}</div></div>${statusBadge(conn)}</div>
    <div class="row-actions" style="margin-top:12px">
      <button class="btn small" type="button" data-open-id="${esc(conn.id)}">Use in cleaner</button>
      <button class="btn ghost small" type="button" data-forget="${esc(conn.id)}">Remove</button>
    </div></div>`;
}

function lokaliseMessage() {
  const item = state.lokalise || {};
  if (item.error) return item.error;
  if (item.done) return item.prompt || "Lokalise session saved.";
  if (item.waiting) return `${item.prompt || "Waiting in the browser."} Click Continue when that step is done.`;
  return item.prompt || "Starting the Lokalise browser…";
}

function viewWorkspace() {
  const titles = { 1: "Project", 2: "Checks", 3: "Summary" };
  const ledes = {
    1: "Pick the workspace, name the project, and choose the languages, memories, and earlier runs that belong to this series.",
    2: "Choose the filters. Options open under a filter once it is selected. AI filters only flag segments for a person to review.",
    3: "Check the setup, then start the run.",
  };
  const step = state.step || 1;
  return `
    <div class="eyebrow">Cleaning run</div>
    <h1>${titles[step]}</h1>
    <p class="lede">${ledes[step]}</p>
    ${stepper()}
    ${step === 1 ? stepProject() : step === 2 ? stepChecks() : stepSummary()}`;
}

function stepper() {
  const labels = ["1 Project", "2 Checks", "3 Summary"];
  return `<div class="steps">${labels.map((label, index) => {
    const n = index + 1;
    const klass = n === state.step ? "on" : n < state.step ? "done" : "";
    return `<button type="button" class="${klass}" data-step="${n}">${label}</button>`;
  }).join("")}</div>`;
}

function stepProject() {
  const conn = state.connections.find((item) => item.id === state.activeId) || state.connections[0];
  const options = state.connections.map((item) => `<option value="${esc(item.id)}" ${item.id === state.activeId ? "selected" : ""}>${esc(item.label)}</option>`).join("");
  const langs = allLangs();
  const memories = visibleTms();
  const prior = previousRuns();
  return `<section class="panel">
    <label class="field"><span>TMS workspace</span><select id="connection">${options}</select></label>
    <p class="sub">${esc(conn ? conn.label : "")}${conn && conn.account ? " · " + esc(conn.account) : ""}</p>
    ${conn && conn.provider === "local" ? `<label class="field"><span>Upload TMX</span><input id="tmx-file" type="file" accept=".tmx"></label>` : ""}
    <label class="field"><span>Project name</span><input id="run-name" type="text" value="${esc(state.draft.name)}" placeholder="April product UI"></label>
    <div class="group-label">Languages</div>
    <div class="chips">${langs.map((lang) => `<button type="button" class="chip ${state.draft.languages.includes(lang) ? "on" : ""}" data-lang="${esc(lang)}">${esc(lang)}</button>`).join("") || `<span class="sub">This workspace has not listed any languages yet.</span>`}</div>
    <div class="split"><div class="group-label" style="margin:0">Memories</div><span class="badge rule">${state.selected.size} selected</span></div>
    <div class="tm-list">${state.draft.languages.length ? (memories.length ? memories.map(tmRow).join("") : `<div class="empty">No memories use the selected languages.</div>`) : `<div class="empty">Select languages to list memories.</div>`}</div>
    <div class="group-label">Mode</div>
    <div class="mode-pick">
      <button type="button" class="mode-card ${state.draft.mode === "merged" ? "on" : ""}" data-mode="merged"><strong>Merged</strong><span class="sub">One corpus and one report. Language pairs stay visible inside it.</span></button>
      <button type="button" class="mode-card ${state.draft.mode === "single" ? "on" : ""}" data-mode="single"><strong>Single</strong><span class="sub">A separate report for each memory.</span></button>
    </div>
    <label class="field"><span>Add a tag</span>
      <div class="row-actions"><input id="tag-text" type="text" value="${esc(state.draft.tagText)}" placeholder="product, legal, ui"><button class="btn secondary small" type="button" id="add-tag">Add tag</button></div>
    </label>
    <div class="chips">${state.draft.tags.map((tag) => `<button class="chip on" type="button" data-drop-tag="${esc(tag)}">${esc(tag)} ×</button>`).join("") || `<span class="sub">Tags are optional. They show up as filters on Statistics.</span>`}</div>
    <div class="group-label">Previous runs</div>
    <p class="sub">Link earlier runs so this project keeps their trend on the same memories.</p>
    <div class="tm-list">${prior.length ? prior.map(priorRow).join("") : `<div class="empty">No earlier runs on this workspace.</div>`}</div>
    <div class="row-actions" style="margin-top:16px"><button class="btn" type="button" data-step="2">Next</button></div>
  </section>`;
}

function priorRow(run) {
  const on = state.draft.linked.includes(run.id);
  const same = (run.tmNames || []).some((name) => selectedNames().includes(name));
  return `<button type="button" class="tm-item ${on ? "on" : ""}" data-link="${esc(run.id)}">
    <span class="box" aria-hidden="true"></span>
    <span><span class="name">${esc(run.name)}</span><span class="detail">${esc((run.createdAt || "").slice(0, 10))} · ${esc((run.tmNames || []).join(", ") || "No memory name")}${same ? " · same memory" : ""}</span></span>
  </button>`;
}

function stepChecks() {
  return `<section class="panel">
    <div class="split"><div class="group-label" style="margin:0">Filters</div>
      <div class="row-actions"><button class="btn ghost small" type="button" id="use-recommended">Recommended</button><button class="btn ghost small" type="button" id="clear-checks">Clear</button></div>
    </div>
    ${checkGroup("Deterministic", "rule")}
    ${checkGroup("AI", "ai")}
    <div class="row-actions" style="margin-top:16px">
      <button class="btn ghost" type="button" data-step="1">Back</button>
      <button class="btn" type="button" data-step="3">Next</button>
    </div>
  </section>`;
}

function stepSummary() {
  const conn = state.connections.find((item) => item.id === state.activeId);
  const memories = state.tms.filter((tm) => state.selected.has(tm.id)).map((tm) => tm.name);
  const checks = state.checks.filter((item) => state.selectedChecks.has(item.id));
  const linked = state.runs.filter((run) => state.draft.linked.includes(run.id)).map((run) => run.name);
  return `<section class="panel">
    <p><strong>${esc(state.draft.name || "Untitled")}</strong> · ${esc(conn ? conn.label : "")}</p>
    <p class="sub">Languages · ${esc(state.draft.languages.join(", ") || "none")}</p>
    <p class="sub">Memories · ${esc(memories.join(", ") || "none")}</p>
    <p class="sub">Mode · ${esc(modeLabel(state.draft.mode || (state.selected.size > 1 ? "" : "single")) || "not chosen")}</p>
    <p class="sub">Tags · ${esc(state.draft.tags.join(", ") || "none")}</p>
    <p class="sub">Linked runs · ${esc(linked.join(", ") || "none")}</p>
    <div class="group-label">Filters</div>
    ${checks.map((item) => `<p class="sub">${esc(item.name)} · ${item.kind === "ai" ? "AI, flag only" : "rule"}${optionSummary(item)}</p>`).join("") || `<p class="sub">No filters selected.</p>`}
    <div class="row-actions" style="margin-top:16px">
      <button class="btn ghost" type="button" data-step="2">Back</button>
      <button class="btn" type="button" id="start-run" ${state.busy ? "disabled" : ""}>Start run</button>
    </div>
  </section>`;
}

function optionSummary(check) {
  if (check.id === "glossary" && state.selectedChecks.has("glossary")) {
    const found = glossaryChoices().find((item) => item.id === state.draft.glossaryId);
    return ` · ${found ? found.name : "no glossary"} · threshold ${state.draft.threshold || 80}${state.draft.caseSensitive ? " · case-sensitive" : ""}`;
  }
  if (check.id === "stale_date" && state.draft.before) return ` · before ${state.draft.before}`;
  if (check.id === "modified_by" && state.draft.users) return ` · ${state.draft.users}`;
  if (check.id === "symbols_only") return ` · ${state.draft.symbolPercent || 90}% non-alphabetic`;
  return "";
}

function tmRow(tm) {
  const on = state.selected.has(tm.id);
  const langs = [tm.source, ...(tm.targets || [])].filter(Boolean).join(" → ");
  return `<button type="button" class="tm-item ${on ? "on" : ""}" data-tm="${esc(tm.id)}">
    <span class="box" aria-hidden="true"></span>
    <span><span class="name">${esc(tm.name)}</span><span class="detail">${esc(langs || "Languages come with the file")}${tm.units != null ? ` · ${fmt(tm.units)} TUs` : ""}</span></span>
  </button>`;
}

function checkGroup(title, kind) {
  const items = state.checks.filter((item) => (item.group || (item.kind === "ai" ? "AI" : "Deterministic")) === title);
  if (!items.length && kind !== "ai") return "";
  return `<div class="group-label">${esc(title)} ${kind === "ai" ? "· model" : "· rules"}</div>
    ${items.map(checkRow).join("")}
    ${kind === "ai" ? aiExtras() : ""}`;
}

function checkRow(check) {
  const on = state.selectedChecks.has(check.id);
  return `<div class="check-block ${on ? "open" : ""}">
    <button type="button" class="check-item ${on ? "on" : ""}" data-check="${esc(check.id)}">
      <span class="box" aria-hidden="true"></span>
      <span class="meta"><span class="name">${esc(check.name)}</span><span class="detail">${esc(check.detail || "")}</span></span>
      <span class="badge ${check.kind === "ai" ? "ai" : "rule"}">${check.kind === "ai" ? "AI" : "Rule"}</span>
    </button>
    ${on ? checkOptions(check) : ""}
  </div>`;
}

function checkOptions(check) {
  if (check.id === "glossary") return glossaryOptions();
  if (check.id === "stale_date") {
    return `<div class="check-options"><label class="field"><span>Remove TUs updated before</span><input id="before-date" type="date" value="${esc(state.draft.before)}"></label></div>`;
  }
  if (check.id === "modified_by") {
    return `<div class="check-options"><label class="field"><span>User names, comma separated</span><input id="user-list" type="text" value="${esc(state.draft.users)}" placeholder="olduser, legacy"></label></div>`;
  }
  if (check.id === "symbols_only") {
    return `<div class="check-options"><label class="field"><span>Non-alphabetic share (%)</span><input id="symbol-percent" type="number" min="50" max="100" value="${esc(state.draft.symbolPercent)}"></label><p class="sub">A segment is removed when at least this share of its characters are not letters. The script default is 90.</p></div>`;
  }
  if (check.kind === "ai") {
    return `<div class="check-options"><p class="sub">This model only finds and flags issues. It does not remove or rewrite the segment. On the results page, a reviewer marks each flag delete or keep.</p></div>`;
  }
  return "";
}

function glossaryOptions() {
  const choices = glossaryChoices();
  const options = choices.map((item) => `<option value="${esc(item.id)}" ${state.draft.glossaryId === item.id ? "selected" : ""}>${esc(item.name)}${item.languages && item.languages.length ? ` · ${item.languages.join(", ")}` : ""}${item.entries != null ? ` · ${item.entries} terms` : ""}</option>`).join("");
  return `<div class="check-options">
    <label class="field"><span>Glossary from this TMS</span><select id="glossary-id">${options || `<option value="">No glossaries found</option>`}</select></label>
    ${state.glossaryNote ? `<p class="sub">${esc(state.glossaryNote)}</p>` : ""}
    <div class="grid-2">
      <label class="field"><span>Fuzzy threshold</span><input id="threshold" type="number" min="0" max="100" value="${esc(state.draft.threshold)}"></label>
      <label class="chip" style="align-self:end"><input id="case-sensitive" type="checkbox" ${state.draft.caseSensitive ? "checked" : ""}> Case-sensitive</label>
    </div>
    <label class="field"><span>Upload .xlsx</span><input id="glossary-file" type="file" accept=".xlsx"></label>
    <p class="sub">Glossary compliance uses TMX_TBcompliance.py. A source term without its target term removes the segment.</p>
  </div>`;
}

function glossaryChoices() {
  const items = (state.glossaries || []).map((item) => ({ ...item }));
  if (state.glossary) {
    items.push({ id: "upload", name: `Uploaded workbook · ${state.glossary.name}`, languages: [], entries: state.glossary.entries });
  }
  return items;
}

function allLangs() {
  const set = new Set();
  state.tms.forEach((tm) => {
    if (tm.source) set.add(tm.source);
    (tm.targets || []).forEach((lang) => { if (lang) set.add(lang); });
  });
  return [...set].sort();
}

function visibleTms() {
  if (!state.draft.languages.length) return [];
  return state.tms.filter((tm) => {
    const own = [tm.source, ...(tm.targets || [])].filter(Boolean);
    if (!own.length) return true;
    return own.some((lang) => state.draft.languages.includes(lang));
  });
}

function previousRuns() {
  return state.runs.filter((run) => run.status === "done" && run.connectionId === state.activeId);
}

function selectedNames() {
  return state.tms.filter((tm) => state.selected.has(tm.id)).map((tm) => tm.name);
}

function modeLabel(mode) {
  if (mode === "merged" || mode === "together") return "Merged";
  if (mode === "single" || mode === "separate") return "Single";
  return mode || "";
}

function aiExtras() {
  return `<div class="card" style="margin-top:10px;box-shadow:none">
    <strong>Model for AI checks</strong>
    <p class="sub">The built-in check uses the severity prompt from the GPT cleaning script. Temperature stays at 0.1. The model only flags issues. The key stays on this machine.</p>
    <label class="field"><span>API key ${state.settings.hasKey ? "(saved)" : ""}</span><input id="api-key" type="password" placeholder="${state.settings.hasKey ? "Saved — enter a new key to replace it" : "sk-…"}" value="${esc(state.draft.key)}"></label>
    <div class="grid-2">
      <label class="field"><span>Model</span><select id="model">
        ${["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"].map((model) => `<option value="${model}" ${state.draft.model === model ? "selected" : ""}>${model}${model === "gpt-3.5-turbo" ? " (script default)" : ""}</option>`).join("")}
      </select></label>
      <label class="field"><span>Segment limit</span><input id="limit" type="number" min="1" max="2000" value="${esc(state.draft.limit)}"></label>
    </div>
    <button class="btn secondary small" type="button" id="save-settings">Save model settings</button>
    <div class="group-label">Add an AI check</div>
    <label class="field"><span>Name</span><input id="ai-name" type="text" value="${esc(state.draft.aiName)}" placeholder="Register and tone"></label>
    <label class="field"><span>What it looks for</span><input id="ai-detail" type="text" value="${esc(state.draft.aiDetail)}" placeholder="Shown next to the check"></label>
    <label class="field"><span>Prompt</span><textarea id="ai-prompt">${esc(state.draft.aiPrompt)}</textarea></label>
    <p class="sub">Keep {source} and {target} in the prompt. Ask for Correct, Minor error, Major error, or Critical error. Anything other than Correct is flagged for review.</p>
    <button class="btn secondary small" type="button" id="add-ai">Add AI check</button>
  </div>`;
}


function viewHistory() {
  const rows = state.runs.map((run) => `<tr>
    <td><button class="link" type="button" data-open-run="${esc(run.id)}">${esc(run.name)}</button>
      ${run.sample ? `<span class="badge sample">Sample</span>` : ""}</td>
    <td>${esc((run.createdAt || "").replace("T", " "))}</td>
    <td>${esc((run.tmNames || []).join(", "))}</td>
    <td>${esc(modeLabel(run.mode))}</td>
    <td>${(run.tags || []).map((tag) => `<span class="badge rule">${esc(tag)}</span>`).join(" ")}</td>
    <td>${esc(run.status)}${run.progress ? ` · ${esc(run.progress)}` : ""}</td>
    <td><button class="btn ghost small" type="button" data-delete-run="${esc(run.id)}">Delete</button></td>
  </tr>`).join("");
  return `<div class="eyebrow">Saved runs</div><h1>History</h1>
    <p class="lede">Every finished run stays here, including the sample history used for trends. Open a row to see the same report the cleaner draws.</p>
    <div class="panel"><table class="grid"><thead><tr><th>Run</th><th>When</th><th>Memories</th><th>Mode</th><th>Tags</th><th>Status</th><th></th></tr></thead>
    <tbody>${rows || `<tr><td colspan="7" class="empty">No runs yet.</td></tr>`}</tbody></table></div>`;
}

function viewRun() {
  const run = state.current;
  if (!run) return `<h1>Loading report…</h1>`;
  if (run.status === "running") {
    return `<div class="eyebrow">Cleaning</div><h1>${esc(run.name)}</h1><p class="lede">${esc(run.progress || "Working…")}</p><div class="note teal">The report will replace this as soon as the checks finish.</div>`;
  }
  if (run.status === "error") {
    return `<div class="eyebrow">Cleaning</div><h1>${esc(run.name)}</h1><div class="note">${esc(run.error || "The run failed.")}</div>`;
  }
  const reports = run.reports || [];
  const tab = Math.min(state.reportTab, Math.max(reports.length - 1, 0));
  const report = reports[tab];
  return `<div class="eyebrow">Translation memory · quality report</div>
    <h1>${esc(run.name)}</h1>
    <p class="lede">${esc((run.createdAt || "").replace("T", " "))} · ${esc(run.connectionLabel || "")} · ${esc(modeLabel(run.mode))} · ${(run.tags || []).map(esc).join(", ") || "no tags"}${run.sample ? " · sample history" : ""}</p>
    ${resultActions(run)}
    ${historyStrip(run)}
    ${reports.length > 1 ? `<div class="mini-bars">${reports.map((item, index) => miniBar(item, index)).join("")}</div>
      <div class="tabs">${reports.map((item, index) => `<button class="tab ${index === tab ? "on" : ""}" type="button" data-tab="${index}">${esc(item.tmName)}</button>`).join("")}</div>` : ""}
    ${report ? reportBody(report, run) : `<div class="empty">This run has no report.</div>`}`;
}

function miniBar(report, index) {
  const width = (key) => pct(report[key], report.original).toFixed(2);
  return `<button class="mini" type="button" data-tab="${index}" style="background:none;border:0;padding:0;text-align:left">
    <span>${esc(report.tmName)}</span>
    <span class="hero-bar" style="height:16px"><span class="hero-seg hero-removed" style="width:${width("removed")}%"></span><span class="hero-seg hero-fixed" style="width:${width("fixed")}%"></span><span class="hero-seg hero-untouched" style="width:${width("untouched")}%"></span></span>
    <span>${pct(report.removed + report.fixed, report.original).toFixed(1)}%</span>
  </button>`;
}

function reportBody(report, run) {
  const original = report.original || 0;
  const seg = (key, klass, label) => {
    const value = pct(report[key], original);
    if (value <= 0) return "";
    const text = value >= 12 ? `${label} · ${value.toFixed(0)}%` : "";
    return `<div class="hero-seg ${klass}" style="width:${value}%" title="${label}: ${fmt(report[key])}"><span>${text}</span></div>`;
  };
  const issues = (report.byCheck || []).filter((item) => item.applied && item.removed > 0)
    .sort((a, b) => b.removed - a.removed);
  const fixes = (report.byCheck || []).filter((item) => item.applied && item.fixed > 0)
    .sort((a, b) => b.fixed - a.fixed);
  const maxIssue = Math.max(1, ...issues.map((item) => item.removed));
  const maxFix = Math.max(1, ...fixes.map((item) => item.fixed));
  const notes = (report.notes || []).map((note) => `<div class="note">${esc(note)}</div>`).join("");
  const aiCount = report.aiCount || (report.aiRows || []).length;
  return `${notes}
    ${aiCount ? `<div class="note teal">${fmt(aiCount)} segments were flagged by AI and stay in the TM until a reviewer marks them delete.</div>` : ""}
    <div class="hero-label">Corpus composition after cleaning</div>
    <div class="hero-bar">${seg("removed", "hero-removed", "Removed")}${seg("fixed", "hero-fixed", "Fixed")}${seg("untouched", "hero-untouched", "Untouched")}</div>
    <div class="hero-legend">
      <span><i class="swatch" style="background:var(--teal)"></i>Untouched and kept · ${fmt(report.untouched)} (${pct(report.untouched, original).toFixed(1)}%)</span>
      <span><i class="swatch" style="background:var(--gold)"></i>Fixed and kept · ${fmt(report.fixed)} (${pct(report.fixed, original).toFixed(1)}%)</span>
      <span><i class="swatch" style="background:var(--coral)"></i>Removed · ${fmt(report.removed)} (${pct(report.removed, original).toFixed(1)}%)</span>
    </div>
    <div class="stat-cards">
      ${statCard("", fmt(original), "Original TUs")}
      ${statCard("c-red", fmt(report.removed), `<b>${pct(report.removed, original).toFixed(1)}%</b> removed`)}
      ${statCard("c-gold", fmt(report.fixed), `<b>${pct(report.fixed, original).toFixed(1)}%</b> fixed and kept`)}
      ${statCard("c-teal", fmt(report.final), `<b>${pct(report.final, original).toFixed(1)}%</b> in the final set`)}
    </div>
    <div class="panels">
      <div class="panel"><h2>Issues found per filter</h2><div class="sub">TUs removed · colour shows share of the largest filter</div>
        ${issues.length ? issues.map((item) => barRow(item.name, item.removed, item.removed / maxIssue * 100, `${pct(item.removed, original).toFixed(1)}% of corpus`, item.kind === "ai" ? "dot-high" : "dot-high", "fill-high")).join("") : `<div class="empty">No segments were removed.</div>`}
      </div>
      <div class="panel"><h2>Fixes applied per filter</h2><div class="sub">TUs kept but corrected by a rule</div>
        ${fixes.length ? fixes.map((item) => barRow(item.name, item.fixed, item.fixed / maxFix * 100, `${pct(item.fixed, original).toFixed(1)}% of corpus`, "dot-fix", "fill-fix")).join("") : `<div class="empty">No automatic fixes.</div>`}
      </div>
    </div>
    ${glossaryBlock(report.glossary)}
    <div class="panel" style="margin-top:16px"><h2>Languages in this run</h2>
      ${(report.languages || []).map((lang) => barRow(lang.pair, lang.removed + lang.fixed, pct(lang.removed + lang.fixed, lang.original), `${fmt(lang.original)} TUs`, "dot-low", "fill-low")).join("") || `<div class="empty">No language pairs.</div>`}
    </div>
    <div class="panel" style="margin-top:16px"><h2>Flagged segments</h2>
      <div class="sub">${fmt(report.rowCount || (report.rows || []).length)} rows · showing ${fmt((report.rows || []).length)}</div>
      <table class="grid"><thead><tr><th>ID</th><th>Source</th><th>Target</th><th>Language</th><th>Outcome</th><th>Detail</th></tr></thead>
      <tbody>${(report.rows || []).map(segmentRow).join("") || `<tr><td colspan="6" class="empty">Nothing was flagged.</td></tr>`}</tbody></table>
    </div>
    <div class="footer-note"><span>${(report.notApplied || []).length ? "Not run: " + esc(report.notApplied.join("; ")) : "Every selected check ran."}</span><span>${esc((run.checks || []).map((item) => `${item.name} (${item.kind === "ai" ? "AI" : "rule"})`).join(" · "))}</span></div>`;
}

function statCard(klass, num, label) {
  return `<div class="card ${klass}"><div class="num">${num}</div><div class="lbl">${label}</div></div>`;
}

function barRow(label, count, width, status, dot, fill) {
  const safe = Math.max(0, Math.min(100, width || 0));
  return `<div class="bar-row"><span class="dot ${dot}"></span><span class="bar-label" title="${esc(label)}">${esc(label)}</span>
    <div class="bar-track"><div class="bar-fill ${fill}" style="width:${safe}%"></div></div>
    <span class="bar-count">${fmt(count)}</span><span class="bar-status">${esc(status)}</span></div>`;
}

function glossaryBlock(block) {
  if (!block) return "";
  const flagged = pct(block.flagged, block.original);
  const kept = pct(block.final, block.original);
  return `<div class="panel" style="margin-top:16px">
    <div class="hero-label">Glossary compliance</div>
    <div class="hero-bar">
      <div class="hero-seg hero-removed" style="width:${flagged}%" title="Flagged: ${fmt(block.flagged)}"><span>${flagged >= 12 ? `Flagged · ${flagged.toFixed(1)}%` : ""}</span></div>
      <div class="hero-seg hero-untouched" style="width:${kept}%" title="Final: ${fmt(block.final)}"><span>${kept >= 12 ? `Final · ${kept.toFixed(1)}%` : ""}</span></div>
    </div>
    <div class="hero-legend">
      <span><i class="swatch" style="background:var(--teal)"></i>Final TM · ${fmt(block.final)} (${kept.toFixed(1)}%)</span>
      <span><i class="swatch" style="background:var(--coral)"></i>Flagged for review · ${fmt(block.flagged)} (${flagged.toFixed(1)}%)</span>
    </div>
    <div class="stat-cards" style="grid-template-columns:repeat(3,1fr)">
      ${statCard("", fmt(block.original), "Original TUs")}
      ${statCard("c-red", fmt(block.flagged), `<b>${flagged.toFixed(1)}%</b> flagged`)}
      ${statCard("c-teal", fmt(block.final), `<b>${kept.toFixed(1)}%</b> retained`)}
    </div>
    <p class="sub">${esc(block.note || "Translation units after terminology verification")}</p>
  </div>`;
}

function resultActions(run) {
  const groups = reviewGroups(run);
  const pending = groups.filter((item) => !(run.reviews || {})[item.key]).length;
  const deleted = groups.filter((item) => (run.reviews || {})[item.key] === "delete" && !item.alreadyRemoved).length;
  const commit = run.commit;
  return `<div class="row-actions" style="margin:12px 0">
    <a class="btn secondary" href="#review/${esc(run.id)}">Review AI flags${groups.length ? ` · ${groups.length}` : ""}</a>
    <button class="btn" type="button" id="commit-run" ${run.status !== "done" ? "disabled" : ""}>Commit to TMS</button>
  </div>
  <p class="sub">${groups.length ? (pending ? `${pending} AI flags still need a decision. Undecided segments stay in the final TM.` : "Every AI flag has a decision.") : "No AI flags on this run."}${deleted ? ` ${deleted} marked delete will be left out of the upload.` : ""}${commit ? ` Last commit: ${esc(commit.message)}` : ""}</p>`;
}

function historyStrip(run) {
  const names = new Set(run.tmNames || []);
  const linked = new Set(run.linkedRunIds || []);
  const rows = state.runs.filter((item) => item.status === "done" && item.id !== run.id && (linked.has(item.id) || (item.tmNames || []).some((name) => names.has(name))));
  if (!rows.length && !(run.linkedRunIds || []).length) return "";
  const shown = rows.slice(0, 8);
  return `<div class="panel" style="margin-bottom:16px"><h2>History on these memories</h2>
    <div class="sub">${linked.size ? `${linked.size} linked ${linked.size === 1 ? "run" : "runs"} from the project step.` : "Earlier runs that share a memory name."}</div>
    ${shown.map((item) => `<p class="sub"><button class="link" type="button" data-open-run="${esc(item.id)}">${esc(item.name)}</button> · ${esc((item.createdAt || "").slice(0, 10))} · ${esc(modeLabel(item.mode))}</p>`).join("") || `<p class="sub">The linked runs are not in this list.</p>`}
  </div>`;
}

function viewReview() {
  const run = state.current;
  if (!run) return `<h1>Loading review…</h1>`;
  const groups = reviewGroups(run);
  return `<div class="eyebrow">Human review</div>
    <h1>${esc(run.name)}</h1>
    <p class="lede">Each row was flagged by an AI check. Delete removes it from the TM that gets uploaded. Keep leaves it in.</p>
    <div class="row-actions"><a class="btn ghost" href="#run/${esc(run.id)}">Back to the report</a></div>
    ${groups.length ? groups.map(reviewCard).join("") : `<div class="empty" style="margin-top:16px">This run has no AI flags.</div>`}`;
}

function reviewCard(item) {
  const run = state.current || {};
  const decision = (run.reviews || {})[item.key] || "";
  const reasons = item.reasons || [item.reason];
  return `<article class="review-card">
    <div class="split"><strong>${esc(item.tuid)}</strong><span class="badge ${decision === "delete" ? "bad" : decision === "keep" ? "ok" : "ai"}">${decision || "pending"}</span></div>
    <p class="sub">${esc(item.pair || "")}${item.tmName ? " · " + esc(item.tmName) : ""} · ${esc(item.checkName || "AI")}</p>
    <div class="pair"><div><div class="sub">Source</div><p>${esc(item.source)}</p></div><div><div class="sub">Target</div><p>${esc(item.target)}</p></div></div>
    <p><strong>Reason.</strong> ${reasons.map(esc).join(" ")}</p>
    ${item.alreadyRemoved ? `<p class="sub">A cleaning rule already removed this segment. Delete or keep does not put it back.</p>` : ""}
    <div class="row-actions">
      <button class="btn small ${decision === "delete" ? "" : "secondary"}" type="button" data-decision="delete" data-key="${esc(item.key)}">Delete</button>
      <button class="btn small ${decision === "keep" ? "" : "secondary"}" type="button" data-decision="keep" data-key="${esc(item.key)}">Keep</button>
    </div>
  </article>`;
}

function reviewGroups(run) {
  const map = new Map();
  aiItems(run).forEach((item) => {
    const current = map.get(item.key);
    if (!current) map.set(item.key, { ...item, reasons: [item.reason].filter(Boolean) });
    else if (item.reason && !current.reasons.includes(item.reason)) current.reasons.push(item.reason);
  });
  return [...map.values()];
}

function aiItems(run) {
  if (!run) return [];
  const out = [];
  const aiIds = new Set((run.checks || []).filter((item) => item.kind === "ai").map((item) => item.id));
  aiIds.add("llm_severity");
  (run.reports || []).forEach((report, index) => {
    if (report.aiRows && report.aiRows.length) {
      report.aiRows.forEach((row) => out.push(row));
      return;
    }
    (report.rows || []).forEach((row) => {
      const hit = (row.checks || []).some((id) => aiIds.has(id));
      const text = row.detail || "";
      if (!hit && !/error severity/i.test(text)) return;
      const ruleRemoved = (row.checks || []).some((id) => !aiIds.has(id));
      out.push({
        key: `sample:${index}:${row.tuid}`,
        tuid: row.tuid,
        source: row.source,
        target: row.target,
        pair: row.pair,
        checkName: "Error severity",
        reason: text || "Flagged by an AI check.",
        alreadyRemoved: ruleRemoved,
      });
    });
  });
  return out;
}

function segmentRow(row) {
  const names = (row.checks || []).join(", ");
  return `<tr class="${esc(row.status)}"><td>${esc(row.tuid)}</td><td>${esc(row.source)}</td><td>${esc(row.target)}</td><td>${esc(row.pair)}</td><td>${esc(row.status)}<div class="sub">${esc(names)}</div></td><td>${esc(row.detail || "")}</td></tr>`;
}

function viewStats() {
  const stats = state.stats;
  if (!stats) return `<h1>Statistics</h1><p class="lede">Loading trends…</p>`;
  const langOptions = uniqueLanguages();
  const errorOptions = uniqueErrors();
  const tagOptions = uniqueTags();
  const drill = (stats.languages || []).find((item) => item.pair === state.drill);
  return `<div class="eyebrow">Across saved runs</div><h1>Statistics</h1>
    <p class="lede">Trends, language drill-ins, and filters for language, error type, and tag. Sample history is included until you turn it off.</p>
    <div class="filters">
      <label class="chip"><input id="include-sample" type="checkbox" ${state.filters.includeSample ? "checked" : ""}> Sample history</label>
      <button class="btn ghost small" type="button" id="clear-filters">Clear filters</button>
    </div>
    <div class="panel" style="margin-bottom:16px">
      <div class="group-label">Languages</div>
      <div class="chips">${langOptions.map((pair) => chip(pair, state.filters.languages.includes(pair), "lang")).join("") || `<span class="sub">No languages yet.</span>`}</div>
      <div class="group-label">Error types</div>
      <div class="chips">${errorOptions.map((item) => chip(item.name, state.filters.errors.includes(item.id), "err", item.id, item.kind)).join("") || `<span class="sub">No error types yet.</span>`}</div>
      <div class="group-label">Tags</div>
      <div class="chips">${tagOptions.map((tag) => chip(tag, state.filters.tags.includes(tag), "tag")).join("") || `<span class="sub">No tags yet.</span>`}</div>
    </div>
    <div class="chart-wrap"><div class="split"><h2>Historical trend</h2><span class="sub">${stats.trend.length} runs</span></div>${trendSvg(stats.trend)}</div>
    <div class="panels">
      <div class="panel"><h2>Languages</h2><div class="sub">Select a language to drill in.</div>
        ${(stats.languages || []).map((lang) => `<button type="button" class="bar-row click-row ${state.drill === lang.pair ? "on" : ""}" data-drill="${esc(lang.pair)}"><span class="dot dot-low"></span><span class="bar-label">${esc(lang.pair)}</span><span class="bar-track"><span class="bar-fill fill-low" style="width:${Math.min(100, lang.rate)}%"></span></span><span class="bar-count">${fmt(lang.removed + lang.fixed)}</span><span class="bar-status">${lang.rate.toFixed(1)}%</span></button>`).join("") || `<div class="empty">No language data for these filters.</div>`}
      </div>
      <div class="panel"><h2>Error types</h2><div class="sub">Rule checks in teal, AI checks in coral.</div>
        ${errorBars(stats.errorTypes)}
      </div>
    </div>
    ${drill ? drillPanel(drill) : ""}
    <div class="panel" style="margin-top:16px"><h2>Tags</h2>
      <div class="chips">${(stats.tags || []).map((tag) => `<span class="badge rule">${esc(tag.tag)} · ${tag.runs} runs · ${fmt(tag.issues)} issues</span>`).join(" ") || `<span class="empty">No tags in this slice.</span>`}</div>
    </div>`;
}

function chip(label, on, kind, id, extra) {
  const value = id || label;
  const coral = extra === "ai" ? "coral" : "";
  return `<button type="button" class="chip ${coral} ${on ? "on" : ""}" data-filter="${kind}" data-value="${esc(value)}">${esc(label)}</button>`;
}

function errorBars(items) {
  if (!items || !items.length) return `<div class="empty">No matching error types.</div>`;
  const max = Math.max(...items.map((item) => item.count));
  return items.map((item) => barRow(
    item.name, item.count, item.count / max * 100, item.kind === "ai" ? "AI" : "Rule",
    item.kind === "ai" ? "dot-high" : "dot-low", item.kind === "ai" ? "fill-high" : "fill-low",
  )).join("");
}

function drillPanel(lang) {
  const names = nameMap();
  const entries = Object.entries(lang.errors || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map((item) => item[1]));
  return `<div class="panel" style="margin-top:16px"><div class="split"><h2>${esc(lang.pair)}</h2><button class="btn ghost small" type="button" id="close-drill">Close</button></div>
    <p class="sub">${fmt(lang.original)} TUs across ${(lang.runs || []).length} runs · ${lang.rate.toFixed(1)}% touched</p>
    ${entries.map(([id, count]) => barRow(names[id] || id, count, count / max * 100, "", "dot-high", "fill-high")).join("") || `<div class="empty">No error breakdown.</div>`}
    <div class="group-label">Runs</div>
    <table class="grid"><thead><tr><th>Run</th><th>Date</th><th>TUs</th><th>Removed</th><th>Fixed</th></tr></thead>
      <tbody>${(lang.runs || []).map((run) => `<tr><td><button class="link" type="button" data-open-run="${esc(run.id)}">${esc(run.name)}</button></td><td>${esc(run.date)}</td><td>${fmt(run.original)}</td><td>${fmt(run.removed)}</td><td>${fmt(run.fixed)}</td></tr>`).join("")}</tbody>
    </table></div>`;
}

function trendSvg(points) {
  if (!points.length) return `<div class="empty">No runs match these filters.</div>`;
  const w = 760, h = 240, pad = 36;
  const maxRate = Math.max(5, ...points.map((item) => item.rate));
  const step = points.length === 1 ? 0 : (w - pad * 2) / (points.length - 1);
  const x = (index) => pad + index * step;
  const y = (rate) => h - pad - (rate / maxRate) * (h - pad * 2);
  const line = points.map((item, index) => `${index ? "L" : "M"}${x(index)},${y(item.rate)}`).join(" ");
  const area = `${line} L${x(points.length - 1)},${h - pad} L${x(0)},${h - pad} Z`;
  const dots = points.map((item, index) => `<circle cx="${x(index)}" cy="${y(item.rate)}" r="4" fill="#E84B4B"><title>${esc(item.date)} ${esc(item.name)} · ${item.rate}%</title></circle>`).join("");
  const labels = points.map((item, index) => `<text x="${x(index)}" y="${h - 12}" text-anchor="middle" font-size="11" fill="#5C6E6A">${esc(item.date.slice(5))}</text>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Issue rate over time">
    <path d="${area}" fill="rgba(0,187,167,.18)"></path>
    <path d="${line}" fill="none" stroke="#00BBA7" stroke-width="3"></path>
    ${dots}${labels}
  </svg>`;
}

function nameMap() {
  const map = {};
  state.checks.forEach((item) => { map[item.id] = item.name; });
  state.runs.forEach((run) => (run.checks || []).forEach((item) => { map[item.id] = item.name; }));
  return map;
}

function uniqueLanguages() {
  const set = new Set();
  state.runs.filter(includeRun).forEach((run) => (run.reports || []).forEach((report) => (report.languages || []).forEach((lang) => set.add(lang.pair))));
  return [...set];
}
function uniqueErrors() {
  const map = new Map();
  state.runs.filter(includeRun).forEach((run) => (run.checks || []).forEach((item) => map.set(item.id, item)));
  return [...map.values()];
}
function uniqueTags() {
  const set = new Set();
  state.runs.filter(includeRun).forEach((run) => (run.tags || []).forEach((tag) => set.add(tag)));
  return [...set];
}
function includeRun(run) {
  return run.status === "done" && (state.filters.includeSample || !run.sample);
}

async function loadTms() {
  paint();
  try {
    const [data, gloss, runs] = await Promise.all([
      api(`/api/connections/${state.activeId}/tms`),
      api(`/api/connections/${state.activeId}/glossaries`),
      api("/api/runs"),
    ]);
    state.tms = data.tms;
    state.glossaries = gloss.glossaries || [];
    state.glossaryNote = gloss.error || "";
    state.runs = runs.runs;
    const ids = new Set(state.tms.map((tm) => tm.id));
    state.selected = new Set([...state.selected].filter((id) => ids.has(id)));
    ensureGlossary();
  } catch (err) {
    state.tms = [];
    toast(err.message);
  }
  if (state.route === "workspace") paint();
}

async function loadRuns() {
  const data = await api("/api/runs");
  state.runs = data.runs;
  if (state.route === "history" || state.route === "stats") paint();
}

async function loadStats() {
  if (!state.runs.length) await loadRuns();
  const params = new URLSearchParams();
  if (state.filters.languages.length) params.set("languages", state.filters.languages.join("|"));
  if (state.filters.errors.length) params.set("errors", state.filters.errors.join(","));
  if (state.filters.tags.length) params.set("tags", state.filters.tags.join(","));
  params.set("include_sample", state.filters.includeSample ? "true" : "false");
  state.stats = await api(`/api/stats?${params.toString()}`);
  if (state.drill && !(state.stats.languages || []).some((item) => item.pair === state.drill)) state.drill = null;
  if (state.route === "stats") paint();
}

let watchGen = 0;
async function loadRun(id) {
  const token = ++watchGen;
  if (!state.runs.length) {
    try {
      const all = await api("/api/runs");
      state.runs = all.runs;
    } catch (err) { /* the report still opens */ }
  }
  const data = await api(`/api/runs/${id}`);
  if (token !== watchGen) return;
  state.current = data.run;
  if (state.route === "run" || state.route === "review") paint();
  if (data.run.status === "running") {
    setTimeout(() => { if (state.route === "run" && state.runId === id) loadRun(id); }, 800);
  }
}

function bind() {
  document.querySelectorAll("[data-open]").forEach((button) => button.addEventListener("click", () => openConnection(button.dataset.open)));
  document.querySelectorAll("[data-open-id]").forEach((button) => button.addEventListener("click", () => openConnection(button.dataset.openId)));
  document.querySelectorAll("[data-forget]").forEach((button) => button.addEventListener("click", () => forget(button.dataset.forget)));
  document.querySelectorAll("form[data-conn]").forEach((form) => form.addEventListener("submit", (event) => saveConnection(event, form)));
  document.querySelectorAll("[data-lokalise]").forEach((button) => button.addEventListener("click", () => startLokalise(button.dataset.lokalise)));
  const connection = $("#connection");
  if (connection) connection.addEventListener("change", () => {
    state.activeId = connection.value;
    localStorage.setItem("tm-active", state.activeId);
    state.selected.clear();
    state.draft.languages = [];
    state.draft.linked = [];
    state.step = 1;
    loadTms();
  });
  document.querySelectorAll("[data-step]").forEach((button) => button.addEventListener("click", () => goStep(button.dataset.step)));
  document.querySelectorAll("[data-lang]").forEach((button) => button.addEventListener("click", () => toggleLang(button.dataset.lang)));
  document.querySelectorAll("[data-link]").forEach((button) => button.addEventListener("click", () => toggleList(state.draft.linked, button.dataset.link)));
  document.querySelectorAll("[data-decision]").forEach((button) => button.addEventListener("click", () => setDecision(button.dataset.key, button.dataset.decision)));
  const commit = $("#commit-run");
  if (commit) commit.addEventListener("click", commitRun);
  document.querySelectorAll("[data-tm]").forEach((button) => button.addEventListener("click", () => toggleSet(state.selected, button.dataset.tm)));
  document.querySelectorAll("[data-check]").forEach((button) => button.addEventListener("click", () => toggleSet(state.selectedChecks, button.dataset.check)));
  const addTag = $("#add-tag");
  if (addTag) addTag.addEventListener("click", () => {
    const input = $("#tag-text");
    const text = (input.value || "").trim();
    if (!text) return;
    text.split(",").map((part) => part.trim()).filter(Boolean).forEach((tag) => {
      if (!state.draft.tags.includes(tag)) state.draft.tags.push(tag);
    });
    input.value = "";
    state.draft.tagText = "";
    paint();
  });
  document.querySelectorAll("[data-drop-tag]").forEach((button) => button.addEventListener("click", () => {
    state.draft.tags = state.draft.tags.filter((tag) => tag !== button.dataset.dropTag);
    paint();
  }));
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => {
    state.draft.mode = button.dataset.mode;
    paint();
  }));
  const recommended = $("#use-recommended");
  if (recommended) recommended.addEventListener("click", () => {
    state.selectedChecks = new Set(["empty", "duplicates", "numbers_only", "symbols_only", "whitespace", "end_punct", "glossary"]);
    ensureGlossary();
    paint();
  });
  const clearChecks = $("#clear-checks");
  if (clearChecks) clearChecks.addEventListener("click", () => { state.selectedChecks = new Set(); paint(); });
  const glossaryId = $("#glossary-id");
  if (glossaryId) glossaryId.addEventListener("change", () => { state.draft.glossaryId = glossaryId.value; });
  const model = $("#model");
  if (model) model.addEventListener("change", () => { state.draft.model = model.value; });
  const limit = $("#limit");
  if (limit) limit.addEventListener("change", () => { state.draft.limit = limit.value; });
  const saveSettings = $("#save-settings");
  if (saveSettings) saveSettings.addEventListener("click", saveSettingsNow);
  const addAi = $("#add-ai");
  if (addAi) addAi.addEventListener("click", addAiCheck);
  const start = $("#start-run");
  if (start) start.addEventListener("click", startRun);
  const file = $("#tmx-file");
  if (file) file.addEventListener("change", uploadTmx);
  const glossary = $("#glossary-file");
  if (glossary) glossary.addEventListener("change", uploadGlossary);
  document.querySelectorAll("[data-open-run]").forEach((button) => button.addEventListener("click", () => { location.hash = `#run/${button.dataset.openRun}`; }));
  document.querySelectorAll("[data-delete-run]").forEach((button) => button.addEventListener("click", () => deleteRun(button.dataset.deleteRun)));
  document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => { state.reportTab = Number(button.dataset.tab); paint(); }));
  const sample = $("#include-sample");
  if (sample) sample.addEventListener("change", () => { state.filters.includeSample = sample.checked; loadStats(); });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => toggleFilter(button.dataset.filter, button.dataset.value)));
  document.querySelectorAll("[data-drill]").forEach((row) => row.addEventListener("click", () => { state.drill = row.dataset.drill; paint(); }));
  const closeDrill = $("#close-drill");
  if (closeDrill) closeDrill.addEventListener("click", () => { state.drill = null; paint(); });
  const clearFilters = $("#clear-filters");
  if (clearFilters) clearFilters.addEventListener("click", () => {
    state.filters = { languages: [], errors: [], tags: [], includeSample: state.filters.includeSample };
    state.drill = null;
    loadStats();
  });
  const continueBtn = $("#lokalise-continue");
  if (continueBtn) continueBtn.addEventListener("click", () => api("/api/lokalise/continue", { method: "POST" }));
}

function toggleSet(set, id) {
  if (set.has(id)) set.delete(id);
  else set.add(id);
  ensureGlossary();
  paint();
}

function toggleList(list, value) {
  const index = list.indexOf(value);
  if (index >= 0) list.splice(index, 1);
  else list.push(value);
  paint();
}

function toggleLang(lang) {
  const list = state.draft.languages;
  const index = list.indexOf(lang);
  if (index >= 0) list.splice(index, 1);
  else list.push(lang);
  const allowed = new Set(visibleTms().map((tm) => tm.id));
  state.selected = new Set([...state.selected].filter((id) => allowed.has(id)));
  paint();
}

function ensureGlossary() {
  if (state.selectedChecks.has("glossary") && !state.draft.glossaryId) {
    const first = glossaryChoices()[0];
    if (first) state.draft.glossaryId = first.id;
  }
}

function goStep(next) {
  const target = Number(next);
  if (target > state.step) {
    for (let step = state.step; step < target; step += 1) {
      const err = validateStep(step);
      if (err) {
        toast(err);
        return;
      }
    }
  }
  state.step = target;
  paint();
}

function validateStep(step) {
  captureDraft();
  ensureGlossary();
  if (step === 1) {
    if (!(state.draft.name || "").trim()) return "Name the project.";
    if (!state.draft.languages.length) return "Select at least one language.";
    if (!state.selected.size) return "Select at least one memory.";
    if (state.selected.size > 1 && !state.draft.mode) return "Choose merged or single.";
  }
  if (step === 2 && !state.selectedChecks.size) return "Select at least one filter.";
  if (step === 2 && state.selectedChecks.has("glossary") && !state.draft.glossaryId) return "Select a glossary for glossary compliance.";
  return "";
}

function toggleFilter(kind, value) {
  const key = kind === "lang" ? "languages" : kind === "err" ? "errors" : "tags";
  const list = state.filters[key];
  const index = list.indexOf(value);
  if (index >= 0) list.splice(index, 1);
  else list.push(value);
  if (kind === "lang") state.drill = value;
  loadStats();
}

async function openConnection(id) {
  state.activeId = id;
  localStorage.setItem("tm-active", id);
  state.selected.clear();
  location.hash = "#workspace";
}

async function saveConnection(event, form) {
  event.preventDefault();
  const body = { provider: form.dataset.conn };
  new FormData(form).forEach((value, key) => { body[key] = value; });
  try {
    const data = await api("/api/connections", { method: "POST", body });
    toast(data.connection.status === "connected" ? `${data.connection.label} is connected.` : (data.connection.error || data.connection.account || "Saved."));
    state.connections = (await api("/api/connections")).connections;
    if (data.connection.provider === "lokalise") state.lokalise = null;
    paint();
  } catch (err) {
    toast(err.message);
  }
}

async function forget(id) {
  await api(`/api/connections/${id}`, { method: "DELETE" });
  state.connections = (await api("/api/connections")).connections;
  if (state.activeId === id) state.activeId = "demo";
  paint();
}

async function startLokalise(id) {
  state.lokalise = { prompt: "Opening the browser…", busy: true };
  paint();
  await api("/api/lokalise/login", { method: "POST", body: { connectionId: id } });
  pollLokalise();
}

async function pollLokalise() {
  const data = await api("/api/lokalise/login");
  state.lokalise = data.state;
  if (state.route === "connect") paint();
  if (data.state.busy) setTimeout(pollLokalise, 1200);
  else state.connections = (await api("/api/connections")).connections;
}

async function saveSettingsNow() {
  captureDraft();
  const body = { openai_model: state.draft.model, llm_limit: Number(state.draft.limit) || 200 };
  if (state.draft.key) body.openai_api_key = state.draft.key;
  state.settings = await api("/api/settings", { method: "PUT", body });
  state.draft.key = "";
  toast(state.settings.hasKey ? "Model settings saved." : "Settings saved. Add an API key before running AI checks.");
  paint();
}

async function addAiCheck() {
  captureDraft();
  try {
    const data = await api("/api/checks", {
      method: "POST",
      body: { name: state.draft.aiName, detail: state.draft.aiDetail, prompt: state.draft.aiPrompt },
    });
    state.checks.push(data.check);
    state.selectedChecks.add(data.check.id);
    state.draft.aiName = "";
    state.draft.aiDetail = "";
    toast(`Added AI check “${data.check.name}”.`);
    paint();
  } catch (err) {
    toast(err.message);
  }
}

async function uploadTmx(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const data = await api("/api/uploads", { method: "POST", body });
    state.tms.push(data.tm);
    state.selected.add(data.tm.id);
    toast(`Added ${data.tm.name}.`);
    paint();
  } catch (err) {
    toast(err.message);
  }
}

async function uploadGlossary(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);
  try {
    const data = await api("/api/glossary", { method: "POST", body });
    state.glossary = data.glossary;
    state.draft.glossaryMode = "upload";
    state.draft.glossaryId = "upload";
    toast(`Glossary loaded · ${data.glossary.entries} terms.`);
    paint();
  } catch (err) {
    toast(err.message);
  }
}

async function startRun() {
  captureDraft();
  const err = validateStep(1) || validateStep(2);
  if (err) {
    toast(err);
    return;
  }
  const glossaryOn = state.selectedChecks.has("glossary");
  const body = {
    name: state.draft.name,
    tags: state.draft.tags,
    languages: state.draft.languages,
    linkedRunIds: state.draft.linked,
    connectionId: state.activeId,
    tmIds: [...state.selected],
    mode: state.selected.size > 1 ? state.draft.mode : (state.draft.mode || "single"),
    checkIds: [...state.selectedChecks],
    before: state.draft.before,
    users: state.draft.users,
    threshold: Number(state.draft.threshold) || 80,
    caseSensitive: !!state.draft.caseSensitive,
    symbolPercent: Number(state.draft.symbolPercent) || 90,
    glossaryMode: glossaryOn ? (state.draft.glossaryId === "upload" ? "upload" : "remote") : "none",
    glossaryId: glossaryOn ? state.draft.glossaryId : "",
  };
  state.busy = true;
  paint();
  try {
    if (state.draft.key || state.draft.model) await saveSettingsQuiet();
    const data = await api("/api/runs", { method: "POST", body });
    state.busy = false;
    state.reportTab = 0;
    location.hash = `#run/${data.run.id}`;
  } catch (err) {
    state.busy = false;
    toast(err.message);
    paint();
  }
}

async function saveSettingsQuiet() {
  const body = { openai_model: state.draft.model, llm_limit: Number(state.draft.limit) || 200 };
  if (state.draft.key) body.openai_api_key = state.draft.key;
  state.settings = await api("/api/settings", { method: "PUT", body });
  state.draft.key = "";
}

async function setDecision(key, decision) {
  if (!state.current) return;
  try {
    const data = await api(`/api/runs/${state.current.id}/review`, { method: "POST", body: { key, decision } });
    state.current = data.run;
    paint();
  } catch (err) {
    toast(err.message);
  }
}

async function commitRun() {
  const run = state.current;
  if (!run) return;
  const note = run.provider === "smartcat"
    ? "Upload the cleaned TM to Smartcat? This replaces the memory contents."
    : run.provider === "phrase"
      ? "Upload the cleaned TM to Phrase? Phrase merges the file, so removed segments can remain there."
      : run.provider === "demo" || run.provider === "local"
        ? "Save the cleaned TMX on this machine?"
        : "Upload the cleaned TM back to the TMS?";
  if (!window.confirm(note)) return;
  try {
    const data = await api(`/api/runs/${run.id}/commit`, { method: "POST" });
    state.current = data.run;
    toast("Commit finished.");
    paint();
  } catch (err) {
    toast(err.message);
  }
}

async function deleteRun(id) {
  await api(`/api/runs/${id}`, { method: "DELETE" });
  state.runs = state.runs.filter((run) => run.id !== id);
  paint();
}

boot().catch((err) => toast(err.message));
