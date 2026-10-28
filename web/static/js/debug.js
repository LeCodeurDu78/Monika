// Debug panel (open the page with ?debug): play every clip, force an emotion, inspect the VRM's expressions.
// Built with DOM calls only (no innerHTML), like the rest of the page.
const EMOTIONS = ['neutre', 'joyeuse', 'taquine', 'boudeuse', 'surprise'];

function el(tag, attrs = {}, text = '') {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text) node.textContent = text;
  return node;
}

function group(title, buttons) {
  const box = el('div', { class: 'dbg-group' });
  box.append(el('h3', {}, title));
  const row = el('div', { class: 'dbg-row' });
  for (const [label, handler] of buttons) {
    const b = el('button', { type: 'button' }, label);
    b.addEventListener('click', handler);
    row.append(b);
  }
  box.append(row);
  return box;
}

export function mountDebugPanel(avatar) {
  const panel = el('aside', { id: 'debug-panel', 'aria-label': 'Debug avatar' });
  panel.append(el('h2', {}, 'Debug avatar'));

  const resolved = avatar.face.resolved;
  const missing = Object.entries(resolved).filter(([, real]) => !real).map(([name]) => name);
  const info = el('p', { class: 'dbg-info' },
    `Expressions : ${Object.entries(resolved).map(([k, v]) => `${k}→${v ?? '✗'}`).join(' · ')}`);
  if (missing.length) info.classList.add('dbg-warn');
  panel.append(info);

  panel.append(group('Clips', avatar.clipNames.map((n) => [n, () => avatar.cues.cue({ clip: n, priority: 9 })])));
  panel.append(group('Émotions (visage + clip)', EMOTIONS.map((e) => [e, () => avatar.cue({ emotion: e, intensity: 0.8 })])));
  panel.append(group('Visage seul', [
    ...EMOTIONS.map((e) => [e, () => avatar.face.setEmotion(e, 0.9)]),
  ]));
  panel.append(group('États', ['idle', 'thinking'].map((s) => [s, () => avatar.setState(s)])));
  panel.append(group('Bouche', [
    ['parler 3 s', () => {
      avatar.setSound(true);
      const t = setInterval(() => avatar.setMouth(0.1 + Math.random() * 0.25), 90);
      setTimeout(() => { clearInterval(t); avatar.setMouth(0); avatar.setSound(false); }, 3000);
    }],
  ]));
  document.body.append(panel);
}
