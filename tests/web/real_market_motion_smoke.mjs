import assert from 'node:assert/strict';

const {
  advanceMeasuredPhase,
  correlationMotionRate,
  dampedMotion,
  observedMotionDecay,
  waveletEnergyTransfer,
} = await import('../../seiltanzer/web/js/real_market_motion.js');

let state = { velocity: 0, phase: 0 };
for (let i = 0; i < 8; i++) state = dampedMotion(state, 0.9, 0.1);
assert.ok(state.velocity > 0, 'a measured shock must create motion');
const shockVelocity = state.velocity;
for (let i = 0; i < 60; i++) state = dampedMotion(state, 0, 0.1);
assert.ok(Math.abs(state.velocity) < shockVelocity * 0.02, 'unchanged inputs must damp motion back to rest');

assert.ok(Math.abs(advanceMeasuredPhase(0.42, 0, 0.2) - 0.42) < 1e-12, 'clock time alone must not advance a market packet');
assert.ok(advanceMeasuredPhase(0.42, 0.8, 0.2) > 0.42, 'measured change must advance a packet');

const transfer = waveletEnergyTransfer([
  { ts: 1_000, micro: 52, intraday: 31, macro: 17 },
  { ts: 2_800, micro: 44, intraday: 39, macro: 17 },
]);
assert.equal(transfer.source, 'micro');
assert.equal(transfer.destination, 'intraday');
assert.equal(transfer.ratePpPer30m, 8);
assert.ok(transfer.magnitude > 0);

const flat = waveletEnergyTransfer([
  { ts: 1_000, micro: 40, intraday: 40, macro: 20 },
  { ts: 2_800, micro: 40, intraday: 40, macro: 20 },
]);
assert.equal(flat.magnitude, 0);

assert.equal(correlationMotionRate({}), 0);
assert.ok(correlationMotionRate({ delta_5m: 0.1 }) > 0);
assert.ok(observedMotionDecay(40, 5) < 0.005, 'an old observation must not animate forever');

console.log(JSON.stringify({ stationaryDecays: true, shockMoves: true, waveletRateIsReal: true }));
