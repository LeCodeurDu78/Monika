"""Retarget a Mixamo animation (mixamorig:* skeleton) onto a Character Creator (CC_Base_*) skeleton
contained in a .glb, then re-inject the animation into the original .glb.

The script:
  0. looks for an .fbx next to the script and converts it to glTF (FBX2glTF)
  1. The Mixamo FBX rest pose is a T-pose -> reference.
  2. The CC model's rest pose is an A-pose -> automatic T-pose of the target rig
     (minimal-arc rotation, top-down).
  3. Constant per-bone offset: C = Ws_T^-1 * Wt_T
  4. For each frame: Wt(t) = Ws(t) * C, then conversion to local rotation.
  5. Root translation: world delta * (target hip height / source).

Usage:
    python add_animations.py          # a single .fbx next to the script
    python add_animations.py Walk     # several .fbx: say which one
"""
import re
import json, struct, sys
import shutil, subprocess
from pathlib import Path
import numpy as np

# ==========================================================================
# A REMPLIR / VERIFIER
# ==========================================================================
HERE = Path(__file__).resolve().parent

# [1] Path of the FBX2glTF executable on your machine.
FBX2GLTF_PATH = '/home/adam/Applications/FBX2glTF-linux/FBX2glTF'

# [2] .glb model to complete (relative to this script, or an absolute path).
DST_GLB = str((HERE / '../static/avatar/model.glb').resolve())

# [3] Output file. Same as DST_GLB = the model is modified in place
OUT_GLB = DST_GLB
# ==========================================================================


def find_fbx():
    """Look for the .fbx in the script's folder (optional filter as an argument)."""
    fbxs = sorted(p for p in HERE.iterdir() if p.suffix.lower() == '.fbx')
    if len(sys.argv) > 1:
        wanted = sys.argv[1].lower()
        fbxs = [p for p in fbxs
                if p.name.lower() == wanted or p.stem.lower() == wanted]
    if not fbxs:
        sys.exit(f'Aucun fichier .fbx trouve dans {HERE}')
    if len(fbxs) > 1:
        sys.exit('Plusieurs .fbx trouves, precisez lequel :\n  ' +
                 '\n  '.join(f'python {Path(__file__).name} {p.stem}' for p in fbxs))
    return fbxs[0]


def get_converter():
    """Return the path of FBX2glTF."""
    p = Path(FBX2GLTF_PATH)
    if p.is_file():
        p.chmod(p.stat().st_mode | 0o111)
        return str(p)
    # tolerance: another executable name in the same folder (FBX2g*)
    if p.parent.is_dir():
        cands = [c for c in p.parent.glob('FBX2g*') if c.is_file()]
        if cands:
            cands[0].chmod(cands[0].stat().st_mode | 0o111)
            print(f'(utilise {cands[0]} au lieu de {p.name})')
            return str(cands[0])
    exe = shutil.which('FBX2glTF')
    if exe:
        return exe
    sys.exit(f'FBX2glTF introuvable a {FBX2GLTF_PATH}\n'
             'Corrigez FBX2GLTF_PATH en haut du script.')


def convert_fbx(fbx):
    """FBX -> glTF (+ buffer .bin). Retourne (chemin gltf, chemin bin)."""
    out_base = HERE / 'converted' / fbx.stem
    out_base.parent.mkdir(exist_ok=True)
    print(f'Conversion de {fbx.name}...')
    r = subprocess.run([get_converter(), '--input', str(fbx),
                        '--output', str(out_base)],
                       capture_output=True, text=True)
    out_dir = Path(str(out_base) + '_out')      # FBX2glTF ajoute "_out"
    gltfs = sorted(out_dir.glob('*.gltf'))
    if r.returncode != 0 or not gltfs:
        sys.exit('Echec de la conversion :\n' + r.stdout[-1500:] + r.stderr[-1500:])
    gltf = gltfs[0]
    uri = json.load(open(gltf))['buffers'][0]['uri']
    return gltf, out_dir / uri


FBX_FILE = find_fbx()
ANIM_NAME = FBX_FILE.stem                       # ex. Idle.fbx -> "Idle"
SRC_GLTF, SRC_BIN = convert_fbx(FBX_FILE)

# back up the model before any write
if OUT_GLB == DST_GLB and not Path(DST_GLB + '.bak').exists():
    shutil.copy2(DST_GLB, DST_GLB + '.bak')
    print(f'sauvegarde : {DST_GLB}.bak')

