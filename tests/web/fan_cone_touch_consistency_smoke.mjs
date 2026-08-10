import assert from 'node:assert/strict';

globalThis.requestAnimationFrame = () => 0;
const { applyAuthoritativeTouchClock } = await import(
  '../../seiltanzer/web/js/touch_clock.js');
const { computeFanView } = await import('../../seiltanzer/web/js/fan.js');

const backendP50 = 75 / (365 * 24 * 60);
const cone = {
  available: true,
  horizon_years: 600 / (365 * 24 * 60),
  first_touch_clock: {
    available: true,
    source: 'authoritative_execution_mc',
    time_basis: 'calendar_elapsed',
    horizon_minutes: 600,
    median_status: 'identified',
    median_resolution_minutes: 75,
    median_resolution_years: backendP50,
    resolved_probability_horizon: 0.70,
  },
  times_frac: [0.25, 0.5, 1],
  p_take_by_t: [0.1, 0.2, 0.4],
  p_stop_by_t: [0.1, 0.2, 0.3],
};
applyAuthoritativeTouchClock(cone);
const fan = computeFanView(cone, 'DECISION');
assert.equal(cone.median_years, backendP50);
assert.equal(cone.touch_clock.median_years, backendP50);
assert.equal(fan.source, 'authoritative_first_resolution_p50');
assert.equal(cone.touch_clock.source, 'authoritative_execution_mc');

console.log('Fan P50 === Cone P50 === backend P50');
