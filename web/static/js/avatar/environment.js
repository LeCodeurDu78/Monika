// Environment: the literature club room at dusk, entirely procedural (no asset, no texture).
//
// Built around the origin so the avatar's own framing keeps working: Monika stands at (0, 0, 0) facing +Z
// (the camera side), the floor is at y = 0 and the whole room fits in a 9 m × 9 m × 3 m box. The camera side
// is left open, so nothing ever sits between her and the lens.
//
// Technique borrowed from OpenSakura (MIT, (c) 2026 MekaHime Inc.), content replaced: seeded PRNG,
// mergeGeometries + InstancedMesh for repeated furniture, sway/flicker lists driven by update(dt).
// No EffectComposer: only GLTFLoader and BufferGeometryUtils are vendored, and the room reads fine without
// depth of field. The avatar keeps owning the renderer, so there is nothing to render or resize here.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const ROOM = { halfX: 4.6, back: -5.0, front: 4.0, height: 3.1 };
const DUSK = 0x2a2434;

const COLORS = {
  floor: 0x6b4f3a,
  wall: 0x7d6f5f,
  ceiling: 0x3b3442,
  board: 0x233a2e,
  frame: 0x4a3728,
  wood: 0x8a6a4a,
  metal: 0x565a5f,
  curtain: 0xd6c8b2,
  glow: 0xe8a86a,
  lampShade: 0xd9d2c4,
  bulb: 0xffd9a0,
  dust: 0xffe6b8,
};

// deterministic layout: the room looks the same on every load
function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Per-vertex colour so one merged geometry can hold several wood and metal tones. */
function tinted(geometry, hex, jitter, rnd) {
  const base = new THREE.Color(hex);
  const count = geometry.getAttribute('position').count;
  const colors = new Float32Array(count * 3);
  const c = new THREE.Color();
  for (let i = 0; i < count; i++) {
    c.copy(base);
    if (jitter) c.offsetHSL(0, 0, (rnd() - 0.5) * jitter);
    colors[i * 3] = c.r; colors[i * 3 + 1] = c.g; colors[i * 3 + 2] = c.b;
  }
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  return geometry;
}

const box = (w, h, d, x = 0, y = 0, z = 0) => new THREE.BoxGeometry(w, h, d).translate(x, y, z);

export class Environment {
  /** @param {THREE.Scene} scene the avatar's scene: the room is added to it, background and fog included */
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    this.group.name = 'literature-club-room';
    this.t = 0;
    this.sway = [];        // { node, amp, speed, phase, tilt }
    this.flicker = [];     // { light, base, phase }
    this.dust = null;
    this._rnd = mulberry32(20261028);
    this._geometries = new Set();
    this._materials = new Map();
    this._previous = { background: scene.background, fog: scene.fog };

