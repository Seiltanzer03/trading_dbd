import assert from 'node:assert/strict';
globalThis.requestAnimationFrame = () => 0;
const {
  computeFocusDomain,
  rebinDistribution,
  empiricalCounts,
  savePersistedLattice,
  loadPersistedLattice,
  clearPersistedLattice,
} = await import('../../seiltanzer/web/js/lattice.js');

const domain = computeFocusDomain({
  edges: [-34.1, -20, -8, -3, 0, 3, 8, 20, 33.9],
  T: 1.31,
  r: -0.07,
  q10: -3.8,
  q50: -0.4,
  q90: 3.0,
});
assert.equal(domain.lo, -2, 'board keeps one R of space beyond the -1R stop');
assert.equal(domain.hi, 2.5, 'take+1R is quantized to a stable 0.25R grid');
assert.ok(domain.lo <= -1 && domain.hi >= 1.31);

const edges = Array.from({ length: 137 }, (_, i) => -34 + i * 0.5);
const probs = edges.slice(0, -1).map((a, i) => {
  const mid = (a + edges[i + 1]) / 2;
  const z = (mid + 0.4) / 2.4;
  return Math.exp(-0.5 * z * z);
});
const total = probs.reduce((a, b) => a + b, 0);
for (let i = 0; i < probs.length; i++) probs[i] /= total;
const rebinned = rebinDistribution(probs, edges, domain.lo, domain.hi, 11);
assert.ok(Math.abs(rebinned.leftTail + rebinned.visibleMass + rebinned.rightTail - 1) < 1e-9);
assert.ok(rebinned.leftTail > 0 && rebinned.rightTail > 0);
assert.ok(rebinned.probs[0] < 0.5 && rebinned.probs.at(-1) < 0.5,
  'outer support must not create giant edge bins');
assert.equal(empiricalCounts([-8, -1.8, -0.2, 0.4, 2.2, 9], rebinned.edges)
  .reduce((a, b) => a + b, 0), 4,
'out-of-window samples stay in tail pockets instead of edge bins');

class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}
const storage = new MemoryStorage();
const persisted = {
  samples: [-1.4, -0.2, 0.6, 1.9],
  dropped: 8,
  green: 2,
  leftTailDropped: 3,
  rightTailDropped: 1,
};
assert.equal(savePersistedLattice('trade-42', persisted, storage), true);
assert.deepEqual(loadPersistedLattice('trade-42', storage), persisted);
assert.equal(clearPersistedLattice('trade-42', storage), true);
assert.equal(loadPersistedLattice('trade-42', storage), null);

console.log(JSON.stringify({ domain, visibleMass: rebinned.visibleMass }));
