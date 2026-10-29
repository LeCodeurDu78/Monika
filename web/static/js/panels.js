// The dashboard panels: pure rendering and the edits the person may request. Text only, never HTML.
// (Kept apart from dashboard.js so it can be tested without a browser: see tests/js/panels.test.mjs.)

export const fmt = (n) => new Intl.NumberFormat('fr-FR').format(n);

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function fill(id, ...children) {
  document.querySelector(`#${id} .body`).replaceChildren(...children);
}

const token = () => document.querySelector('meta[name="monika-token"]')?.content || '';

// Ask the server to change something; returns an error text, or null when it worked
export async function apiRequest(url, method, body) {
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

// `editing` freezes every panel while something is being typed (a refresh would wipe it);
// `messages` holds the last error to show, per panel.
export const ui = { editing: false, messages: { memory: '', character: '' } };

/**
 * One line of editable text with Modifier / Supprimer.
 * onSave(text) and onDelete() return an error text, or null when it worked.
 */
function editableRow({ text, meta, channel, label, maxLength, allowEmpty = false, deleteLabel = 'Supprimer', onSave, onDelete, refresh }) {
  const li = el('li');
  const body = el('span', 'text', text);
  const edit = el('button', '', 'Modifier');
  const remove = el('button', 'danger', deleteLabel);
  const actions = el('span', 'actions');
  actions.append(edit, remove);
  if (meta) li.append(el('span', 'meta muted', meta));
  li.append(body, actions);

  remove.onclick = async () => {
    remove.disabled = edit.disabled = true;
    ui.messages[channel] = (await onDelete()) || '';
    refresh();
  };
  edit.onclick = () => {
    ui.editing = true;
    const box = el('textarea');
    box.value = text;
    box.maxLength = maxLength;
    box.setAttribute('aria-label', label);
    const save = el('button', 'primary', 'Enregistrer');
    const cancel = el('button', '', 'Annuler');
    const row = el('div', 'row');
    row.append(save, cancel);
    const problem = el('p', 'err');
    const form = el('div', 'edit');
    form.append(box, problem, row);
    li.replaceChildren(form);
    box.focus();
    cancel.onclick = () => { ui.editing = false; refresh(); };
    save.onclick = async () => {
      if (!allowEmpty && !box.value.trim()) return;
      save.disabled = cancel.disabled = true;
      const error = await onSave(box.value);
      if (!error) { ui.messages[channel] = ''; ui.editing = false; refresh(); return; }
      problem.textContent = error;  // The form stays open: the message is shown inside it
      save.disabled = cancel.disabled = false;
    };
  };
  return li;
}

function factRow(fact, refresh) {
  const url = `/api/memory/${encodeURIComponent(fact.id)}`;
  return editableRow({
    text: fact.text, channel: 'memory', label: 'Texte du souvenir', maxLength: 1000, refresh,
    onSave: (text) => apiRequest(url, 'PUT', { text }),
    onDelete: () => apiRequest(url, 'DELETE'),
  });
}

function noteRow(note, refresh) {
  const url = `/api/journal/${encodeURIComponent(note.id)}`;
  return editableRow({
    text: note.text, meta: noteDate(note.at), channel: 'character', label: 'Texte de la note', maxLength: 600, refresh,
    onSave: (text) => apiRequest(url, 'PUT', { text }),
    onDelete: () => apiRequest(url, 'DELETE'),
  });
}

function noteDate(at) {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' });
}

const EMOTION_LABELS = { neutre: 'neutre', joyeuse: 'joyeuse', taquine: 'taquine', boudeuse: 'boudeuse', surprise: 'surprise' };
const EMOTION_MIN_BAR = 0.05;   // 26 emotions exist: the panel only shows the ones that are actually alive
let emotionLabels = {};         // filled from /api/emotions (setEmotionLabels)

/** Display names served by /api/emotions; the four built-in ones remain if it never arrives. */
export function setEmotionLabels(specs) {
  const emotions = specs && specs.emotions;
  if (!emotions || typeof emotions !== 'object') return;
  emotionLabels = Object.fromEntries(Object.entries(emotions)
    .filter(([, entry]) => entry && typeof entry.label === 'string')
    .map(([name, entry]) => [name, entry.label]));
}

function bar(fraction, className = 'bar') {
  const box = el('div', className);
  const inner = el('i');
  inner.style.width = `${Math.max(0, Math.min(100, Math.round(fraction * 100)))}%`;
  box.append(inner);
  return box;
}

function relationState(s, refresh) {
  const nodes = [
    el('div', 'relation-level', s.level_label),
    el('div', 'muted', `favorabilité ${Math.round(s.favorability * 100)} %`),
    bar(s.favorability),
  ];
  const emotions = el('div', 'emotions');
  for (const [name, value] of Object.entries(s.emotions)) {
    if (name !== s.dominant && value < EMOTION_MIN_BAR) continue;
    const line = el('div', `emotion${name === s.dominant ? ' dominant' : ''}`);
    line.append(el('span', 'name', emotionLabels[name] || EMOTION_LABELS[name] || name), bar(value, 'bar mini'));
    emotions.append(line);
  }
  nodes.push(el('div', 'muted', s.dominant === 'neutre' ? 'Humeur : égale' : `Humeur : ${s.dominant}`), emotions,
    el('div', 'muted', `${fmt(s.messages)} message(s) · ${fmt(s.active_days)} jour(s) de présence · série en cours : ${fmt(s.streak)}`));
  const reset = el('button', 'danger', 'Repartir de zéro');
  reset.onclick = async () => {
    if (!confirm('Remettre la relation à zéro (niveau, favorabilité et humeur) ?')) return;
    reset.disabled = true;
    ui.messages.character = (await apiRequest('/api/relation/reset', 'POST')) || '';
    refresh();
  };
  nodes.push(reset);
  return nodes;
}

function journalPanel(journal, refresh) {
  const nodes = [el('h3', '', 'Journal de Monika')];
  if (journal === null || journal === undefined) {
    nodes.push(el('p', 'muted', 'Journal désactivé (JOURNAL_ENABLED).'));
    return nodes;
  }
  const impression = journal.impression;
  if (impression) {
    const list = el('ul', 'facts impression');
    list.append(editableRow({
      text: impression, channel: 'character', label: 'Impression d’ensemble', maxLength: 900, allowEmpty: true,
      deleteLabel: 'Oublier', refresh,
      onSave: (text) => apiRequest('/api/impression', 'PUT', { text }),
      onDelete: () => apiRequest('/api/impression', 'PUT', { text: '' }),
    }));
    nodes.push(el('div', 'muted', 'Impression d’ensemble'), list);
  }
  if (journal.threads.length) nodes.push(el('div', 'muted', `À reprendre : ${journal.threads.join(' · ')}`));
  if (!journal.notes.length) {
    nodes.push(el('p', 'muted', impression ? 'Pas de note pour l’instant.' : 'Mon journal est encore vide : j’écris une note tous les quelques échanges.'));
    return nodes;
  }
  const list = el('ul', 'facts');
  for (const note of [...journal.notes].reverse()) list.append(noteRow(note, refresh));
  const box = el('div', 'scroll');
  box.append(list);
  const clear = el('button', 'danger', 'Effacer tout le journal');
  clear.onclick = async () => {
    if (!confirm('Effacer toutes les notes, l’impression d’ensemble et les sujets à reprendre ?')) return;
    clear.disabled = true;
    ui.messages.character = (await apiRequest('/api/journal', 'DELETE')) || '';
    refresh();
  };
  nodes.push(el('div', 'muted', `${journal.notes.length} note(s)`), box, clear);
  return nodes;
}

export const sections = {
  context(c) {
    if (!c) return fill('p-context', el('p', 'muted', 'Démarrage en cours…'));
    if (c.error) return fill('p-context', el('p', 'err', c.error));
    const track = bar(c.tokens / c.budget);
    fill('p-context',
      el('div', '', `${fmt(c.tokens)} tokens sur un budget de ${fmt(c.budget)}`), track,
      el('div', 'muted', `${fmt(c.messages)} message(s) ; fenêtre du modèle : ${fmt(c.window)} tokens` +
        (c.summary_tokens ? ` ; résumé des anciens échanges : ${fmt(c.summary_tokens)} tokens` : '')));
  },
  memory(m, refresh) {
    if (ui.editing) return;  // Do not wipe what is being typed
    if (m.error) return fill('p-memory', el('p', 'err', m.error));
    if (!m.enabled) return fill('p-memory', el('p', 'muted', 'Mémoire rapide désactivée ou indisponible.'));
    const nodes = [];
    if (ui.messages.memory) nodes.push(el('p', 'err', ui.messages.memory));
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
  character(c, refresh) {
    if (ui.editing) return;
    if (!c) return fill('p-relation', el('p', 'muted', 'Démarrage en cours…'));
    if (c.error) return fill('p-relation', el('p', 'err', c.error));
    if (!c.enabled) return fill('p-relation', el('p', 'muted', 'Personnage désactivé (CHARACTER_ENABLED).'));
    const nodes = [];
    if (ui.messages.character) nodes.push(el('p', 'err', ui.messages.character));
    if (c.state) nodes.push(...relationState(c.state, refresh));
    nodes.push(...journalPanel(c.journal, refresh));
    fill('p-relation', ...nodes);
  },
};

export function render(snap, refresh) {
  const a = snap.app;
  document.getElementById('meta').textContent = `${a.model} · mode ${a.mode}`;
  sections.context(snap.context);
  sections.memory(snap.memory, refresh);
  sections.character(snap.character, refresh);
}
