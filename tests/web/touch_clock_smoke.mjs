import assert from 'node:assert/strict';
import {
  authoritativeTouchClock,
  applyAuthoritativeTouchClock,
} from '../../seiltanzer/web/js/touch_clock.js';

const backend = {
  available: true,
  source: 'authoritative_execution_mc',
  time_basis: 'calendar_elapsed',
  horizon_minutes: 600,
  median_status: 'identified',
  median_resolution_minutes: 132,
  median_resolution_years: 132 / (365 * 24 * 60),
  resolved_probability_horizon: 0.72,
};
const cone = {
  median_years: 0.01,
  first_touch_clock: backend,
  // These values must not trigger any browser stochastic arithmetic.
  r0: -0.99, T: 8, sigma_R: 99, rv_iv_ratio: 2.5,
};
const clock = authoritativeTouchClock(cone);
assert.equal(clock.median_minutes, 132);
assert.equal(clock.source, 'authoritative_execution_mc');
applyAuthoritativeTouchClock(cone);
assert.equal(cone.touch_clock.median_minutes, 132);
assert.equal(cone.median_years, backend.median_resolution_years);
assert.equal(cone.model_conditional_median_years, 0.01);

const beyond = {
  first_touch_clock: {
    ...backend, median_status: 'beyond_horizon',
    median_resolution_minutes: null, median_resolution_years: null,
    resolved_probability_horizon: 0.49,
  },
};
applyAuthoritativeTouchClock(beyond);
assert.equal(beyond.median_years, null);
assert.equal(beyond.touch_clock.median_minutes, null);
assert.equal(beyond.touch_clock.resolved_probability_horizon, 0.49);

const unavailable = { first_touch_clock: { available: false } };
applyAuthoritativeTouchClock(unavailable);
assert.equal(unavailable.touch_clock, undefined);
assert.equal(unavailable.median_years, null);

console.log(JSON.stringify({ clock, beyond: beyond.touch_clock }));