# ---------------------------------------------------------------- quaternions
def qmul(a, b):
    x1, y1, z1, w1 = a; x2, y2, z2, w2 = b
    return np.array([
        w1*x2 + x1*w2 + y1*z2 - z1*y2,
        w1*y2 - x1*z2 + y1*w2 + z1*x2,
        w1*z2 + x1*y2 - y1*x2 + z1*w2,
        w1*w2 - x1*x2 - y1*y2 - z1*z2])

def qconj(q):
    return np.array([-q[0], -q[1], -q[2], q[3]])

def qnorm(q):
    return q / np.linalg.norm(q)

def qrot(q, v):
    u = q[:3]; w = q[3]
    return 2.0*np.dot(u, v)*u + (w*w - np.dot(u, u))*v + 2.0*w*np.cross(u, v)

def norm_name(n):
    if not n:
        return n
    m = re.match(r'^mixamorig[:_]?(.+)$', n)
    if m:
        return 'mixamorig:' + m.group(1)
    return n

def q_between(a, b):
    """Minimal-arc rotation taking vector a onto vector b."""
    a = a/np.linalg.norm(a); b = b/np.linalg.norm(b)
    d = float(np.dot(a, b))
    if d > 0.999999:
        return np.array([0., 0., 0., 1.])
    if d < -0.999999:
        axis = np.cross(a, [1., 0., 0.])
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0., 1., 0.])
        axis /= np.linalg.norm(axis)
        return np.array([axis[0], axis[1], axis[2], 0.])
    axis = np.cross(a, b)
    q = np.array([axis[0], axis[1], axis[2], 1.0 + d])
    return qnorm(q)

# ------------------------------------------------------------------ mapping
M = {
    'mixamorig:Hips': 'CC_Base_Hip',
    'mixamorig:Spine': 'CC_Base_Waist',
    'mixamorig:Spine1': 'CC_Base_Spine01',
    'mixamorig:Spine2': 'CC_Base_Spine02',
    'mixamorig:Neck': 'CC_Base_NeckTwist01',
    'mixamorig:Head': 'CC_Base_Head',
}
for S, C_ in (('Left', 'L'), ('Right', 'R')):
    M[f'mixamorig:{S}Shoulder'] = f'CC_Base_{C_}_Clavicle'
    M[f'mixamorig:{S}Arm'] = f'CC_Base_{C_}_Upperarm'
    M[f'mixamorig:{S}ForeArm'] = f'CC_Base_{C_}_Forearm'
    M[f'mixamorig:{S}Hand'] = f'CC_Base_{C_}_Hand'
    M[f'mixamorig:{S}UpLeg'] = f'CC_Base_{C_}_Thigh'
    M[f'mixamorig:{S}Leg'] = f'CC_Base_{C_}_Calf'
    M[f'mixamorig:{S}Foot'] = f'CC_Base_{C_}_Foot'
    M[f'mixamorig:{S}ToeBase'] = f'CC_Base_{C_}_ToeBase'
    for mf, cf in (('Thumb', 'Thumb'), ('Index', 'Index'), ('Middle', 'Mid'),
                   ('Ring', 'Ring'), ('Pinky', 'Pinky')):
        for k in (1, 2, 3):
            M[f'mixamorig:{S}Hand{mf}{k}'] = f'CC_Base_{C_}_{cf}{k}'

# "shared" CC bones that exactly double a mapped bone (same rest position, same parent)
CLONES = {'CC_Base_L_ToeBaseShareBone': 'CC_Base_L_ToeBase',
          'CC_Base_R_ToeBaseShareBone': 'CC_Base_R_ToeBase'}

# child used to define the bone's direction (Mixamo side)
DIRCHILD = {
    'mixamorig:Hips': 'mixamorig:Spine',
    'mixamorig:Spine': 'mixamorig:Spine1',
    'mixamorig:Spine1': 'mixamorig:Spine2',
    'mixamorig:Spine2': 'mixamorig:Neck',
    'mixamorig:Neck': 'mixamorig:Head',
}
for S in ('Left', 'Right'):
    DIRCHILD[f'mixamorig:{S}Shoulder'] = f'mixamorig:{S}Arm'
    DIRCHILD[f'mixamorig:{S}Arm'] = f'mixamorig:{S}ForeArm'
    DIRCHILD[f'mixamorig:{S}ForeArm'] = f'mixamorig:{S}Hand'
    DIRCHILD[f'mixamorig:{S}Hand'] = f'mixamorig:{S}HandMiddle1'
    DIRCHILD[f'mixamorig:{S}UpLeg'] = f'mixamorig:{S}Leg'
    DIRCHILD[f'mixamorig:{S}Leg'] = f'mixamorig:{S}Foot'
    DIRCHILD[f'mixamorig:{S}Foot'] = f'mixamorig:{S}ToeBase'
    for mf in ('Thumb', 'Index', 'Middle', 'Ring', 'Pinky'):
        DIRCHILD[f'mixamorig:{S}Hand{mf}1'] = f'mixamorig:{S}Hand{mf}2'
        DIRCHILD[f'mixamorig:{S}Hand{mf}2'] = f'mixamorig:{S}Hand{mf}3'

