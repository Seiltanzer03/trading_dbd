// First-touch clock adapter. All stochastic arithmetic lives on the backend in
// the authoritative execution MC; this module only normalizes its display shape.

function finite(value) {
  return value !== null && value !== undefined && value !== ''
    && Number.isFinite(Number(value));
}

export function authoritativeTouchClock(cone) {
  if (!cone || typeof cone !== 'object') return null;
  const source = cone.first_touch_clock || cone.touch_clock;
  if (!source || source.available === false) return null;
  const minutes = finite(source.median_resolution_minutes)
    ? Number(source.median_resolution_minutes)
    : (finite(source.median_minutes) ? Number(source.median_minutes) : null);
  const years = finite(source.median_resolution_years)
    ? Number(source.median_resolution_years)
    : (finite(source.median_years) ? Number(source.median_years) : null);
  return {
    median_minutes: minutes,
    median_years: years,
    median_status: source.median_status || (minutes == null ? 'beyond_horizon' : 'identified'),
    resolved_probability_horizon: finite(source.resolved_probability_horizon)
      ? Number(source.resolved_probability_horizon) : null,
    horizon_minutes: finite(source.horizon_minutes) ? Number(source.horizon_minutes) : null,
    source: source.source || 'authoritative_execution_mc',
    time_basis: source.time_basis || 'calendar_elapsed',
  };
}

export function applyAuthoritativeTouchClock(cone) {
  if (!cone || typeof cone !== 'object') return cone;
  const clock = authoritativeTouchClock(cone);
  if (clock) {
    if (finite(cone.median_years)) cone.model_conditional_median_years = Number(cone.median_years);
    cone.touch_clock = clock;
    cone.median_years = clock.median_status === 'identified' ? clock.median_years : null;
  } else {
    delete cone.touch_clock;
    cone.median_years = null;
  }
  return cone;
}

// Backward-compatible symbol for wrappers loaded from an older cached app.js.
// It no longer computes, extrapolates or selects a barrier.
export const applyLocalTouchClock = applyAuthoritativeTouchClock;
