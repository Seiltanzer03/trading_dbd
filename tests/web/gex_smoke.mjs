import assert from 'node:assert/strict';

class FakeGradient { addColorStop() {} }
class FakeContext {
  setTransform() {}
  clearRect() {}
  fillRect() {}
  strokeRect() {}
  fillText() {}
  beginPath() {}
  moveTo() {}
  lineTo() {}
  stroke() {}
  fill() {}
  arc() {}
  save() {}
  restore() {}
  setLineDash() {}
  drawImage() {}
  createLinearGradient() { return new FakeGradient(); }
  createRadialGradient() { return new FakeGradient(); }
  measureText(text) { return { width: String(text).length * 6 }; }
}

class FakeElement {
  constructor(id = '', tag = 'div') {
    this.id = id;
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.listeners = new Map();
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} };
  }
  addEventListener(name, fn) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(fn);
  }
  click() { for (const fn of this.listeners.get('click') || []) fn({}); }
  appendChild(child) { this.children.push(child); return child; }
  insertBefore(child, before) {
    if (!before) return this.appendChild(child);
    const i = this.children.indexOf(before);
    if (i < 0) return this.appendChild(child);
    this.children.splice(i, 0, child); return child;
  }
  replaceChildren(...children) { this.children = [...children]; }
  remove() {}
  getBoundingClientRect() { return { width: 800, height: 420 }; }
  querySelector(sel) {
    if (sel.includes('canvas')) return this.children.find((c) => c.tagName === 'CANVAS') || null;
    if (sel.includes('js-plotly-plot')) return null;
    return null;
  }
  querySelectorAll(sel) {
    if (sel.includes('canvas')) return this.children.filter((c) => c.tagName === 'CANVAS');
    return [];
  }
}

class FakeCanvas extends FakeElement {
  constructor() { super('', 'canvas'); this.width = 800; this.height = 420; this.ctx = new FakeContext(); }
  getContext() { return this.ctx; }
}

const elements = new Map();
const head = new FakeElement('head', 'head');
function getElement(id) {
  if (!elements.has(id)) elements.set(id, new FakeElement(id));
  return elements.get(id);
}

globalThis.document = {
  head,
  querySelector(sel) { return getElement(sel.replace('#', '')); },
  createElement(tag) { return tag.toLowerCase() === 'canvas' ? new FakeCanvas() : new FakeElement('', tag); }
};
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};

globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({
    available: true,
    timestamps: [1000, 2000],
    times_iso: ['2026-08-06T10:00:00Z', '2026-08-06T11:00:00Z'],
    price_grid: [90.0, 100.0, 110.0],
    plot_range: [88, 116],
    heatmap: [[-20, -10], [10, 20], [90, 120]],
    trajectories: {
      flip: [{ ts: 1000, price: 98.0 }, { ts: 2000, price: 99.0 }],
      call_wall: [{ ts: 1000, price: 110.0, gex: 100 }, { ts: 2000, price: 110.0, gex: 120 }],
      put_wall: [{ ts: 1000, price: 90.0, gex: -100 }, { ts: 2000, price: 90.0, gex: -120 }]
    },
    path_pressure_history: [{ ts: 1000, obstruction: .2 }, { ts: 2000, obstruction: .4 }],
    summary: {
      gamma_regime: 'POSITIVE / PINNING CONTEXT',
      current_price: 100,
      snapshot_count: 2,
      history_hours: 1,
      flip: { price: 99.0, migration_6h: null, persistence: .5 },
      call_wall: { price: 110.0, migration_6h: null, persistence: .5, strength: 1 },
      put_wall: { price: 90.0, migration_6h: null, persistence: 1, strength: 1 },
      take_path: 'OBSTRUCTED · CALL WALL IN PATH',
      corridor_state: 'THIN FRICTION',
      obstruction_score: .4,
      authority: 'context_only',
      independent_vote: false
    }
  })
});
globalThis.window = { fetch: (...args) => globalThis.fetch(...args) };

const { initGex, updateGex, updateLiveGex } = await import('../../seiltanzer/web/js/gex.js');

initGex();
const ridgeMock = {
  available: true,
  scale: 1,
  price: 100,
  proxy_spot_current: 380,
  proxy_transform: 'direct',
  trade: { entry: 100, stop: 95, take: 112, direction: 'long' },
  snapshots: [{ gex: { available: true, strikes: [90, 100, 110], net: [-100, 50, 300], zero_flip: 98 } }]
};

await updateGex(ridgeMock);
updateLiveGex({ price: 101, trade: ridgeMock.trade });

getElement('btn-gex-snapshot').click();
getElement('btn-gex-migration').click();
getElement('btn-gex-snapshot').click();
const holder = getElement('gex-evol-canvas');
assert.ok(holder.children.length <= 1, `renderer leak: ${holder.children.length} children`);
assert.ok(head.children.some((n) => n.id === 'premium-analytics-theme'), 'premium theme should install once');

console.log('GEX Smoke Test Passed Successfully');
