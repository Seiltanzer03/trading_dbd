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

assert.equal(domain.lo, -2, 'window must keep 1R of space beyond the -1R stop');
assert.equal(domain.hi, 3.5, 'window must keep 1R of space beyond the take');
assert.equal(domain.compressed, true);

const rebinned = rebinDistribution(probs, edges, domain.lo, domain.hi, 11);
assert.equal(rebinned.probs.length, 11);
assert.equal(rebinned.edges.length, 12);
assert.ok(Math.abs(rebinned.probs.reduce((a, b) => a + b, 0) - 1) < 1e-9,
  'visible shape must be normalized inside the actionable window');
assert.ok(Math.abs(
  rebinned.leftTail + rebinned.visibleMass + rebinned.rightTail - 1,
) < 1e-9, 'tail and visible masses must preserve the full distribution');
assert.ok(rebinned.leftTail > 0 && rebinned.rightTail > 0,
  'distant option tails must be retained as separate diagnostics');
assert.ok(rebinned.probs[0] < 0.5 && rebinned.probs.at(-1) < 0.5,
  'tails must not be folded into giant ordinary edge bins');

const nasdaqDomain = computeFocusDomain({
  edges: [-34.1, -25, -15, -8, -3, 0, 3, 8, 15, 25, 33.9],
  T: 1.31,
  r: -0.07,
});
assert.equal(nasdaqDomain.lo, -2);
assert.equal(nasdaqDomain.hi, 2.5,
  'a short target must still get about 1R of right-side breathing room');

// Landed balls are real R values. Samples outside the current visual window are
// not misrepresented as ordinary edge-bin landings.
const samples = [-3.0, -1.5, -0.3, 0.2, 0.9, 2.7, 4.5];
const countsA = empiricalCounts(samples, rebinned.edges);
assert.equal(countsA.reduce((a, b) => a + b, 0), 5,
  'out-of-window samples must not be folded into the first/last bins');

const shifted = computeFocusDomain({ edges, T: 2.5, r: 0.31 });
const shiftedEdges = Array.from(
  { length: 12 },
  (_, i) => shifted.lo + (shifted.hi - shifted.lo) * i / 11,
);
const countsB = empiricalCounts(samples, shiftedEdges);
assert.equal(countsB.reduce((a, b) => a + b, 0), 5,
  'small live moves must retain all samples that remain inside the stable window');

console.log(JSON.stringify({
  domain,
  nasdaqDomain,
  shifted,
  leftTail: rebinned.leftTail,
  visibleMass: rebinned.visibleMass,
  rightTail: rebinned.rightTail,
  visibleBalls: countsB.reduce((a, b) => a + b, 0),
}));
