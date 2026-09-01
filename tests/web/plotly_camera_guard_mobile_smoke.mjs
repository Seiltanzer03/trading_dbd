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
  dispatchEvent(event) {
    this.dispatch(event.type, event);
    return true;
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
  projection: { type: 'perspective' },
};

function plotlyCamera(camera) {
  return {
    eye: { x: 1.25, y: 1.25, z: 1.25, ...(camera?.eye || {}) },
    center: { x: 0, y: 0, z: 0, ...(camera?.center || {}) },
    up: { x: 0, y: 0, z: 1, ...(camera?.up || {}) },
    projection: { type: 'perspective', ...(camera?.projection || {}) },
  };
}

const graph = new FakeTarget();
const fakeWindow = new FakeTarget();
fakeWindow.PointerEvent = class PointerEvent {};
fakeWindow.visualViewport = new FakeTarget();
graph.layout = { scene: { camera: clone(INIT_CAM) } };
graph._fullLayout = { scene: { camera: plotlyCamera(INIT_CAM), dragmode: 'orbit' } };

globalThis.window = fakeWindow;
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(performance.now()), 0);

const writes = [];
const emitAfter = (el) => queueMicrotask(() => el.emit('plotly_afterplot'));
fakeWindow.Plotly = {
  newPlot(el, traces, layout) {
    el.data = clone(traces || []);
    el.layout = clone(layout || { scene: { camera: INIT_CAM } });
    el._fullLayout = { scene: {
      camera: plotlyCamera(el.layout.scene?.camera || INIT_CAM),
      dragmode: el.layout.scene?.dragmode || 'orbit',
    } };
    emitAfter(el);
    return Promise.resolve(el);
  },
  react(el, traces, layout) {
    el.data = clone(traces || []);
    el.layout = clone(layout || el.layout);
    el._fullLayout.scene.camera = plotlyCamera(el.layout.scene?.camera || INIT_CAM);
    el._fullLayout.scene.dragmode = el.layout.scene?.dragmode || el._fullLayout.scene.dragmode;
    emitAfter(el);
    return Promise.resolve(el);
  },
  restyle(el) {
    return Promise.resolve(el);
  },
  relayout(el, update) {
    writes.push(clone(update));
    if (update['scene.camera']) {
      el._fullLayout.scene.camera = plotlyCamera(update['scene.camera']);
      el.layout.scene.camera = clone(update['scene.camera']);
    }
    if (update['scene.dragmode']) {
      el._fullLayout.scene.dragmode = update['scene.dragmode'];
      el.layout.scene.dragmode = update['scene.dragmode'];
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
graph.emit('plotly_afterplot');
await sleep(180);
assert.equal(graph.style.touchAction, 'none', 'browser gestures must not own the WebGL surface');
assert.equal(graph.style.overscrollBehavior, 'contain');
assert.equal(writes.length, 0,
  'Plotly-expanded center/projection defaults must not create an idle relayout loop');
assert.deepEqual(guard.getSavedCamera(), plotlyCamera(INIT_CAM),
  'the guard must own a complete canonical initial camera');

await guard.setDragMode('turntable');
await sleep(350);
const writesAfterTurntable = writes.length;
const cameraWritesAfterTurntable = writes.filter((update) => update['scene.camera']).length;
await sleep(260);
assert.equal(writes.length, writesAfterTurntable,
  'selecting turntable without input must become completely quiescent');
assert.equal(
  writes.filter((update) => update['scene.camera']).length,
  cameraWritesAfterTurntable,
  'turntable selection must not start background camera restores',
);
assert.equal(guard.getDragMode(), 'turntable');
await guard.setDragMode('orbit');
await sleep(350);

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
  plotlyCamera(graph._fullLayout.scene.camera),
  plotlyCamera(USER_CAM),
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
  plotlyCamera(graph._fullLayout.scene.camera),
  plotlyCamera(ZOOM_CAM),
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
assert.deepEqual(plotlyCamera(graph._fullLayout.scene.camera), plotlyCamera(USER_CAM));

const coneSource = await readFile(
  new URL('../../seiltanzer/web/js/cone.js', import.meta.url), 'utf8');
const ivSource = await readFile(
  new URL('../../seiltanzer/web/js/iv_surface.js', import.meta.url), 'utf8');
assert.match(coneSource, /createPlotlyCameraGuard/, 'cone must use the shared camera guard');
assert.match(ivSource, /createPlotlyCameraGuard/, 'IV surface must use the shared camera guard');
assert(writes.length >= 2, 'guard must restore the camera after real resets');

console.log(JSON.stringify({
  canonicalPlotlyDefaults: true,
  idleRelayoutLoopAbsent: true,
  turntableSelectionQuiescent: true,
  postPointerFinalRelayoutRetained: true,
  staleInitRejectedDuringSettling: true,
  zoomRetainedDuringWheelSettling: true,
  twoFingerTouchFallbackRetained: true,
  browserGestureOwnershipDisabled: true,
  guardedPanels: ['cone', 'iv_surface'],
}));
