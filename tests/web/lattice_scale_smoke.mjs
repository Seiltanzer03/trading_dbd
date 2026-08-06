import assert from 'node:assert/strict';
globalThis.requestAnimationFrame = () => 0;
const {
  computeFocusDomain,
  rebinDistribution,
  empiricalCounts,
  deterministicTarget,
} = await import('../../seiltanzer/web/js/lattice.js');

const domain = computeFocusDomain({
  edges: [-34.1, -20, -8, -3, 0, 3, 8, 20, 33.9],
  T: 1.31,
  r: -0.07,
});
assert.equal(domain.lo, -2, 'board keeps one R beyond the -1R stop');
assert.equal(domain.hi, 2.5, 'take+1R stays on a stable 0.25R grid');
assert.ok(domain.lo <= -1 && domain.hi >= 1.31);

const edges = Array.from({ length: 137 }, (_, i) => -34 + i * 0.5);
const probs = edges.slice(0, -1).map((a, i) => {
  const mid = (a + edges[i + 1]) / 2;
  const z = (mid + 0.4) / 2.4;
  return Math.exp(-0.5 * z * z);
});
const total = probs.reduce((a, b) => a + b, 0);
for (let i = 0; i < probs.length; i++) probs[i] /= total;
const rebinned = rebinDistribution(probs, edges, domain.lo, domain.hi, 24);
assert.ok(Math.abs(rebinned.leftTail + rebinned.visibleMass + rebinned.rightTail - 1) < 1e-9);
assert.ok(rebinned.leftTail > 0 && rebinned.rightTail > 0);
assert.ok(rebinned.probs[0] < 0.25 && rebinned.probs.at(-1) < 0.25,
  'outer support must not create giant endpoint bins');
assert.equal(empiricalCounts([-8, -1.8, -0.2, 0.4, 2.2, 9], rebinned.edges)
  .reduce((a, b) => a + b, 0), 4,
'out-of-window samples stay outside ordinary board bins');

const sampleA = Array.from({ length: 240 }, (_, i) => deterministicTarget(rebinned.probs, rebinned.edges, i));
const sampleB = Array.from({ length: 240 }, (_, i) => deterministicTarget(rebinned.probs, rebinned.edges, i));
assert.deepEqual(sampleA, sampleB, 'the same current mass must create the same deterministic ball sequence');
const counts = empiricalCounts(sampleA, rebinned.edges);
const empirical = counts.map((count) => count / sampleA.length);
const maxError = Math.max(...empirical.map((value, i) => Math.abs(value - rebinned.probs[i])));
assert.ok(maxError < 0.035, `deterministic board must converge to current mass; max error=${maxError}`);

const shifted = rebinned.probs.map((p, i) => p * (i + 1));
const shiftedTotal = shifted.reduce((a, b) => a + b, 0);
for (let i = 0; i < shifted.length; i++) shifted[i] /= shiftedTotal;
const baseMean = sampleA.reduce((sum, value) => sum + value, 0) / sampleA.length;
const shiftedSample = Array.from({ length: 240 }, (_, i) => deterministicTarget(shifted, rebinned.edges, i));
const shiftedMean = shiftedSample.reduce((sum, value) => sum + value, 0) / shiftedSample.length;
assert.ok(shiftedMean > baseMean, 'balls must move with the updated current distribution');

console.log(JSON.stringify({ domain, visibleMass: rebinned.visibleMass, maxError }));
