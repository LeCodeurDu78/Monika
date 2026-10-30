// Visual effects on top of the stage render: depth of field (the room softens, Monika stays sharp), a
// bloom on the only truly bright things (the window panes and the lamp bulb) and a light vignette.
//
// Everything here is optional. A composer that cannot be built, a render that throws, or a frame rate
// that collapses all fall back to the plain render: the avatar never stops because of an effect.
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { VignetteShader } from 'three/addons/shaders/VignetteShader.js';

const MIN_FPS = 24;   // below this, a smooth orbit is worth more than the effects
const WARMUP_S = 3;   // shader compilation and the model load skew the first seconds
const WINDOW_S = 2;   // one measurement window: long enough to be stable, short enough to react

export class PostFX {
  /**
   * @param {THREE.WebGLRenderer} renderer
   * @param {THREE.Scene} scene
   * @param {THREE.PerspectiveCamera} camera
   * @param {() => number} focus distance from the camera to the point that must stay sharp
   */
  constructor(renderer, scene, camera, focus) {
    this.renderer = renderer;
    this.camera = camera;
    this.focus = focus;
    this.onFallback = null;   // (reason: string) => void, called once if the effects are dropped
    this.enabled = true;

    this.composer = new EffectComposer(renderer);
    this.composer.addPass(new RenderPass(scene, camera));

    // Aperture is in blur-per-metre: her body (about a metre deep) stays almost sharp, the room behind
    // reaches the maximum blur and reads as background.
    this.dof = new BokehPass(scene, camera, { focus: 2, aperture: 0.008, maxblur: 0.006 });
    this.composer.addPass(this.dof);

    // Threshold under the luminance of the window panes (about 0.46 in linear light): only they and the
    // bulb bloom, the lit floor stays put.
    this.bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.45, 0.5, 0.35);
    this.composer.addPass(this.bloom);

    this.vignette = new ShaderPass(VignetteShader);
    this.vignette.uniforms.offset.value = 0.65;   // how far into the corners it starts
    this.vignette.uniforms.darkness.value = 1.0;  // 1 = towards black, so a light corner darkening
    this.composer.addPass(this.vignette);

    this.composer.addPass(new OutputPass());      // tone mapping and sRGB once, at the very end

    this._frames = 0;
    this._window = 0;
    this._elapsed = 0;
  }

  /** Follow the canvas (and the device pixel ratio) the way the renderer does. */
  resize(width, height) {
    this.composer.setPixelRatio(this.renderer.getPixelRatio());
    this.composer.setSize(width, height);
    this.dof.uniforms.aspect.value = this.camera.aspect;
  }

  /** Draw one frame with the effects; false means the caller must draw it plainly. */
  render(dt) {
    if (!this.enabled) return false;
    try {
      this.dof.uniforms.focus.value = this.focus();
      this.composer.render(dt);
    } catch (err) {
      this._fallback('erreur de rendu');
      console.warn('[fx] post-traitement abandonné :', err);
      return false;
    }
    if (dt > 0) this._measure(dt);
    return true;
  }

  /** Drop the effects for good: the avatar keeps rendering, just without them. */
  _fallback(reason) {
    if (!this.enabled) return;
    this.enabled = false;
    try {
      this.composer.dispose();
    } catch (err) { /* nothing left to free */ }
    this.onFallback?.(reason);
  }

  /** Drop the effects when the frame rate shows they cost more than they bring. */
  _measure(dt) {
    this._elapsed += dt;
    if (this._elapsed < WARMUP_S) return;
    this._frames++;
    this._window += dt;
    if (this._window < WINDOW_S) return;
    const fps = this._frames / this._window;
    this._frames = 0;
    this._window = 0;
    if (fps < MIN_FPS) this._fallback(`rendu à ${Math.round(fps)} images/s`);
  }
}
