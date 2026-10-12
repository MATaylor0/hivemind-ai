"use strict";

// HiveMind admin console. Vanilla JS: views over /api/*, and a console that streams `hivemind` commands.

const $ = (sel, root = document) => root.querySelector(sel);
const h = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem("hm." + key); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem("hm." + key, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};

const state = { env: null, overview: null, environments: [] };

async function api(path) {
  const sep = path.includes("?") ? "&" : "?";
  const res = await fetch(`/api/${path}${state.env ? `${sep}env=${encodeURIComponent(state.env)}` : ""}`, {
    credentials: "same-origin",
  });
  if (res.status === 401) throw new Error("Session expired: reopen the URL printed by `hivemind ui`.");
  return res.json();
}

function currentEnv() {
  return state.environments.find((e) => e.name === state.env) || null;
}

function badge(text, cls = "") { return `<span class="badge ${h(cls)}">${h(text)}</span>`; }
function tierBadge(tier) { return tier ? badge(tier, `tier-${tier}`) : ""; }
function errorBox(message) { return `<div class="notice err"><strong>Problem</strong><pre>${h(message)}</pre></div>`; }
function money(n) { return n == null ? "–" : `$${Number(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`; }
function envArg() { return state.env ? ` -e ${state.env}` : ""; }

// ------------------------------------------------------------------ views

