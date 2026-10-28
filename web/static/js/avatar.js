// Avatar: Monika's 3D stage. A VRM model, motion clips retargeted onto it (ClipLibrary), expressions (FaceController)
// and a cue controller that decides what to play. Playback adapted from OpenSakura (MIT, (c) 2026 MekaHime Inc.).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { ClipLibrary } from './avatar/clip_library.js';
import { FaceController } from './avatar/face.js';
import { CueController } from './avatar/cues.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const FOV = 28;
const FRAMING = {
  // visible height / visible width, as a fraction of the body height (feet to head); centre relative to the head bone
  bust: { height: 0.56, width: 0.62, centre: (head, feet, H) => head - 0.12 * H },
  full: { height: 1.35, width: 0.9, centre: (head, feet, H) => feet + 0.55 * H },
};
// the page reports a sound level 0..1 (loudness of Monika's voice); this scales it to a mouth opening
const MOUTH_GAIN = 3.5;

export class Avatar extends ClipLibrary {
  constructor(canvas, opts = {}) {
    super({ clipsBase: opts.clipsBase || '/static/clips/', onStatus: opts.onStatus });
    this.canvas = canvas;
    this.view = opts.view || new URLSearchParams(location.search).get('view') || 'bust';
    this.error = null;
    this.ready = false;
    this.state = 'idle';
    this.idleName = 'breathing';
    this.current = null;
    this.mixer = null;
    this._chainId = 0;
    this.onSettle = null;
    this.face = new FaceController();
    this.cues = new CueController({ player: this, face: this.face });
    this._tmpA = new THREE.Vector3();
    this._tmpB = new THREE.Vector3();
  }

