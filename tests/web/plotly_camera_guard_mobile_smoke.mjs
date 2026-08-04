import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createPlotlyCameraGuard } from '../../seiltanzer/web/js/plotly_camera_guard.js';

class FakeTarget {
  constructor() {
    this.dom = new Map();
    this.plotly = new Map();
  }
  addEventListener(name, fn) {
    if (!this.dom.has(name)) this.dom.set(name, []);
    this.dom.get(name).push(fn);
  }
  removeEventListener(name, fn) {
    this.dom.set(name, (this.dom.get(name) || []).filter((item) => item !== fn));
  }
  dispatch(name, payload = {}) {
    for (const fn of this.dom.get(name) || []) fn({ type: name, ...payload });
  }
  on(name, fn) {
    if (!this.plotly.has(name)) this.plotly.set(name, []);
    this.plotly.get(name).push(fn);
  }
  emit(name, payload = {}) {
    for (const fn of this.plotly.get(name) || []) fn(payload);
  }
}

const clone = (value) => JSON.parse(JSON.stringify(value));
const INIT_CAM = {
  eye: { x: 0.15, y: 2.3, z: 0.65 },
  up: { x: 0, y: 0, z: 1 },
};
const USER_CAM = {
  eye: { x: -0.71, y: 0.43, z: 0.54 },
  center: { x: 0.09, y: -0.03, z: 0.02 },
  up: { x: 0, y: 0, z: 1 },
};
const ZOOM_CAM = {
  eye: { x: -0.34, y: 0.21, z: 0.29 },
  center: { x: 0.13, y: -0.08, z: 0.04 },
  up: { x: 0, y: 0, z: 1 },
};

const graph = new FakeTarget();
const fakeWindow = new FakeTarget();
fakeWindow.PointerEvent = class PointerEvent {};
fakeWindow.visualViewport = new FakeTarget();
graph.layout = { scene: { camera: clone(INIT_CAM) } };
graph._fullLayout = { scene: { camera: clone(INIT_CAM) } };

globalThis.window = fakeWindow;
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(performance.now()), 0);

const writes = [];
fakeWindow.Plotly = {
  relayout(el, update) {
    writes.push(clone(update));
    if (update['scene.camera']) {
      el._fullLayout.scene.camera = clone(update['scene.camera']);
      el.layout.scene.camera = clone(update['scene.camera']);
    }
    el.emit('plotly_relayout', clone(update));
    queueMicrotask(() => el.emit('plotly_afterplot'));
    return Promise.resolve(el);
  },
};

const guard = createPlotlyCameraGuard(graph, INIT_CAM);
guard.arm();
await new Promise((resolve) => setTimeout(resolve, 20));

// Reproduce the phone sequence which the previous smoke test missed:
// partial touch relayout -> Plotly resets _fullLayout -> pointerup -> redraw.
graph.dispatch('pointerdown', { pointerId: 1 });
graph.emit('plotly_relayouting', {
  'scene.camera.eye.x': USER_CAM.eye.x,
  'scene.camera.eye.y': USER_CAM.eye.y,
  'scene.camera.eye.z': USER_CAM.eye.z,
  'scene.camera.center': USER_CAM.center,
});
// Internal WebGL/responsive reset happens before the DOM pointerup callback.
graph._fullLayout.scene.camera = clone(INIT_CAM);
graph.layout.scene.camera = clone(INIT_CAM);
graph.emit('plotly_afterplot');
fakeWindow.dispatch('pointerup', { pointerId: 1 });
await new Promise((resolve) => setTimeout(resolve, 650));
assert.deepEqual(
  graph._fullLayout.scene.camera,
  USER_CAM,
  'touch rotation must survive a reset that happens before pointerup',
);

// Reproduce pinch/wheel zoom followed by a live react and viewport resize.
graph.dispatch('wheel');
graph.emit('plotly_relayouting', {
  'scene.camera.eye.x': ZOOM_CAM.eye.x,
  'scene.camera.eye.y': ZOOM_CAM.eye.y,
  'scene.camera.eye.z': ZOOM_CAM.eye.z,
  'scene.camera.center': ZOOM_CAM.center,
});
graph.emit('plotly_relayout', { 'scene.camera': clone(ZOOM_CAM) });
// Programmatic redraw emits a camera-shaped reset; it is not a user gesture and
// therefore must not replace the saved zoom.
await new Promise((resolve) => setTimeout(resolve, 300));
graph._fullLayout.scene.camera = clone(INIT_CAM);
graph.layout.scene.camera = clone(INIT_CAM);
graph.emit('plotly_relayout', { 'scene.camera': clone(INIT_CAM) });
graph.emit('plotly_afterplot');
fakeWindow.dispatch('resize');
fakeWindow.visualViewport.dispatch('resize');
await new Promise((resolve) => setTimeout(resolve, 1050));
assert.deepEqual(
  graph._fullLayout.scene.camera,
  ZOOM_CAM,
  'pinch/wheel zoom must survive live redraw and mobile viewport resize',
);

const coneSource = await readFile(
  new URL('../../seiltanzer/web/js/cone.js', import.meta.url), 'utf8');
const ivSource = await readFile(
  new URL('../../seiltanzer/web/js/iv_surface.js', import.meta.url), 'utf8');
assert.match(coneSource, /createPlotlyCameraGuard/, 'cone must use the shared camera guard');
assert.match(ivSource, /createPlotlyCameraGuard/, 'IV surface must use the shared camera guard');
assert(writes.length >= 2, 'guard must restore the camera after resets');

console.log(JSON.stringify({
  touchRotationRetained: true,
  zoomRetainedAfterReact: true,
  zoomRetainedAfterViewportResize: true,
  guardedPanels: ['cone', 'iv_surface'],
}));
