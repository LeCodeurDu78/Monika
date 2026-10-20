"use strict";

// Dashboard panel of the merged page (served as an ES module, so none of these names leak into script.js).
// Everything shown here can come from the web (memories, tool arguments and results): the DOM is only ever
// built with textContent, never innerHTML.

// The server injects the dashboard fragment (static/dashboard/dashboard.html) into the avatar page and writes the
// change token on the panel itself. No token (fragment opened raw) means no dashboard: the panel is removed.
const dashNode = document.getElementById("dash");
const TOKEN = dashNode ? dashNode.dataset.token || "" : "";
const DASH_ON = Boolean(TOKEN) && !TOKEN.startsWith("__");
const $ = (id) => document.getElementById(id);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (method !== "GET") headers["X-Dashboard-Token"] = TOKEN;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await response.json(); } catch (_) { /* no body */ }
  if (!response.ok) throw new Error((data && data.detail) || `Erreur ${response.status}`);
  return data;
}

let toastTimer = null;
function toast(message, isError = false) {
  const box = $("toast");
  box.textContent = message;
  box.className = "toast" + (isError ? " error" : "");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, isError ? 6000 : 3000);
}

function parseTime(value) {
  if (!value) return null;
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return isNaN(date) ? null : date;
}
function formatDate(value) {
  const date = parseTime(value);
  return date ? date.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
}
function formatClock(value) {
  const date = parseTime(value);
  return date ? date.toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}
function formatDuration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} min ${String(seconds % 60).padStart(2, "0")} s` : `${seconds} s`;
}
function plural(n, one, many) { return `${n} ${n > 1 ? many : one}`; }

// ---------------------------------------------------------------- facts
let allFacts = [];

async function loadFacts() {
  const state = $("facts-state");
  try {
    const data = await api("/api/dashboard/facts");
    allFacts = data.facts;
    state.textContent = "";
    state.className = "state";
    renderFacts();
  } catch (error) {
    allFacts = [];
    renderFacts();
    state.className = "state error";
    state.textContent = "";
    state.append(error.message + " ", el("button", { type: "button", class: "quiet", text: "Réessayer", onclick: loadFacts }));
  }
}

function renderFacts() {
  const needle = $("facts-filter").value.trim().toLowerCase();
  const shown = allFacts.filter((fact) => !needle || fact.text.toLowerCase().includes(needle));
  const list = $("facts");
  list.replaceChildren(...shown.map(factItem));
  $("facts-count").textContent = allFacts.length ? plural(allFacts.length, "souvenir", "souvenirs") : "";
  const state = $("facts-state");
  if (!state.classList.contains("error")) {
    if (!allFacts.length) state.textContent = "Elle ne sait encore rien de toi. Les faits durables de vos conversations apparaîtront ici.";
    else if (!shown.length) state.textContent = "Aucun souvenir ne correspond à ce filtre.";
    else state.textContent = "";
  }
}

function factItem(fact) {
  const text = el("p", { class: "fact-text", text: fact.text });
  const when = formatDate(fact.updated || fact.created);
  const meta = when ? el("div", { class: "fact-meta", text: `Retenu ou modifié le ${when}` }) : null;
  const actions = el("div", { class: "fact-actions" });
  const item = el("li", { class: "fact" }, text, meta, actions);

  const showButtons = () => {
    actions.replaceChildren(
      el("button", { type: "button", class: "quiet", text: "Corriger", onclick: () => startEdit(fact, item, text, actions, showButtons) }),
      el("button", { type: "button", class: "danger", text: "Oublier", onclick: () => askForget(fact, item, actions, showButtons) }),
    );
  };
  showButtons();
  return item;
}

function startEdit(fact, item, text, actions, restore) {
  const area = el("textarea", { maxlength: 500, "aria-label": "Texte du souvenir" });
  area.value = fact.text;
  const save = el("button", { type: "button", class: "primary", text: "Enregistrer" });
  const cancel = () => { area.replaceWith(text); restore(); };
  const submit = async () => {
    const value = area.value.trim();
    if (!value) { toast("Un souvenir vide ne se corrige pas : utilise « Oublier ».", true); return; }
    if (value === fact.text) { cancel(); return; }
    save.disabled = true;
    try {
      await api(`/api/dashboard/facts/${encodeURIComponent(fact.id)}`, { method: "PUT", body: { text: value } });
      fact.text = value;
      text.textContent = value;
      area.replaceWith(text);
      restore();
      toast("Souvenir corrigé.");
    } catch (error) {
      save.disabled = false;
      toast(error.message, true);
    }
  };
  area.addEventListener("keydown", (event) => {
    if (event.key === "Escape") cancel();
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) submit();
  });
  save.addEventListener("click", submit);
  text.replaceWith(area);
  actions.replaceChildren(save, el("button", { type: "button", class: "quiet", text: "Annuler", onclick: cancel }));
  area.focus();
}

function askForget(fact, item, actions, restore) {
  const yes = el("button", { type: "button", class: "danger", text: "Oui, oublier" });
  yes.addEventListener("click", async () => {
    yes.disabled = true;
    try {
      await api(`/api/dashboard/facts/${encodeURIComponent(fact.id)}`, { method: "DELETE" });
      allFacts = allFacts.filter((other) => other.id !== fact.id);
      renderFacts();
      toast("Souvenir oublié.");
    } catch (error) {
      yes.disabled = false;
      toast(error.message, true);
    }
  });
  actions.replaceChildren(
    el("span", { class: "confirm", text: "Oublier ce souvenir ?" }),
    yes,
    el("button", { type: "button", class: "quiet", text: "Garder", onclick: restore }),
  );
}

// ---------------------------------------------------------------- live activity
const openJobs = new Set();

function renderOverview(data) {
  const active = data.jobs.filter((job) => job.active).length;
  const parts = [];
  parts.push(data.main_task ? `Elle travaille sur « ${data.main_task.label} ».` : "Elle n'a rien en cours dans la conversation.");
  if (active) parts.push(`${plural(active, "tâche de fond tourne", "tâches de fond tournent")}.`);
  $("status").textContent = parts.join(" ");

  const body = $("live-body");
  const nodes = [];

  if (data.main_task) {
    nodes.push(el("p", { class: "small", text: `Depuis ${formatDuration(data.now - data.main_task.started)}` }));
  } else if (!data.running.length && !data.jobs.length) {
    nodes.push(el("p", { class: "idle", text: "Rien à signaler. Les actions en cours et les tâches de fond s'affichent ici." }));
  }

  if (data.running.length) {
    nodes.push(el("h3", { text: "Outils en cours" }));
    nodes.push(el("ul", { class: "live-list" }, ...data.running.map((call) =>
      el("li", {},
        el("span", { class: "tool-name", text: call.tool }),
        el("div", { class: "small", text: `${call.source === "fond" ? "Tâche de fond" : "Conversation"}, ${formatDuration(data.now - call.started)}` }),
        el("div", { class: "small", text: call.args }),
      ))));
  }

  if (data.jobs.length) {
    nodes.push(el("h3", { text: "Tâches de fond" }));
    nodes.push(el("ul", { class: "live-list" }, ...data.jobs.map(jobItem(data.now))));
  }
  body.replaceChildren(...nodes);
}

function jobItem() {
  return (job) => {
    const head = el("div", {}, el("strong", { text: `#${job.id} ` }), job.goal);
    const state = el("div", { class: "small", text: `${job.status}, ${formatDuration(job.elapsed_s)}` });
    const item = el("li", {}, head, state);
    if (job.active) {
      const cancel = el("button", { type: "button", class: "danger", text: "Annuler" });
      cancel.addEventListener("click", async () => {
        cancel.disabled = true;
        try {
          await api(`/api/dashboard/jobs/${job.id}/cancel`, { method: "POST" });
          toast(`Annulation de la tâche #${job.id} demandée.`);
          refreshOverview();
        } catch (error) { cancel.disabled = false; toast(error.message, true); }
      });
      item.append(cancel);
    } else if (job.result) {
      const details = el("details", {}, el("summary", { text: "Résultat" }), el("p", { text: job.result }));
      details.open = openJobs.has(job.id);
      details.addEventListener("toggle", () => (details.open ? openJobs.add(job.id) : openJobs.delete(job.id)));
      item.append(details);
    }
    return item;
  };
}

