// ClipLibrary: loads motion clips (GLB) and retargets them onto the loaded VRM's normalized humanoid.
// Adapted from OpenSakura (MIT, (c) 2026 MekaHime Inc.), web/js/avatar.js.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RIGS, VRM_PARENT, KEEP_POSTURE, XZ_RANGE } from './rigs.js';

// Hand the main thread back for a moment (a message task: not throttled like timers in background tabs)
const yieldTask = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });

export class ClipLibrary {
  constructor({ clipsBase = '/static/clips/', onStatus = () => {} } = {}) {
    this.clipsBase = clipsBase;
    this.onStatus = onStatus;
    this.vrm = null;
    this.manifest = null;
    this.clipCache = new Map();
    this._srcCache = new Map();
    this._restInfo = null;
  }

  _vrmNode(name) { return this.vrm?.humanoid?.getNormalizedBoneNode(name) || null; }
  _vrmRaw(name) { return this.vrm?.humanoid?.getRawBoneNode(name) || null; }

  _computeVrmRest() {
    const pos = {};
    const v = new THREE.Vector3();
    for (const vrmName of Object.keys(VRM_PARENT)) {
      const n = this._vrmRaw(vrmName);
      if (n) { n.getWorldPosition(v); pos[vrmName] = v.clone(); }
    }
    return { pos, hipsLocal: this._vrmNode('hips').position.clone() };
  }

  // nearest VRM ancestor that exists in this model and is driven by the clip
  _drivenAncestor(vrmName, driven) {
    let p = VRM_PARENT[vrmName];
    while (p && !(driven.has(p))) p = VRM_PARENT[p];
    return p || null;
  }

  /** Download + parse one clip file (GLB/glTF via GLTFLoader).
   *  Cached per file, so several manifest names can share one file; the rest pose is remembered so
   *  each bake starts from it. */
  _loadSource(file, onProgress) {
    if (this._srcCache.has(file)) return this._srcCache.get(file);
    const url = this.clipsBase + encodeURIComponent(file);
    const prog = (e) => { if (onProgress && e.total) onProgress(e.loaded / e.total); };
    const pending = (async () => {
      let root, clips, format;
      {
        const gltf = await new GLTFLoader().loadAsync(url, prog);
        root = gltf.scene; clips = gltf.animations || []; format = 'glb';
      }
      const rest = [];
      root.traverse((o) => rest.push([o, o.position.clone(), o.quaternion.clone(), o.scale.clone()]));
      return { root, clips, format, rest };
    })();
    this._srcCache.set(file, pending);
    pending.catch(() => this._srcCache.delete(file));
    return pending;
  }

  /** Which RIGS entry describes this skeleton (by bone names). */
  _detectRig(root, forced) {
    const names = new Set();
    root.traverse((o) => { if (o.name) names.add(o.name); });
    const has = (rig, key) => [...names].some((n) => rig.norm(n) === key);
    for (const rig of RIGS) {
      if (forced && rig.id !== forced) continue;
      if ([rig.hips, rig.head, ...rig.thighs].every((k) => has(rig, k))) return rig;
    }
    const sample = [...names].slice(0, 8).join(', ');
    throw new Error(`unknown skeleton (bones: ${sample}…) — add a rig to RIGS in avatar/rigs.js`);
  }

