// Settings page: builds every control with textContent (never innerHTML), reads and writes through the
// API. Requests are made here rather than with panels.apiRequest() because the write answer tells which
// keys need a restart, and apiRequest() only returns an error string or null.

const stateText = document.getElementById('save-state');
const restartBox = document.getElementById('restart');
const groupsBox = document.getElementById('groups');
const healthBox = document.getElementById('health');
const healthNote = document.getElementById('health-note');
const secretState = document.getElementById('secret-state');
const wordsState = document.getElementById('words-state');
const WORD_TIERS = ['mild', 'bad', 'severe'];

let values = {};   // name -> value as loaded, to know what actually changed
const HEALTH_EVERY_MS = 5000;

async function request(url, method = 'GET', body) {
  try {
    const response = await fetch(url, {
      method,
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return { ok: false, error: data.error || `Erreur ${response.status}` };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: 'Monika ne répond pas.' };
  }
}

function say(text, kind = '') {
  stateText.textContent = text;
  stateText.className = kind === 'error' ? 'meta err' : 'meta muted';
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function inputValue(input) {
  if (input.dataset.kind === 'bool') return input.checked;
  if (input.dataset.kind !== 'number') return input.value;
  if (input.value.trim() === '') return null;   // Empty number: leave the current value alone
  return Number(input.value);
}

function fieldControl(field) {
  const wrapper = element('div', `field${field.kind === 'long' ? ' wide' : ''}`);
  const label = element('label', '', (field.pinned ? '🔒 ' : '') + field.name);
  let input;
  if (field.kind === 'bool') {
    input = element('input');
    input.type = 'checkbox';
    input.checked = !!field.value;
  } else if (field.kind === 'long') {
    input = element('textarea');
    input.rows = 4;
    input.value = String(field.value ?? '');
  } else {
    input = element('input');
    input.type = field.kind === 'number' ? 'number' : 'text';
    if (field.kind === 'number') input.step = Number.isInteger(field.value) ? '1' : 'any';
    input.value = String(field.value ?? '');
  }
  input.id = `set-${field.name}`;
  input.dataset.name = field.name;
  input.dataset.kind = field.kind;
  input.disabled = !!field.pinned;
  label.htmlFor = input.id;
  input.addEventListener('input', () => { wrapper.classList.toggle('dirty', inputValue(input) !== values[field.name]); });
  wrapper.append(label, input);
  if (field.pinned) wrapper.append(element('span', 'hint', 'figé par .env'));
  return wrapper;
}

function renderGroups(data) {
  groupsBox.replaceChildren();
  values = {};
  for (const group of data.groups || []) {
    groupsBox.append(element('h3', 'group-title', group.title));
    const box = element('div', 'fields');
    for (const field of group.fields) {
      values[field.name] = field.value;
      box.append(fieldControl(field));
    }
    groupsBox.append(box);
  }
  document.getElementById('settings-file').textContent = data.settings_file || 'settings.json';
}

function renderWords(words) {
  for (const tier of WORD_TIERS) {
    document.getElementById(`words-${tier}`).value = ((words && words[tier]) || []).join('\n');
  }
}

async function saveWords() {
  const body = {};
  for (const tier of WORD_TIERS) {
    body[tier] = document.getElementById(`words-${tier}`).value
      .split('\n').map((line) => line.trim()).filter(Boolean);
  }
  const result = await request('/api/settings/words', 'POST', body);
  if (!result.ok) {
    wordsState.textContent = result.error;
    wordsState.className = 'hint err';
    return;
  }
  const counts = (result.data && result.data.words) || {};
  await load();
  wordsState.textContent = `Liste enregistrée : ${counts.mild || 0} mild, ${counts.bad || 0} bad, `
    + `${counts.severe || 0} severe.`;
  wordsState.className = 'hint';
}

function renderSecret(defined) {
  secretState.textContent = defined
    ? 'Une clé est configurée (sa valeur n’est jamais affichée).'
    : 'Aucune clé configurée : le modèle ne répondra pas.';
  secretState.className = defined ? 'hint' : 'hint warn';
}

async function load() {
  const result = await request('/api/settings');
  if (!result.ok) { say(result.error, 'error'); return; }
  renderGroups(result.data);
  renderWords(result.data.words);
  renderSecret(!!(result.data.secrets || {}).OPENAI_API_KEY);
  restartBox.hidden = true;
  say('');
}

async function save() {
  const patch = {};
  for (const input of groupsBox.querySelectorAll('[data-name]')) {
    const name = input.dataset.name;
    if (input.disabled) continue;
    const value = inputValue(input);
    if (value === null || value === values[name]) continue;
    patch[name] = value;
  }
  if (!Object.keys(patch).length) { say('Rien à enregistrer.'); return; }
  const result = await request('/api/settings', 'POST', patch);
  if (!result.ok) { say(result.error, 'error'); return; }
  await load();
  const restart = (result.data && result.data.restart_required) || [];
  if (restart.length) {
    restartBox.hidden = false;
    restartBox.textContent = 'Redémarre Monika pour appliquer : ' + restart.join(', ') + '.';
  }
  say('Enregistré.');
}

function renderHealth(rows, took) {
  healthBox.replaceChildren();
  for (const row of rows) {
    const item = document.createElement('li');
    item.dataset.ok = row.ok === true ? 'true' : row.ok === false ? 'false' : 'off';
    item.append(element('span', 'light'), element('span', 'who', row.name), element('span', 'detail', row.detail));
    healthBox.append(item);
  }
  healthNote.textContent = `Sondé en ${took} ms · rafraîchi toutes les 5 s quand cet onglet est visible.`;
}

async function refreshHealth() {
  const result = await request('/api/health');
  if (!result.ok) { healthNote.textContent = result.error; return; }
  renderHealth(result.data.rows || [], result.data.took_ms ?? 0);
}

async function saveSecret() {
  const input = document.getElementById('secret');
  const value = input.value.trim();
  if (!value) { say('La clé est vide.', 'error'); return; }
  const result = await request('/api/settings/secret', 'POST', { name: 'OPENAI_API_KEY', value });
  if (!result.ok) { say(result.error, 'error'); return; }
  input.value = '';
  await load();
  restartBox.hidden = false;
  restartBox.textContent = 'Clé enregistrée dans le .env : redémarre Monika pour qu’elle soit utilisée.';
  say('Clé enregistrée.');
}

document.getElementById('save').addEventListener('click', save);
document.getElementById('reload').addEventListener('click', load);
document.getElementById('health-refresh').addEventListener('click', refreshHealth);
document.getElementById('secret-save').addEventListener('click', saveSecret);
document.getElementById('words-save').addEventListener('click', saveWords);

load();
refreshHealth();
setInterval(() => { if (!document.hidden) refreshHealth(); }, HEALTH_EVERY_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshHealth(); });
