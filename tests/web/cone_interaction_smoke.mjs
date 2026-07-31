import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const sourcePath = new URL('../../seiltanzer/web/js/cone.js', import.meta.url);
const source = (await readFile(sourcePath, 'utf8')).replace(
  "import { approach } from './anim.js';",
  'const approach = (cur, target, dt, speed = 8) => ' +
    '(cur == null ? target : cur + (target - cur) * (1 - Math.exp(-speed * dt)));',
);
const { initCone } = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

class FakeTarget {
  constructor() {
    this.dom = new Map();
    this.plotly = new Map();
  }
  addEventListener(name, fn) {
    if (!this.dom.has(name)) this.dom.set(name, []);
    this.dom.get(name).push(fn);
  }
  dispatch(name) {
    for (const fn of this.dom.get(name) || []) fn({ type: name });
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

const raf = [];
globalThis.requestAnimationFrame = (fn) => {
  raf.push(fn);
  return raf.length;
};
globalThis.window = fakeWindow;
globalThis.document = { querySelector: () => graph };

const writes = [];
const clone = (v) => JSON.parse(JSON.stringify(v));
fakeWindow.Plotly = {
  newPlot(el, traces, layout) {
    writes.push('newPlot');
    el.data = clone(traces);
    el.layout = clone(layout);
    el._fullLayout = { scene: { camera: clone(layout.scene.camera) } };
    return Promise.resolve(el);
  },
  react(el, traces, layout) {
    writes.push('react');
    el.data = clone(traces);
    el.layout = clone(layout);
    el._fullLayout.scene.camera = clone(layout.scene.camera);
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
    }
    return Promise.resolve();
  },
  Plots: { resize() {} },
};

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
  };
}

const api = initCone('#cone-plot');
api.setData(cone(), { r: 0.05 });
runFrames(3);

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
  eye: { x: -1.34, y: 0.72, z: 1.18 },
  center: { x: 0.08, y: -0.04, z: 0.02 },
  up: { x: 0, y: 0, z: 1 },
};
graph.dispatch('pointerdown');
graph._fullLayout.scene.camera = clone(draggedCamera);
graph.emit('plotly_relayouting', { 'scene.camera.eye.x': draggedCamera.eye.x });

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
runFrames(2);
await new Promise((resolve) => setTimeout(resolve, 180));
runFrames(8);
assert.deepEqual(
  graph._fullLayout.scene.camera,
  draggedCamera,
  'the released camera must survive the deferred structural refresh',
);
assert.equal(graph.layout.scene.uirevision, 'probability-cone-camera-v3');

console.log(JSON.stringify({
  cameraRetained: true,
  writesWhileHeld: 0,
  firstPeak: Number(rowPeaks[0].toFixed(3)),
  horizonPeak: Number(rowPeaks.at(-1).toFixed(3)),
  minZ: Number(minZ.toFixed(3)),
  envelopeWidens: true,
}));
