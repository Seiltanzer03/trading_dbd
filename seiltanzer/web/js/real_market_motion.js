// Deterministic motion primitives for analytics visuals.
//
// Time may integrate or damp an already measured market impulse, but it must
// never create one.  These helpers deliberately have no clock or randomness;
// callers supply real deltas and elapsed time so stationary inputs converge to
// rest and a new observation can create a bounded shock response.

export function clampMotion(value, lo = -1, hi = 1) {
  const n = Number(value);
  return Math.max(lo, Math.min(hi, Number.isFinite(n) ? n : 0));
}

export function dampedMotion(state = {}, target = 0, dtSeconds = 0, options = {}) {
  const dt = clampMotion(dtSeconds, 0, Number(options.maxDtSeconds || 0.25));
  const response = Math.max(0, Number(options.response || 7));
  const damping = Math.max(0, Number(options.damping || 4.5));
  const maxSpeed = Math.max(1e-9, Number(options.maxSpeed || 1));
  const previousVelocity = clampMotion(state.velocity || 0, -maxSpeed, maxSpeed);
  const measuredTarget = clampMotion(target, -maxSpeed, maxSpeed);
  const drive = (measuredTarget - previousVelocity) * (1 - Math.exp(-response * dt));
  const velocity = clampMotion(
    (previousVelocity + drive) * Math.exp(-damping * dt),
    -maxSpeed,
    maxSpeed,
  );
  const phase = Number(state.phase || 0) + velocity * dt;
  return { velocity, phase, energy: Math.abs(velocity) };
}

export function advanceMeasuredPhase(phase = 0, measuredRate = 0, dtSeconds = 0) {
  const dt = clampMotion(dtSeconds, 0, 0.25);
  const rate = clampMotion(measuredRate, -1, 1);
  const next = Number(phase || 0) + rate * dt;
  return ((next % 1) + 1) % 1;
}

export function observedMotionDecay(ageSeconds = 0, halfLifeSeconds = 8) {
  const age = Math.max(0, Number(ageSeconds) || 0);
  const halfLife = Math.max(0.05, Number(halfLifeSeconds) || 8);
  return Math.exp(-Math.LN2 * age / halfLife);
}

export function waveletEnergyTransfer(flow = [], lookback = 6) {
  if (!Array.isArray(flow) || flow.length < 2) {
    return { source: null, destination: null, ratePpPer30m: 0, magnitude: 0, deltas: {} };
  }
  const end = flow.at(-1);
  const start = flow[Math.max(0, flow.length - 1 - Math.max(1, lookback))];
  const elapsedMinutes = Math.max((Number(end.ts) - Number(start.ts)) / 60, 1);
  const keys = ['micro', 'intraday', 'macro'];
  const deltas = Object.fromEntries(keys.map((key) => [key, Number(end[key] || 0) - Number(start[key] || 0)]));
  const source = keys.reduce((best, key) => deltas[key] < deltas[best] ? key : best, keys[0]);
  const destination = keys.reduce((best, key) => deltas[key] > deltas[best] ? key : best, keys[0]);
  if (source === destination || deltas[source] >= 0 || deltas[destination] <= 0) {
    return { source: null, destination: null, ratePpPer30m: 0, magnitude: 0, deltas };
  }
  const transferred = Math.min(-deltas[source], deltas[destination]);
  const ratePpPer30m = transferred * 30 / elapsedMinutes;
  return {
    source, destination,
    ratePpPer30m,
    magnitude: clampMotion(ratePpPer30m / 12, 0, 1),
    deltas,
  };
}

export function correlationMotionRate(link = {}) {
  const candidates = [
    [link.delta_5m, 5],
    [link.delta_15m, 15],
    [link.delta_1h, 60],
  ].filter(([value]) => Number.isFinite(Number(value)));
  if (!candidates.length) return 0;
  const perHour = candidates.map(([value, minutes]) => Number(value) * 60 / minutes);
  const strongest = perHour.reduce((best, value) => Math.abs(value) > Math.abs(best) ? value : best, 0);
  return clampMotion(strongest / 1.2, -1, 1);
}
