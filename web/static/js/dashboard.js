// The page: connects to Monika, drives the avatar and refreshes the dashboard panels (see panels.js).
import { render, setEmotionLabels } from './panels.js';
import { mountChat } from './chat.js';
import { mountVoice } from './voice.js';
import { setLanguage, startI18n } from './i18n.js';

const STATE_TEXT = { idle: 'Au repos', listening: 'J’écoute', thinking: 'Je réfléchis', speaking: 'Je parle' };
const SPEAKING_LEVEL = 0.02;
// The server sends 20 ms RMS windows (speech sits around 0.02–0.15).
// shape its own player produces, so the same curve is applied here; the animation stays its own.
const mouthLevel = (rms) => Math.min(1, Math.pow(Math.min(1, rms * 4.5), 0.85));
const TAB_KEY = 'monika.tab';   // remembered, so a phone reopens on the section it was on
const PANEL_KEY = 'monika.panelWidth';   // the panel keeps the width it was dragged to
const HIDDEN_KEY = 'monika.panelHidden'; // "rangé" state

// The loading screen (index.html): real progress while the model downloads, then a fade.
const LOADING = {
  box: document.getElementById('loading'),
  step: document.getElementById('loading-step'),
  fill: document.getElementById('loading-fill'),
};

function setLoading(fraction, text) {
  if (text && LOADING.step) LOADING.step.textContent = text;
  if (LOADING.fill) LOADING.fill.style.width = `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%`;
}

function doneLoading() {
  const box = LOADING.box;
  if (!box || box.classList.contains('done')) return;
  box.classList.add('done');
  box.addEventListener('transitionend', () => box.remove(), { once: true });
  setTimeout(() => box.remove(), 900);   // reduced motion: there is no transition to wait for
}

class Page {
  constructor() {
    this.avatar = null;  // loaded lazily: the dashboard must keep working even without the 3D files
    this.state = 'idle';
    this.lastSound = 0;
    this.refreshTimer = null;
    this.retry = 500;
    this.chat = null;  // the conversation panel: the page is a client like the terminal
    this.voice = null;  // microphone and spoken answers, in the browser (voice.js)
  }

  speakingNow() { return performance.now() - this.lastSound < 400; }

  chip() {
    const speaking = this.speakingNow();
    this.avatar?.setSound(speaking);
    const shown = speaking ? 'speaking' : this.state;
    const chip = document.getElementById('state-chip');
    chip.dataset.state = shown;
    chip.textContent = STATE_TEXT[shown];
  }

  setState(state) {
    this.state = state;
    this.avatar?.setState(state);
    this.chip();
  }

  onMessage(msg) {
    if (msg.type === 'hello') { this.setState(msg.state); this.refresh(); this.chat?.seed(); }
    else if (msg.type === 'state') this.setState(msg.state);
    else if (msg.type === 'chat') this.chat?.receive(msg);
    else if (msg.type === 'voice' || msg.type === 'audio') this.voice?.onEvent(msg);
    else if (msg.type === 'level') {
      this.avatar?.setMouth(mouthLevel(msg.value));
      if (msg.value > SPEAKING_LEVEL) { this.lastSound = performance.now(); this.chip(); }
    } else if (msg.type === 'avatar') this.avatar?.cue(msg);
    else if (msg.type === 'avatar_chain') this.avatar?.chain(msg.actions);
    else if (msg.type === 'activity') this.scheduleRefresh(400);
    else if (msg.type === 'character') this.scheduleRefresh(200);
  }