    this._shell();
    this._blackboard();
    this._windows();
    this._desks();
    this._lamps();
    this._dust();
    this._lighting();
    scene.add(this.group);
  }

  // ---------- small helpers ----------
  _mat(hex, opts = {}) {
    const key = `${hex}|${JSON.stringify(opts)}`;
    let material = this._materials.get(key);
    if (!material) {
      material = new THREE.MeshLambertMaterial({ color: hex, ...opts });
      this._materials.set(key, material);
    }
    return material;
  }

  /** Register a geometry and return a mesh already placed in the room. */
  _mesh(geometry, material, x = 0, y = 0, z = 0) {
    this._geometries.add(geometry);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(x, y, z);
    this.group.add(mesh);
    return mesh;
  }

  _block(w, h, d, hex, x, y, z, opts) {
    return this._mesh(box(w, h, d), this._mat(hex, opts), x, y, z);
  }

  // ---------- room shell ----------
  _shell() {
    // floor: real planks, instanced, each one a slightly different shade
    const plankW = 0.24;
    const depth = ROOM.front - ROOM.back;
    const count = Math.ceil((ROOM.halfX * 2) / plankW);
    const plank = new THREE.BoxGeometry(plankW - 0.012, 0.03, depth);
    this._geometries.add(plank);
    const floor = new THREE.InstancedMesh(plank, this._mat(0xffffff, { vertexColors: false }), count);
    const matrix = new THREE.Matrix4();
    const color = new THREE.Color();
    for (let i = 0; i < count; i++) {
      matrix.makeTranslation(-ROOM.halfX + plankW * (i + 0.5), -0.015, (ROOM.front + ROOM.back) / 2);
      floor.setMatrixAt(i, matrix);
      color.setHex(COLORS.floor).offsetHSL(0, 0, (this._rnd() - 0.5) * 0.09);
      floor.setColorAt(i, color);
    }
    floor.instanceMatrix.needsUpdate = true;
    floor.frustumCulled = false;  // instances spread far past the geometry's own bounding sphere
    this.group.add(floor);

    const midZ = (ROOM.front + ROOM.back) / 2;
    const wallDepth = ROOM.front - ROOM.back;
    this._block(ROOM.halfX * 2 + 0.3, ROOM.height, 0.12, COLORS.wall, 0, ROOM.height / 2, ROOM.back, { side: THREE.DoubleSide });
    for (const x of [-ROOM.halfX - 0.06, ROOM.halfX + 0.06]) {
      this._block(0.12, ROOM.height, wallDepth, COLORS.wall, x, ROOM.height / 2, midZ, { side: THREE.DoubleSide });
    }
    this._block(ROOM.halfX * 2 + 0.6, 0.1, wallDepth + 0.4, COLORS.ceiling, 0, ROOM.height + 0.05, midZ);
    // wooden dado rail across the back wall, the only detail visible behind her bust
    this._block(ROOM.halfX * 2, 0.07, 0.05, COLORS.frame, 0, 0.95, ROOM.back + 0.08);
    this._block(ROOM.halfX * 2, 0.03, 0.09, 0xb9ab92, 0, 0.055, ROOM.back + 0.08);
  }

  _blackboard() {
    const z = ROOM.back + 0.18;
    this._block(4.8, 1.86, 0.07, COLORS.frame, 0, 2.0, z - 0.03);
    this._block(4.6, 1.66, 0.02, COLORS.board, 0, 2.0, z + 0.02);
    this._block(4.7, 0.06, 0.2, COLORS.frame, 0, 1.13, z + 0.08);          // chalk tray
    this._block(0.08, 0.03, 0.03, 0xf2efe6, 0.7, 1.18, z + 0.09);          // chalk
    this._block(0.06, 0.03, 0.03, 0xe8dfc8, -1.4, 1.18, z + 0.09);
    this._block(0.16, 0.06, 0.04, 0x2f6a8c, -2.0, 1.18, z + 0.09);         // board eraser
    // a few faint chalk lines so it does not read as a flat slab
    for (const [w, y, x] of [[1.6, 2.28, -1.3], [1.1, 2.06, -1.55], [0.8, 1.84, 1.4]]) {
      this._block(w, 0.015, 0.005, 0x8fae9c, x, y, z + 0.035);
    }
  }

  _windows() {
    const x = -ROOM.halfX - 0.02;
    for (const z of [-3.4, -0.9, 1.6]) {
      const w = 1.7, h = 1.65, sill = 1.0;
      const y = sill + h / 2;
      // dusk light itself: unlit so it stays bright whatever the room lighting does
      const glow = new THREE.PlaneGeometry(w, h);
      const pane = this._mesh(glow, new THREE.MeshBasicMaterial({ color: COLORS.glow, side: THREE.DoubleSide }), x + 0.03, y, z);
      pane.rotation.y = Math.PI / 2;
      this._materials.set(`glow|${z}`, pane.material);

      // frame
      this._block(0.08, 0.08, w + 0.16, COLORS.frame, x + 0.05, sill, z);
      this._block(0.08, 0.08, w + 0.16, COLORS.frame, x + 0.05, sill + h, z);
      for (const side of [-1, 1]) this._block(0.08, h + 0.16, 0.08, COLORS.frame, x + 0.05, y, z + side * (w / 2 + 0.04));
      // lattice: two mullions and one transom
      for (const dz of [-w / 6, w / 6]) this._block(0.05, h, 0.05, COLORS.frame, x + 0.05, y, z + dz);
      this._block(0.05, 0.05, w, COLORS.frame, x + 0.05, sill + h * 0.62, z);

      // curtains, hung at the top edges, swaying into the room
      for (const side of [-1, 1]) {
        const pivot = new THREE.Group();
        pivot.position.set(x + 0.16, sill + h + 0.2, z + side * (w / 2 + 0.02));
        const cloth = new THREE.PlaneGeometry(0.46, h + 0.3, 6, 1);
        const mesh = new THREE.Mesh(cloth, this._mat(COLORS.curtain, { side: THREE.DoubleSide }));
        this._geometries.add(cloth);
        this._materials.set(`curtain|${z}|${side}`, mesh.material);
        mesh.rotation.y = Math.PI / 2;
        mesh.position.y = -(h + 0.3) / 2;
        pivot.add(mesh);
        this.group.add(pivot);
        this.sway.push({ node: pivot, amp: 0.02 + this._rnd() * 0.015, speed: 0.5 + this._rnd() * 0.4,
                         phase: this._rnd() * Math.PI * 2, tilt: false });
      }
    }
  }

  _desks() {
    // one desk + its chair, merged; instanced in two rows facing the blackboard
    const rnd = this._rnd;
    const parts = [];
    const add = (w, h, d, x, y, z, hex, jitter = 0.08) => parts.push(tinted(box(w, h, d, x, y, z), hex, jitter, rnd));
    add(1.15, 0.05, 0.55, 0, 0.72, 0, COLORS.wood);
    add(1.0, 0.3, 0.03, 0, 0.52, 0.24, COLORS.wood, 0.05);                 // modesty panel
    for (const sx of [-0.5, 0.5]) for (const sz of [-0.21, 0.21]) add(0.045, 0.72, 0.045, sx, 0.36, sz, COLORS.metal, 0.05);
    add(0.44, 0.05, 0.42, 0, 0.44, 0.75, COLORS.wood);                     // seat
    add(0.44, 0.42, 0.05, 0, 0.66, 0.96, COLORS.wood);                     // back
    for (const sx of [-0.19, 0.19]) for (const sz of [0.57, 0.93]) add(0.035, 0.44, 0.035, sx, 0.22, sz, COLORS.metal, 0.05);

    const geometry = mergeGeometries(parts, false);
    parts.forEach((part) => part.dispose());
    this._geometries.add(geometry);
    const cols = [-2.6, 0, 2.6];
    const rows = [-3.9, -2.2];
    const desks = new THREE.InstancedMesh(geometry, this._mat(0xffffff, { vertexColors: true }), cols.length * rows.length);
    const matrix = new THREE.Matrix4();
    let i = 0;
    for (const z of rows) for (const x of cols) {
      matrix.makeTranslation(x, 0, z);
      desks.setMatrixAt(i++, matrix);
    }
    desks.instanceMatrix.needsUpdate = true;
    desks.frustumCulled = false;
    this.group.add(desks);
  }

  _lamps() {
    // A single pendant, near the blackboard: the one that hung in the middle of the room sat right in
    // front of the camera and cluttered the bust framing.
    for (const z of [-3.2]) {
      const pivot = new THREE.Group();
      pivot.position.set(0, ROOM.height, z);
      const cord = new THREE.CylinderGeometry(0.008, 0.008, 0.55, 6);
      const shade = new THREE.CylinderGeometry(0.05, 0.3, 0.24, 16, 1, true);
      const bulb = new THREE.SphereGeometry(0.08, 12, 8);
      this._geometries.add(cord).add(shade).add(bulb);

      const line = new THREE.Mesh(cord, this._mat(0x2b2b2b));
      line.position.y = -0.275;
      const hood = new THREE.Mesh(shade, this._mat(COLORS.lampShade, { side: THREE.DoubleSide }));
      hood.position.y = -0.66;
      const glow = new THREE.Mesh(bulb, new THREE.MeshBasicMaterial({ color: COLORS.bulb }));
      this._materials.set(`bulb|${z}`, glow.material);
      glow.position.y = -0.75;
      pivot.add(line, hood, glow);

      const light = new THREE.PointLight(0xffcf9a, 7, 9, 2);
      light.position.y = -0.8;
      pivot.add(light);
      this.flicker.push({ light, base: light.intensity, phase: this._rnd() * 10 });

      this.group.add(pivot);
      this.sway.push({ node: pivot, amp: 0.012, speed: 0.7, phase: this._rnd() * Math.PI * 2, tilt: true });
    }
  }

  _dust() {
    const count = 240;
    const positions = new Float32Array(count * 3);
    this._dustSpeed = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      positions[i * 3] = (this._rnd() - 0.5) * ROOM.halfX * 1.9;
      positions[i * 3 + 1] = 0.2 + this._rnd() * (ROOM.height - 0.4);
      positions[i * 3 + 2] = ROOM.back + 0.3 + this._rnd() * (ROOM.front - ROOM.back - 0.6);
      this._dustSpeed[i] = 0.015 + this._rnd() * 0.035;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    this._geometries.add(geometry);
    const material = new THREE.PointsMaterial({ color: COLORS.dust, size: 0.022, transparent: true, opacity: 0.5,
                                                depthWrite: false, sizeAttenuation: true });
    this._materials.set('dust', material);
    this.dust = new THREE.Points(geometry, material);
    this.dust.frustumCulled = false;
    this.group.add(this.dust);
  }

  _lighting() {
    // low sun through the left windows, warm and raking
    const sun = new THREE.DirectionalLight(0xffb887, 2.1);
    sun.position.set(-6, 3.4, 1.2);
    sun.target.position.set(1.5, 0.8, -0.6);
    this.group.add(sun, sun.target);
    // soft bounce so the corners are not pitch black
    const fill = new THREE.HemisphereLight(0xb7a6d6, 0x2a2018, 0.35);
    this.group.add(fill);

    this.scene.background = new THREE.Color(DUSK);
    this.scene.fog = new THREE.Fog(DUSK, 5, 17);
  }

  /** Animate the slow life of the room: curtains and lamps swaying, dust drifting, lamps flickering. */
  update(dt) {
    this.t += dt;
    const t = this.t;
    for (const s of this.sway) {
      s.node.rotation.z = Math.sin(t * s.speed + s.phase) * s.amp;
      if (s.tilt) s.node.rotation.x = Math.cos(t * s.speed * 0.7 + s.phase) * s.amp * 0.6;
    }
    for (const f of this.flicker) f.light.intensity = f.base * (1 + Math.sin(t * 17 + f.phase) * 0.03);
    if (this.dust) {
      const attr = this.dust.geometry.getAttribute('position');
      for (let i = 0; i < attr.count; i++) {
        const speed = this._dustSpeed[i];
        const y = attr.getY(i) + speed * dt * 0.6;
        const x = attr.getX(i) + Math.sin(t * 0.35 + i) * speed * dt * 0.8;
        attr.setXYZ(i, x, y > ROOM.height - 0.15 ? 0.2 : y, attr.getZ(i));
      }
      attr.needsUpdate = true;
    }
  }

  /** Remove the room and free its GPU memory; the page can then fall back to a bare stage. */
  dispose() {
    this.scene.remove(this.group);
    for (const geometry of this._geometries) geometry.dispose();
    for (const material of this._materials.values()) material.dispose();
    this._geometries.clear();
    this._materials.clear();
    this.scene.background = this._previous.background;
    this.scene.fog = this._previous.fog;
  }
}
