import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createPlotlyCameraGuard } from '../../seiltanzer/web/js/plotly_camera_guard.js';

class FakeTarget {
  constructor() {
    this.dom = new Map();
    this.plotly = new Map();
    this.style = {};
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
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
const emitAfter = (el) => queueMicrotask(() => el.emit('plotly_afterplot'));
fakeWindow.Plotly = {
  newPlot(el, traces, layout) {
    el.data = clone(traces || []);
    el.layout = clone(layout || { scene: { camera: INIT_CAM } });
    el._fullLayout = { scene: { camera: clone(el.layout.scene?.camera || INIT_CAM) } };
    emitAfter(el);
    return Promise.resolve(el);
  },
  react(el, traces, layout) {
    el.data = clone(traces || []);
    el.layout = clone(layout || el.layout);
    el._fullLayout.scene.camera = clone(el.layout.scene?.camera || INIT_CAM);
    emitAfter(el);
    return Promise.resolve(el);
  },
  restyle(el) {
    return Promise.resolve(el);
  },
  relayout(el, update) {
    writes.push(clone(update));
    if (update['scene.camera']) {
      el._fullLayout.scene.camera = clone(update['scene.camera']);
      el.layout.scene.camera = clone(update['scene.camera']);
    }
    el.emit('plotly_relayout', clone(update));
    emitAfter(el);
    return Promise.resolve(el);
  },
  Plots: {
    resize(el) {
      emitAfter(el);
      return Promise.resolve(el);
    },
  },
  purge() {},
};

const guard = createPlotlyCameraGuard(graph, INIT_CAM);
guard.arm();
await sleep(30);
assert.equal(graph.style.touchAction, 'none', 'browser gestures must not own the WebGL surface');
assert.equal(graph.style.overscrollBehavior, 'contain');

// Real phone ordering missed by the previous test: pointerup runs in the
// window capture phase first, while Plotly publishes its final camera later.
graph.dispatch('pointerdown', { pointerId: 1 });
fakeWindow.dispatch('pointerup', { pointerId: 1 });
await sleep(8);
graph._fullLayout.scene.camera = clone(USER_CAM);
graph.layout.scene.camera = clone(USER_CAM);
graph.emit('plotly_relayout', { 'scene.camera': clone(USER_CAM) });

// A responsive WebGL redraw then exposes the initial camera. It must not beat
// the user's later final camera during the settling window.
graph._fullLayout.scene.camera = clone(INIT_CAM);
graph.layout.scene.camera = clone(INIT_CAM);
graph.emit('plotly_relayout', { 'scene.camera': clone(INIT_CAM) });
graph.emit('plotly_afterplot');
await sleep(950);
assert.deepEqual(
  graph._fullLayout.scene.camera,
  USER_CAM,
  'post-pointer final camera must survive a racing responsive INIT reset',
);
assert.equal(guard.getState(), 'idle');

// Mobile pinch may be represented as a wheel gesture. No restore is allowed
// while wheel interaction or its settling phase is still active.
graph.dispatch('wheel');
graph.emit('plotly_relayouting', { 'scene.camera': clone(ZOOM_CAM) });
await sleep(40);
graph._fullLayout.scene.camera = clone(INIT_CAM);
graph.layout.scene.camera = clone(INIT_CAM);
graph.emit('plotly_relayout', { 'scene.camera': clone(INIT_CAM) });
graph.emit('plotly_afterplot');
fakeWindow.dispatch('resize');
fakeWindow.visualViewport.dispatch('resize');
await sleep(1100);
assert.deepEqual(
  graph._fullLayout.scene.camera,
  ZOOM_CAM,
  'pinch/wheel zoom must survive redraw and viewport resize',
);

// Keep a real touch fallback even when PointerEvent exists: WebKit/WebViews can
// advertise Pointer Events while Plotly gl3d still drives gestures from touch.
graph.dispatch('touchstart', { touches: [{}, {}] });
graph._fullLayout.scene.camera = clone(USER_CAM);
graph.emit('plotly_relayouting', { 'scene.camera': clone(USER_CAM) });
fakeWindow.dispatch('touchend', { touches: [{}] });
assert.equal(guard.getState(), 'interacting', 'first finger release must not end a pinch');
fakeWindow.dispatch('touchend', { touches: [] });
await sleep(950);
assert.deepEqual(graph._fullLayout.scene.camera, USER_CAM);

const coneSource = await readFile(
  new URL('../../seiltanzer/web/js/cone.js', import.meta.url), 'utf8');
const ivSource = await readFile(
  new URL('../../seiltanzer/web/js/iv_surface.js', import.meta.url), 'utf8');
assert.match(coneSource, /createPlotlyCameraGuard/, 'cone must use the shared camera guard');
assert.match(ivSource, /createPlotlyCameraGuard/, 'IV surface must use the shared camera guard');
assert(writes.length >= 2, 'guard must restore the camera after real resets');

console.log(JSON.stringify({
  postPointerFinalRelayoutRetained: true,
  staleInitRejectedDuringSettling: true,
  zoomRetainedDuringWheelSettling: true,
  twoFingerTouchFallbackRetained: true,
  browserGestureOwnershipDisabled: true,
  guardedPanels: ['cone', 'iv_surface'],
}));