const views = {
  async overview() {
    const o = state.overview;
    if (o.error) return errorBox(o.error);
    const c = o.counts;
    const tiles = [
      ["Agents", `${c.active_agents}${c.active_agents !== c.agents ? ` / ${c.agents}` : ""}`],
      ["Processes", c.processes], ["Teams", c.teams], ["Tools", c.tools], ["Knowledge", c.knowledge], ["Goals", c.goals],
      ["Monthly budget", money(c.monthly_budget_usd)],
      ["Policy errors", c.errors, c.errors ? "err" : ""],
      ["Warnings", c.warnings, c.warnings ? "warn" : ""],
    ];
    const findings = o.findings.length
      ? `<ul class="findings">${o.findings.map((f) => `<li>${badge(f.level, f.level === "error" ? "err" : "warn")}
          <span><strong>${h(f.subject)}</strong> ${h(f.message)}</span></li>`).join("")}</ul>`
      : `<p class="empty">No policy findings. Every agent passes the organisation's rules.</p>`;
    const goalTree = (nodes) => `<ul class="goals">${nodes.map((g) => `<li>
        <strong>${h(g.title)}</strong> <code>${h(g.id)}</code>
        ${g.agents.length ? `<div class="agents">served by ${g.agents.map((a) => `<a href="#/agents/${h(a)}">${h(a)}</a>`).join(", ")}</div>` : ""}
        ${g.children.length ? goalTree(g.children) : ""}</li>`).join("")}</ul>`;
    const env = currentEnv();
    const sp = o.sharepoint;
    return `
      <h1>${h(o.org.name)}</h1>
      <p class="sub">${h(o.org.description)} <span class="mono">${h(o.org.root)}</span></p>
      ${env && env.protected ? `<div class="notice"><strong>${h(env.name)} is protected.</strong> Read-only here: changes go
        through a pull request and the approval-gated <em>Deploy agents</em> workflow.</div>` : ""}
      <div class="tiles">${tiles.map(([l, v, cls]) => `<div class="tile ${cls || ""}"><div class="label">${h(l)}</div>
        <div class="value">${h(v)}</div></div>`).join("")}</div>
      <div class="grid-2">
        <div><h2>Goals</h2><div class="card">${o.goals.length ? goalTree(o.goals) : `<p class="empty">No goals defined.</p>`}</div></div>
        <div><h2>Policy findings</h2><div class="card">${findings}</div></div>
      </div>
      <h2>Teams</h2>
      <div class="table-wrap"><table><thead><tr><th>Team</th><th>Description</th><th>Agents</th></tr></thead><tbody>
        ${o.teams.map((t) => `<tr><td><strong>${h(t.name)}</strong></td><td>${h(t.description)}</td>
          <td>${t.agents.map((a) => `<a href="#/agents/${h(a)}">${h(a)}</a>`).join(", ") || "–"}</td></tr>`).join("")}
      </tbody></table></div>
      ${sp ? `<h2>SharePoint site</h2><div class="card facts">
        <div><span>Site</span>${h(sp.site)}</div><div><span>Rules library</span>${h(sp.rules_library)}</div>
        <div><span>Owned by the business</span>Word documents per organisation, role, team and agent folder</div></div>` : ""}`;
  },

  async chart() {
    const o = state.overview;
    if (o.error) return errorBox(o.error);
    const node = (n) => `<li><div class="node tier-${h(n.tier)}" data-agent="${h(n.name)}" tabindex="0" role="link">
        <b>${h(n.title)}</b><small>${h(n.name)}</small><small>${h(n.tier)}${n.team ? ` · ${h(n.team)}` : ""}</small>
        ${n.reviewed_by ? `<div class="rel">reviewed by ${h(n.reviewed_by)}</div>` : ""}
        ${n.collaborates_with.length ? `<div class="rel">works with ${h(n.collaborates_with.join(", "))}</div>` : ""}
        ${n.enabled ? "" : `<div class="rel">${badge("disabled", "warn")}</div>`}
      </div>${n.children.length ? `<ul>${n.children.map(node).join("")}</ul>` : ""}</li>`;
    return `<h1>Org chart</h1>
      <p class="sub">Reporting lines drive delegation (down) and escalation (up). Reviewers and collaborators are noted on each card.</p>
      <div class="legend">${["executive", "manager", "reviewer", "worker"].map((t) => tierBadge(t)).join("")}</div>
      <div class="org-scroll">${o.chart.map((root) => `<ul class="org">${node(root)}</ul>`).join("")}</div>`;
  },

  async agents() {
    const o = state.overview;
    if (o.error) return errorBox(o.error);
    const q = store.get("agentFilter", "");
    const rows = o.agents.map((a) => `<tr class="click" data-agent="${h(a.name)}" data-search="${h(
        [a.name, a.title, a.team, a.role, a.tier, a.owner, a.model].join(" ").toLowerCase())}">
      <td><strong>${h(a.title)}</strong><br><span class="mono">${h(a.name)}</span></td>
      <td>${tierBadge(a.tier)}</td><td>${h(a.team || "–")}</td><td>${h(a.reports_to || "–")}</td>
      <td>${h(a.owner || "–")}</td><td class="mono">${h(a.model)}</td>
      <td class="num">${a.tools.length}${a.write_tools.length ? ` ${badge(`${a.write_tools.length} write`, "warn")}` : ""}</td>
      <td>${a.channels.map((c) => badge(c, "info")).join(" ")}</td>
      <td class="num">${money(a.monthly_budget_usd)}</td>
      <td>${a.enabled ? badge("enabled", "ok") : badge("disabled", "warn")}</td></tr>`).join("");
    return `<h1>Agents</h1>
      <p class="sub">Every agent defined in the org directory. Select one to see exactly what it will be deployed with.</p>
      <div class="toolbar"><input type="search" id="agent-filter" placeholder="Filter by name, team, owner…" value="${h(q)}">
        <button data-run="new --help">New agent…</button></div>
      <div class="table-wrap"><table><thead><tr><th>Agent</th><th>Tier</th><th>Team</th><th>Reports to</th><th>Owner</th>
        <th>Model</th><th>Tools</th><th>Channels</th><th>Budget/mo</th><th>Status</th></tr></thead>
        <tbody id="agent-rows">${rows}</tbody></table></div>`;
  },

  async agent(name, opts = {}) {
    const a = await api(`agents/${encodeURIComponent(name)}${opts.fetch ? "?fetch=1" : ""}`);
    if (a.error) return errorBox(a.error);
    const spec = a.spec;
    const b = a.build;
    const facts = [
      ["Role", spec.role], ["Team", spec.team], ["Owner", spec.owner], ["Reports to", spec.reports_to],
      ["Reviewed by", spec.reviewed_by], ["Works with", (spec.collaborates_with || []).join(", ")],
      ["Model", b && b.model], ["Budget / month", spec.budget ? money(spec.budget.monthly_usd) : "–"],
      ["Cost centre", spec.budget && spec.budget.cost_center], ["Foundry name", b && b.name],
      ["Content hash", b && b.hash], ["Private memory", b && b.memory_store],
      ["Autonomy", (spec.autonomy && spec.autonomy.level || "approve").replace("_", " ")],
      ["Approved by", spec.autonomy && spec.autonomy.approved_by], ["Eval dataset", spec.evals && spec.evals.dataset],
    ];
    const env = currentEnv();
    const remote = env && env.provider === "foundry";
    return `
      <p><a href="#/agents">← Agents</a></p>
      <h1>${h(a.title)} ${tierBadge(state.overview.agents.find((x) => x.name === name)?.tier)}</h1>
      <p class="sub">${h(a.description)}</p>
      <div class="toolbar">
        <button data-run="render ${h(name)}${h(envArg())}">Render in console</button>
        <button data-run="validate${h(envArg())}">Validate</button>
        ${remote && !(b && b.remote_sources_fetched) ? `<button id="fetch-remote" data-agent="${h(name)}">Fetch SharePoint rules</button>` : ""}
        <span class="mono sub" style="margin:0">${h(a.source_file)}</span>
      </div>
      ${a.findings.length ? `<div class="card"><ul class="findings">${a.findings.map((f) =>
        `<li>${badge(f.level, f.level === "error" ? "err" : "warn")}<span>${h(f.message)}</span></li>`).join("")}</ul></div>` : ""}
      ${a.compile_error ? errorBox(a.compile_error) : ""}
      <div class="card facts">${facts.map(([k, v]) => `<div><span>${h(k)}</span>${h(v || "–")}</div>`).join("")}</div>
      ${a.goals.length ? `<h2>Goals</h2><div class="card">${a.goals.map((g) => `<div>${h(g.chain.join(" → "))}</div>`).join("")}</div>` : ""}
      <h2>Tools and knowledge</h2>
      <div class="table-wrap"><table><thead><tr><th>Id</th><th>Kind</th><th>Access</th><th>Description</th></tr></thead><tbody>
        ${a.tools.map((t) => `<tr><td class="mono">${h(t.id)}</td><td>${h(t.kind)}${t.knowledge ? ` ${badge("knowledge", "info")}` : ""}</td>
          <td>${t.access === "write" ? badge("write", "warn") : badge("read")}${t.user_context ? ` ${badge("as user", "info")}` : ""}</td>
          <td>${h(t.description)}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">No tools.</td></tr>`}
      </tbody></table></div>
      ${(spec.schedules || []).length ? `<h2>Schedules</h2><div class="table-wrap"><table><thead><tr><th>Name</th><th>Cron</th><th>Time zone</th><th>Input</th></tr></thead><tbody>
        ${spec.schedules.map((s) => `<tr><td>${h(s.name)}</td><td class="mono">${h(s.cron)}</td><td>${h(s.time_zone || "UTC")}</td><td>${h(s.input)}</td></tr>`).join("")}
        </tbody></table></div>` : ""}
      ${b ? `
        <h2>Instruction sources</h2>
        <div class="card"><ol style="margin:0;padding-left:20px">${b.sources.map((s) => `<li class="mono">${h(s)}</li>`).join("")}</ol></div>
        <h2>Compiled instructions</h2>
        <pre class="prompt">${h(b.instructions)}</pre>
        <details><summary>Foundry agent definition (JSON)</summary><pre class="prompt">${h(JSON.stringify(b.definition, null, 2))}</pre></details>
        <details><summary>Version metadata</summary><pre class="prompt">${h(JSON.stringify(b.metadata, null, 2))}</pre></details>` : ""}`;
  },

  async plan() {
    const env = currentEnv();
    if (!env) return `<h1>Plan</h1><p class="empty">Choose an environment.</p>`;
    const auto = env.provider === "local";
    const run = store.get(`plan.${env.name}`, false);
    const header = `<h1>Plan · ${h(env.name)}</h1>
      <p class="sub">What <code>apply</code> would change in ${h(env.name)}: agents whose compiled definition differs
      from the deployed version get a new version; nothing else is touched.</p>`;
    if (!auto && !run) {
      return `${header}<div class="toolbar"><button class="primary" id="run-plan">Check ${h(env.name)}</button>
        <span class="sub" style="margin:0">Calls Foundry with your Azure sign-in (az login).</span></div>`;
    }
    const p = await api("plan");
    if (p.error) return header + errorBox(p.error);
    const sym = { create: ["+ create", "ok"], update: ["~ update", "warn"], delete: ["- delete", "err"], unchanged: ["= unchanged", ""] };
    const s = p.summary;
    return `${header}
      <div class="tiles">${["create", "update", "delete", "unchanged"].map((k) =>
        `<div class="tile"><div class="label">${k}</div><div class="value">${s[k]}</div></div>`).join("")}</div>
      <div class="toolbar">
        ${env.protected
          ? `<div class="notice" style="margin:0">Protected: apply through a pull request and the <em>Deploy agents</em> workflow.</div>`
          : `<button class="primary" data-run="apply -e ${h(env.name)}" ${s.create + s.update + s.delete ? "" : "disabled"}>Apply…</button>`}
        <button data-run="plan -e ${h(env.name)}">Plan in console</button>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Change</th><th>Agent</th><th>Deployed</th><th>Reason</th></tr></thead><tbody>
        ${p.items.map((i) => `<tr><td>${badge(sym[i.action][0], sym[i.action][1])}</td>
          <td><a href="#/agents/${h(i.name.replace(env.prefix || "", ""))}">${h(i.name)}</a></td>
          <td class="mono">${i.deployed_version ? `v${h(i.deployed_version)}` : "–"}</td><td>${h(i.reason)}</td></tr>`).join("")}
      </tbody></table></div>`;
  },

  async autonomy() {
    const a = await api("autonomy");
    if (a.error) return errorBox(a.error);
    const env = currentEnv();
    const rank = (lvl) => a.levels.indexOf(lvl);
    const levelBadge = (lvl) => badge(lvl.replace("_", " "), ["shadow", "suggest"].includes(lvl) ? "" :
      lvl === "approve" ? "warn" : "ok");
    const rows = a.agents.map((x) => {
      const casesOk = x.cases != null && x.cases >= x.min_cases;
      return `<tr class="click" data-agent="${h(x.name)}">
        <td><strong>${h(x.title)}</strong><br><span class="mono">${h(x.name)}</span></td>
        <td>${levelBadge(x.declared)}${x.capped ? ` → ${levelBadge(x.effective)} <span class="sub">capped</span>` : ""}</td>
        <td>${x.approved_by ? `${h(x.approved_by)}<br><span class="sub">${h(x.approved_on)}</span>` : "–"}</td>
        <td class="num">${x.cases == null ? badge("invalid", "err") : `${x.cases} / ${x.min_cases}`}
          ${casesOk ? "" : badge("short", "err")}</td>
        <td class="num">${Math.round(x.pass_rate * 100)}%</td>
        <td>${x.gated_tools.map((t) => badge(t, "warn")).join(" ") || "–"}</td>
        <td>${x.held_tools.map((t) => badge(t)).join(" ") || "–"}</td></tr>`;
    }).join("");
    return `<h1>Autonomy${env ? ` · ${h(env.name)}` : ""}</h1>
      <p class="sub">How far each agent may act on its own, and the evidence behind it. Agents move up a level by
      pull request: more golden eval cases, a passing gate and a named approver.
      ${a.max_autonomy ? `This environment caps every agent at <strong>${h(a.max_autonomy.replace("_", " "))}</strong>.` : ""}</p>
      <div class="legend">${a.levels.map((l) => `${levelBadge(l)}`).join(" → ")}</div>
      <div class="toolbar">
        ${env ? `<button data-run="gate -e ${h(env.name)}">Run eval gates</button>` : ""}
        ${env && env.provider === "foundry" ? `<button data-run="gate -e ${h(env.name)} --dry-run">Show gate commands</button>` : ""}
      </div>
      <div class="table-wrap"><table><thead><tr><th>Agent</th><th>Level</th><th>Approved by</th>
        <th>Eval cases (have / need)</th><th>Gate pass rate</th><th>Approval-gated tools</th><th>Held back</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
      <p class="sub" style="margin-top:12px">Levels: shadow (runs alongside people, takes no action) · suggest (recommends)
      · approve (acts, each change approved) · autonomous · autonomous sampled (a share reviewed afterwards).</p>`;
  },

  async processes() {
    const p = await api("processes");
    if (p.error) return errorBox(p.error);
    const env = currentEnv();
    const local = env && env.provider === "local";
    const step = (x) => x.agent
      ? `<a class="badge info" href="#/agents/${h(x.agent)}">${h(x.agent)}</a>`
      : badge(x.kind === "RequestExternalInput" || x.kind === "Question" ? "person" : x.kind, x.kind === "RequestExternalInput" || x.kind === "Question" ? "warn" : "");
    const cards = p.processes.map((x) => `<div class="card" style="margin-bottom:14px">
      <h2 style="margin-top:0">${h(x.title)} ${x.enabled ? "" : badge("disabled", "warn")}</h2>
      <p class="sub">${h(x.description)}</p>
      <div class="facts" style="margin-bottom:10px"><div><span>Owner</span>${h(x.owner || "–")}</div>
        <div><span>Goal</span>${h(x.goal || "–")}</div><div><span>Workflow</span><span class="mono">${h(x.workflow)}</span></div>
        <div><span>Deployed as</span><span class="mono">${h(x.foundry_name)}</span></div></div>
      <div class="toolbar">${x.steps.map(step).join(" → ") || `<span class="empty">no steps</span>`}</div>
      ${x.findings.length ? `<ul class="findings">${x.findings.map((f) => `<li>${badge(f.level, f.level === "error" ? "err" : "warn")}<span>${h(f.message)}</span></li>`).join("")}</ul>` : ""}
      ${x.triggers.length ? `<div class="sub">Runs ${x.triggers.map((t) => `<span class="mono">${h(t.cron)}</span> (${h(t.time_zone)})`).join(", ")}</div>` : ""}
      ${local ? `<div class="toolbar"><button data-run="process run ${h(x.name)} '${h((x.triggers[0] || {}).input || "Start")}' --answer approved -e ${h(env.name)}">Run locally (auto-approve)</button></div>` : ""}
    </div>`).join("");
    return `<h1>Processes</h1>
      <p class="sub">End-to-end processes run by agents, with people at the approval points. Each is a Microsoft Agent
      Framework workflow, deployed as a Foundry hosted agent.</p>${cards || `<p class="empty">No processes defined.</p>`}`;
  },
};

// ------------------------------------------------------------------ routing

async function render() {
  const [, view = "overview", arg] = location.hash.split("/");
  const main = $("#main");
  document.querySelectorAll("nav.side a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === (view === "agents" && arg ? "agents" : view)));
  try {
    if (!state.overview) await loadOverview();
    const html = view === "agents" && arg ? await views.agent(decodeURIComponent(arg))
      : views[view] ? await views[view]() : await views.overview();
    main.innerHTML = html;
  } catch (err) {
    main.innerHTML = errorBox(err.message || String(err));
  }
  bindView();
}

async function loadOverview() {
  state.overview = await api("overview");
  if (state.overview.environments) state.environments = state.overview.environments;
  $("#org-name").textContent = state.overview.org ? state.overview.org.name : "";
  renderEnvPicker();
  renderChips();
}

function renderEnvPicker() {
  const sel = $("#env");
  sel.innerHTML = `<option value="">(no environment)</option>` + state.environments.map((e) =>
    `<option value="${h(e.name)}" ${e.name === state.env ? "selected" : ""}>${h(e.name)}${e.provider === "foundry" ? " · Foundry" : ""}${e.protected ? " · protected" : ""}</option>`).join("");
}

function bindView() {
  document.querySelectorAll("[data-agent]").forEach((el) => {
    if (el.id === "fetch-remote") return;
    const go = () => { location.hash = `#/agents/${encodeURIComponent(el.dataset.agent)}`; };
    el.addEventListener("click", go);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  document.querySelectorAll("[data-run]").forEach((el) =>
    el.addEventListener("click", () => runCommand(el.dataset.run)));
  const filter = $("#agent-filter");
  if (filter) {
    const apply = () => {
      const q = filter.value.trim().toLowerCase();
      store.set("agentFilter", filter.value);
      document.querySelectorAll("#agent-rows tr").forEach((tr) => { tr.hidden = q && !tr.dataset.search.includes(q); });
    };
    filter.addEventListener("input", apply);
    apply();
  }
  const fetchRemote = $("#fetch-remote");
  if (fetchRemote) fetchRemote.addEventListener("click", async () => {
    $("#main").innerHTML = await views.agent(fetchRemote.dataset.agent, { fetch: true });
    bindView();
  });
  const runPlan = $("#run-plan");
  if (runPlan) runPlan.addEventListener("click", () => { store.set(`plan.${state.env}`, true); render(); });
}

// ------------------------------------------------------------------ console

const out = $("#console-out");
const history = store.get("history", []);
let historyIndex = history.length;

function print(text, cls = "") {
  const span = document.createElement("span");
  if (cls) span.className = cls;
  span.textContent = text;
  out.appendChild(span);
  out.scrollTop = out.scrollHeight;
}

function confirmDialog(message) {
  return new Promise((resolve) => {
    const dlg = $("#confirm");
    $("#confirm-text").textContent = message;
    const done = (ok) => { dlg.close(); resolve(ok); };
    $("#confirm-ok").onclick = () => done(true);
    $("#confirm-cancel").onclick = () => done(false);
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}

let running = false;
async function runCommand(command, confirmed = false) {
  command = command.trim().replace(/^hivemind\s+/, "");
  if (!command) return;
  if (running) { print("\nA command is still running.\n", "meta"); return; }
  setConsoleOpen(true);
  if (!confirmed) {
    if (history[history.length - 1] !== command) history.push(command);
    store.set("history", history.slice(-100));
    historyIndex = history.length;
    print(`\n$ hivemind ${command}\n`, "cmd");
  }
  running = true;
  let changed = false;
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, confirm: confirmed }),
    });
    if (res.status === 409) {
      const body = await res.json();
      running = false;
      if (await confirmDialog(body.message)) return runCommand(command, true);
      print("cancelled\n", "meta");
      return;
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({ error: res.statusText }));
      print(`${body.error}\n`, "bad");
      return;
    }
    changed = confirmed;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const marker = buffer.indexOf("\x1e");
      if (marker >= 0) {
        print(buffer.slice(0, marker));
        buffer = buffer.slice(marker);
      } else {
        print(buffer);
        buffer = "";
      }
    }
    if (buffer.startsWith("\x1e")) {
      const result = JSON.parse(buffer.slice(1));
      print(`[exit ${result.exit}${result.seconds != null ? ` · ${result.seconds}s` : ""}]\n`, result.exit === 0 ? "good" : "bad");
    }
  } catch (err) {
    print(`${err.message || err}\n`, "bad");
  } finally {
    running = false;
  }
  if (changed) { state.overview = null; render(); }
}