  scheduleRefresh(delay) {
    clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => this.refresh(), delay);
  }

  async refresh() {
    try {
      const response = await fetch('/api/status', { cache: 'no-store' });
      const data = await response.json();
      if (data.app?.language) setLanguage(data.app.language);
      render(data, () => this.refresh());
    } catch (err) {
      /* The link chip already tells the connection is down */
    }
  }

  connect() {
    const link = document.getElementById('link-chip');
    // wss behind the HTTPS proxy (tailscale serve): a browser refuses a plain ws from an https page
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    ws.binaryType = 'arraybuffer';  // Monika's voice comes back as PCM frames, not as JSON
    ws.onopen = () => { link.dataset.link = 'on'; link.textContent = 'Connectée'; this.retry = 500; };
    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') { this.voice?.onAudio(event.data); return; }
      this.onMessage(JSON.parse(event.data));
    };
    ws.onclose = () => {
      link.dataset.link = 'off';
      link.textContent = 'Connexion perdue : nouvelle tentative…';
      this.voice?.onClose();  // the microphone must not stay open on a dead link
      setTimeout(() => this.connect(), this.retry);
      this.retry = Math.min(this.retry * 2, 8000);
    };
    this.ws = ws;
  }

  async loadAvatar(note) {
    const fail = (text) => { note.hidden = false; note.textContent = text; doneLoading(); };
    try {
      const { Avatar } = await import('./avatar.js');
      this.avatar = new Avatar(document.getElementById('avatar'));
    } catch (err) {
      console.error('[avatar]', err);
      fail('Avatar 3D indisponible : bibliothèques manquantes. Lance « python scripts/vendor_web.py » une fois.');
      return;
    }
    if (!this.avatar.init()) { fail(this.avatar.error); return; }
    this.avatar.setState(this.state);
    await this.loadEmotionSpecs();
    setLoading(0, 'Téléchargement du modèle…');
    try {
      this.modelInfo = await this.avatar.load('/static/assets/avatar.vrm', '/static/assets/clips.json',
        (fraction) => setLoading(fraction));
    } catch (err) {
      fail(`Le modèle 3D n’a pas pu être chargé : ${err.message || err}`);
      return;
    }
    doneLoading();
    if (new URLSearchParams(location.search).has('debug')) {
      import('./debug.js').then((m) => m.mountDebugPanel(this.avatar)).catch((err) => console.error('[debug]', err));
    }
  }

  /** The emotion table (labels, expressions, gestures) served by the backend; the page's own fallbacks cover a failure. */
  async loadEmotionSpecs() {
    try {
      const response = await fetch('/api/emotions', { cache: 'no-store' });
      if (!response.ok) return;
      const specs = await response.json();
      setEmotionLabels(specs);
    } catch (err) {
      console.warn('[emotions] table indisponible, table interne utilisée', err);
    }
  }

  async start() {
    startI18n();
    const note = document.getElementById('stage-note');
    window.monika = this;  // Handy in the browser console, and for the automated checks
    this.chat = mountChat();
    this.voice = mountVoice({ status: (text) => this.chat?.status(text) });
    setLoading(0, 'Chargement de Monika…');
    await this.loadAvatar(note);
    this.connect();
    this.refresh();
    setInterval(() => { if (!document.hidden) this.refresh(); }, 3000);
    setInterval(() => this.chip(), 250);
  }
}

