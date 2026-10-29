// Camera controls for the avatar's stage: left-drag orbits, the wheel moves closer or further,
// right-drag up/down slides the focus from her head to her shoes.
//
// Same gestures as OpenSakura (MIT, (c) 2026 MekaHime Inc.), reimplemented without OrbitControls:
// only GLTFLoader and BufferGeometryUtils are vendored, and the orbit is a hundred lines. The camera
// rides a sphere around a focus point that follows her body, so breathing and gestures never jitter
// the view (the corrected head position is eased, not copied), and it never sinks into the floor.
import * as THREE from 'three';

const FLOOR = 0.15;          // the camera never goes below the room's floor
const ROTATE = 0.005;        // radians per pixel
const ZOOM = 0.0011;         // wheel sensitivity
const PHI_MIN = 0.35;        // polar angle: 0 = straight above her, PI / 2 = level
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
    this.maxDistance = 4.5;    // the room is 9 m deep: beyond that the camera would leave it
    this.placed = false;
    this._point = new THREE.Vector3();
    this._rotate = null;
    this._focus = null;
    this._bind();
  }

  /** First framing: aimed at `centre`, `distance` away, with `focusT` on her body. */
  place(centre, distance, focusT) {
    this.target.copy(centre);
    this.distance = clamp(distance, this.minDistance, this.maxDistance);
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
    const distance = clamp(this.distance, this.minDistance, this.maxDistance);
    const horizontal = Math.sin(this.phi) * distance;
    this.camera.position.set(
      this.target.x + horizontal * Math.sin(this.theta),
      this.target.y + Math.cos(this.phi) * distance,
      this.target.z + horizontal * Math.cos(this.theta),
    );
    this._aboveFloor();
    this.camera.lookAt(this.target);
  }

  /** Keep the camera out of the floor, and keep the orbit consistent so the next frame does not snap. */
  _aboveFloor() {
    const cam = this.camera.position;
    if (cam.y >= FLOOR) return;
    cam.y = FLOOR;
    this.phi = Math.acos(clamp((FLOOR - this.target.y) / Math.max(1e-4, this.distance), -1, 1));
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
