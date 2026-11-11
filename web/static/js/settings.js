// Settings page: builds every control with textContent (never innerHTML), reads and writes through
// the API. Requests are made here rather than with panels.apiRequest() because the write answer
// tells which keys need a restart.
import { setLanguage, startI18n } from './i18n.js';

startI18n();

const stateText = document.getElementById('save-state');
const restartBox = document.getElementById('restart');
const groupsBox = document.getElementById('groups');
const navBox = document.getElementById('settings-nav');
const healthBox = document.getElementById('health');
const healthNote = document.getElementById('health-note');
const providerState = document.getElementById('provider-state');
const providerSelect = document.getElementById('provider-select');
const providerUrl = document.getElementById('provider-url');
const providerUrlField = document.getElementById('provider-url-field');
const languageSelect = document.getElementById('language-select');

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
  const label = element('label', '', (field.pinned ? '🔒 ' : '') + field.label);
  let input;
  if (field.kind === 'bool') {
    input = element('input');
    input.type = 'checkbox';
    input.checked = !!field.value;
  } else if (field.kind === 'select') {
    input = element('select');
    for (const option of field.options || []) {
      const node = element('option', '', option.label);
      node.value = option.value;
      input.append(node);
    }
    input.value = String(field.value ?? '');
  } else if (field.kind === 'long') {
    input = element('textarea');
    input.rows = 5;
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
  const mark = () => wrapper.classList.toggle('dirty', inputValue(input) !== values[field.name]);
  input.addEventListener('input', mark);
  input.addEventListener('change', mark);
  wrapper.append(label, input);
  if (field.hint) wrapper.append(element('span', 'hint', field.hint));
  if (field.pinned) wrapper.append(element('span', 'hint', 'figé par .env'));
  return wrapper;
}

function renderGroups(data) {
  groupsBox.replaceChildren();
  navBox.replaceChildren();
  values = {};
  (data.groups || []).forEach((group, index) => {
    const section = element('section', 'panel settings-card');
    section.id = `group-${index}`;
    const title = element('h2', '', group.title);
    section.append(title);
    const box = element('div', 'fields');
    for (const field of group.fields) {
      values[field.name] = field.value;
      box.append(fieldControl(field));
    }
    section.append(box);
    groupsBox.append(section);

    const link = element('a', '', group.title);
    link.href = `#${section.id}`;
    link.addEventListener('click', () => {
      navBox.querySelectorAll('a').forEach((node) => node.classList.toggle('active', node === link));
    });
    navBox.append(link);
  });
  navBox.querySelector('a')?.classList.add('active');
  languageSelect.value = data.language === 'en' ? 'en' : 'fr';
  providerSelect.value = data.provider || 'custom';
  providerUrl.value = data.base_url || '';
  updateProviderFields();
}

/** The base URL only matters for a custom endpoint; presets fill it server-side. */
function updateProviderFields() {
  const custom = providerSelect.value === 'custom';
  providerUrlField.hidden = !custom;
  providerUrl.disabled = !custom;
}

async function load() {
  const result = await request('/api/settings');
  if (!result.ok) { say(result.error, 'error'); return; }
  if (result.data.language) setLanguage(result.data.language);
  renderGroups(result.data);
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
  const restart = (result.data && result.data.restart_required) || [];
  await load();
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

async function saveProvider() {
  const provider = providerSelect.value;
  const baseUrl = providerUrl.value.trim();
  if (provider === 'custom' && !baseUrl) {
    providerState.textContent = "Indique l'URL de base du fournisseur OpenAI Custom.";
    providerState.className = 'hint err';
    return;
  }
  const key = document.getElementById('provider-key').value.trim();
  const result = await request('/api/settings/provider', 'POST', { provider, key, base_url: baseUrl });
  if (!result.ok) {
    providerState.textContent = result.error;
    providerState.className = 'hint err';
    return;
  }
  document.getElementById('provider-key').value = '';
  await load();
  const restart = (result.data && result.data.restart_required) || [];
  providerState.textContent = restart.length
    ? `Fournisseur appliqué. Redémarre Monika : ${restart.join(', ')}.`
    : 'Fournisseur appliqué.';
  providerState.className = 'hint';
}

async function changeLanguage() {
  const language = languageSelect.value === 'en' ? 'en' : 'fr';
  setLanguage(language);
  const result = await request('/api/settings/language', 'POST', { language });
  if (!result.ok) { say(result.error, 'error'); return; }
  say('Langue appliquée.');
  await load();
}

document.getElementById('save').addEventListener('click', save);
document.getElementById('reload').addEventListener('click', load);
document.getElementById('health-refresh').addEventListener('click', refreshHealth);
document.getElementById('provider-save').addEventListener('click', saveProvider);
providerSelect.addEventListener('change', updateProviderFields);
languageSelect.addEventListener('change', changeLanguage);

load();
refreshHealth();
setInterval(() => { if (!document.hidden) refreshHealth(); }, HEALTH_EVERY_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshHealth(); });