# --------------------------------------------------------------- squelettes
class Skel:
    def __init__(self, nodes, roots):
        self.nodes = nodes
        self.parent = {}
        for i, n in enumerate(nodes):
            for c in n.get('children', []):
                self.parent[c] = i
        self.name2i = {n.get('name'): i for i, n in enumerate(nodes) if n.get('name')}
        self.order = []
        def walk(i):
            self.order.append(i)
            for c in nodes[i].get('children', []):
                walk(c)
        for r in roots:
            walk(r)

    def lr(self, i):
        return np.array(self.nodes[i].get('rotation', [0., 0., 0., 1.]), dtype=float)

    def lt(self, i):
        return np.array(self.nodes[i].get('translation', [0., 0., 0.]), dtype=float)

    def rest_world(self):
        """(world rotation, world position) at rest, per node index."""
        R, P = {}, {}
        for i in self.order:
            p = self.parent.get(i)
            if p is None:
                R[i] = self.lr(i); P[i] = self.lt(i)
            else:
                R[i] = qnorm(qmul(R[p], self.lr(i)))
                P[i] = P[p] + qrot(R[p], self.lt(i))
        return R, P

def slerp(a, b, t):
    d = float(np.dot(a, b))
    if d < 0:
        b = -b; d = -d
    if d > 0.9995:
        return qnorm(a + t*(b - a))
    th = np.arccos(d)
    return (np.sin((1-t)*th)*a + np.sin(t*th)*b) / np.sin(th)

def resample(t_src, vals, t_ref, is_rot):
    """Resample vals (defined at times t_src) onto the times t_ref."""
    if len(t_src) == 1:
        return np.tile(vals[0], (len(t_ref), 1))
    out = np.zeros((len(t_ref), vals.shape[1]))
    for k, t in enumerate(t_ref):
        j = int(np.clip(np.searchsorted(t_src, t, side='right') - 1, 0, len(t_src) - 2))
        dt = t_src[j+1] - t_src[j]
        u = 0.0 if dt < 1e-12 else float(np.clip((t - t_src[j]) / dt, 0.0, 1.0))
        if is_rot:
            out[k] = slerp(qnorm(vals[j]), qnorm(vals[j+1]), u)
        else:
            out[k] = vals[j] + u*(vals[j+1] - vals[j])
    return out

def load_src():
    js = json.load(open(SRC_GLTF))
    for n in js['nodes']:
        if 'name' in n:
            n['name'] = norm_name(n['name'])
    buf = open(SRC_BIN, 'rb').read()
    def acc(i):
        a = js['accessors'][i]; bv = js['bufferViews'][a['bufferView']]
        off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
        nc = {'SCALAR': 1, 'VEC3': 3, 'VEC4': 4}[a['type']]
        assert a['componentType'] == 5126, 'attendu float32'
        return np.frombuffer(buf, '<f4', a['count']*nc, off).reshape(a['count'], nc).astype(np.float64)
    sk = Skel(js['nodes'], js['scenes'][js.get('scene', 0)]['nodes'])
    anim = js['animations'][0]

    # reference timeline = the one with the most keyframes
    ref_times = max((acc(s['input'])[:, 0] for s in anim['samplers']), key=len)

    rot, tra, nres = {}, {}, 0
    for ch in anim['channels']:
        s = anim['samplers'][ch['sampler']]
        assert s.get('interpolation', 'LINEAR') == 'LINEAR'
        path = ch['target']['path']
        if path not in ('rotation', 'translation'):
            continue                      # ignore scale, etc.
        t = acc(s['input'])[:, 0]
        v = acc(s['output'])
        if len(t) != len(ref_times) or not np.allclose(t, ref_times, atol=1e-6):
            v = resample(t, v, ref_times, path == 'rotation'); nres += 1
        (rot if path == 'rotation' else tra)[ch['target']['node']] = v
    if nres:
        print(f'{nres} canaux reechantillonnes sur la timeline de reference')
    return sk, ref_times, rot, tra