  /** Create the WebGL stage. Returns false (and sets this.error) when WebGL is unavailable. */
  init() {
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true });
    } catch (err) {
      this.error = 'WebGL n’est pas disponible : impossible d’afficher l’avatar 3D.';
      return false;
    }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 50);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x5a4a6a, 1.35));
    const key = new THREE.DirectionalLight(0xffffff, 1.7);
    key.position.set(1.2, 2.4, 2.2);
    this.scene.add(key);
    this.clock = new THREE.Clock();
    this._resize();
    if ('ResizeObserver' in window) new ResizeObserver(() => this._resize()).observe(this.canvas);
    else window.addEventListener('resize', () => this._resize());
    this.renderer.setAnimationLoop(() => this._tick());
    if (new URLSearchParams(location.search).has('debug')) window.__avatar = this;
    return true;
  }

  _resize() {
    const w = this.canvas.clientWidth || 1, h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this._frame();
  }

  /** Camera framing from the model's own proportions (head and feet bones), whatever its scale. */
  _frame() {
    const humanoid = this.vrm?.humanoid;
    const head = humanoid?.getRawBoneNode('head');
    if (!head) return;
    head.getWorldPosition(this._tmpA);
    const lf = humanoid.getRawBoneNode('leftFoot'), rf = humanoid.getRawBoneNode('rightFoot');
    let feetY = 0;
    if (lf && rf) { lf.getWorldPosition(this._tmpB); const ly = this._tmpB.y; rf.getWorldPosition(this._tmpB); feetY = (ly + this._tmpB.y) / 2; }
    const headY = this._tmpA.y;
    const H = Math.max(0.5, headY - feetY);
    const f = FRAMING[this.view] || FRAMING.bust;
    const half = Math.tan(THREE.MathUtils.degToRad(FOV / 2));
    const dist = Math.max((f.height * H) / 2 / half, (f.width * H) / 2 / (half * this.camera.aspect));
    const cy = f.centre(headY, feetY, H);
    this.camera.position.set(this._tmpA.x, cy + 0.02 * H, this._tmpA.z + dist);
    this.camera.lookAt(this._tmpA.x, cy, this._tmpA.z);
  }

  // ---------- loading ----------
  async load(vrmUrl = '/static/avatar.vrm', manifestUrl = '/static/clips.json') {
    const loader = new GLTFLoader();
    loader.register((parser) => new VRMLoaderPlugin(parser));
    const gltf = await loader.loadAsync(vrmUrl);
    const vrm = gltf.userData.vrm;
    if (!vrm) throw new Error('le fichier chargé n’est pas un VRM');
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    if (vrm.meta?.metaVersion === '0') VRMUtils.rotateVRM0(vrm);
    vrm.scene.traverse((o) => { o.frustumCulled = false; });
    if (this.vrm) { this.scene.remove(this.vrm.scene); VRMUtils.deepDispose(this.vrm.scene); }
    this.vrm = vrm;
    this.scene.add(vrm.scene);
    vrm.scene.updateMatrixWorld(true);
    if (vrm.lookAt) { vrm.lookAt.target = this.camera; vrm.lookAt.autoUpdate = true; }
    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.clipCache.clear();
    this._srcCache.clear();
    this._restInfo = this._computeVrmRest();
    this.face.attach(vrm.expressionManager);
    this._frame();
    try {
      this.manifest = await (await fetch(manifestUrl, { cache: 'no-cache' })).json();
      this.idleName = this.manifest.idle || 'breathing';
    } catch (err) { this.manifest = { clips: [] }; }
    await this.getClip(this.idleName);
    this.ready = true;
    this.cues.setState(this.state);
    this._prefetch();
    return { version: vrm.meta?.metaVersion === '0' ? 'VRM 0.x' : 'VRM 1.0', clips: this.manifest.clips.length };
  }

  /** Download the remaining clip files in the background; baking stays lazy (it costs main-thread time). */
  async _prefetch() {
    const files = [...new Set((this.manifest?.clips || []).map((c) => c.file))];
    for (const f of files) {
      if (this._srcCache.has(f)) continue;
      try { await this._loadSource(f); } catch (err) { /* reported when the clip is actually played */ }
      await sleep(30);
    }
  }

  /** Clip names the debug panel can offer (manifest entries marked "hidden" are left out). */
  get clipNames() { return (this.manifest?.clips || []).filter((c) => !c.hidden).map((c) => c.name); }

  /** Exact-name check: only manifest clips can ever be played from a cue. */
  has(name) { return (this.manifest?.clips || []).some((c) => c.name === name); }

  // ---------- playback ----------
  // The mixer fills any weight below 1 with the bones' binding pose (the VRM's T-pose), so every switch is
  // a cross-fade whose two weights sum to 1; an action is never faded in from 0 on its own.
  _play(clip, { loop = true, fade = 0.35 } = {}) {
    if (!this.mixer || !clip) return null;
    let action = this.mixer.clipAction(clip);
    if (action === this.current) {
      // already playing: a loop just keeps going (chain [Talk, Talk], idle() while idling) ...
      if (loop) { action.setLoop(THREE.LoopRepeat, Infinity); action.clampWhenFinished = false; return action; }
      // ... a one-shot starts over, cross-fading from its held last pose into a twin action
      action = this.mixer.clipAction(this._twin(clip));
    }
    action.reset();
    action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    action.clampWhenFinished = !loop;
    action.enabled = true;
    action.setEffectiveWeight(1);
    if (this.current && fade > 0) { this.current.fadeOut(fade); action.fadeIn(fade); }
    else if (this.current) this.current.stop();       // fade 0: hard cut
    action.play();                                    // first clip ever: straight in at full weight
    this.current = action;
    return action;
  }

  /** A second action for the same clip (mixer.clipAction returns one action per clip). */
  _twin(clip) {
    this._twins ??= new WeakMap();
    if (!this._twins.has(clip)) this._twins.set(clip, clip.clone());
    return this._twins.get(clip);
  }

  /** Back to the idle loop (a no-op when it is already playing); cancels any running chain. */
  async idle() {
    this._chainId++;
    const clip = await this.getClip(this.idleName);
    if (clip) this._play(clip, { loop: true, fade: 0.6 });
  }

  /** Play an action chain [{name, duration}], then return to idle. Manifest entries marked "once" play a
   *  single time (never cut short) and hold their last pose; the rest loop to fill the duration.
   *  "fade" sets the cross-fade into the clip (seconds). */
  async playChain(actions = []) {
    const id = ++this._chainId;
    for (const a of actions) {
      if (id !== this._chainId) return;
      const clip = await this.getClip(a.name);
      if (id !== this._chainId) return;
      if (!clip) { this.onStatus(`no clip for ${a.name}`); continue; }
      const entry = this._entry(a.name) || {};
      const once = !!entry.once;
      let dur = Math.max(0.4, Number(a.duration) || clip.duration);
      if (once) dur = Math.max(dur, clip.duration);
      const fade = entry.fade != null && Number(entry.fade) >= 0 ? Number(entry.fade) : 0.35;
      this._play(clip, { loop: !once, fade });
      this.onStatus(`▶ ${a.name} (${dur.toFixed(1)}s)`);
      await sleep(dur * 1000);
    }
    if (id === this._chainId) { if (this.onSettle) this.onSettle(); else this.idle(); }
  }

  async playOnce(name, duration) {
    return this.playChain([{ name, duration: duration || 0 }]);
  }

  /** Loop one clip until something else is requested (used for the thinking and talking baselines). */
  async hold(name, fade = 0.4) {
    const id = ++this._chainId;
    const clip = await this.getClip(name);
    if (id !== this._chainId) return;
    if (clip) this._play(clip, { loop: true, fade });
    else this.idle();
  }


  // ---------- public API used by the page ----------
  setState(state) { this.state = state; if (this.ready) this.cues.setState(state); }
  setSound(flag) { this.cues.setSound(flag); }
  setMouth(level) { this.face.setMouth(level * MOUTH_GAIN); }
  cue(msg) { return this.cues.cue(msg); }

  _tick() {
    const dt = Math.min(0.1, this.clock.getDelta());
    this.mixer?.update(dt);
    this.face.update(dt, this.vrm?.expressionManager);
    this.vrm?.update(dt);
    this.renderer.render(this.scene, this.camera);
  }
}
