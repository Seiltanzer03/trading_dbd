import assert from 'node:assert/strict';

class FakeElement {
  constructor(id = '') {
    this.id = id;
    this.children = [];
    this.style = {};
    this.classList = {
      toggle: () => {},
      add: () => {},
      remove: () => {}
    };
    this.listeners = new Map();
  }
  addEventListener(name, fn) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(fn);
  }
  querySelector() {
    return new FakeCanvas();
  }
  getBoundingClientRect() {
    return { width: 800, height: 420 };
  }
  replaceChildren() {
    this.children = [];
  }
}

class FakeCanvas {
  constructor() {
    this.width = 800;
    this.height = 420;
    this.style = {};
  }
  getContext() {
    return {
      clearRect: () => {},
      fillRect: () => {},
      strokeRect: () => {},
      fillText: () => {},
      beginPath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      stroke: () => {},
      save: () => {},
      restore: () => {},
      setLineDash: () => {}
    };
  }
}

const elements = new Map();

globalThis.document = {
  querySelector(sel) {
    const id = sel.replace('#', '');
    if (!elements.has(id)) {
      elements.set(id, new FakeElement(id));
    }
    return elements.get(id);
  }
};

globalThis.fetch = async () => ({
  ok: true,
  json: async () => ({
    available: true,
    timestamps: [1000, 2000],
    times_iso: ["2026-08-06T10:00:00Z", "2026-08-06T11:00:00Z"],
    price_grid: [90.0, 100.0, 110.0],
    heatmap: [[0, 0], [10, 20], [0, 0]],
    trajectories: {
      flip: [{ ts: 1000, price: 98.0 }, { ts: 2000, price: 99.0 }],
      call_wall: [{ ts: 1000, price: 110.0, gex: 100 }, { ts: 2000, price: 110.0, gex: 120 }],
      put_wall: [{ ts: 1000, price: 90.0, gex: -100 }, { ts: 2000, price: 90.0, gex: -120 }]
    },
    summary: {
      gamma_regime: "POSITIVE (PINNING / MEAN REVERSION)",
      flip: { price: 99.0, dist: 1.0, migration_6h: 1.0 },
      call_wall: { price: 110.0, dist: 10.0, migration_6h: 0.0, r_per_hour: 0.0 },
      put_wall: { price: 90.0, dist: 10.0, migration_6h: 0.0, r_per_hour: 0.0 },
      take_path: "CLEAR",
      path_pressure: 0.5,
      authority: "context_only",
      independent_vote: false
    }
  })
});

const { initGex, updateGex, updateLiveGex } = await import('../../seiltanzer/web/js/gex.js');

initGex();

const ridgeMock = {
  available: true,
  scale: 1,
  price: 100,
  proxy_spot_current: 380,
  proxy_transform: 'direct',
  snapshots: [
    {
      gex: {
        available: true,
        strikes: [90, 100, 110],
        net: [-100, 50, 300],
        zero_flip: 98,
        top: [{ strike: 110, gex: 300 }]
      }
    }
  ]
};

await updateGex(ridgeMock);
updateLiveGex({ price: 101 });

console.log('GEX Smoke Test Passed Successfully');
