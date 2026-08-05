// Experimental option-center overlays for the existing 3D cone and 2D fan.
//
// The underlying visualisations remain untouched.  This module listens to the
// public cone wrapper and paints two deliberately subordinate diagnostics:
//   • ROBUST FORWARD — the shrunken/capped drift already used by Expected/CVaR;
//   • RND MEAN H — the raw terminal mean of the option-implied distribution.
// A rejected raw mean remains visible as context, but never masquerades as an
// optimizer input.

const browser = typeof window !== 'undefined' && typeof document !== 'undefined';
const H = 360;
const LIVE_DECAY_TAU = 0.18;
const ROBUST_META = 'seiltanzer-option-center-robust';
const RAW_META = 'seiltanzer-option-center-raw';
const OVERLAY_COLOR = '#4E6375';
const RAW_COLOR = '#E8622A';

function finite(value) {
  const out = Number(value);
  return Number.isFinite(out) ? out : null;
}

function clamp(value, lo, hi) {
  return Math.max(lo, Math.min(hi, Number(value) || 0));
}

function approach(current, target, dt, speed) {
  if (!Number.isFinite(target)) return current;
  if (!Number.isFinite(current)) return target;
  return current + (target - current) * (1 - Math.exp(-speed * dt));
}

export function histogramQuantile(weights, edges, q = 0.5) {
  if (!Array.isArray(weights) || !Array.isArray(edges)
      || edges.length !== weights.length + 1 || !weights.length) return null;
  const clean = weights.map((value) => Math.max(0, Number(value) || 0));
  const total = clean.reduce((sum, value) => sum + value, 0);
  if (!(total > 0)) return null;
  const target = clamp(q, 0, 1) * total;
  let cumulative = 0;
  for (let i = 0; i < clean.length; i++) {
    const next = cumulative + clean[i];
    if (target <= next || i === clean.length - 1) {
      const width = Number(edges[i + 1]) - Number(edges[i]);
      const fraction = clean[i] > 0 ? (target - cumulative) / clean[i] : 0.5;
      return Number(edges[i]) + clamp(fraction, 0, 1) * width;
    }
    cumulative = next;
  }
  return Number(edges.at(-1));
}

export function buildConditionalMedianPath(cone) {
  const density = cone?.density;
  const times = cone?.times_frac;
  const edges = cone?.edges;
  const r0 = finite(cone?.r0);
  if (!Array.isArray(density) || !Array.isArray(times) || !Array.isArray(edges)
      || density.length !== times.length || r0 == null) return [];
  const path = [{ tau: 0, offset: 0, alive: 1 }];
  let lastOffset = 0;
  for (let i = 0; i < density.length; i++) {
    const row = density[i];
    const tau = clamp(times[i], 0, 1);
    const alive = Array.isArray(row)
      ? row.reduce((sum, value) => sum + Math.max(0, Number(value) || 0), 0)
      : 0;
    const median = alive >= 0.015 ? histogramQuantile(row, edges, 0.5) : null;
    if (Number.isFinite(median)) lastOffset = median - r0;
    path.push({ tau, offset: lastOffset, alive });
  }
  path.sort((a, b) => a.tau - b.tau);
  return path.filter((point, index) =>
    index === path.length - 1 || Math.abs(point.tau - path[index + 1].tau) > 1e-9);
}

export function interpolateMedianOffset(path, tau) {
  if (!Array.isArray(path) || !path.length) return 0;
  const t = clamp(tau, 0, 1);
  if (t <= path[0].tau) return path[0].offset;
  for (let i = 1; i < path.length; i++) {
    if (t <= path[i].tau) {
      const a = path[i - 1], b = path[i];
      const span = Math.max(b.tau - a.tau, 1e-9);
      const u = (t - a.tau) / span;
      return a.offset + (b.offset - a.offset) * u;
    }
  }
  return path.at(-1).offset;
}

export function liveImpulseShape(tau, decayTau = LIVE_DECAY_TAU) {
  const t = Math.max(0, Number(tau) || 0);
  const d = Math.max(0.03, Number(decayTau) || LIVE_DECAY_TAU);
  const x = t / d;
  return x * Math.exp(1 - x);
}

