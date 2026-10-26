// The page: connects to Monika, drives the avatar and fills the dashboard panels (text only, never HTML).
import { Avatar } from './avatar.js';

const STATE_TEXT = { idle: 'Au repos', listening: 'J’écoute', thinking: 'Je réfléchis', speaking: 'Je parle' };
const SPEAKING_LEVEL = 0.02;
const fmt = (n) => new Intl.NumberFormat('fr-FR').format(n);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fill(id, ...children) {
  document.querySelector(`#${id} .body`).replaceChildren(...children);
}

const token = () => document.querySelector('meta[name="monika-token"]')?.content || '';

// Ask the server to change something; returns an error text, or null when it worked
async function apiRequest(url, method, body) {
  try {
    const response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Monika-Token': token() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.ok) return null;
    const data = await response.json().catch(() => ({}));
    return data.error || `Erreur ${response.status}`;
  } catch (err) {
    return 'Monika ne répond pas.';
  }
}

const ui = { editingMemory: false, message: '' };

function factRow(fact, refresh) {
  const li = el('li');
  const text = el('span', 'text', fact.text);
  const edit = el('button', '', 'Modifier');
  const remove = el('button', 'danger', 'Supprimer');
  const actions = el('span', 'actions');
  actions.append(edit, remove);
  li.append(text, actions);

  remove.onclick = async () => {
    remove.disabled = edit.disabled = true;
    ui.message = (await apiRequest(`/api/memory/${encodeURIComponent(fact.id)}`, 'DELETE')) || '';
    refresh();
  };
  edit.onclick = () => {
    ui.editingMemory = true;
    const box = el('textarea');
    box.value = fact.text;
    box.maxLength = 1000;
    box.setAttribute('aria-label', 'Texte du souvenir');
    const save = el('button', 'primary', 'Enregistrer');
    const cancel = el('button', '', 'Annuler');
    const row = el('div', 'row');
    row.append(save, cancel);
    const problem = el('p', 'err');
    const form = el('div', 'edit');
    form.append(box, problem, row);
    li.replaceChildren(form);
    box.focus();
    cancel.onclick = () => { ui.editingMemory = false; refresh(); };
    save.onclick = async () => {
      if (!box.value.trim()) return;
      save.disabled = cancel.disabled = true;
      const error = await apiRequest(`/api/memory/${encodeURIComponent(fact.id)}`, 'PUT', { text: box.value });
      if (!error) { ui.message = ''; ui.editingMemory = false; refresh(); return; }
      problem.textContent = error;  // The form stays open: the message is shown inside it
      save.disabled = cancel.disabled = false;
    };
  };
  return li;
}

const sections = {
  context(c) {
    if (!c) return fill('p-context', el('p', 'muted', 'Démarrage en cours…'));
    if (c.error) return fill('p-context', el('p', 'err', c.error));
    const bar = el('div', 'bar');
    const fillBar = el('i');
    fillBar.style.width = `${Math.min(100, (100 * c.tokens) / c.budget)}%`;
    bar.append(fillBar);
    fill('p-context',
      el('div', '', `${fmt(c.tokens)} tokens sur un budget de ${fmt(c.budget)}`), bar,
      el('div', 'muted', `${fmt(c.messages)} message(s) ; fenêtre du modèle : ${fmt(c.window)} tokens` +
        (c.summary_tokens ? ` ; résumé des anciens échanges : ${fmt(c.summary_tokens)} tokens` : '')));
  },
  memory(m, refresh) {
    if (ui.editingMemory) return;  // Do not wipe what is being typed
    if (m.error) return fill('p-memory', el('p', 'err', m.error));
    if (!m.enabled) return fill('p-memory', el('p', 'muted', 'Mémoire rapide désactivée ou indisponible.'));
    const nodes = [];
    if (ui.message) nodes.push(el('p', 'err', ui.message));
    if (!m.facts.length) nodes.push(el('p', 'muted', 'Je ne sais encore rien de toi.'));
    else {
      const list = el('ul', 'facts');
      for (const fact of m.facts) list.append(factRow(fact, refresh));
      const box = el('div', 'scroll');
      box.append(list);
      nodes.push(el('div', 'muted', `${m.facts.length} souvenir(s)`), box);
    }
    fill('p-memory', ...nodes);
  },
};

function render(snap, refresh) {
  const a = snap.app;
  document.getElementById('meta').textContent = `${a.model} · mode ${a.mode}`;
  sections.context(snap.context);
  sections.memory(snap.memory, refresh);
}

class Page {
  constructor() {
    this.avatar = new Avatar(document.getElementById('avatar'));
    this.state = 'idle';
    this.lastSound = 0;
    this.refreshTimer = null;
    this.retry = 500;
  }

  chip() {
    const speaking = performance.now() - this.lastSound < 400;
    const shown = speaking ? 'speaking' : this.state;
    const chip = document.getElementById('state-chip');
    chip.dataset.state = shown;
    chip.textContent = STATE_TEXT[shown];
  }

  setState(state) {
    this.state = state;
    this.avatar.setState(state);
    this.chip();
  }

  onMessage(msg) {
    if (msg.type === 'hello') { this.setState(msg.state); this.refresh(); }
    else if (msg.type === 'state') this.setState(msg.state);
    else if (msg.type === 'level') {
      if (msg.value > SPEAKING_LEVEL) { this.lastSound = performance.now(); this.chip(); }
    } else if (msg.type === 'activity') this.scheduleRefresh(400);
  }

  scheduleRefresh(delay) {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => this.refresh(), delay);
  }

  async refresh() {
    try {
      const response = await fetch('/api/status', { cache: 'no-store' });
      render(await response.json(), () => this.refresh());
    } catch (err) {
      /* The link chip already tells the connection is down */
    }
  }

  connect() {
    const link = document.getElementById('link-chip');
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onopen = () => { link.dataset.link = 'on'; link.textContent = 'Connectée'; this.retry = 500; };
    ws.onmessage = (event) => this.onMessage(JSON.parse(event.data));
    ws.onclose = () => {
      link.dataset.link = 'off';
      link.textContent = 'Connexion perdue : nouvelle tentative…';
      setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 8000);
    };
    this.ws = ws;
  }

  async start() {
    const note = document.getElementById('stage-note');
    window.monika = this;  // Handy in the browser console, and for the automated checks
    if (this.avatar.init()) {
      try {
        this.modelInfo = await this.avatar.load('/static/model.glb');
      } catch (err) {
        note.hidden = false;
        note.textContent = `Le modèle 3D n’a pas pu être chargé : ${err.message || err}`;
      }
    } else {
      note.hidden = false;
      note.textContent = this.avatar.error;
    }
    this.connect();
    this.refresh();
    setInterval(() => { if (!document.hidden) this.refresh(); }, 3000);
    setInterval(() => this.chip(), 250);
  }
}

new Page().start();
