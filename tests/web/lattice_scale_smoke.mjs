import assert from 'node:assert/strict';
globalThis.requestAnimationFrame = () => 0;
const {
  computeFocusDomain,
  rebinDistribution,
  empiricalCounts,
  deterministicTarget,
  empiricalKernelDistribution,
  totalVariationDistance,
  empiricalMoments,
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

const kde = empiricalKernelDistribution(sampleA, rebinned.edges);
const tv = totalVariationDistance(kde, rebinned.probs);
assert.ok(tv < 0.10, `landed-ball KDE must represent current RND; TV=${tv}`);
const moments = empiricalMoments(sampleA);
assert.ok(Number.isFinite(moments.mean) && Number.isFinite(moments.sigma)
  && Number.isFinite(moments.skew), 'landed balls must produce usable empirical moments');

const shifted = rebinned.probs.map((p, i) => p * (i + 1));
const shiftedTotal = shifted.reduce((a, b) => a + b, 0);
for (let i = 0; i < shifted.length; i++) shifted[i] /= shiftedTotal;
const baseMean = sampleA.reduce((sum, value) => sum + value, 0) / sampleA.length;
const shiftedSample = Array.from({ length: 240 }, (_, i) => deterministicTarget(shifted, rebinned.edges, i));
const shiftedMean = shiftedSample.reduce((sum, value) => sum + value, 0) / shiftedSample.length;
assert.ok(shiftedMean > baseMean, 'balls must move with the updated current distribution');

const ball = {
  dirs: Array.from({ length: 9 }, (_, i) => i % 2 === 0),
  seg: 0, t: 0, rights: 0, speed: 1, impacted: false, impactMs: 0,
};
let landed = false;
for (let i = 0; i < 20 && !landed; i++) {
  landed = advanceBallKinematics(ball, 100, 9).landed;
}
assert.equal(landed, true, 'ball must complete every peg row and the final landing leg');
assert.equal(ball.seg, 9);
assert.equal(ball.impacted, true);
assert.equal(advanceBallKinematics(ball, 100, 9).expired, false,
  'landed ball must remain visible during impact hold');
assert.equal(advanceBallKinematics(ball, 130, 9).expired, true,
  'moving sprite may disappear only after its landed contribution is visible');

console.log(JSON.stringify({ domain, visibleMass: rebinned.visibleMass, maxError, tv, moments }));