export function optionCenterModel(cone, liveR = null) {
  const rawMeanR = finite(cone?.market_mean_r);
  const baseR = finite(liveR) ?? finite(cone?.r0);
  const driftR = finite(cone?.drift_R) ?? 0;
  const rejectedGapR = finite(cone?.forward_drift_rejected);
  const source = String(cone?.forward_drift_source || 'unavailable');
  if (rawMeanR == null || baseR == null) return null;
  const accepted = source === 'bl_forward_shrunk' && rejectedGapR == null;
  return {
    rawMeanR,
    baseR,
    rawGapR: rawMeanR - baseR,
    driftR,
    robustForwardR: baseR + driftR,
    accepted,
    rejectedGapR,
    source,
  };
}

export function robustForwardPath(cone, liveR = null, points = 25) {
  const model = optionCenterModel(cone, liveR);
  if (!model) return [];
  const n = Math.max(2, Math.floor(points));
  return Array.from({ length: n }, (_, index) => {
    const tau = index / (n - 1);
    return { tau, r: model.baseR + model.driftR * tau };
  });
}

function cloneCamera(el) {
  const camera = el?._fullLayout?.scene?.camera;
  return camera ? JSON.parse(JSON.stringify(camera)) : null;
}

function traceIndex(el, meta) {
  return Array.isArray(el?.data)
    ? el.data.findIndex((trace) => trace?.meta === meta)
    : -1;
}

function nearestIndex(values, target) {
  let best = 0, distance = Infinity;
  values.forEach((value, index) => {
    const d = Math.abs(Number(value) - target);
    if (d < distance) { distance = d; best = index; }
  });
  return best;
}

function surfaceHeight(el, r, tau) {
  const surface = el?.data?.find((trace) => trace?.type === 'surface');
  const xs = surface?.x, ys = surface?.y, z = surface?.z;
  if (!Array.isArray(xs) || !Array.isArray(ys) || !Array.isArray(z) || !z.length) return 0.02;
  const yi = nearestIndex(ys, tau);
  const xi = nearestIndex(xs, r);
  const value = finite(z?.[yi]?.[xi]);
  return (value ?? 0.02) + 0.014;
}

async function deleteConeOverlays(el) {
  const P = window.Plotly;
  if (!P || !el?.data) return;
  const indexes = [traceIndex(el, ROBUST_META), traceIndex(el, RAW_META)]
    .filter((index) => index >= 0)
    .sort((a, b) => b - a);
  if (!indexes.length) return;
  const camera = cloneCamera(el);
  for (const index of indexes) await P.deleteTraces(el, index);
  if (camera) await P.relayout(el, { 'scene.camera': camera });
}

async function syncConeOverlays(state) {
  if (!browser) return;
  const el = document.querySelector('#cone-plot');
  const P = window.Plotly;
  if (!P || !el?.data?.length) return;
  const model = optionCenterModel(state.cone, state.liveR);
  if (!model || !state.cone?.option_anchored) {
    await deleteConeOverlays(el);
    return;
  }
  await deleteConeOverlays(el);
  const camera = cloneCamera(el);
  const T = finite(state.cone?.T) ?? 2.5;
  const path = robustForwardPath(state.cone, state.liveR, 25);
  const robustTrace = {
    type: 'scatter3d', mode: 'lines', meta: ROBUST_META,
    x: path.map((point) => clamp(point.r, -1, T)),
    y: path.map((point) => point.tau),
    z: path.map((point) => surfaceHeight(el, clamp(point.r, -1, T), point.tau)),
    line: { color: OVERLAY_COLOR, width: 4, dash: 'dash' },
    name: 'ROBUST FORWARD · AI', showlegend: true,
    hovertemplate: `robust forward: %{x:+.2f}R<br>время: %{y:.0%}<br>`
      + `drift used by Expected/CVaR: ${model.driftR >= 0 ? '+' : ''}${model.driftR.toFixed(3)}R<extra></extra>`,
  };
  const rawX = clamp(model.rawMeanR, -1, T);
  const rawStatus = model.accepted
    ? 'raw mean accepted after shrink/cap'
    : 'raw mean context only; rejected from optimizer';
  const rawTrace = {
    type: 'scatter3d', mode: 'markers', meta: RAW_META,
    x: [rawX], y: [1], z: [surfaceHeight(el, rawX, 1) + 0.018],
    marker: {
      size: 7, color: model.accepted ? RAW_COLOR : 'rgba(138,135,125,0.9)',
      symbol: 'diamond-open', line: { color: '#FFFFFF', width: 1 },
    },
    name: model.accepted ? 'RND MEAN H · RAW' : 'RND MEAN H · REJECTED',
    showlegend: true,
    hovertemplate: `raw RND mean H: ${model.rawMeanR >= 0 ? '+' : ''}${model.rawMeanR.toFixed(3)}R<br>`
      + `${rawStatus}<extra></extra>`,
  };
  await P.addTraces(el, [robustTrace, rawTrace]);
  if (camera) await P.relayout(el, { 'scene.camera': camera });
}