def load_dst():
    with open(DST_GLB, 'rb') as f:
        magic, ver, total = struct.unpack('<III', f.read(12))
        assert magic == 0x46546C67 and ver == 2
        jlen, jtyp = struct.unpack('<II', f.read(8))
        js = json.loads(f.read(jlen))
        blen, btyp = struct.unpack('<II', f.read(8))
        assert btyp == 0x004E4942
        bindata = f.read(blen)
    sk = Skel(js['nodes'], js['scenes'][js.get('scene', 0)]['nodes'])
    return js, bindata, sk

# ==========================================================================
src, times, srot, stra = load_src()
djs, dbin, dst = load_dst()

miss_src = [m for m in M if m not in src.name2i]
if miss_src:
    print('os source introuvables :', miss_src)

nf = len(times)
print(f'source : {len(src.nodes)} noeuds, {nf} frames, {times[-1]:.2f}s '
      f'({(nf-1)/times[-1]:.0f} fps)')

# --- repos ---------------------------------------------------------------
SR, SP = src.rest_world()          # T-pose Mixamo
TR0, TP0 = dst.rest_world()        # A-pose CC

# check: is the source rest pose really a T-pose?
lh = SP[src.name2i['mixamorig:LeftHand']]
hips_h = SP[src.name2i['mixamorig:Hips']][1]
print(f'source main G au repos : {np.round(lh,3)} (T-pose attendue, |x| grand)')

# --- automatic T-pose of the target rig -------------------------------------
TR = dict(TR0)   # rotations monde T-posees
TP = dict(TP0)

mapped_dst = {dst.name2i[c]: m for m, c in M.items()
              if c in dst.name2i and m in src.name2i}
missing = [c for c in M.values() if c not in dst.name2i]
if missing:
    print('os cible introuvables :', missing)

def refresh_subtree(i):
    """Recompute the subtree's TR/TP starting at i from TR[i], TP[i]."""
    stack = [i]
    while stack:
        p = stack.pop()
        for c in dst.nodes[p].get('children', []):
            if 'mesh' in dst.nodes[c]:
                continue
            TR[c] = qnorm(qmul(TR[p], dst.lr(c)))
            TP[c] = TP[p] + qrot(TR[p], dst.lt(c))
            stack.append(c)

# walk the mapped bones from top to bottom
order_mapped = [i for i in dst.order if i in mapped_dst]
for i in order_mapped:
    mb = mapped_dst[i]
    ch = DIRCHILD.get(mb)
    if ch is None or ch not in src.name2i:
        continue
    cdst = M.get(ch)
    if cdst is None or cdst not in dst.name2i:
        continue
    j = dst.name2i[cdst]
    ds = SP[src.name2i[ch]] - SP[src.name2i[mb]]      # direction voulue (T-pose)
    dt = TP[j] - TP[i]                                 # direction actuelle
    if np.linalg.norm(ds) < 1e-9 or np.linalg.norm(dt) < 1e-9:
        continue
    q = q_between(dt, ds)
    TR[i] = qnorm(qmul(q, TR[i]))
    refresh_subtree(i)

print('T-pose auto : main G ->', np.round(TP[dst.name2i['CC_Base_L_Hand']], 3),
      '| repos A-pose ->', np.round(TP0[dst.name2i['CC_Base_L_Hand']], 3))

# --- constant per-bone offsets --------------------------------------------
C = {}
for i, mb in mapped_dst.items():
    si = src.name2i[mb]
    C[i] = qnorm(qmul(qconj(SR[si]), TR[i]))
for clone, ref in CLONES.items():
    if clone in dst.name2i and ref in dst.name2i:
        ri = dst.name2i[ref]
        if ri in C:
            C[dst.name2i[clone]] = C[ri]
            mapped_dst[dst.name2i[clone]] = mapped_dst[ri]

# --- root translation scale ------------------------------------------------
hip_dst = dst.name2i['CC_Base_Hip']
scale = TP0[hip_dst][1] / hips_h
print(f'hauteur hanche source {hips_h:.3f} / cible {TP0[hip_dst][1]:.3f} -> echelle {scale:.3f}')

