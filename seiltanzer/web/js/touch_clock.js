// Local first-touch calendar clock for the Probability Cone/Fan.
//
// Barrier probabilities remain the option-implied competing first-passage MC.
// This helper only converts the current distance-to-barrier into a useful
// calendar-time P50 using the variance clock that is visible *now*:
//   - sigma_R: option-implied full-horizon standard deviation in R;
//   - term_slope: deterministic redistribution of variance through the horizon;
//   - rv_iv_ratio: current realized/implied volatility ratio.
//
// For a one-sided Brownian first-passage time, P(tau <= t)=0.5 when
// distance / (sigma * sqrt(variance_time)) = Phi^-1(0.75).
// We invert the same term-structure variance integral used by fan.js.

const Z75 = 0.6744897501960817;

function finite(v) {
  return Number.isFinite(Number(v));
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, Number(v)));
}

export function termVarianceFraction(tau, termSlope = 0) {
  const t = clamp(tau, 0, 1);
  const s = clamp(termSlope || 0, -0.6, 0.6);
  // Same linear forward-vol schedule as rn_cone/fan.js:
  // g(t)=a+b*t, RMS-normalised so total variance at t=1 is unchanged.
  const a = 1 - s;
  const b = 2 * s;
  const total = a * a + a * b + b * b / 3;
  const partial = a * a * t + a * b * t * t + b * b * t * t * t / 3;
  return total > 1e-12 ? Math.max(0, partial / total) : t;
}

export function invertTermVariance(targetVarianceFrac, termSlope = 0) {
  const target = Number(targetVarianceFrac);
  if (!(target > 0)) return 0;
  if (target >= 1) return target === 1 ? 1 : null;
  let lo = 0, hi = 1;
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (termVarianceFraction(mid, termSlope) < target) lo = mid;
    else hi = mid;
  }
  return (lo + hi) / 2;
}

export function localTouchClock(cone) {
  if (!cone || !finite(cone.horizon_years) || !(Number(cone.horizon_years) > 0)
      || !finite(cone.sigma_R) || !(Number(cone.sigma_R) > 0)
      || !finite(cone.r0) || !finite(cone.T)) return null;

  const horizon = Number(cone.horizon_years);
  const sigmaR = Number(cone.sigma_R);
  const r = Number(cone.r0);
  const T = Number(cone.T);
  const stopDistance = Math.max(1e-9, r + 1);
  const takeDistance = Math.max(1e-9, T - r);
  const pStop = finite(cone.p_stop) ? Number(cone.p_stop) : null;
  const pTake = finite(cone.p_take) ? Number(cone.p_take) : null;

  // If one competing barrier clearly dominates, its touch clock is the useful
  // operational clock. Otherwise use the geometrically nearest barrier.
  let barrier = stopDistance <= takeDistance ? 'stop' : 'take';
  if (pStop != null && pTake != null) {
    if (pStop >= 0.60 && pStop > pTake) barrier = 'stop';
    else if (pTake >= 0.60 && pTake > pStop) barrier = 'take';
  }
  const distance = barrier === 'stop' ? stopDistance : takeDistance;

  // Near-term calendar time should react to the realized pace that the fan
  // already shows as its dashed envelope. Keep the correction bounded because
  // RV is noisy; it changes the clock, never the option barrier probability.
  const rvIv = finite(cone.rv_iv_ratio) ? clamp(cone.rv_iv_ratio, 0.50, 2.50) : 1;
  const varianceNeeded = (distance / (Z75 * sigmaR)) ** 2;
  const optionVarianceTarget = varianceNeeded / (rvIv * rvIv);
  const tau = invertTermVariance(optionVarianceTarget, cone.term_slope || 0);
  const medianYears = tau == null ? null : tau * horizon;

  return {
    barrier,
    distance_r: distance,
    median_years: medianYears,
    tau,
    rv_iv_ratio: rvIv,
    variance_target: optionVarianceTarget,
    source: 'local_first_touch_variance_clock',
  };
}

export function applyLocalTouchClock(cone) {
  if (!cone || typeof cone !== 'object') return cone;
  const original = finite(cone.model_median_years)
    ? Number(cone.model_median_years)
    : (finite(cone.median_years) ? Number(cone.median_years) : null);
  const clock = localTouchClock(cone);
  if (!clock) return cone;

  cone.model_median_years = original;
  cone.touch_clock = clock;
  // When the local P50 lies beyond the option horizon, keep the original
  // resolved-path median rather than inventing a finite in-horizon touch.
  if (clock.median_years != null && Number.isFinite(clock.median_years)) {
    cone.median_years = clock.median_years;
  }
  return cone;
}
