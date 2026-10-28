// CueController: turns Monika's state and the backend's "avatar" cues into animation, without ever fighting itself.
// No three.js dependency (the player is injected), so it is unit-testable.
//
// Rules:
//  - a baseline follows the conversation state: thinking -> "Think", speaking -> "Talk", otherwise idle;
//  - a cue ({clip, emotion, intensity, priority}) plays once over the baseline, then the baseline resumes;
//  - a running cue is only replaced by one of equal or higher priority (the face always follows the latest emotion);
//  - only clips present in the manifest can be played: an unknown name falls back to the emotion's own clips.

export const EMOTION_CLIPS = {
  joyeuse: ['Yay', 'Laugh'],
  taquine: ['LeanIn', 'Laugh'],
  boudeuse: ['Pout', 'FoldArms'],
  surprise: ['Surprised'],
};

export const BASELINE_CLIPS = { thinking: 'Think', speaking: 'Talk' };
export const DEFAULT_PRIORITY = 2;

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export class CueController {
  /**
   * @param {object} player  { has(name), hold(name), playChain(actions), idle(), onSettle }
   * @param {object} face    { setEmotion(emotion, intensity) }
   */
  constructor({ player, face }) {
    this.player = player;
    this.face = face;
    this.state = 'idle';
    this.sound = false;
    this.active = null;          // { priority } while a cue's clip is playing
    this._rotation = 0;          // alternates between an emotion's clips so she does not repeat herself
    player.onSettle = () => this.settle();
  }

  baselineClip() {
    // Audible speech wins over "thinking": the page stays in the thinking state for the whole turn, voice included
    if (this.sound) return BASELINE_CLIPS.speaking;
    if (this.state === 'thinking') return BASELINE_CLIPS.thinking;
    return null;
  }

  _applyBaseline() {
    const clip = this.baselineClip();
    if (clip && this.player.has(clip)) return this.player.hold(clip);
    return this.player.idle();
  }

  setState(state) {
    this.state = state;
    if (!this.active) this._applyBaseline();
  }

  /** True while sound is audible (the page debounces it). */
  setSound(flag) {
    flag = !!flag;
    if (flag === this.sound) return;
    this.sound = flag;
    this.face.setSpeaking?.(flag);
    if (!this.active) this._applyBaseline();
  }

  _pickClip(msg) {
    if (typeof msg.clip === 'string' && this.player.has(msg.clip)) return msg.clip;
    const options = (EMOTION_CLIPS[msg.emotion] || []).filter((n) => this.player.has(n));
    if (!options.length) return null;
    return options[this._rotation++ % options.length];
  }

  /** @returns {string|null} the clip that was started, if any */
  cue(msg = {}) {
    if (!msg || typeof msg !== 'object') return null;
    if (typeof msg.emotion === 'string') this.face.setEmotion(msg.emotion, clamp(Number(msg.intensity ?? 0.5), 0, 1));
    const clip = this._pickClip(msg);
    if (!clip) return null;
    const priority = Number.isFinite(msg.priority) ? msg.priority : DEFAULT_PRIORITY;
    if (this.active && this.active.priority > priority) return null;
    this.active = { priority };
    const duration = Number(msg.duration_s);
    this.player.playChain([{ name: clip, duration: duration > 0 ? clamp(duration, 0.4, 30) : 0 }]);
    return clip;
  }

  /** Called by the player when a chain ends. */
  settle() {
    this.active = null;
    this._applyBaseline();
  }
}
