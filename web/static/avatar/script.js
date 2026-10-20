import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const MODEL_URL = "/avatar/model.glb";  // absolute: the page is served from "/", a relative path would resolve to /model.glb (404)
const POLL_INTERVAL_MS = 100;
const JAW_MAX_ANGLE = 0.30;          // radians, maximum jaw opening
const JAW_BONE_NAMES = ["CC_Base_JawRoot", "CC_Base_UpperJaw"];
const HEAD_BONE_NAME = "CC_Base_Head";
const EYE_BONE_NAMES = ["CC_Base_L_Eye", "CC_Base_R_Eye"];

const AURA_COLORS = {
    idle: 0x6ee7ff,
    listening: 0x7dd3fc,
    thinking: 0xc4b5fd,
    speaking: 0xfb7185,
};
const STATUS_LABELS = {
    idle: "Au repos",
    listening: "Je t'écoute",
    thinking: "Je réfléchis",
    speaking: "Je parle",
};

// Which clip (baked into model.glb by dev_animations/add_animations.py) to play for each state. A state without
// dedicated mocap falls back to "Idle": add an entry here the day a new animation is baked
// (e.g. "speaking": "Talking" once Talking.fbx has been added to the model).
const STATE_CLIPS = {
    idle: "Idle",
    listening: "Idle",
    thinking: "Thinking",
    speaking: "Idle",
};
const ANIMATION_CROSSFADE_SECONDS = 0.35;

let jawSign = parseFloat(localStorage.getItem("monika_jaw_sign") || "1");

// ---------------------------------------------------------------------------
// Scene setup
// ---------------------------------------------------------------------------
const holder = document.getElementById("canvas-holder");
const scene = new THREE.Scene();

