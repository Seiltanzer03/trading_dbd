import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = () => 0;

const {
  histogramQuantile,
  buildConditionalMedianPath,
  interpolateMedianOffset,
  liveImpulseShape,
} = await import('../../seiltanzer/web/js/fan.js');

const edges = [-1, 0, 1, 2];
assert.equal(histogramQuantile([0.2, 0.6, 0.2], edges, 0.5), 0.5);

const path = buildConditionalMedianPath({
  r0: 0.4,
  edges,
  times_frac: [0.2, 0.6, 1.0],
  density: [
    [0.2, 0.6, 0.2], // median 0.50 -> +0.10R from r0
    [0.5, 0.4, 0.1], // median 0.00 -> -0.40R from r0
    [0, 0, 0],       // no survivors: retain previous reliable center
  ],
});

assert.equal(path[0].tau, 0);
assert.equal(path[0].offset, 0);
assert.ok(path[1].offset > 0, 'early option center should point upward');
assert.ok(path[2].offset < 0, 'later option center should be able to bend downward');
assert.equal(path[3].offset, path[2].offset,
  'very low surviving mass must retain the last reliable median');
assert.ok(interpolateMedianOffset(path, 0.4) < path[1].offset,
  'interpolation must follow the curved median path');

assert.equal(liveImpulseShape(0), 0);
assert.ok(Math.abs(liveImpulseShape(0.18) - 1) < 1e-12,
  'live impulse should peak near the early decay point');
assert.ok(liveImpulseShape(1) < 0.08,
  'live tape must not be extrapolated through the whole option horizon');

console.log(JSON.stringify({ path, impulseAtHorizon: liveImpulseShape(1) }));
