import assert from 'node:assert/strict';

class FakeTarget {
  constructor() {
    this.dom = new Map();
    this.plotly = new Map();
  }
  addEventListener(name, fn) {
    if (!this.dom.has(name)) this.dom.set(name, []);
    this.dom.get(name).push(fn);
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

const graph = new FakeTarget();
const fakeWindow = new FakeTarget();
fakeWindow.PointerEvent = class PointerEvent {};
fakeWindow.visualViewport = new FakeTarget();

const raf = [];
globalThis.requestAnimationFrame = (fn) => {
  raf.push(fn);
  return raf.length;
};
globalThis.window = fakeWindow;
globalThis.document = { querySelector: () => graph };

const INIT_CAM = {
  eye: { x: 0.15, y: 2.3, z: 0.65 },
  up: { x: 0, y: 0, z: 1 },
};
const clone = (value) => value == null ? value : JSON.parse(JSON.stringify(value));
const writes = [];

function emitAfterPlot(el) {
  queueMicrotask(() => el.emit('plotly_afterplot'));
}

fakeWindow.Plotly = {
  newPlot(el, traces, layout) {
    writes.push('newPlot');
    el.data = clone(traces);
    el.layout = clone(layout);
    el._fullLayout = { scene: { camera: clone(layout.scene?.camera || INIT_CAM) } };
    emitAfterPlot(el);
    return Promise.resolve(el);
  },
  react(el, traces, layout) {
    writes.push('react');
    el.data = clone(traces);
    el.layout = clone(layout);
    // Reproduce the mobile regression: a responsive/structural redraw returns
    // the WebGL scene to the initial camera before plotly_afterplot.
    el._fullLayout.scene.camera = clone(INIT_CAM);
    emitAfterPlot(el);
    return Promise.resolve(el);
  },
  restyle() {
    writes.push('restyle');
    return Promise.resolve();
  },
  relayout(el, update) {
    writes.push('relayout');
    if (update['scene.camera']) {
      el._fullLayout.scene.camera = clone(update['scene.camera']);
      if (!el.layout.scene) el.layout.scene = {};
      el.layout.scene.camera = clone(update['scene.camera']);
    }
    el.emit('plotly_relayout', clone(update));
    emitAfterPlot(el);
    return Promise.resolve();
  },
  Plots: {
    resize(el) {
      writes.push('resize');
      el._fullLayout.scene.camera = clone(INIT_CAM);
      if (!el.layout.scene) el.layout.scene = {};
      el.layout.scene.camera = clone(INIT_CAM);
      emitAfterPlot(el);
      return Promise.resolve();
    },
  },
};

const { initCone } = await import(
  `../../seiltanzer/web/js/cone.js?camera-smoke=${Date.now()}`
);

function runFrames(count = 1, start = performance.now()) {
  for (let i = 0; i < count; i++) {
    const frame = raf.shift();
    if (!frame) break;
    frame(start + i * 16.67);
  }
}

function cone(T = 2.5) {
  const edges = Array.from({ length: 22 }, (_, i) => -1 + i * ((T + 1) / 21));
  const row = (mu, sd, mass) => edges.slice(0, -1).map((_, i) => {
    const x = (edges[i] + edges[i + 1]) / 2;
    return mass * Math.exp(-0.5 * ((x - mu) / sd) ** 2);
  });
  return {
    available: true,
    option_anchored: true,
    probability_available: true,
    T,
    r0: 0.05,
    sigma_R: 1.7,
    drift_R: 0.08,
    skew: -0.12,
    edges,
    times_frac: [0.08, 0.24, 0.5, 0.76, 1],
    density: [
      row(0.06, 0.18, 0.95),
      row(0.10, 0.31, 0.88),
      row(0.16, 0.48, 0.70),
      row(0.22, 0.66, 0.48),
      row(0.28, 0.82, 0.31),
    ],
    p_stop_by_t: [0.01, 0.03, 0.09, 0.17, 0.25],
    p_take_by_t: [0.00, 0.01, 0.04, 0.10, 0.18],
    horizon_years: 1 / 365,
    median_years: 0.5 / 365,
    p_take: 0.18,
    p_stop: 0.25,
    unresolved: 0.57,
  };
}

const api = initCone('#cone-plot');
api.setData(cone(), { r: 0.05 });
await Promise.resolve();
runFrames(4);
await new Promise((resolve) => setTimeout(resolve, 30));

const surface = graph.data[0];
const rowPeaks = surface.z.map((row) => Math.max(...row));
const minZ = Math.min(...surface.z.flat());
assert(surface.z.flat().every(Number.isFinite), 'probability sheet must contain only finite values');
assert(minZ >= -1e-12, 'probability sheet must not fall below the floor axis');
assert(
  rowPeaks.at(-1) < rowPeaks[0] - 0.05,
  'one global density scale must form a widening cone, not an equal-height awning',
);
const q20 = graph.data[6].x;
const q80 = graph.data[7].x;
assert(
  q80.at(-1) - q20.at(-1) > q80[0] - q20[0],
  'the central probability envelope must widen with time',
);

const draggedCamera = {
  eye: { x: -0.72, y: 0.48, z: 0.56 },
  center: { x: 0.08, y: -0.04, z: 0.02 },
  up: { x: 0, y: 0, z: 1 },
};
graph.dispatch('pointerdown');
graph._fullLayout.scene.camera = clone(draggedCamera);
graph.emit('plotly_relayouting', {
  'scene.camera.eye.x': draggedCamera.eye.x,
  'scene.camera.eye.y': draggedCamera.eye.y,
  'scene.camera.eye.z': draggedCamera.eye.z,
  'scene.camera.center': draggedCamera.center,
});

const writesAtPointerDown = writes.length;
api.setData(cone(3), { r: 0.34 });
api.updateLive({ r: 0.34 });
await new Promise((resolve) => setTimeout(resolve, 420));
runFrames(12);
assert.equal(
  writes.length,
  writesAtPointerDown,
  'live updates must perform zero Plotly writes while the pointer is held',
);

fakeWindow.dispatch('pointerup');
runFrames(8);
await new Promise((resolve) => setTimeout(resolve, 360));
runFrames(12);
await Promise.resolve();
assert.deepEqual(
  graph._fullLayout.scene.camera,
  draggedCamera,
  'rotation and zoom must survive the deferred structural refresh',
);

fakeWindow.dispatch('resize');
runFrames(4);
await new Promise((resolve) => setTimeout(resolve, 220));
await Promise.resolve();
assert.deepEqual(
  graph._fullLayout.scene.camera,
  draggedCamera,
  'rotation and zoom must survive a mobile responsive resize',
);

const zoomedCamera = {
  eye: { x: -0.38, y: 0.25, z: 0.31 },
  center: { x: 0.12, y: -0.07, z: 0.04 },
  up: { x: 0, y: 0, z: 1 },
};
graph._fullLayout.scene.camera = clone(zoomedCamera);
graph.emit('plotly_relayouting', {
  'scene.camera.eye.x': zoomedCamera.eye.x,
  'scene.camera.eye.y': zoomedCamera.eye.y,
  'scene.camera.eye.z': zoomedCamera.eye.z,
  'scene.camera.center': zoomedCamera.center,
});
graph.emit('plotly_relayout', { 'scene.camera': clone(zoomedCamera) });
api.setData(cone(4), { r: 0.41 });
runFrames(6);
await new Promise((resolve) => setTimeout(resolve, 360));
runFrames(10);
await Promise.resolve();
assert.deepEqual(
  graph._fullLayout.scene.camera,
  zoomedCamera,
  'a pinch/scroll zoom must remain fixed after the next model rebuild',
);
assert.equal(graph.layout.scene.uirevision, 'probability-cone-camera-v3');

console.log(JSON.stringify({
  cameraRetainedAfterReact: true,
  cameraRetainedAfterResize: true,
  zoomRetained: true,
  writesWhileHeld: 0,
  firstPeak: Number(rowPeaks[0].toFixed(3)),
  horizonPeak: Number(rowPeaks.at(-1).toFixed(3)),
  minZ: Number(minZ.toFixed(3)),
  envelopeWidens: true,
}));