async function refreshOverview() {
  try {
    renderOverview(await api("/api/dashboard/overview"));
  } catch (error) {
    $("status").textContent = "Le tableau de bord ne joint plus Monika. Est-elle toujours lancée ?";
  }
}

// ---------------------------------------------------------------- journal
const STATUS_LABELS = { ok: "réussi", error: "échec", blocked: "bloqué", cancelled: "annulé" };
let rows = [];          // newest first
let hasMore = false;

function filters() {
  return { status: $("f-status").value, source: $("f-source").value, tool: $("f-tool").value.trim() };
}
function query(extra = {}) {
  const params = new URLSearchParams({ limit: "50", ...extra });
  for (const [key, value] of Object.entries(filters())) if (value) params.set(key, value);
  return `/api/dashboard/actions?${params}`;
}

function rowNodes(row) {
  const failed = row.status === "error" || row.status === "blocked";
  const main = el("tr", { class: "row" + (failed ? " failed" : ""), tabindex: "0", "aria-expanded": "false", "data-id": row.id },
    el("td", { class: "time", text: formatClock(row.ts) }),
    el("td", { text: row.source === "agent" ? "Conversation" : row.source === "fond" ? "Tâche de fond" : row.source }),
    el("td", {}, el("span", { class: "tool-name", text: row.tool })),
    el("td", { class: "args", text: row.args }),
    el("td", { class: "num", text: row.duration_ms >= 1000 ? `${(row.duration_ms / 1000).toFixed(1)} s` : row.duration_ms > 0 ? `${row.duration_ms} ms` : "—" }),
    el("td", {}, el("span", { class: "badge " + (failed ? "failed" : row.status === "ok" ? "ok" : ""), text: STATUS_LABELS[row.status] || row.status })),
  );
  let detail = null;
  const toggle = () => {
    if (detail) { detail.remove(); detail = null; main.setAttribute("aria-expanded", "false"); return; }
    detail = el("tr", { class: "detail" }, el("td", { colspan: "6", text: row.preview || "Pas de résultat enregistré." }));
    main.after(detail);
    main.setAttribute("aria-expanded", "true");
  };
  main.addEventListener("click", toggle);
  main.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
  return main;
}

