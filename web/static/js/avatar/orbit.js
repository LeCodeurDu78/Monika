// Camera controls for the avatar's stage: left-drag orbits, the wheel moves closer or further,
// right-drag up/down slides the focus from her head to her shoes.
//
// Same gestures as OpenSakura (MIT, (c) 2026 MekaHime Inc.), reimplemented without OrbitControls:
// only GLTFLoader and BufferGeometryUtils are vendored, and the orbit is a hundred lines. The camera
// rides a sphere around a focus point that follows her body, so breathing and gestures never jitter
// the view (the corrected head position is eased, not copied), and it never sinks into the floor,
// never crosses the ceiling and never leaves the room through a wall.
import * as THREE from 'three';
import { ROOM } from './environment.js';

const FLOOR = 0.15;          // the camera never goes below the room's floor
const CEILING = ROOM.height - 0.25;  // ...nor through its ceiling: the lens needs a little room
const WALL_MARGIN = 0.35;            // ...nor through its walls
// The back wall (z = ROOM.back) and the two side walls (±ROOM.halfX) bound the orbit; the camera side
// is left open, so this radius keeps the camera inside whatever the azimuth.
const MAX_REACH = Math.min(ROOM.halfX, -ROOM.back) - WALL_MARGIN;
const ROTATE = 0.005;        // radians per pixel
const ZOOM = 0.0011;         // wheel sensitivity
const PHI_MIN = 0.35;        // polar angle floor: 0 = straight above her, PI / 2 = level
const PHI_MAX = 1.85;
const START_PHI = Math.PI / 2 - 0.04;  // a hair above the focus point, like the fixed framing was

const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export class Orbit {
  /**
   * @param {THREE.PerspectiveCamera} camera
   * @param {HTMLCanvasElement} canvas
   * @param {(out: THREE.Vector3, focusT: number) => boolean} focusPoint fills `out` with the point on
   *   her body at `focusT` (1 = head, 0 = shoes); false while there is no model to follow
   */
  constructor(camera, canvas, focusPoint) {
    this.camera = camera;
    this.canvas = canvas;
    this.focusPoint = focusPoint;
    this.target = new THREE.Vector3();
    this.theta = 0;            // azimuth: 0 = straight in front (+Z), she faces the camera
    this.phi = START_PHI;      // elevation
    this.distance = 2;
    this.focusT = 1;
    this.minDistance = 0.7;
    this.maxDistance = 4.5;    // absolute limit; the walls tighten it when the view is level
    this.placed = false;
    this._point = new THREE.Vector3();
    this._rotate = null;
    this._focus = null;
    this._bind();
  }

  /** First framing: aimed at `centre`, `distance` away, with `focusT` on her body. */
  place(centre, distance, focusT) {
    this.target.copy(centre);
    this.distance = clamp(distance, this.minDistance, this._wallDistance(START_PHI));
    this.focusT = clamp(focusT, 0, 1);
    this.theta = 0;
    this.phi = START_PHI;
    this.placed = true;
    this.update();
  }

  /** Ease the focus point onto her body, then put the camera back on its sphere. Call once per frame. */
  follow(dt) {
    if (!this.focusPoint || !this.focusPoint(this._point, this.focusT)) return;
    const rate = this._focus ? 14 : 1.5;  // snappy while the user is right-dragging the focus
    this.target.lerp(this._point, Math.min(1, dt * rate));
    this.update();
  }

  update() {
    // Ceiling first, then the walls: pushing the angle down under the ceiling widens the reach, so the
    // distance has to be bounded with the angle the camera will really use. Bounding it downwards only
    // relaxes the ceiling, so one pass on each is enough.
    const wanted = Math.max(this.minDistance, this.distance);
    this.phi = clamp(this.phi, this._ceilingAngle(wanted), PHI_MAX);
    const distance = Math.min(wanted, this._wallDistance(this.phi));
    const horizontal = Math.sin(this.phi) * distance;
    this.camera.position.set(
      this.target.x + horizontal * Math.sin(this.theta),
      this.target.y + Math.cos(this.phi) * distance,
      this.target.z + horizontal * Math.cos(this.theta),
    );
    this._aboveFloor(distance);
    this.camera.lookAt(this.target);
  }

  /** How far it may pull back at this angle: looking level, the walls stop it instead of being crossed. */
  _wallDistance(phi) {
    return Math.min(this.maxDistance, MAX_REACH / Math.max(0.2, Math.sin(phi)));
  }

  /** The steepest angle the ceiling allows at this distance: higher up, the camera would leave the room. */
  _ceilingAngle(distance) {
    const reach = (CEILING - this.target.y) / Math.max(1e-4, distance);
    return Math.max(PHI_MIN, Math.acos(clamp(reach, -1, 1)));
  }

  /** Keep the camera out of the floor, and keep the orbit consistent so the next frame does not snap. */
  _aboveFloor(distance) {
    const cam = this.camera.position;
    if (cam.y >= FLOOR) return;
    cam.y = FLOOR;
    this.phi = Math.acos(clamp((FLOOR - this.target.y) / Math.max(1e-4, distance), -1, 1));
  }

  _bind() {
    const canvas = this.canvas;
    canvas.addEventListener('contextmenu', (event) => event.preventDefault());
    canvas.addEventListener('pointerdown', (event) => {
      if (event.button === 0) this._rotate = { x: event.clientX, y: event.clientY };
      else if (event.button === 2) this._focus = { y: event.clientY, t: this.focusT };
      else return;
      try { canvas.setPointerCapture(event.pointerId); } catch (err) { /* not supported */ }
      canvas.classList.add('dragging');
      event.preventDefault();
    });
    canvas.addEventListener('pointermove', (event) => this._move(event));
    const end = (event) => {
      if (event.type === 'pointercancel' || event.button === 0) this._rotate = null;
      if (event.type === 'pointercancel' || event.button === 2) this._focus = null;
      try { canvas.releasePointerCapture(event.pointerId); } catch (err) { /* not supported */ }
      canvas.classList.remove('dragging');
    };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('wheel', (event) => {
      event.preventDefault();
      this.distance = clamp(this.distance * Math.exp(event.deltaY * ZOOM), this.minDistance, this.maxDistance);
      this.update();
    }, { passive: false });
  }

  _move(event) {
    if (this._rotate) {
      this.theta -= (event.clientX - this._rotate.x) * ROTATE;
      this.phi = clamp(this.phi - (event.clientY - this._rotate.y) * ROTATE, PHI_MIN, PHI_MAX);
      this._rotate = { x: event.clientX, y: event.clientY };
      this.update();
    }
    if (this._focus) {
      const travel = Math.max(220, this.canvas.clientHeight * 0.45);  // px of drag for head -> shoes
      this.focusT = clamp(this._focus.t - (event.clientY - this._focus.y) / travel, 0, 1);
    }
  }
}
