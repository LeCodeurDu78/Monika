// The page: connects to Monika, drives the avatar and refreshes the dashboard panels (see panels.js).
import { render, setEmotionLabels } from './panels.js';

const STATE_TEXT = { idle: 'Au repos', listening: 'J’écoute', thinking: 'Je réfléchis', speaking: 'Je parle' };
const SPEAKING_LEVEL = 0.02;

class Page {
  constructor() {
    this.avatar = null;  // loaded lazily: the dashboard must keep working even without the 3D files
    // Post-processing: "?fx=low" turns it off, "?fx=on" forces it on, otherwise the setting decides
    // (read from the first /api/status, so the page never guesses what the backend allows).
    const fx = new URLSearchParams(location.search).get('fx');
    this.fx = fx === null ? null : fx !== 'low';
    this.state = 'idle';
    this.lastSound = 0;
    this.refreshTimer = null;
    this.retry = 500;
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
    if (msg.type === 'hello') { this.setState(msg.state); this.refresh(); }
    else if (msg.type === 'state') this.setState(msg.state);
    else if (msg.type === 'level') {
      this.avatar?.setMouth(msg.value);
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
      const status = await response.json();
      if (this.fx === null && typeof status?.app?.fx === 'boolean') this.fx = status.app.fx;
      render(status, () => this.refresh());
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

  async loadAvatar(note) {
    const fail = (text) => { note.hidden = false; note.textContent = text; };
    try {
      const { Avatar } = await import('./avatar.js');
      this.avatar = new Avatar(document.getElementById('avatar'), {
        fx: this.fx === true,
        onFxFallback: (reason) => {
          note.hidden = false;
          note.textContent = `Effets visuels désactivés : ${reason}.`;
        },
      });
    } catch (err) {
      console.error('[avatar]', err);
      fail('Avatar 3D indisponible : bibliothèques manquantes. Lance « python scripts/vendor_web.py » une fois.');
      return;
    }
    if (!this.avatar.init()) { fail(this.avatar.error); return; }
    this.avatar.setState(this.state);
    await this.loadEmotionSpecs();
    try {
      this.modelInfo = await this.avatar.load('/static/avatar.vrm', '/static/clips.json');
    } catch (err) {
      fail(`Le modèle 3D n’a pas pu être chargé : ${err.message || err}`);
      return;
    }
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
      this.avatar?.setSpecs(specs);
      setEmotionLabels(specs);
    } catch (err) {
      console.warn('[emotions] table indisponible, table interne utilisée', err);
    }
  }

  async start() {
    const note = document.getElementById('stage-note');
    window.monika = this;  // Handy in the browser console, and for the automated checks
    await this.refresh();  // before the avatar: the status says whether the visual effects are allowed
    await this.loadAvatar(note);
    this.connect();
    this.refresh();
    setInterval(() => { if (!document.hidden) this.refresh(); }, 3000);
    setInterval(() => this.chip(), 250);
  }
}

new Page().start();
