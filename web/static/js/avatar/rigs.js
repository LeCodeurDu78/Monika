// Source skeletons (UE5 mannequin, Mixamo, KayKit) -> VRM humanoid bone tables used to retarget motion clips.
// Adapted from OpenSakura (MIT, (c) 2026 MekaHime Inc.), web/js/avatar.js.
// ---------- source rigs ----------
// A rig maps normalised source bone names -> [VRM humanoid bone, source child for the rest direction,
// VRM child for the rest direction]. `norm` turns a loaded node name into a map key (loaders strip
// '.', ':' and '/' from names). `hips`/`head`/`thighs` frame the skeleton, `leg` (hip, knee, ankle)
// sets the unit scale. The first rig whose frame bones all exist wins; clips.json can force one ("rig").
const FINGERS = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
const fingerSegs = (f) => (f === 'Thumb' ? ['Metacarpal', 'Proximal', 'Distal'] : ['Proximal', 'Intermediate', 'Distal']);
const SIDES = [['l', 'left'], ['r', 'right']];
// rows: [source key, VRM bone, source child, VRM child]. In source keys '<s>' is l|r and '<side>' is
// left|right; a VRM name starting with '<Side>' gets the side prefix (leftUpperArm).
// fingerKey(s, side, finger, 1..3) names the source finger joints (null: the rig has no fingers).
function rigMap(rows, fingerKey) {
  const map = {};
  for (const [src, vrm, sc, vc] of rows) {
    for (const [s, side] of (src.includes('<') ? SIDES : [['', '']])) {
      const k = (x) => x && x.replace('<s>', s).replace('<side>', side);
      const v = (x) => x && (x.startsWith('<Side>') ? side + x.slice(6) : x);
      map[k(src)] = [v(vrm), k(sc), v(vc)];
    }
  }
  if (fingerKey) for (const [s, side] of SIDES) for (const f of FINGERS) {
    const segs = fingerSegs(f);
    for (let i = 0; i < 3; i++) {
      const next = i < 2 ? fingerKey(s, side, f, i + 2) : null;
      map[fingerKey(s, side, f, i + 1)] = [`${side}${f}${segs[i]}`, next, next ? `${side}${f}${segs[i + 1]}` : null];
    }
  }
  return map;
}
// UE5-mannequin names: the Quaternius Universal Animation Library clips shipped here, Unreal exports.
const UE5 = rigMap([
  ['pelvis', 'hips', 'spine_01', 'spine'],
  ['spine_01', 'spine', 'spine_02', 'chest'],
  ['spine_02', 'chest', 'spine_03', 'upperChest'],
  ['spine_03', 'upperChest', 'neck_01', 'neck'],
  ['neck_01', 'neck', 'head', 'head'],
  ['head', 'head', null, null],
  ['clavicle_<s>', '<Side>Shoulder', 'upperarm_<s>', '<Side>UpperArm'],
  ['upperarm_<s>', '<Side>UpperArm', 'lowerarm_<s>', '<Side>LowerArm'],
  ['lowerarm_<s>', '<Side>LowerArm', 'hand_<s>', '<Side>Hand'],
  ['hand_<s>', '<Side>Hand', 'middle_01_<s>', '<Side>MiddleProximal'],
  ['thigh_<s>', '<Side>UpperLeg', 'calf_<s>', '<Side>LowerLeg'],
  ['calf_<s>', '<Side>LowerLeg', 'foot_<s>', '<Side>Foot'],
  ['foot_<s>', '<Side>Foot', 'ball_<s>', '<Side>Toes'],
  ['ball_<s>', '<Side>Toes', null, null],
], (s, side, f, i) => `${f === 'Little' ? 'pinky' : f.toLowerCase()}_0${i}_${s}`);
// Mixamo names ("mixamorig:LeftArm" etc.).
const MIXAMO = rigMap([
  ['hips', 'hips', 'spine', 'spine'],
  ['spine', 'spine', 'spine1', 'chest'],
  ['spine1', 'chest', 'spine2', 'upperChest'],
  ['spine2', 'upperChest', 'neck', 'neck'],
  ['neck', 'neck', 'head', 'head'],
  ['head', 'head', null, null],
  ['<side>shoulder', '<Side>Shoulder', '<side>arm', '<Side>UpperArm'],
  ['<side>arm', '<Side>UpperArm', '<side>forearm', '<Side>LowerArm'],
  ['<side>forearm', '<Side>LowerArm', '<side>hand', '<Side>Hand'],
  ['<side>hand', '<Side>Hand', '<side>handmiddle1', '<Side>MiddleProximal'],
  ['<side>upleg', '<Side>UpperLeg', '<side>leg', '<Side>LowerLeg'],
  ['<side>leg', '<Side>LowerLeg', '<side>foot', '<Side>Foot'],
  ['<side>foot', '<Side>Foot', '<side>toebase', '<Side>Toes'],
  ['<side>toebase', '<Side>Toes', null, null],
], (s, side, f, i) => `${side}hand${f === 'Little' ? 'pinky' : f.toLowerCase()}${i}`);
// KayKit character rigs (upperarm.l, wrist.l, ...): no clavicles, neck or finger joints.
const KAYKIT = rigMap([
  ['hips', 'hips', 'spine', 'spine'],
  ['spine', 'spine', 'chest', 'chest'],
  ['chest', 'chest', 'head', 'upperChest'],
  ['head', 'head', null, null],
  ['upperarm<s>', '<Side>UpperArm', 'lowerarm<s>', '<Side>LowerArm'],
  ['lowerarm<s>', '<Side>LowerArm', 'wrist<s>', '<Side>Hand'],
  ['wrist<s>', '<Side>Hand', 'hand<s>', '<Side>MiddleProximal'],
  ['upperleg<s>', '<Side>UpperLeg', 'lowerleg<s>', '<Side>LowerLeg'],
  ['lowerleg<s>', '<Side>LowerLeg', 'foot<s>', '<Side>Foot'],
  ['foot<s>', '<Side>Foot', 'toes<s>', '<Side>Toes'],
  ['toes<s>', '<Side>Toes', null, null],
], null);
export const RIGS = [
  { id: 'ue5', map: UE5, norm: (n) => n.toLowerCase(),
    hips: 'pelvis', head: 'head', thighs: ['thigh_l', 'thigh_r'], leg: ['thigh_l', 'calf_l', 'foot_l'] },
  { id: 'mixamo', map: MIXAMO, norm: (n) => n.replace(/^mixamorig\d*[:_]?/i, '').toLowerCase(),
    hips: 'hips', head: 'head', thighs: ['leftupleg', 'rightupleg'], leg: ['leftupleg', 'leftleg', 'leftfoot'] },
  { id: 'kaykit', map: KAYKIT, norm: (n) => n.replace(/[.:\s]/g, '').toLowerCase(),
    hips: 'hips', head: 'head', thighs: ['upperlegl', 'upperlegr'], leg: ['upperlegl', 'lowerlegl', 'footl'] },
];
// VRM humanoid hierarchy (normalized rig), used to find the nearest driven ancestor of a bone.
export const VRM_PARENT = {
  hips: null, spine: 'hips', chest: 'spine', upperChest: 'chest', neck: 'upperChest', head: 'neck',
  leftShoulder: 'upperChest', leftUpperArm: 'leftShoulder', leftLowerArm: 'leftUpperArm', leftHand: 'leftLowerArm',
  rightShoulder: 'upperChest', rightUpperArm: 'rightShoulder', rightLowerArm: 'rightUpperArm', rightHand: 'rightLowerArm',
  leftUpperLeg: 'hips', leftLowerLeg: 'leftUpperLeg', leftFoot: 'leftLowerLeg', leftToes: 'leftFoot',
  rightUpperLeg: 'hips', rightLowerLeg: 'rightUpperLeg', rightFoot: 'rightLowerLeg', rightToes: 'rightFoot',
};
for (const S of ['left', 'right']) for (const f of FINGERS) {
  const segs = fingerSegs(f);
  segs.forEach((seg, i) => { VRM_PARENT[`${S}${f}${seg}`] = i === 0 ? `${S}Hand` : `${S}${f}${segs[i - 1]}`; });
}
// Torso and shoulders keep the VRM's own modelled posture (source motion is applied on top of it);
// limbs take the source's rest directions (T-pose vs A-pose arms, leg splay).
export const KEEP_POSTURE = new Set(['hips', 'spine', 'chest', 'upperChest', 'neck', 'head', 'leftShoulder', 'rightShoulder']);
export const XZ_RANGE = 0.3;     // in-place sway the hips may keep (m); anything beyond is clamped so she never walks away
