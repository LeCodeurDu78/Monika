// Monika's 3D avatar: idle/thinking animations, simulated blinking.
import * as THREE from 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.min.js';
import { GLTFLoader } from 'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/GLTFLoader.js';

const BLINK_DOWN_S = 0.06;
const BLINK_UP_S = 0.09;
const BLINK_SQUASH = 0.92;     // How much the eyes are flattened at the bottom of a blink

export class Avatar {
  constructor(canvas) {
    this.canvas = canvas;
    this.state = 'idle';
    this.blink = 0;
    this.clip = '';
    this.ready = false;
    this.error = null;
    this.eyes = [];
    this._nextBlinkAt = 0;
    this._blinkT = -1;
    this._doubleBlink = false;
  }

  init() {
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, alpha: true });
    } catch (err) {
      this.error = 'WebGL est indisponible dans ce navigateur : l’avatar ne peut pas s’afficher.';
      return false;
    }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(24, 1, 0.05, 50);
    this.scene.add(new THREE.AmbientLight(0xffffff, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(1.5, 2.5, 3);
    this.scene.add(key);
    this.clock = new THREE.Clock();
    new ResizeObserver(() => this._resize()).observe(this.canvas);
    this._resize();
    return true;
  }

  async load(url) {
    const gltf = await new GLTFLoader().loadAsync(url);
    this.model = gltf.scene;
    this.scene.add(this.model);
    this.mixer = new THREE.AnimationMixer(this.model);
    this.actions = {};
    for (const clip of gltf.animations) this.actions[clip.name] = this.mixer.clipAction(clip);
    const names = Object.keys(this.actions);
    this.model.traverse((node) => {
      if (node.name === 'CC_Base_L_Eye' || node.name === 'CC_Base_R_Eye') this.eyes.push(node);
      if (node.name === 'CC_Base_Head') this.head = node;
      if (node.isMesh) node.frustumCulled = false;  // Skinned meshes move out of their bind-pose bounds
    });
    this.eyeBase = this.eyes.map((e) => e.scale.y);
    this._play(this.actions.Idle ? 'Idle' : names[0], 0);
    this.mixer.update(0);
    this.model.updateMatrixWorld(true);
    this._frame();
    this.ready = true;
    this.renderer.setAnimationLoop(() => this._tick());
    return { clips: names, eyes: this.eyes.length };
  }

  _frame() {
    // Bust framing: centered on the head, a little below it, wide enough for the shoulders
    const head = new THREE.Vector3();
    (this.head || this.model).getWorldPosition(head);
    const box = new THREE.Box3().setFromObject(this.model);
    const height = Math.max(box.max.y - box.min.y, 0.5);
    this.target = new THREE.Vector3(head.x, head.y - 0.095 * height, head.z);
    const visible = 0.42 * height;  // Vertical extent to show
    const dist = (visible / 2) / Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    this.camera.position.set(this.target.x, this.target.y + 0.02 * height, this.target.z + dist);
    this.camera.lookAt(this.target);
  }

  _resize() {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _play(name, fade = 0.4) {
    const next = this.actions[name];
    if (!next || this.clip === name) return;
    const prev = this.actions[this.clip];
    next.reset().setEffectiveWeight(1).play();
    if (prev && fade > 0) prev.crossFadeTo(next, fade, false);
    else if (prev) prev.stop();
    this.clip = name;
  }

  setState(state) {
    this.state = state;
    if (this.ready) this._play(state === 'thinking' && this.actions.Thinking ? 'Thinking' : 'Idle');
  }

  _tick() {
    const dt = Math.min(this.clock.getDelta(), 0.1);
    this.mixer.update(dt);
    // Blink: eyes flattened on their own bone, at random intervals, sometimes twice
    this._updateBlink(dt);
    this.eyes.forEach((eye, i) => { eye.scale.y = this.eyeBase[i] * (1 - BLINK_SQUASH * this.blink); });
    this.renderer.render(this.scene, this.camera);
  }

  _updateBlink(dt) {
    const now = performance.now();
    if (this._blinkT < 0) {
      if (now >= this._nextBlinkAt) {
        this._blinkT = 0;
        this._doubleBlink = Math.random() < 0.15;
      }
      this.blink = 0;
      return;
    }
    this._blinkT += dt;
    const t = this._blinkT;
    if (t < BLINK_DOWN_S) this.blink = t / BLINK_DOWN_S;
    else if (t < BLINK_DOWN_S + BLINK_UP_S) this.blink = 1 - (t - BLINK_DOWN_S) / BLINK_UP_S;
    else {
      this.blink = 0;
      this._blinkT = -1;
      this._nextBlinkAt = now + (this._doubleBlink ? 120 : 2500 + Math.random() * 3500);
      this._doubleBlink = false;
    }
  }

  // Test helper: freeze the blink at a given amount
  pose({ blink = null } = {}) {
    if (blink !== null) { this._nextBlinkAt = Infinity; this._blinkT = -1; this.blink = blink; }
  }
}