/** La poignée entre l'avatar et le panneau : glisser pour la largeur, bouton central pour replier. */
function mountLayout() {
  const layout = document.getElementById('layout');
  const splitter = document.getElementById('splitter');
  const toggle = document.getElementById('panel-toggle');
  const panels = document.getElementById('panels');
  if (!layout || !splitter || !toggle || !panels) return null;
  const MIN = 300;   // the width lives on .layout itself: it redefines --panel-w
  const DEFAULT_RATIO = 0.38;   // same shape as the CSS fallback, so the first frame does not jump
  const defaultWidth = () => Math.min(620, Math.max(MIN, Math.round(window.innerWidth * DEFAULT_RATIO)));
  const maxWidth = () => Math.max(MIN, Math.round(window.innerWidth * 0.7));
  let width = defaultWidth();   // the width the panel gets back when it is unfolded
  let hidden = false;
  let dragging = false;
  let reframeTimer = 0;

  // Always a px value: the column has to interpolate, and "0" must stay reachable.
  const setColumn = (px) => layout.style.setProperty('--panel-w', `${Math.max(0, Math.round(px))}px`);
  const apply = (px) => {
    width = Math.round(Math.min(Math.max(px, MIN), maxWidth()));
    if (!hidden) setColumn(width);
  };
  const remember = () => {
    try { localStorage.setItem(PANEL_KEY, String(width)); } catch (err) { /* private mode */ }
  };
  const reframe = () => window.dispatchEvent(new Event('resize'));   // the avatar canvas reframes

  try {
    const saved = Number(localStorage.getItem(PANEL_KEY));
    if (saved > 0) width = Math.min(Math.max(saved, MIN), maxWidth());
  } catch (err) { /* private mode */ }
  setColumn(width);

  // On a phone the panel sits under the avatar: the arrow must point down/up, not right/left.
  const stacked = window.matchMedia('(max-width: 900px)');
  const arrow = (folded) => (stacked.matches ? (folded ? '▲' : '▼') : (folded ? '❮' : '❯'));

  // Téléphone en paysage : avatar et panneau côte à côte, pas de poignée, donc rien à ranger.
  const sideBySide = window.matchMedia('(max-width: 900px) and (max-height: 500px)');
  const show = (value, animate = true) => {
    if (value && sideBySide.matches) value = false;
    hidden = value;
    if (!animate) layout.classList.add('instant');
    // A window made narrower while folded must not hand back a too-wide panel.
    if (!value) width = Math.min(width, maxWidth());
    setColumn(value ? 0 : width);   // the column folds to 0px, the CSS animates it
    layout.classList.toggle('collapsed', value);
    panels.toggleAttribute('inert', value);   // folded: not reachable with the keyboard
    toggle.setAttribute('aria-expanded', String(!value));
    toggle.title = value ? 'Afficher le panneau' : 'Masquer le panneau';
    toggle.textContent = arrow(value);
    try { localStorage.setItem(HIDDEN_KEY, value ? '1' : '0'); } catch (err) { /* private mode */ }
    if (!animate) requestAnimationFrame(() => layout.classList.remove('instant'));
    // The canvas only knows its final width once the fold is over.
    clearTimeout(reframeTimer);
    reframeTimer = setTimeout(reframe, animate ? 360 : 0);
  };
  let startHidden = false;
  try { startHidden = localStorage.getItem(HIDDEN_KEY) === '1'; } catch (err) { /* private mode */ }
  show(startHidden, false);
  stacked.addEventListener('change', () => { toggle.textContent = arrow(hidden); });
  sideBySide.addEventListener('change', () => { if (sideBySide.matches && hidden) show(false, false); });

  // La poignée redimensionne le panneau, au doigt comme à la souris : en hauteur sur téléphone (retenue par section),
  // en largeur sur grand écran. Tirée presque jusqu'au bord, elle range le panneau ; tirée à l'envers quand il est
  // rangé, elle le rouvre ; un simple clic bascule.
  const SHEET_MIN = 150;    // hauteur minimale d'un volet ouvert
  const SHEET_FOLD = 100;   // en dessous, on relâche = on range
  const WIDTH_FOLD = 150;   // idem pour la largeur sur grand écran
  const tabsBar = document.querySelector('.tabs');
  const tabName = () => (document.getElementById('tab-dashboard')?.checked ? 'dashboard' : 'chat');
  const sheetKey = () => `monika.sheetH.${tabName()}`;
  const sheetMax = () => Math.max(SHEET_MIN, layout.clientHeight - (tabsBar?.offsetHeight || 0) - splitter.offsetHeight - 90);
  const applySheet = () => {   // remet la hauteur retenue pour la section affichée (ou celle par défaut du CSS)
    let saved = 0;
    try { saved = Number(localStorage.getItem(sheetKey())); } catch (err) { /* private mode */ }
    if (saved > 0) layout.style.setProperty('--sheet-h', `${Math.round(Math.min(Math.max(saved, SHEET_MIN), sheetMax()))}px`);
    else layout.style.removeProperty('--sheet-h');
  };
  applySheet();

  let drag = null;
  let swiped = false;   // un glissement ne doit pas déclencher en plus le « click » qui le suit
  const endDrag = () => {
    drag = null;
    layout.classList.remove('resizing');
    splitter.classList.remove('dragging');
    document.body.style.userSelect = '';
    window.removeEventListener('pointermove', onDragMove);
    window.removeEventListener('pointerup', onDragEnd);
    window.removeEventListener('pointercancel', onDragCancel);
  };
  // Le geste s'écoute sur la fenêtre, pas sur la poignée (30 px) : le doigt comme la souris peuvent en sortir.
  function onDragMove(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const d = (drag.vertical ? event.clientY : event.clientX) - drag.p;   // vers le haut / la gauche = plus grand
    if (!drag.moved) {
      if (Math.abs(d) < 8) return;
      drag.moved = true;
      swiped = true;
      if (hidden) {   // rangé : tirer dans le sens de l'ouverture le rouvre à la taille retenue
        if (d < 0) { applySheet(); show(false); }
        endDrag();
        window.addEventListener('pointerup', () => setTimeout(() => { swiped = false; }, 400), { once: true });
        return;
      }
      layout.classList.add('resizing');   // pas d'animation : le doigt mène
      splitter.classList.add('dragging');
      document.body.style.userSelect = 'none';
    }
    if (drag.vertical) {
      drag.now = Math.min(Math.max(drag.size - d, 0), sheetMax());
      layout.style.setProperty('--sheet-h', `${Math.round(drag.now)}px`);
    } else {
      drag.now = Math.min(Math.max(drag.size - d, 0), maxWidth());
      setColumn(drag.now);
    }
  }
  function onDragEnd(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const { moved, now, vertical } = drag;
    endDrag();
    if (!moved) return;
    setTimeout(() => { swiped = false; }, 400);
    if (vertical) {
      if (now < SHEET_FOLD) { applySheet(); show(true); return; }
      try { localStorage.setItem(sheetKey(), String(Math.round(now))); } catch (err) { /* private mode */ }
    } else {
      if (now < WIDTH_FOLD) { setColumn(width); show(true); return; }
      width = Math.round(Math.min(Math.max(now, MIN), maxWidth()));
      setColumn(width);
      remember();
    }
    reframe();
  }
  function onDragCancel() { endDrag(); applySheet(); if (!hidden) setColumn(width); }
  splitter.addEventListener('pointerdown', (event) => {
    if (sideBySide.matches || drag || event.button > 0) return;
    const vertical = stacked.matches;
    const box = panels.getBoundingClientRect();
    drag = {
      id: event.pointerId, vertical, p: vertical ? event.clientY : event.clientX,
      size: hidden ? 0 : (vertical ? box.height : box.width), moved: false, now: 0,
    };
    window.addEventListener('pointermove', onDragMove);
    window.addEventListener('pointerup', onDragEnd);
    window.addEventListener('pointercancel', onDragCancel);
  });

  toggle.onclick = (event) => {
    event.stopPropagation();
    if (swiped) { swiped = false; return; }
    show(!hidden);
  };

  splitter.addEventListener('keydown', (event) => {
    if (hidden || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return;
    event.preventDefault();
    layout.classList.add('instant');
    apply(width + (event.key === 'ArrowLeft' ? 24 : -24));   // left widens the panel
    requestAnimationFrame(() => layout.classList.remove('instant'));
    remember();
    reframe();
  });
  splitter.addEventListener('dblclick', () => {   // double-click: back to the default width
    try { localStorage.removeItem(PANEL_KEY); } catch (err) { /* private mode */ }
    layout.classList.add('instant');
    apply(defaultWidth());
    requestAnimationFrame(() => layout.classList.remove('instant'));
    reframe();
  });
  window.addEventListener('resize', () => {   // a narrower window must not leave the panel overflowing
    if (hidden) return;
    const clamped = Math.min(width, maxWidth());
    if (clamped !== width) { width = clamped; setColumn(width); }
  });
  window.addEventListener('resize', () => { if (stacked.matches) applySheet(); });   // rotation : hauteur à nouveau bornée
  return { show, stacked, applySheet };
}

/** The radios switch sections on their own (CSS); this only remembers the choice and reframes. */
function mountTabs(layout) {
  const radios = [...document.querySelectorAll('.tab-radio')];
  if (!radios.length) return;
  let saved = '';
  try { saved = localStorage.getItem(TAB_KEY) || ''; } catch (err) { /* private mode */ }
  const wanted = document.getElementById(`tab-${saved === 'dashboard' ? 'dashboard' : 'chat'}`);
  if (wanted) wanted.checked = true;
  // Sur téléphone, choisir le tableau de bord ne doit jamais l'ouvrir sur un volet rangé.
  const unfold = () => { if (layout && layout.stacked.matches) layout.show(false, false); };
  if (wanted && wanted.id === 'tab-dashboard') unfold();
  layout?.applySheet();
  for (const radio of radios) {
    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      if (radio.id === 'tab-dashboard') unfold();
      layout?.applySheet();   // chaque section garde sa propre hauteur
      try { localStorage.setItem(TAB_KEY, radio.id.replace('tab-', '')); } catch (err) { /* private mode */ }
      // The avatar canvas may have changed size: let it reframe.
      window.dispatchEvent(new Event('resize'));
    });
  }
}

/**
 * Clavier virtuel : la page est calée sur la zone réellement visible (--app-h, --app-top), sinon le champ
 * de saisie se retrouve sous le clavier ou la page glisse sous la barre d'adresse. `html.kb` = clavier ouvert.
 */
function mountViewport() {
  const vv = window.visualViewport;
  if (!vv) return;
  const root = document.documentElement;
  const sync = () => {
    if (vv.scale > 1.01) return;   // pincé pour zoomer : on ne touche à rien
    root.style.setProperty('--app-h', `${Math.round(vv.height)}px`);
    root.style.setProperty('--app-top', `${Math.round(vv.offsetTop)}px`);
    root.classList.toggle('kb', window.innerHeight - vv.height > 140);
  };
  vv.addEventListener('resize', sync);
  vv.addEventListener('scroll', sync);
  sync();
}

mountViewport();
const layout = mountLayout();
mountTabs(layout);
new Page().start();