function ensureFanCanvas() {
  if (!browser) return null;
  const base = document.querySelector('#cone-fan');
  const holder = base?.parentElement;
  if (!base || !holder) return null;
  let overlay = holder.querySelector('#option-center-fan-overlay');
  if (!overlay) {
    overlay = document.createElement('canvas');
    overlay.id = 'option-center-fan-overlay';
    overlay.setAttribute('aria-hidden', 'true');
    Object.assign(overlay.style, {
      position: 'absolute', inset: '0', width: '100%', height: `${H}px`,
      pointerEvents: 'none', zIndex: '4',
    });
    const style = window.getComputedStyle(holder);
    if (style.position === 'static') holder.style.position = 'relative';
    holder.appendChild(overlay);
  }
  return { base, overlay };
}

function canvasContext(canvas, width) {
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(H * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(H * dpr);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, H);
  return ctx;
}

function projectedMove(trail, windowMs, projectionSec) {
  if (trail.length < 2) return 0;
  const end = trail.at(-1);
  const points = trail.filter((point) => end.ts - point.ts <= windowMs);
  if (points.length < 2) return 0;
  const first = points[0];
  const elapsed = Math.max((end.ts - first.ts) / 1000, 1);
  return (end.r - first.r) / elapsed * projectionSec;
}

function targetLiveImpulse(trail) {
  const fast = clamp(projectedMove(trail, 18000, 30), -0.38, 0.38);
  const slow = clamp(projectedMove(trail, 90000, 80), -0.30, 0.30);
  return clamp(fast * 0.68 + slow * 0.32, -0.45, 0.45);
}