# --- source animation per frame ----------------------------------------------
def src_world(f):
    R, P = {}, {}
    for i in src.order:
        r = srot[i][f] if i in srot else src.lr(i)
        t = stra[i][f] if i in stra else src.lt(i)
        p = src.parent.get(i)
        if p is None:
            R[i] = qnorm(r); P[i] = t
        else:
            R[i] = qnorm(qmul(R[p], qnorm(r)))
            P[i] = P[p] + qrot(R[p], t)
    return R, P

# --- computing the target tracks ---------------------------------------------
skip_mesh = {i for i, n in enumerate(dst.nodes) if 'mesh' in n}
bone_order = [i for i in dst.order if i not in skip_mesh]

tracks_rot = {i: np.zeros((nf, 4)) for i in C}
track_hip = np.zeros((nf, 3))
hip_parent = dst.parent[hip_dst]

for f in range(nf):
    SRf, SPf = src_world(f)
    W = {}
    for i in bone_order:
        p = dst.parent.get(i)
        if i in C:
            w = qnorm(qmul(SRf[src.name2i[mapped_dst[i]]], C[i]))
            W[i] = w
            lq = w if p is None else qnorm(qmul(qconj(W[p]), w))
            tracks_rot[i][f] = lq
        else:
            W[i] = dst.lr(i) if p is None else qnorm(qmul(W[p], dst.lr(i)))
    # translation racine
    dp = (SPf[src.name2i['mixamorig:Hips']] - SP[src.name2i['mixamorig:Hips']]) * scale
    world_hip = TP0[hip_dst] + dp
    track_hip[f] = qrot(qconj(TR0[hip_parent]), world_hip - TP0[hip_parent])

# quaternion continuity (avoids flips in linear interpolation)
for i, tr in tracks_rot.items():
    for f in range(1, nf):
        if np.dot(tr[f-1], tr[f]) < 0:
            tr[f] = -tr[f]

print(f'{len(tracks_rot)} pistes de rotation + 1 piste de translation, {nf} frames')

# --- writing the GLB ------------------------------------------------------
def pad4(b, fill=b'\x00'):
    return b + fill * ((4 - len(b) % 4) % 4)

buf = bytearray(dbin)
bvs = djs.setdefault('bufferViews', [])
accs = djs.setdefault('accessors', [])

def add_accessor(arr, typ):
    global buf
    while len(buf) % 4:
        buf += b'\x00'
    off = len(buf)
    data = np.ascontiguousarray(arr, dtype='<f4').tobytes()
    buf += data
    bvs.append({'buffer': 0, 'byteOffset': off, 'byteLength': len(data)})
    a = {'bufferView': len(bvs)-1, 'componentType': 5126,
         'count': int(arr.shape[0]), 'type': typ}
    if typ == 'SCALAR':
        a['min'] = [float(arr.min())]; a['max'] = [float(arr.max())]
    accs.append(a)
    return len(accs)-1

t_acc = add_accessor(times.reshape(-1, 1), 'SCALAR')
samplers, channels = [], []
for i, tr in sorted(tracks_rot.items()):
    o = add_accessor(tr, 'VEC4')
    samplers.append({'input': t_acc, 'interpolation': 'LINEAR', 'output': o})
    channels.append({'sampler': len(samplers)-1,
                     'target': {'node': i, 'path': 'rotation'}})
o = add_accessor(track_hip, 'VEC3')
samplers.append({'input': t_acc, 'interpolation': 'LINEAR', 'output': o})
channels.append({'sampler': len(samplers)-1,
                 'target': {'node': hip_dst, 'path': 'translation'}})

anims = djs.setdefault('animations', [])
anims[:] = [a for a in anims if a.get('name') != ANIM_NAME]   # remplace l'ancienne
anims.append({'name': ANIM_NAME, 'samplers': samplers, 'channels': channels})
djs['buffers'][0]['byteLength'] = len(buf)

jchunk = pad4(json.dumps(djs, separators=(',', ':')).encode('utf-8'), b' ')
bchunk = pad4(bytes(buf), b'\x00')
total = 12 + 8 + len(jchunk) + 8 + len(bchunk)
with open(OUT_GLB, 'wb') as f:
    f.write(struct.pack('<III', 0x46546C67, 2, total))
    f.write(struct.pack('<II', len(jchunk), 0x4E4F534A)); f.write(jchunk)
    f.write(struct.pack('<II', len(bchunk), 0x004E4942)); f.write(bchunk)
print(f'ecrit {OUT_GLB} ({total/1e6:.2f} Mo)')