  /** Retarget one source clip onto this VRM -> AnimationClip on the VRM's normalized bones. Runs in slices
   *  of a few ms so the avatar keeps moving while a clip bakes on its first play; bakes are queued one at a
   *  time because clips that share a file share its skeleton (see _queueBake). */
  async _bake(src, srcClip, { rig: forcedRig, speed = 1, lockXZ = false } = {}) {
    const vrm = this.vrm;
    const isVRM0 = vrm.meta?.metaVersion === '0';
    const srcRoot = src.root;
    for (const [o, p0, q0, s0] of src.rest) { o.position.copy(p0); o.quaternion.copy(q0); o.scale.copy(s0); }
    const rig = this._detectRig(srcRoot, forcedRig);
    const MAP = rig.map;
    // Wrap the source so its whole world frame can be rotated (Z-up FBX, Blender's -90° X root, ...).
    const wrapper = new THREE.Group();
    wrapper.add(srcRoot);
    wrapper.updateMatrixWorld(true);
    const bones = {};
    srcRoot.traverse((o) => { const k = rig.norm(o.name || ''); if (MAP[k] && !bones[k]) bones[k] = o; });
    // nearest mapped ancestor of every mapped bone, from the loaded hierarchy (no per-rig parent table)
    const srcParent = {};
    for (const [k, o] of Object.entries(bones)) {
      let p = o.parent;
      while (p && p !== wrapper && bones[rig.norm(p.name || '')] !== p) p = p.parent;
      srcParent[k] = p && p !== wrapper ? rig.norm(p.name) : null;
    }
    // Frame alignment from the skeleton itself: up = hips→head, right = left thigh→right thigh.
    // Build the same basis on the VRM rest pose and rotate the source frame onto it.
    const basisQuat = (P, hips, head, thighL, thighR) => {
      const up = P[head].clone().sub(P[hips]).normalize();
      const right = P[thighR].clone().sub(P[thighL]).normalize();
      const fwd = new THREE.Vector3().crossVectors(right, up).normalize();
      right.crossVectors(up, fwd).normalize();
      return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(right, up, fwd));
    };
    const frameKeys = [rig.hips, rig.head, ...rig.thighs];
    const P0 = {};
    for (const n of frameKeys) P0[n] = bones[n].getWorldPosition(new THREE.Vector3());
    const qF = basisQuat(P0, ...frameKeys);
    const qV = basisQuat(this._restInfo.pos, 'hips', 'head', 'leftUpperLeg', 'rightUpperLeg');
    // Snap to the nearest axis-aligned rotation: rigs differ by up-axis / facing conventions (multiples
    // of 90°); a few degrees on top of that is only spine posture, not a different frame.
    const m = new THREE.Matrix4().makeRotationFromQuaternion(qV.clone().multiply(qF.clone().invert()));
    const ax = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()];
    m.extractBasis(ax[0], ax[1], ax[2]);
    const snap = (v, not) => {
      const c = [v.x, v.y, v.z].map((x, i) => (i === not ? 0 : x));
      const i = c.reduce((b, x, j) => (Math.abs(x) > Math.abs(c[b]) ? j : b), 0);
      return [new THREE.Vector3().setComponent(i, Math.sign(c[i]) || 1), i];
    };
    const [sx, ix] = snap(ax[0], -1), [sy] = snap(ax[1], ix);
    const sz = new THREE.Vector3().crossVectors(sx, sy);
    wrapper.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(sx, sy, sz));
    wrapper.updateMatrixWorld(true);
    const frameEuler = new THREE.Euler().setFromQuaternion(wrapper.quaternion);

    // rest pose capture (the source is loaded in its rest / bind pose), now in the VRM's frame
    const restQ = {}, restP = {};
    const q = new THREE.Quaternion(), p = new THREE.Vector3();
    for (const [name, b] of Object.entries(bones)) {
      b.getWorldQuaternion(q); restQ[name] = q.clone();
      b.getWorldPosition(p); restP[name] = p.clone();
    }
    // per-bone correction: rotate VRM rest direction onto the source rest direction
    const corr = {};
    const dF = new THREE.Vector3(), dV = new THREE.Vector3();
    const computeCorr = (name) => {
      if (name in corr) return corr[name];
      const [vrmName, fChild, vChild] = MAP[name];
      if (KEEP_POSTURE.has(vrmName)) { corr[name] = new THREE.Quaternion(); return corr[name]; }
      let c = null;
      if (fChild && vChild && bones[fChild] && restP[name] && this._restInfo.pos[vrmName] && this._restInfo.pos[vChild]) {
        bones[fChild].getWorldPosition(dF); dF.sub(restP[name]);
        dV.copy(this._restInfo.pos[vChild]).sub(this._restInfo.pos[vrmName]);
        if (dF.lengthSq() > 1e-8 && dV.lengthSq() > 1e-8) c = new THREE.Quaternion().setFromUnitVectors(dV.normalize(), dF.normalize());
      }
      if (!c) { const par = srcParent[name]; c = par ? computeCorr(par).clone() : new THREE.Quaternion(); }
      corr[name] = c;
      return c;
    };
    const driven = new Set();
    const pairs = [];
    for (const name of Object.keys(bones)) {
      const vrmName = MAP[name][0];
      if (this._vrmNode(vrmName) && !driven.has(vrmName)) { driven.add(vrmName); pairs.push([name, vrmName]); computeCorr(name); }
    }
    // diagnostics: rest arm angle (90° = T-pose, ~45° = A-pose) and hips height of the source
    const keyOf = (vrmName) => Object.keys(MAP).find((k) => MAP[k][0] === vrmName);
    const ua = keyOf('leftUpperArm'), hd = keyOf('leftHand');
    let restArmDeg = null;
    if (restP[ua] && restP[hd]) {
      const d = restP[hd].clone().sub(restP[ua]).normalize();
      restArmDeg = Math.round(THREE.MathUtils.radToDeg(Math.acos(Math.max(-1, Math.min(1, -d.y)))));
    }
    const fps = 30;
    // enough samples to reach the clip's end (t is clamped below), also when the float duration sits a hair
    // under a whole number of frames — a missing last key would hold the pose at every loop seam
    const n = Math.max(2, Math.ceil(srcClip.duration * fps - 1e-4) + 1);
    const times = new Float32Array(n);
    const rot = {}; for (const [, v] of pairs) rot[v] = new Float32Array(n * 4);
    const hipsPos = new Float32Array(n * 3);
    const mixer = new THREE.AnimationMixer(srcRoot);
    const action = mixer.clipAction(srcClip);
    action.setLoop(THREE.LoopOnce, 1); action.clampWhenFinished = true;     // t = duration is the last frame, not the first
    action.play();
    const target = {}, inv = new THREE.Quaternion(), delta = new THREE.Quaternion(), local = new THREE.Quaternion();
    const hipsRest = this._restInfo.hipsLocal;
    // unit scale from leg length (axis-independent): FBX is often centimetres, GLB metres, VRM metres
    const segLen = (P, a, b) => (P[a] && P[b]) ? P[a].distanceTo(P[b]) : 0;
    const [legA, legB, legC] = rig.leg;
    const legF = segLen(restP, legA, legB) + segLen(restP, legB, legC);
    const legV = segLen(this._restInfo.pos, 'leftUpperLeg', 'leftLowerLeg') + segLen(this._restInfo.pos, 'leftLowerLeg', 'leftFoot');
    let scale = (legF > 1e-6 && legV > 1e-6) ? legV / legF : NaN;
    if (!(scale > 0.001 && scale < 10)) {
      console.warn(`[avatar] ${srcClip.name}: implausible unit scale ${scale}; assuming centimetres`);
      scale = legV > 1e-6 ? legV / 85 : 0.01;
    }
    const hipsBone = bones[rig.hips];
    const hips0 = new THREE.Vector3();
    // Ground contact: per frame, her lowest foot point (VRM leg FK with the baked rotations) is matched
    // to the source's lowest foot point (scaled), so different leg proportions never sink or float her.
    const R0 = this._restInfo.pos;
    const srcFeet = Object.keys(bones).filter((k) => /^(left|right)(Foot|Toes)$/.test(MAP[k][0]));
    const chains = ['left', 'right'].map((S) => ['hips', `${S}UpperLeg`, `${S}LowerLeg`, `${S}Foot`, `${S}Toes`].filter((b) => R0[b]));
    const rotOf = (b) => (driven.has(b) ? b : this._drivenAncestor(b, driven));
    const ground = srcFeet.length && chains.every((c) => c.length >= 4 && c.slice(0, 4).every((b) => rotOf(b))) ? { chains } : null;
    const gp = new THREE.Vector3(), go = new THREE.Vector3();
    let maxDy = 0, maxDxz = 0, gMin = 0, gMax = 0;
    let slice = performance.now();
    for (let i = 0; i < n; i++) {
      if (performance.now() - slice > 6) { await yieldTask(); slice = performance.now(); }
      const t = Math.min(srcClip.duration, i / fps);
      times[i] = t / speed;
      mixer.setTime(t);
      wrapper.updateMatrixWorld(true);
      for (const [name, vrmName] of pairs) {
        bones[name].getWorldQuaternion(q);
        inv.copy(restQ[name]).invert();
        delta.copy(q).multiply(inv);                      // world delta from rest
        target[vrmName] = (target[vrmName] || new THREE.Quaternion()).copy(delta).multiply(corr[name]);
      }
      for (const [, vrmName] of pairs) {
        const par = this._drivenAncestor(vrmName, driven);
        if (par) local.copy(target[par]).invert().multiply(target[vrmName]); else local.copy(target[vrmName]);
        const o = i * 4;
        rot[vrmName][o] = isVRM0 ? -local.x : local.x; rot[vrmName][o + 1] = local.y;
        rot[vrmName][o + 2] = isVRM0 ? -local.z : local.z; rot[vrmName][o + 3] = local.w;
      }
      // hips: full vertical motion; horizontal sway relative to the first frame, clamped so she stays on her spot
      hipsBone.getWorldPosition(p); p.sub(restP[rig.hips]).multiplyScalar(scale);
      if (i === 0) hips0.copy(p);
      const sx = p.x - hips0.x, sz = p.z - hips0.z;
      maxDxz = Math.max(maxDxz, Math.hypot(sx, sz));
      let dy = Math.max(-1.0, Math.min(1.0, p.y));
      const dx = lockXZ ? 0 : Math.max(-XZ_RANGE, Math.min(XZ_RANGE, sx));
      const dz = lockXZ ? 0 : Math.max(-XZ_RANGE, Math.min(XZ_RANGE, sz));
      if (ground) {
        let srcLow = Infinity, vrmLow = Infinity;
        for (const k of srcFeet) { bones[k].getWorldPosition(gp); srcLow = Math.min(srcLow, (gp.y - restP[k].y) * scale); }
        for (const chain of ground.chains) {
          gp.set(R0.hips.x + dx, R0.hips.y + dy, R0.hips.z + dz);
          for (let j = 1; j < chain.length; j++) {
            gp.add(go.copy(R0[chain[j]]).sub(R0[chain[j - 1]]).applyQuaternion(target[rotOf(chain[j - 1])]));
            if (j >= 3) vrmLow = Math.min(vrmLow, gp.y - R0[chain[j]].y);
          }
        }
        const g = Math.max(-0.15, Math.min(0.15, srcLow - vrmLow));
        if (Number.isFinite(g)) { dy = Math.max(-1.0, Math.min(1.0, dy + g)); gMin = Math.min(gMin, g); gMax = Math.max(gMax, g); }
      }
      maxDy = Math.max(maxDy, Math.abs(dy));
      hipsPos[i * 3] = hipsRest.x + (isVRM0 ? -dx : dx);
      hipsPos[i * 3 + 1] = hipsRest.y + dy;
      hipsPos[i * 3 + 2] = hipsRest.z + (isVRM0 ? -dz : dz);
    }
    action.stop(); mixer.uncacheRoot(srcRoot);
    wrapper.remove(srcRoot);
    for (const [o, p0, q0, s0] of src.rest) { o.position.copy(p0); o.quaternion.copy(q0); o.scale.copy(s0); }
    const deg = (r) => Math.round(THREE.MathUtils.radToDeg(r));
    this.lastBake = { clip: srcClip.name, rig: rig.id, format: src.format, scale: +scale.toFixed(5), legF: +legF.toFixed(3),
                      legV: +legV.toFixed(3), maxDy: +maxDy.toFixed(3), maxDxz: +maxDxz.toFixed(3), lockXZ,
                      bones: pairs.length, frames: n, frameFix: [deg(frameEuler.x), deg(frameEuler.y), deg(frameEuler.z)],
                      restArmDeg, srcHipsY: +(restP[rig.hips].y).toFixed(3),
                      groundFix: ground ? [+gMin.toFixed(3), +gMax.toFixed(3)] : null };
    this.onStatus(`baked ${srcClip.name} (${rig.id} ${src.format}): frame fix ${this.lastBake.frameFix.join('/')}° · scale ${this.lastBake.scale} · hips Δy≤${this.lastBake.maxDy}m sway≤${this.lastBake.maxDxz}m${lockXZ ? ' (xz locked)' : ''} · ${pairs.length} bones`);
    const tracks = [];
    for (const [, vrmName] of pairs) {
      tracks.push(new THREE.QuaternionKeyframeTrack(`${this._vrmNode(vrmName).name}.quaternion`, times, rot[vrmName]));
    }
    tracks.push(new THREE.VectorKeyframeTrack(`${this._vrmNode('hips').name}.position`, times, hipsPos));
    return new THREE.AnimationClip(srcClip.name || 'clip', srcClip.duration / speed, tracks);
  }

  /** clips.json entry for a name. Entry fields: file (GLB/glTF/FBX in assets/clips), name, clip (animation
   *  name inside a multi-clip file, matched exactly or by the part after a '|', e.g. "Idle_Loop" for
   *  FBXLoader's "Armature|Idle_Loop"; default: the first), rig (force a RIGS id), speed (playback rate > 0),
   *  once (play once, hold the last pose), fade (cross-fade seconds, 0 = cut), lockXZ (no horizontal hip
   *  sway: set it for clips with real root motion such as walk cycles; otherwise the hips keep the clip's
   *  own sway, clamped to ±XZ_RANGE of its first frame), hidden (keep out of clipNames). */
  _entry(name) {
    const clips = this.manifest?.clips || [];
    return clips.find((c) => c.name === name) || clips.find((c) => c.name.includes(name) || c.file.includes(name)) || null;
  }

  /** Run bakes one after another (a bake poses the shared source skeleton across several slices). */
  _queueBake(fn) {
    const run = (this._bakeTail || Promise.resolve()).then(fn, fn);
    this._bakeTail = run.catch(() => {});
    return run;
  }

  /** Baked clip for a manifest name (downloads + retargets on first use). */
  async getClip(name, onProgress) {
    if (!this.vrm || !this.manifest) return null;
    const entry = this._entry(name);
    if (!entry) return null;
    if (this.clipCache.has(entry.name)) return this.clipCache.get(entry.name);
    const pending = (async () => {
      const src = await this._loadSource(entry.file, onProgress);
      const srcClip = entry.clip
        ? src.clips.find((c) => c.name === entry.clip) || src.clips.find((c) => c.name.split('|').pop() === entry.clip)
        : src.clips[0];
      if (!srcClip) throw new Error(`no animation ${entry.clip ? `"${entry.clip}" ` : ''}in ${entry.file}`);
      let speed = Number(entry.speed ?? 1);
      if (!(speed > 0)) { console.warn(`[avatar] ${entry.name}: speed must be > 0, using 1`); speed = 1; }
      const clip = await this._queueBake(() => this._bake(src, srcClip, { rig: entry.rig, speed, lockXZ: !!entry.lockXZ }));
      clip.name = entry.name;
      return clip;
    })();
    this.clipCache.set(entry.name, pending);
    try { const c = await pending; this.clipCache.set(entry.name, c); return c; }
    catch (e) { this.clipCache.delete(entry.name); this.onStatus(`clip ${entry.name} failed: ${e.message}`); return null; }
  }
}