function renderActions() {
  $("actions").replaceChildren(...rows.map(rowNodes));
  $("more").hidden = !hasMore;
  const state = $("actions-state");
  state.className = "state";
  state.textContent = rows.length ? "" : "Aucune action ne correspond à ces filtres.";
  const names = new Set(rows.map((row) => row.tool));
  $("tool-names").replaceChildren(...[...names].sort().map((name) => el("option", { value: name })));
}

async function loadActions() {
  try {
    const data = await api(query());
    rows = data.actions;
    hasMore = data.has_more;
    renderActions();
  } catch (error) {
    const state = $("actions-state");
    state.className = "state error";
    state.textContent = error.message;
  }
}

async function loadMore() {
  if (!rows.length) return;
  $("more").disabled = true;
  try {
    const data = await api(query({ before_id: rows[rows.length - 1].id }));
    rows = rows.concat(data.actions);
    hasMore = data.has_more;
    renderActions();
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("more").disabled = false;
  }
}

// New rows only: expanded rows stay open and nothing jumps while someone reads.
async function pollNewActions() {
  if (!rows.length) return loadActions();
  try {
    const data = await api(query());
    const newest = rows[0].id;
    const fresh = data.actions.filter((row) => row.id > newest);
    if (fresh.length) {
      rows = fresh.concat(rows);
      const body = $("actions");
      body.prepend(...fresh.map(rowNodes));
      $("actions-state").textContent = "";
    }
  } catch (_) { /* the overview poll already reports a lost connection */ }
}

async function loadFailures() {
  const box = $("failures");
  try {
    const data = await api("/api/dashboard/failures?days=7");
    if (!data.tools.length) {
      box.replaceChildren(el("p", { text: "Aucun échec d'outil ces 7 derniers jours." }));
      return;
    }
    box.replaceChildren(
      el("p", { text: "Échecs des 7 derniers jours. Un clic filtre le journal sur l'outil." }),
      el("ul", {}, ...data.tools.map((item) => {
        const button = el("button", { type: "button" },
          el("strong", { class: "tool-name", text: item.tool }),
          ` : ${plural(item.failures, "échec", "échecs")} sur ${plural(item.total, "appel", "appels")}`,
          el("div", { class: "small", text: `Dernier : ${item.last_error}` }),
        );
        button.addEventListener("click", () => {
          $("f-tool").value = item.tool;
          $("f-status").value = "failed";
          loadActions();
          $("journal").scrollIntoView({ block: "start" });
        });
        return el("li", {}, button);
      })),
    );
  } catch (_) { box.replaceChildren(); }
}

// ---------------------------------------------------------------- drawer + start
let isOpen = false;

function refreshAll() {
  loadFacts();
  loadActions();
  loadFailures();
  refreshOverview();
}

function setOpen(open) {
  isOpen = open;
  document.body.classList.toggle("dash-open", open);
  $("dash").inert = !open;
  $("dash-toggle").setAttribute("aria-expanded", String(open));
  history.replaceState(null, "", open ? "#dashboard" : location.pathname + location.search);
  if (open) {
    refreshAll();
    $("dash-close").focus();
  } else {
    $("dash-toggle").focus();
  }
}

if (!DASH_ON) {
  for (const id of ["dash", "dash-toggle", "toast"]) {
    const node = $(id);
    if (node) node.remove();
  }
} else {
  let debounce = null;
  $("facts-filter").addEventListener("input", renderFacts);
  for (const id of ["f-status", "f-source"]) $(id).addEventListener("change", loadActions);
  $("f-tool").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(loadActions, 300); });
  $("more").addEventListener("click", loadMore);

  $("dash-toggle").addEventListener("click", () => setOpen(!isOpen));
  $("dash-close").addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !isOpen) return;
    if (event.target.matches("textarea, input, select")) return; // Escape there cancels an edit, not the panel
    setOpen(false);
  });
  window.addEventListener("hashchange", () => { if ((location.hash === "#dashboard") !== isOpen) setOpen(!isOpen); });

  // Nothing is polled while the panel is closed.
  setInterval(() => { if (isOpen && !document.hidden) refreshOverview(); }, 2000);
  setInterval(() => { if (isOpen && !document.hidden) { pollNewActions(); loadFailures(); } }, 5000);
  document.addEventListener("visibilitychange", () => { if (isOpen && !document.hidden) { refreshOverview(); pollNewActions(); } });

  if (location.hash === "#dashboard") setOpen(true);
}
