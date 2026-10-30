// Debug panel (open the page with ?debug): play every clip, force an emotion, hold an expression preset.
// Built with DOM calls only (no innerHTML), like the rest of the page.
const EMOTIONS = ['joyeuse', 'taquine', 'boudeuse', 'tristesse', 'colere', 'excitee'];
const EXPRESSIONS = ['happy', 'angry', 'sad', 'relaxed', 'surprised'];   // the VRM presets the face drives

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

  panel.append(group('Clips', avatar.clipNames.map((n) => [n, () => avatar.playOnce(n, 4)])));
  panel.append(group('Émotions (visage + clip)', EMOTIONS.map((e) => [e, () => avatar.cue({ emotion: e, intensity: 0.8 })])));
  panel.append(group('Visage seul', EXPRESSIONS.map((n) => [n, () => avatar.setExpression(n, 1)])));
  panel.append(group('Bouche', [
    ['parler 3 s', () => {
      avatar.setSound(true);
      const t = setInterval(() => avatar.setMouth(0.1 + Math.random() * 0.25), 90);
      setTimeout(() => { clearInterval(t); avatar.setMouth(0); avatar.setSound(false); }, 3000);
    }],
  ]));
  document.body.append(panel);
}
