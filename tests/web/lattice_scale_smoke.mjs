import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = () => 0;
const {
  computeFocusDomain,
  rebinDistribution,
  empiricalCounts,
} = await import('../../seiltanzer/web/js/lattice.js');

const edges = Array.from({ length: 12 }, (_, i) => -15 + i * 3);
const probs = [0.01, 0.02, 0.04, 0.08, 0.16, 0.25, 0.20, 0.12, 0.07, 0.03, 0.02];
const domain = computeFocusDomain({ edges, T: 2.5, r: 0.2, q10: -3, q90: 5 });

assert.ok(domain.lo <= -1, 'stop must remain visible');
assert.ok(domain.hi >= 2.5, 'take must remain visible');
assert.ok(domain.hi - domain.lo <= 6.01, 'distant tails must not stretch the board');
assert.equal(domain.compressed, true);

const rebinned = rebinDistribution(probs, edges, domain.lo, domain.hi, 11);
assert.equal(rebinned.probs.length, 11);
assert.equal(rebinned.edges.length, 12);
assert.ok(Math.abs(rebinned.probs.reduce((a, b) => a + b, 0) - 1) < 1e-9,
  'rebinning must preserve probability mass');
assert.ok(rebinned.probs[0] > 0 && rebinned.probs.at(-1) > 0,
  'compressed tails must be retained in edge bins');

// Уже упавшие шарики хранятся в R, поэтому при смене визуального масштаба
// их общее число обязано сохраняться, а не сбрасываться в ноль.
const samples = [-2.2, -1.1, -0.3, 0.2, 0.9, 2.7, 4.5];
const countsA = empiricalCounts(samples, rebinned.edges);
const shifted = computeFocusDomain({ edges, T: 2.5, r: 0.31, q10: -2.8, q90: 4.8 });
const shiftedEdges = Array.from(
  { length: 12 },
  (_, i) => shifted.lo + (shifted.hi - shifted.lo) * i / 11,
);
const countsB = empiricalCounts(samples, shiftedEdges);
assert.equal(countsA.reduce((a, b) => a + b, 0), samples.length);
assert.equal(countsB.reduce((a, b) => a + b, 0), samples.length,
  'live rescaling must preserve landed ball count');

console.log(JSON.stringify({
  domain,
  shifted,
  mass: rebinned.probs.reduce((a, b) => a + b, 0),
  landed: countsB.reduce((a, b) => a + b, 0),
}));