function setConsoleOpen(open) {
  $("#console").classList.toggle("collapsed", !open);
  $("#console-toggle").textContent = open ? "hide" : "show";
  $("#console-toggle").setAttribute("aria-expanded", String(open));
  store.set("consoleOpen", open);
}

function renderChips() {
  const e = envArg();
  const chips = ["validate", "tree", `inventory${e}`, `plan${e || " -e local"}`, `gate${e || " -e local"}`, "process list", "budget", "--help"];
  $("#chips").innerHTML = chips.map((c) => `<button type="button" data-chip="${h(c)}">${h(c)}</button>`).join(" ");
  document.querySelectorAll("[data-chip]").forEach((b) => b.addEventListener("click", () => runCommand(b.dataset.chip)));
}

$("#console-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("#console-input");
  const value = input.value;
  input.value = "";
  runCommand(value);
});
$("#console-input").addEventListener("keydown", (e) => {
  const input = e.target;
  if (e.key === "ArrowUp" && historyIndex > 0) { historyIndex -= 1; input.value = history[historyIndex]; e.preventDefault(); }
  if (e.key === "ArrowDown") {
    historyIndex = Math.min(history.length, historyIndex + 1);
    input.value = history[historyIndex] || "";
    e.preventDefault();
  }
});
$("#console-clear").addEventListener("click", () => { out.textContent = ""; });
$("#console-toggle").addEventListener("click", () => setConsoleOpen($("#console").classList.contains("collapsed")));
$("#env").addEventListener("change", (e) => {
  state.env = e.target.value || null;
  store.set("env", state.env);
  state.overview = null;
  render();
});
$("#refresh").addEventListener("click", () => { state.overview = null; render(); });
window.addEventListener("hashchange", render);

// ------------------------------------------------------------------ start

(async function start() {
  setConsoleOpen(store.get("consoleOpen", true));
  const session = await fetch("/api/session", { credentials: "same-origin" }).then((r) => r.json()).catch(() => ({}));
  state.env = store.get("env", session.default_env || null);
  print("HiveMind console. Commands run as `hivemind <command>` against this org directory.\n" +
        "Changes ask for confirmation; protected environments are read-only here.\n", "meta");
  await render();
  if (state.env && !state.environments.some((e) => e.name === state.env)) {
    state.env = null;
    state.overview = null;
    await render();
  }
})();
