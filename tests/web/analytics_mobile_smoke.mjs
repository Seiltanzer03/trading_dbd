import assert from 'node:assert/strict';

class ClassList {
  constructor() { this.values = new Set(); }
  toggle(name, enabled) { if (enabled) this.values.add(name); else this.values.delete(name); }
  add(name) { this.values.add(name); }
  remove(name) { this.values.delete(name); }
  contains(name) { return this.values.has(name); }
}

class FakePanel {
  constructor(id) {
    this.id = id;
    this.style = {};
    this.dataset = {};
    this.classList = new ClassList();
  }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  querySelectorAll() { return []; }
}

const listeners = new Map();
function addListener(type, fn) {
  if (!listeners.has(type)) listeners.set(type, new Set());
  listeners.get(type).add(fn);
}
function dispatch(event) {
  for (const fn of listeners.get(event.type) || []) fn(event);
  return true;
}

const root = { classList: new ClassList(), style: {}, dataset: {} };
const body = { style: {}, dataset: {} };
const panels = new Map([
  ['#panel-gex-evol', new FakePanel('panel-gex-evol')],
  ['#panel-macro-regime', new FakePanel('panel-macro-regime')],
  ['#panel-wavelet', new FakePanel('panel-wavelet')],
  ['#panel-correlation', new FakePanel('panel-correlation')],
]);

globalThis.window = {
  innerWidth: 390,
  innerHeight: 844,
  devicePixelRatio: 3,
  matchMedia: () => ({ matches: true }),
  addEventListener: addListener,
  dispatchEvent: dispatch,
  visualViewport: { addEventListener() {} },
  CustomEvent: class CustomEvent { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
};
globalThis.document = {
  documentElement: root,
  body,
  querySelector(selector) { return panels.get(selector) || null; },
  querySelectorAll() { return []; },
};

globalThis.IntersectionObserver = class IntersectionObserver {
  constructor(callback) { this.callback = callback; }
  observe(target) {
    target.classList.add('analytics-offscreen');
    this.callback([{ target, isIntersecting: false }]);
  }
};

const {
  isAnalyticsMobile,
  analyticsMobileDpr,
  decimateSurfaceTrace,
  installAnalyticsMobileRuntime,
} = await import('../../seiltanzer/web/js/analytics_mobile.js');

assert.equal(isAnalyticsMobile(), true);
assert.equal(analyticsMobileDpr(), 1.5);

const rows = 80;
const cols = 120;
const x = Array.from({ length: cols }, (_, i) => i);
const y = Array.from({ length: rows }, (_, i) => i * 10);
const z = Array.from({ length: rows }, (_, r) => Array.from({ length: cols }, (_, c) => r * 1000 + c));
const surfacecolor = z.map((row) => row.map((v) => v / 100000));
const trace = { type: 'surface', x, y, z, surfacecolor, name: 'SURFACE' };
const out = decimateSurfaceTrace(trace, 30, 44);

assert.notEqual(out, trace);
assert.ok(out.z.length <= 30);
assert.ok(out.z[0].length <= 44);
assert.equal(out.x[0], 0);
assert.equal(out.x.at(-1), cols - 1);
assert.equal(out.y[0], 0);
assert.equal(out.y.at(-1), (rows - 1) * 10);
assert.equal(out.z[0][0], 0);
assert.equal(out.z.at(-1).at(-1), (rows - 1) * 1000 + (cols - 1));
assert.equal(out.surfacecolor.length, out.z.length);
assert.equal(out.surfacecolor[0].length, out.z[0].length);

const small = { type: 'surface', z: [[1, 2], [3, 4]], x: [0, 1], y: [0, 1] };
assert.equal(decimateSurfaceTrace(small, 30, 44), small);

installAnalyticsMobileRuntime();
const gexPanel = panels.get('#panel-gex-evol');
const originalContentVisibility = gexPanel.style.contentVisibility;
window.dispatchEvent(new window.CustomEvent('seiltanzer:3d-busy'));
assert.equal(root.classList.contains('analytics-3d-busy'), true);
assert.equal(gexPanel.dataset.mobileSuspended, '1');
assert.equal(gexPanel.style.pointerEvents, 'none');
assert.equal(gexPanel.style.contentVisibility, originalContentVisibility);
assert.equal(root.style.overscrollBehaviorY, 'none');
assert.equal(root.style.overflowAnchor, 'none');

window.__seiltanzer3dBusy = false;
window.dispatchEvent(new window.CustomEvent('seiltanzer:3d-idle'));
assert.equal(root.classList.contains('analytics-3d-busy'), false);
assert.equal(gexPanel.dataset.mobileSuspended, undefined);
assert.equal(gexPanel.style.pointerEvents, '');
assert.equal(gexPanel.style.contentVisibility, originalContentVisibility);
assert.equal(root.style.overscrollBehaviorY, '');
assert.equal(root.style.overflowAnchor, '');

console.log(JSON.stringify({
  mobile: isAnalyticsMobile(),
  dpr: analyticsMobileDpr(),
  rowsBefore: rows,
  rowsAfter: out.z.length,
  colsBefore: cols,
  colsAfter: out.z[0].length,
  endpointsPreserved: true,
  layoutStableDuring3d: true,
  pageGestureLockRestored: true,
}));
