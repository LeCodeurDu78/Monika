// The chat panel: the same conversation as the terminal. The page sends a line, the answer arrives
// over the page's WebSocket as it is written. Text only, never HTML.
import { apiCall, el } from './panels.js';

const MAX_CHARS = 4000;

export function mountChat() {
  const panel = document.getElementById('p-chat');
  if (!panel) return null;
  const body = panel.querySelector('.body');

  const log = el('div', 'chat-log');
  log.setAttribute('role', 'log');
  log.setAttribute('aria-live', 'polite');
  log.setAttribute('aria-label', 'Conversation');

  const note = el('p', 'chat-note');

  const input = el('textarea');
  input.rows = 2;
  input.maxLength = MAX_CHARS;
  input.placeholder = 'Écris à Monika… (Entrée envoie, Maj+Entrée ajoute une ligne)';
  input.setAttribute('aria-label', 'Message pour Monika');

  const mic = el('button', 'mic', '🎙 Parler');  // wired by voice.js (the microphone lives there)
  mic.type = 'button';
  mic.setAttribute('aria-pressed', 'false');
  mic.title = 'Parler à Monika au micro (nécessite https ou localhost)';
  const send = el('button', 'primary', 'Envoyer');
  send.type = 'submit';
  const stop = el('button', '', 'Arrêter');
  stop.type = 'button';
  const row = el('div', 'row');
  row.append(mic, send, stop);
  const form = el('form', 'chat-form');
  form.append(input, row);
  body.append(log, note, form);

  let pending = null;  // the bubble being streamed, so deltas land in one place
  let busy = false;

  function scroll() {
    log.scrollTop = log.scrollHeight;
  }

  function line(role, text) {
    const node = el('div', `chat-line ${role}`, text);
    log.append(node);
    scroll();
    return node;
  }

  function setBusy(value) {
    busy = value;
    send.disabled = value;
    stop.disabled = !value;
  }

  /** One live message from the kernel (see web/chat.py). */
  function receive(msg) {
    if (msg.kind === 'user') {
      pending = null;
      line(msg.role === 'auto' ? 'auto' : 'user', msg.text);
      note.textContent = '';
      setBusy(true);
    } else if (msg.kind === 'delta') {
      if (!pending) pending = line('monika', '');
      pending.textContent += msg.text;
      scroll();
    } else if (msg.kind === 'end') {
      if (pending && msg.text) pending.textContent = msg.text;
      pending = null;
      setBusy(false);
    } else if (msg.kind === 'error') {
      line('error', msg.text);
      pending = null;
      setBusy(false);
    } else if (msg.kind === 'note') {
      line('note', msg.text);
    }
  }

  /** The conversation so far: on load, and again after a reconnection. */
  async function seed() {
    const { error, data } = await apiCall('/api/chat', 'GET');
    log.replaceChildren();
    pending = null;
    if (error) {
      note.textContent = error;
      return;
    }
    if (data.enabled === false) note.textContent = 'La conversation dans la page est indisponible.';
    for (const item of data.items || []) line(item.role === 'auto' ? 'auto' : item.role, item.text);
    setBusy(Boolean(data.busy));
    if (data.busy && log.lastElementChild?.classList.contains('monika')) {
      pending = log.lastElementChild;  // the answer in progress keeps growing where it is
    }
  }

  form.onsubmit = async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || busy) return;
    send.disabled = true;
    note.textContent = '';
    const { error } = await apiCall('/api/chat', 'POST', { text });
    if (error) {
      note.textContent = error;
      send.disabled = busy;
      return;
    }
    input.value = '';
  };

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  stop.onclick = async () => {
    stop.disabled = true;
    const { error, data } = await apiCall('/api/chat/stop', 'POST');
    note.textContent = error || data.message || '';
    if (error) stop.disabled = !busy;
  };

  /** A short line under the conversation (voice state, microphone problems). */
  function status(text) {
    note.textContent = text || '';
  }

  setBusy(false);
  return { receive, seed, status };
}