const camera = new THREE.PerspectiveCamera(32, (holder.clientWidth || window.innerWidth) / (holder.clientHeight || window.innerHeight), 0.05, 50);
camera.position.set(0, 1.55, 1.35);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(holder.clientWidth || window.innerWidth, holder.clientHeight || window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;
holder.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.45, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.minDistance = 0.6;
controls.maxDistance = 2.6;
controls.maxPolarAngle = Math.PI * 0.62;
controls.minPolarAngle = Math.PI * 0.25;
controls.enablePan = false;
controls.update();

// Lighting: soft, "webcam overlay" style rather than a harsh realistic render.
scene.add(new THREE.AmbientLight(0xffffff, 0.9));

const keyLight = new THREE.DirectionalLight(0xfff2e6, 1.1);
keyLight.position.set(1.2, 2.2, 1.6);
scene.add(keyLight);

const rimLight = new THREE.DirectionalLight(0x8ab4ff, 0.6);
rimLight.position.set(-1.5, 1.6, -1.2);
scene.add(rimLight);

const auraLight = new THREE.PointLight(AURA_COLORS.idle, 1.2, 3.0);
auraLight.position.set(0, 1.1, 0.6);
scene.add(auraLight);

// ---------------------------------------------------------------------------
// Load the model
// ---------------------------------------------------------------------------
const bones = {};        // name -> THREE.Bone
const boneBase = {};     // name -> { rotation: Euler, scale: Vector3 } d'origine
const eyeMeshes = [];    // meshes that "blink"

let jawBone = null;
let headBone = null;
let loadedModel = null;
let faceSign = parseFloat(localStorage.getItem("monika_face_sign") || "1");

let mixer = null;              // THREE.AnimationMixer, created once the model is loaded
const actions = {};            // clip name ("Idle", "Thinking", ...) -> THREE.AnimationAction
let activeAction = null;

function findBone(root, name) {
    let found = null;
    root.traverse((obj) => {
        if (!found && obj.isBone && obj.name === name) found = obj;
    });
    return found;
}

function rememberBase(bone) {
    boneBase[bone.name] = {
        rotation: bone.rotation.clone(),
        scale: bone.scale.clone(),
    };
}

function frameCamera(model) {
    // 1. Force Three.js to compute the exact spatial position of the bones before the first render
    model.updateMatrixWorld(true);

    const box = new THREE.Box3().setFromObject(model);
    const center = new THREE.Vector3();
    box.getCenter(center); // kept to centre on the X and Z axes

    let eyeLevelY = 1.55;
    let bustTargetY = 1.35;

    // 2. If the head was found, frame on it
    if (headBone) {
        const headPos = new THREE.Vector3();
        headBone.getWorldPosition(headPos);

        eyeLevelY = headPos.y;           // camera height = eye height
        bustTargetY = headPos.y - 0.25;  // camera target = 25 cm below the head (the bust)
    } else {
        // fallback in case the bone is not found
        eyeLevelY = box.min.y + 1.7 * 0.85;
        bustTargetY = box.min.y + 1.7 * 0.65;
    }

    // 3. Camera placement
    const camDistance = 1.2; // distance back
    camera.position.set(center.x, eyeLevelY, center.z + camDistance * faceSign);
    camera.near = 0.05;
    camera.far = 50;
    camera.updateProjectionMatrix();

    // 4. Aim the camera at the bust
    controls.target.set(center.x, bustTargetY, center.z);
    controls.minDistance = 0.2;
    controls.maxDistance = 5.0; // allow zooming out
    controls.update();

    // 5. Move the aura light onto the bust
    auraLight.position.set(center.x, bustTargetY, center.z + 0.3 * faceSign);
    auraLight.distance = 3.0;
}

const loader = new GLTFLoader();
loader.load(
    MODEL_URL,
    (gltf) => {
        const model = gltf.scene;
        model.position.set(0, 0, 0);
        scene.add(model);

        // Find the useful bones. Missing ones are tolerated (we only animate what exists).
        for (const name of JAW_BONE_NAMES) {
            const b = findBone(model, name);
            if (b) { bones[name] = b; rememberBase(b); if (!jawBone) jawBone = b; }
        }
        headBone = findBone(model, HEAD_BONE_NAME);
        for (const name of EYE_BONE_NAMES) {
            const b = findBone(model, name);
            if (b) { bones[name] = b; rememberBase(b); }
        }

        // Find the eye meshes (for blinking), excluding eyebrows/eyelashes.
        model.traverse((obj) => {
            if (obj.isMesh || obj.isSkinnedMesh) {
                const n = obj.name.toLowerCase();
                if (n.includes("eyes")) eyeMeshes.push(obj);
            }
        });

        loadedModel = model;
        frameCamera(model);
        setupAnimations(model, gltf.animations);

        document.getElementById("loading").style.display = "none";
    },
    undefined,
    (err) => {
        console.error("Erreur de chargement du modèle :", err);
        document.getElementById("loading").textContent =
            "Impossible de charger model.glb (voir la console).";
    }
);

// ---------------------------------------------------------------------------
// Animation clips (Idle / Thinking, baked into model.glb by dev_animations/add_animations.py) - they replace the
// procedural movements that served as a placeholder before there was real mocap.
// ---------------------------------------------------------------------------
function setupAnimations(model, clips) {
    mixer = new THREE.AnimationMixer(model);
    for (const clip of clips) {
        const action = mixer.clipAction(clip);
        action.setEffectiveWeight(0);
        action.play();               // active from the start, but at zero weight until selected
        actions[clip.name] = action;
    }
    if (Object.keys(actions).length === 0) {
        console.warn("Aucune animation dans model.glb — avez-vous lancé dev_animations/add_animations.py ?");
        return;
    }
    activeAction = null;
    playStateAnimation(currentState, 0); // initial pose, without a fade (otherwise a flash of the rest pose)
}

function playStateAnimation(state, fadeSeconds = ANIMATION_CROSSFADE_SECONDS) {
    if (!mixer) return; // model not loaded yet; setupAnimations() will replay the current state
    const wanted = STATE_CLIPS[state] || "Idle";
    const next = actions[wanted] || actions.Idle;
    if (!next || next === activeAction) return;

    const previous = activeAction;
    activeAction = next;

    next.reset().setEffectiveTimeScale(1).setEffectiveWeight(1).fadeIn(fadeSeconds).play();
    if (previous) previous.fadeOut(fadeSeconds);
}

// ---------------------------------------------------------------------------
// State polling (idle / listening / thinking / speaking + amplitude)
// ---------------------------------------------------------------------------
let currentState = "idle";
let targetAmplitude = 0;
let smoothedAmplitude = 0;

async function pollState() {
    try {
        const res = await fetch("/api/state", { cache: "no-store" });
        if (res.ok) {
            const data = await res.json();
            if (data.state !== currentState) applyState(data.state);
            targetAmplitude = data.amplitude ?? 0;
        }
    } catch (e) {
        // The server may not be ready at the very start; just retry.
    } finally {
        setTimeout(pollState, POLL_INTERVAL_MS);
    }
}

function applyState(state) {
    currentState = state;
    playStateAnimation(state);
    const color = AURA_COLORS[state] ?? AURA_COLORS.idle;
    document.getElementById("status-dot").style.background = `#${color.toString(16).padStart(6, "0")}`;
    document.getElementById("status-dot").style.boxShadow = `0 0 12px 3px #${color.toString(16).padStart(6, "0")}`;
    document.getElementById("status-label").textContent = STATUS_LABELS[state] ?? state;
}

pollState();

// ---------------------------------------------------------------------------
// Blink (fake: the Y scale of the "eyes" meshes is briefly squeezed)
// ---------------------------------------------------------------------------
let nextBlinkAt = performance.now() + 2000;
let blinkPhase = 0; // 0 = open, otherwise the animation in progress

function updateBlink(now) {
    if (eyeMeshes.length === 0) return;

    if (blinkPhase === 0 && now >= nextBlinkAt) {
        blinkPhase = { start: now, duration: 110 };
        nextBlinkAt = now + 2600 + Math.random() * 3200;
    }
    if (typeof blinkPhase === "object") {
        const t = (now - blinkPhase.start) / blinkPhase.duration;
        if (t >= 1) {
            for (const m of eyeMeshes) m.scale.y = 1;
            blinkPhase = 0;
        } else {
            // triangle 0->1->0 for a quick there-and-back blink
            const k = t < 0.5 ? t * 2 : (1 - t) * 2;
            const scaleY = 1 - k * 0.92;
            for (const m of eyeMeshes) m.scale.y = scaleY;
        }
    }
}

// ---------------------------------------------------------------------------
// Animation loop
// ---------------------------------------------------------------------------
const clock = new THREE.Clock();
let elapsedTime = 0; // accumulated by hand (see animate) so clock.getDelta() is called only once per frame
const mouse = { x: 0, y: 0 };
window.addEventListener("pointermove", (e) => {
    mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouse.y = (e.clientY / window.innerHeight) * 2 - 1;
});

function animate() {
    requestAnimationFrame(animate);
    const delta = clock.getDelta();
    elapsedTime += delta;
    const t = elapsedTime;
    const now = performance.now();

    // Smoothed amplitude (server updates arrive at ~10 Hz, interpolated at 60 Hz for a fluid mouth).
    smoothedAmplitude += (targetAmplitude - smoothedAmplitude) * 0.35;

    // Whole body (spine, arms, legs, head...): driven by the active mocap clip (Idle/Thinking, see
    // setupAnimations/playStateAnimation). Everything below is ADDED on top of the pose the mixer just
    // set, never replacing it, or the animation would be erased.
    if (mixer) mixer.update(delta);

    // Head gaze toward the mouse + small movement while speaking: additive layer on the animated pose
    // of the current frame (the mocap already provided this frame's base rotation, we only add a small delta).
    if (headBone) {
        const lookX = mouse.y * 0.06;
        const lookY = mouse.x * 0.10;
        const bob = currentState === "speaking" ? Math.sin(t * 2.2) * 0.01 : 0;
        headBone.rotation.x += lookX + bob;
        headBone.rotation.y += lookY;
    }

    // Eye gaze: these bones are absent from the Mixamo skeleton, so the mixer never touches them -
    // no conflict to handle here.
    for (const name of EYE_BONE_NAMES) {
        const bone = bones[name];
        if (!bone) continue;
        const base = boneBase[name];
        bone.rotation.y = base.rotation.y + mouse.x * 0.05;
        bone.rotation.x = base.rotation.x + mouse.y * 0.03;
    }

    // Jaw: follows the audio amplitude while Monika speaks, otherwise stays closed.
    const jawTarget = currentState === "speaking" ? smoothedAmplitude : 0;
    for (const name of JAW_BONE_NAMES) {
        const bone = bones[name];
        if (!bone) continue;
        const base = boneBase[name];
        bone.rotation.x = base.rotation.x + jawSign * jawTarget * JAW_MAX_ANGLE;
    }

    updateBlink(now);

    // Aura: colour by state, with a light pulse.
    const color = AURA_COLORS[currentState] ?? AURA_COLORS.idle;
    auraLight.color.setHex(color);
    auraLight.intensity = 1.0 + (currentState === "speaking" ? smoothedAmplitude * 0.8 : Math.sin(t * 1.4) * 0.15);

    controls.update();
    renderer.render(scene, camera);
}
animate();

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------
// The canvas follows its holder, not the window: the holder shrinks when the dashboard panel docks on the right.
new ResizeObserver(() => {
    const w = holder.clientWidth, h = holder.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
}).observe(holder);