function drawFanOverlay(state) {
  const pair = ensureFanCanvas();
  if (!pair) return;
  const { base, overlay } = pair;
  const width = base.clientWidth || base.parentElement?.clientWidth || 0;
  if (!(width > 0)) return;
  const ctx = canvasContext(overlay, width);
  const cone = state.cone;
  const model = optionCenterModel(cone, state.curR);
  if (!model || !cone?.option_anchored) return;

  const T = finite(cone.T) ?? 2.5;
  const r0 = finite(cone.r0) ?? model.baseR;
  const sigma = finite(cone.sigma_R) ?? 1;
  const termSlope = finite(cone.term_slope) ?? 0;
  const medianPath = buildConditionalMedianPath(cone);
  const rNow = finite(state.curR) ?? model.baseR;
  const optionCenter = (tau) => medianPath.length
    ? rNow + interpolateMedianOffset(medianPath, tau)
    : rNow + (finite(cone.drift_R) ?? 0) * tau;
  const centerAt = (tau) => optionCenter(tau)
    + state.liveImpulse * liveImpulseShape(tau, LIVE_DECAY_TAU);
  const centerSamples = Array.from({ length: 17 }, (_, index) => centerAt(index / 16));
  const centerLo = Math.min(...centerSamples);
  const centerHi = Math.max(...centerSamples);
  const padL = 58, padR = 16, padT = 40, padB = 34;
  const plotW = width - padL - padR, plotH = H - padT - padB;
  const yLo = Math.min(-1.35, rNow - 0.4, centerLo - 0.25);
  const yHi = Math.max(T + 0.45, rNow + 0.4, centerHi + 0.25);
  const X = (tau) => padL + tau * plotW;
  const Y = (r) => padT + (yHi - r) / Math.max(yHi - yLo, 1e-9) * plotH;

  ctx.save();
  ctx.beginPath(); ctx.rect(padL, padT, plotW, plotH); ctx.clip();
  const path = robustForwardPath(cone, rNow, 65);
  ctx.beginPath();
  path.forEach((point, index) => {
    const x = X(point.tau), y = Y(point.r);
    if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
  });
  ctx.strokeStyle = 'rgba(78,99,117,0.82)';
  ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]); ctx.stroke(); ctx.setLineDash([]);

  const rawY = Y(clamp(model.rawMeanR, yLo, yHi));
  const rawX = X(1);
  ctx.translate(rawX, rawY); ctx.rotate(Math.PI / 4);
  ctx.strokeStyle = model.accepted ? RAW_COLOR : 'rgba(138,135,125,0.95)';
  ctx.lineWidth = 1.7; ctx.strokeRect(-4, -4, 8, 8);
  ctx.restore();

  ctx.font = '8px "IBM Plex Mono", monospace';
  ctx.textAlign = 'right';
  ctx.fillStyle = OVERLAY_COLOR;
  ctx.fillText(`ROBUST FWD ${model.robustForwardR >= 0 ? '+' : ''}${model.robustForwardR.toFixed(2)}R`, width - padR - 8,
    clamp(Y(model.robustForwardR) - 6, padT + 9, H - padB - 5));
  ctx.fillStyle = model.accepted ? RAW_COLOR : 'rgba(138,135,125,0.95)';
  const rawLabel = model.accepted ? 'RND MEAN H' : 'RND MEAN H · REJECTED';
  ctx.fillText(`${rawLabel} ${model.rawMeanR >= 0 ? '+' : ''}${model.rawMeanR.toFixed(2)}R`, width - padR - 8,
    clamp(rawY + 13, padT + 10, H - padB - 4));

  // Keep lint/static checks aware that the scale intentionally follows fan.js.
  void r0; void sigma; void termSlope;
}

const state = {
  cone: null, liveR: null, curR: null, liveImpulse: 0, trail: [],
  coneTimer: null,
};

function scheduleConeSync(delay = 45) {
  if (!browser) return;
  if (state.coneTimer) clearTimeout(state.coneTimer);
  state.coneTimer = setTimeout(() => {
    state.coneTimer = null;
    syncConeOverlays(state).catch(() => {});
  }, delay);
}

if (browser) {
  window.addEventListener('seiltanzer:cone-data', (event) => {
    state.cone = event.detail?.cone || null;
    if (finite(state.cone?.r0) != null && finite(state.liveR) == null) {
      state.liveR = finite(state.cone.r0);
      state.curR = state.liveR;
    }
    scheduleConeSync(80);
  });
  window.addEventListener('seiltanzer:cone-live', (event) => {
    const r = finite(event.detail?.r);
    if (r == null) return;
    state.liveR = r;
    const now = performance.now();
    if (!state.trail.length || Math.abs(r - state.trail.at(-1).r) > 1e-7) {
      state.trail.push({ r, ts: now });
    }
    state.trail = state.trail.filter((point) => now - point.ts <= 120000);
    scheduleConeSync(120);
  });

  let last = performance.now();
  const frame = (now) => {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    state.curR = approach(state.curR, state.liveR, dt, 6);
    state.liveImpulse = approach(state.liveImpulse, targetLiveImpulse(state.trail), dt, 4);
    drawFanOverlay(state);
    window.requestAnimationFrame(frame);
  };
  window.requestAnimationFrame(frame);
}
