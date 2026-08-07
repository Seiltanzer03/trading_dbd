import assert from 'node:assert/strict';
import {
  localTouchClock,
  termVarianceFraction,
  invertTermVariance,
} from '../../seiltanzer/web/js/touch_clock.js';

// Variance integral must be monotone and invertible.
let prev = -1;
for (let i = 0; i <= 100; i++) {
  const t = i / 100;
  const v = termVarianceFraction(t, 0.4);
  assert.ok(v >= prev - 1e-12);
  prev = v;
  if (v > 0 && v < 1) {
    assert.ok(Math.abs(invertTermVariance(v, 0.4) - t) < 1e-8);
  }
}

const common = {
  available: true,
  T: 2.41,
  sigma_R: 4.0,
  horizon_years: 14.1 / 365,
  term_slope: 0.25,
  p_stop: 0.90,
  p_take: 0.10,
};

const nearStop = localTouchClock({ ...common, r0: -0.65, rv_iv_ratio: 1.0 });
const farStop = localTouchClock({ ...common, r0: -0.10, rv_iv_ratio: 1.0 });
assert.equal(nearStop.barrier, 'stop');
assert.equal(farStop.barrier, 'stop');
assert.ok(nearStop.median_years < farStop.median_years,
  'moving closer to stop must shorten the touch clock');

const optionPace = localTouchClock({ ...common, r0: -0.65, rv_iv_ratio: 1.0 });
const fasterRealized = localTouchClock({ ...common, r0: -0.65, rv_iv_ratio: 1.4 });
assert.ok(fasterRealized.median_years < optionPace.median_years,
  'RV above IV must shorten near-term calendar touch time');

// Tight-distance regression: the clock must not remain tied to a 14-day
// expiry when the active barrier is only a small fraction of 1R away.
const tight = localTouchClock({
  ...common,
  r0: -0.92,
  sigma_R: 5.0,
  rv_iv_ratio: 1.3,
  term_slope: 0.15,
});
assert.ok(tight.median_years * 365 * 24 < 2.0,
  `tight stop clock unexpectedly slow: ${tight.median_years * 365 * 24}h`);

console.log(JSON.stringify({
  nearStopHours: nearStop.median_years * 365 * 24,
  farStopHours: farStop.median_years * 365 * 24,
  fasterRealizedHours: fasterRealized.median_years * 365 * 24,
  tightHours: tight.median_years * 365 * 24,
}));
