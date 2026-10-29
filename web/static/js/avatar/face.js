// FaceController: drives the VRM expressions (mood, blink, lip-sync). No three.js dependency, so it is unit-testable.
//
// Expression names differ between VRM generations (VRM 0.x: Joy/Sorrow/Fun/A/O and a custom "Surprised";
// VRM 1.0: happy/sad/relaxed/aa/oh/surprised). Each logical name is resolved ONCE against the names the loaded
// model actually exposes, so a model with unusual names degrades gracefully instead of silently doing nothing.

export const EXPR_NAMES = ['happy', 'angry', 'sad', 'relaxed', 'surprised'];

export const EXPRESSION_ALIASES = {
  happy: ['happy', 'Joy', 'joy'],
  angry: ['angry', 'Angry'],
  sad: ['sad', 'Sorrow', 'sorrow'],
  relaxed: ['relaxed', 'Fun', 'fun'],
  surprised: ['surprised', 'Surprised', 'surprise'],
  aa: ['aa', 'A', 'a'],
  oh: ['oh', 'O', 'o'],
  blink: ['blink', 'Blink'],
};

// Built-in fallback: expression weights of the original moods, used until (or unless) the table served by
// /api/emotions arrives. The full table lives in character/emotion_spec.py, never in this file.
export const DEFAULT_EMOTION_EXPR = {
  neutre: {},
  joyeuse: { happy: 0.45, relaxed: 0.75 },
  taquine: { happy: 0.35, relaxed: 0.5 },
  boudeuse: { angry: 0.4, relaxed: 0.25 },
  surprise: { surprised: 0.7 },
};

// Presets that open the mouth are capped so they never fight the lip-sync.
const MOUTH_OPENING_CAPS = { happy: 0.45, surprised: 0.6 };
const MOOD_WEIGHT = 0.4;      // share of the slow mood kept as a permanent baseline
const PULSE_HOLD_S = 2.0;     // a reaction holds at full strength this long (and while she speaks)...
const PULSE_FADE_S = 4.0;     // ...then fades over this long
const MAX_WEIGHT = 0.8;

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

/** Map each logical expression to the real name the model exposes (or null when it has none). */
export function resolveExpressions(em) {
  const out = {};
  for (const [logical, candidates] of Object.entries(EXPRESSION_ALIASES)) {
    out[logical] = candidates.find((n) => { try { return !!em.getExpression(n); } catch (e) { return false; } }) ?? null;
  }
  return out;
}

export class FaceController {
  constructor() {
    this.resolved = {};
    this.emotionExpr = DEFAULT_EMOTION_EXPR;
    this.speaking = false;
    this._mood = {};
    this._pulse = {};
    this._pulseAge = 99;
    this._manual = null;
    this._now = {};
    this._mouth = 0;
    this._mouthTarget = 0;
    this._blinkT = 2;
    this._blinkV = 0;
  }

  /** Call once the VRM is loaded. Returns the resolved name table (handy for the debug panel). */
  attach(expressionManager) {
    this.resolved = expressionManager ? resolveExpressions(expressionManager) : {};
    return this.resolved;
  }

  /** Emotion table from /api/emotions: name -> expression weights. An unusable payload changes nothing. */
  setSpecs(specs) {
    const emotions = specs && specs.emotions;
    if (!emotions || typeof emotions !== 'object') return;
    const table = { neutre: {} };
    for (const [name, entry] of Object.entries(emotions)) table[name] = (entry && entry.expr) || {};
    this.emotionExpr = table;
  }

  /** The slow mood keeps a soft baseline; a full-strength pulse fires now and holds while she speaks. */
  setEmotion(emotion, intensity = 0.5, { pulse = true } = {}) {
    const base = this.emotionExpr[emotion] || {};
    const k = clamp(0.35 + Number(intensity) * 0.75, 0.25, 1);
    const t = {};
    for (const n of EXPR_NAMES) t[n] = (base[n] || 0) * k;
    this._mood = t;
    if (pulse) { this._pulse = t; this._pulseAge = 0; }
    this._manual = null;
  }

  /** Debug: hold one preset (weight 0 releases it) until the next setEmotion(). */
  hold(name, weight = 1) {
    const t = {};
    for (const n of EXPR_NAMES) t[n] = n === name ? weight : 0;
    this._manual = t;
  }

  setSpeaking(flag) { this.speaking = !!flag; }
  setMouth(level) { this._mouthTarget = clamp(Number(level) || 0, 0, 1); }

  /** Expression targets for this frame (before smoothing). */
  targets(dt) {
    this._pulseAge = this.speaking ? 0 : (this._pulseAge ?? 99) + dt;
    const age = this._pulseAge;
    const pf = age < PULSE_HOLD_S ? 1 : Math.max(0, 1 - (age - PULSE_HOLD_S) / PULSE_FADE_S);
    const out = {};
    for (const n of EXPR_NAMES) {
      if (this._manual) { out[n] = this._manual[n] || 0; continue; }
      const mood = (this._mood[n] || 0) * MOOD_WEIGHT;
      const pulse = (this._pulse[n] || 0) * (1 - MOOD_WEIGHT) * pf;
      out[n] = Math.min(MAX_WEIGHT, mood + pulse);
    }
    for (const [n, cap] of Object.entries(MOUTH_OPENING_CAPS)) out[n] = Math.min(out[n] || 0, cap);
    return out;
  }

  /** Advance one frame and write the weights to the VRM expression manager. */
  update(dt, em, random = Math.random) {
    if (!em) return;
    const set = (logical, v) => { const name = this.resolved[logical]; if (name) em.setValue(name, v); };
    const targets = this.targets(dt);
    for (const n of EXPR_NAMES) {
      const cur = this._now[n] || 0;
      const next = cur + ((targets[n] || 0) - cur) * Math.min(1, dt * 4);
      this._now[n] = next;
      set(n, next);
    }
    // lip-sync: fast attack, slower release; the jaw never goes past about half open
    const rate = this._mouthTarget > this._mouth ? 22 : 14;
    this._mouth += (this._mouthTarget - this._mouth) * Math.min(1, dt * rate);
    const m = this.speaking ? this._mouth : 0;
    set('aa', Math.min(0.5, m * 0.55));
    set('oh', Math.min(0.15, m * 0.15));
    // blink
    this._blinkT -= dt;
    if (this._blinkT <= 0) { this._blinkV = 1; this._blinkT = 2.2 + random() * 4; }
    if (this._blinkV > 0) {
      this._blinkV = Math.max(0, this._blinkV - dt * 7);
      set('blink', Math.sin(Math.min(1, this._blinkV) * Math.PI));
    } else set('blink', 0);
  }
}
