import assert from 'node:assert/strict';

const listeners = new Map();
const fakePanel = {
  classList: { contains: () => false, toggle: () => {} },
  querySelectorAll: () => [],
  style: {},
  dataset: {},
};

globalThis.window = {
  innerWidth: 390,
  devicePixelRatio: 3,
  matchMedia: () => ({ matches: true }),
  addEventListener(name, fn) { listeners.set(name, fn); },
  dispatchEvent() {},
  visualViewport: { addEventListener() {} },
};
globalThis.document = {
  // Intentionally no documentElement: the Node smoke harness is a partial DOM.
  querySelector(sel) { return sel === 'html' ? null : fakePanel; },
  querySelectorAll() { return []; },
  addEventListener() {},
  body: null,
};
globalThis.CustomEvent = class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } };

const mobile = await import('../../seiltanzer/web/js/analytics_mobile.js');
assert.equal(mobile.isAnalyticsMobile(), true);
assert.equal(mobile.analyticsMobileDpr(), 1.5);
assert.doesNotThrow(() => mobile.installAnalyticsMobileRuntime());
assert.ok(listeners.has('resize'));
assert.ok(listeners.has('seiltanzer:3d-busy'));
assert.ok(listeners.has('seiltanzer:3d-idle'));

console.log('Advanced analytics mobile smoke passed');
