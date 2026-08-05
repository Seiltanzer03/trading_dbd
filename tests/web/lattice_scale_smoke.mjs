import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = () => 0;
const {
  computeFocusDomain,
  rebinDistribution,
  empiricalCounts,
} = await import('../../seiltanzer/web/js/lattice.js');

// The pre-PR21 behaviour followed P10/P90 instead of forcing a fixed trade-only
// crop. Stop, take and live r still remain visible and the span stays bounded.
const coarseEdges = Array.from({ length: 12 }, (_, i) => -15 + i * 3);
const coarseProbs = [0.01, 0.02, 0.04, 0.08, 0.16, 0.25, 0.20, 0.12, 0.07, 0.03, 0.02];
const domain = computeFocusDomain({
  edges: coarseEdges,
  T: 2.5,
  r: 0.2,
  q10: -3,
  q90: 5,
});

assert.equal(domain.lo, -2);
assert.equal(domain.hi, 4,
  'quantile-aware window must not collapse back to the fixed -2R…take+1R crop');
assert.ok(domain.lo <= -1 && domain.hi >= 2.5,
  'stop and take must remain visible');
assert.equal(domain.compressed, true);

const rebinned = rebinDistribution(coarseProbs, coarseEdges, domain.lo, domain.hi, 11);
assert.equal(rebinned.probs.length, 11);
assert.equal(rebinned.edges.length, 12);
assert.ok(Math.abs(rebinned.probs.reduce((a, b) => a + b, 0) - 1) < 1e-9,
  'visible shape must be normalized inside the selected window');
assert.ok(Math.abs(
  rebinned.leftTail + rebinned.visibleMass + rebinned.rightTail - 1,
) < 1e-9, 'tail and visible masses must preserve the full distribution');
assert.ok(rebinned.leftTail > 0 && rebinned.rightTail > 0,
  'distant option tails must remain separate diagnostics');
assert.ok(rebinned.probs[0] < 0.5 && rebinned.probs.at(-1) < 0.5,
  'tails must not be folded into giant ordinary edge bins');

// NAS100-like broad support: quantiles shift the window toward the actual mass,
// while the old maximum-span rule prevents the proxy support from flattening the
// entire board.
const nasdaqDomain = computeFocusDomain({
  edges: [-34.1, -25, -15, -8, -3, 0, 3, 8, 15, 25, 33.9],
  T: 1.31,
  r: -0.07,
  q10: -3.8,
  q90: 3.0,
});
assert.equal(nasdaqDomain.lo, -2.25);
assert.equal(nasdaqDomain.hi, 3.25);
assert.ok(nasdaqDomain.lo <= -1 && nasdaqDomain.hi >= 1.31);

// A smooth input density must remain single-peaked after focusing. The window
// may crop tails, but it must not create artificial edge peaks or a flat
// trade-only plateau.
const smoothEdges = Array.from({ length: 137 }, (_, i) => -34 + i * 0.5);
const smoothProbs = smoothEdges.slice(0, -1).map((a, i) => {
  const mid = (a + smoothEdges[i + 1]) / 2;
  const z = (mid + 0.4) / 2.4;
  return Math.exp(-0.5 * z * z);
});
const smoothTotal = smoothProbs.reduce((a, b) => a + b, 0);
for (let i = 0; i < smoothProbs.length; i++) smoothProbs[i] /= smoothTotal;
const smooth = rebinDistribution(
  smoothProbs,
  smoothEdges,
  nasdaqDomain.lo,
  nasdaqDomain.hi,
  11,
);
const peak = Math.max(...smooth.probs);
const peakIndex = smooth.probs.indexOf(peak);
assert.ok(peakIndex > 0 && peakIndex < smooth.probs.length - 1,
  'the visible distribution peak must remain inside the board');
assert.ok(peak > smooth.probs[0] * 1.35 && peak > smooth.probs.at(-1) * 1.35,
  'a smooth central density must not become an edge-dominated or flat profile');

// Landed balls are real R values. Samples outside the current visual window are
// not misrepresented as ordinary edge-bin landings.
const samples = [-3.0, -1.5, -0.3, 0.2, 0.9, 2.7, 4.5];
const countsA = empiricalCounts(samples, smooth.edges);
assert.equal(countsA.reduce((a, b) => a + b, 0), 5,
  'out-of-window samples must not be folded into the first/last bins');

const shifted = computeFocusDomain({
  edges: smoothEdges,
  T: 1.31,
  r: 0.04,
  q10: -3.75,
  q90: 3.05,
});
const shiftedEdges = Array.from(
  { length: 12 },
  (_, i) => shifted.lo + (shifted.hi - shifted.lo) * i / 11,
);
const countsB = empiricalCounts(samples, shiftedEdges);
assert.ok(countsB.reduce((a, b) => a + b, 0) >= 5,
  'small live moves must retain samples that remain inside the stable quantile window');

console.log(JSON.stringify({
  domain,
  nasdaqDomain,
  shifted,
  peakIndex,
  leftTail: smooth.leftTail,
  visibleMass: smooth.visibleMass,
  rightTail: smooth.rightTail,
  visibleBalls: countsB.reduce((a, b) => a + b, 0),
}));
