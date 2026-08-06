import assert from 'node:assert/strict';
globalThis.requestAnimationFrame = () => 0;
const {
  computeFocusDomain,
  rebinDistribution,
  empiricalCounts,
  deterministicTarget,
  buildGaltonDistribution,
  advanceBallKinematics,
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

const rebinned = rebinDistribution(probs, edges, domain.lo, domain.hi, 11);
assert.ok(Math.abs(rebinned.leftTail + rebinned.visibleMass + rebinned.rightTail - 1) < 1e-9);
assert.ok(rebinned.leftTail > 0 && rebinned.rightTail > 0);
assert.equal(empiricalCounts([-8, -1.8, -0.2, 0.4, 2.2, 9], rebinned.edges)
  .reduce((a, b) => a + b, 0), 4,
'out-of-window samples stay outside ordinary board bins');

const galton = buildGaltonDistribution({
  probs,
  edges,
  T: 1.31,
  r: -0.07,
  q10: -3.47,
  q50: -0.40,
  q90: 2.68,
});
assert.equal(galton.probs.length, 11, '10 peg decisions must map to exactly 11 bins');
assert.equal(galton.edges.length, 12);
assert.ok(Math.abs(galton.probs.reduce((a, b) => a + b, 0) - 1) < 1e-12);
const peak = Math.max(...galton.probs);
const peakIndex = galton.probs.indexOf(peak);
assert.ok(peakIndex > 0 && peakIndex < galton.probs.length - 1,
  'Galton projection must have an interior bell peak');
for (let i = 1; i <= peakIndex; i++) {
  assert.ok(galton.probs[i] >= galton.probs[i - 1] - 1e-12,
    'left side of the Galton bell must rise toward the mode');
}
for (let i = peakIndex + 1; i < galton.probs.length; i++) {
  assert.ok(galton.probs[i] <= galton.probs[i - 1] + 1e-12,
    'right side of the Galton bell must fall after the mode');
}
assert.ok(galton.probs[0] < peak * 0.45 && galton.probs.at(-1) < peak * 0.45,
  'wide option support must not flatten the Galton board');

const sampleA = Array.from({ length: 440 }, (_, i) => deterministicTarget(galton.probs, galton.edges, i));
const sampleB = Array.from({ length: 440 }, (_, i) => deterministicTarget(galton.probs, galton.edges, i));
assert.deepEqual(sampleA, sampleB, 'the same Galton model must create the same deterministic sequence');
const counts = empiricalCounts(sampleA, galton.edges);
const empirical = counts.map((count) => count / sampleA.length);
const maxError = Math.max(...empirical.map((value, i) => Math.abs(value - galton.probs[i])));
assert.ok(maxError < 0.015, `landed balls must converge to the Galton bell; max error=${maxError}`);

const shifted = buildGaltonDistribution({
  probs,
  edges,
  T: 1.31,
  r: 0.7,
  q10: -1.0,
  q50: 0.75,
  q90: 2.5,
});
const baseMean = sampleA.reduce((sum, value) => sum + value, 0) / sampleA.length;
const shiftedSample = Array.from({ length: 440 }, (_, i) => deterministicTarget(shifted.probs, shifted.edges, i));
const shiftedMean = shiftedSample.reduce((sum, value) => sum + value, 0) / shiftedSample.length;
assert.ok(shiftedMean > baseMean, 'the Galton bell must move with CURRENT RND centre');

const ball = {
  dirs: Array.from({ length: 10 }, (_, i) => i < 6),
  seg: 0, t: 0, rights: 0, speed: 1, impacted: false, impactMs: 0,
};
let landed = false;
for (let i = 0; i < 24 && !landed; i++) {
  landed = advanceBallKinematics(ball, 100, 10).landed;
}
assert.equal(landed, true, 'ball must pass all 10 rows and the final bin segment');
assert.equal(ball.seg, 10);
assert.equal(ball.rights, 6, 'physical path must terminate in its actual right-count bin');
assert.equal(ball.impacted, true);
assert.equal(advanceBallKinematics(ball, 80, 10).expired, false,
  'landed ball remains visible briefly at impact');
assert.equal(advanceBallKinematics(ball, 80, 10).expired, true,
  'moving sprite disappears only after the contribution has landed');

console.log(JSON.stringify({ domain, visibleMass: rebinned.visibleMass, maxError,
  galton: { center: galton.center, sigma: galton.sigma, peakIndex } }));
