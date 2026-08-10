// Lightweight live-tick bus for advanced analytics modules.
// app.js already calls updateLiveGex() on every WS price tick; gex.js publishes
// that packet here so other analytics can react without opening duplicate WS feeds.
//
// Performance rule: browser paint, not websocket cadence, defines the maximum
// useful visual update rate. Incoming ticks are therefore coalesced into the next
// animation frame while their path/shock information is retained. This removes
// redundant work without changing any analytical model or visible animation.

const listeners = new Set();
let lastTs = 0;
let lastPrice = null;
let impulse = 0;
let seq = 0;

let pendingRaw = null;
let pendingLastInputPrice = null;
let pendingPeakShock = 0;
let pendingAbsPathBp = 0;
let pendingCount = 0;
let framePending = false;

const nowMs = () => (typeof performance !== 'undefined' && typeof performance.now === 'function')
  ? performance.now() : Date.now();

function analyticsGestureBusy() {
  if (typeof window === 'undefined') return false;
  if (window.__seiltanzer3dBusy) return true;
  return Boolean(typeof document !== 'undefined'
    && document.documentElement?.classList?.contains?.('analytics-3d-busy'));
}

function pageHidden() {
  return Boolean(typeof document !== 'undefined' && document.hidden);
}

function requestVisualFrame(fn) {
  if (typeof requestAnimationFrame === 'function') return requestAnimationFrame(fn);
  return setTimeout(() => fn(nowMs()), 16);
}

function scheduleFlush() {
  if (framePending || !pendingRaw || pageHidden() || analyticsGestureBusy()) return;
  framePending = true;
  requestVisualFrame(() => {
    framePending = false;
    flushMarketTick();
    // A new tick may have arrived while listeners were rendering.
    if (pendingRaw) scheduleFlush();
  });
}

function flushMarketTick() {
  if (!pendingRaw || pageHidden() || analyticsGestureBusy()) return;

  const raw = pendingRaw;
  const price = Number(raw.price);
  const count = Math.max(1, pendingCount);
  const now = nowMs();
  const dtSec = lastTs ? Math.max(0.016, (now - lastTs) / 1000) : 0;
  const retBp = lastPrice && lastPrice > 0 ? Math.log(price / lastPrice) * 1e4 : 0;
  const speedBpSec = dtSec ? retBp / dtSec : 0;
  const netShock = Math.min(1, Math.abs(retBp) / 3.0);
  const pathShock = Math.min(1, pendingAbsPathBp / 6.0);
  const shock = Math.max(netShock, pendingPeakShock, pathShock);

  // Preserve the old exponential impulse semantics when multiple raw ticks were
  // collapsed into one visual frame: N input ticks still contribute N decays.
  const decay = Math.pow(0.86, count);
  impulse = impulse * decay + shock * (1 - decay);
  seq += count;

  const packet = {
    ...raw,
    price,
    prevPrice: lastPrice,
    retBp,
    speedBpSec,
    impulse,
    direction: Math.sign(retBp),
    dtSec,
    seq,
    now,
    coalescedTicks: count,
  };

  lastTs = now;
  lastPrice = price;
  pendingRaw = null;
  pendingPeakShock = 0;
  pendingAbsPathBp = 0;
  pendingCount = 0;
  pendingLastInputPrice = price;

  listeners.forEach((fn) => {
    try { fn(packet); } catch (err) { console.warn('market tick listener failed', err); }
  });
}

export function subscribeMarketTick(fn) {
  if (typeof fn !== 'function') return () => {};
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function publishMarketTick(raw = {}) {
  const price = Number(raw.price);
  if (!Number.isFinite(price) || price <= 0) return;

  const previousInput = pendingLastInputPrice ?? lastPrice;
  if (previousInput && previousInput > 0) {
    const stepBp = Math.log(price / previousInput) * 1e4;
    const stepShock = Math.min(1, Math.abs(stepBp) / 3.0);
    pendingPeakShock = Math.max(pendingPeakShock, stepShock);
    pendingAbsPathBp += Math.abs(stepBp);
  }

  pendingRaw = { ...raw, price };
  pendingLastInputPrice = price;
  pendingCount += 1;
  scheduleFlush();
}

// While a user rotates a 3D scene we intentionally hold analytical visual ticks:
// Plotly/WebGL keeps the gesture at full native FPS, then the newest market state
// is delivered immediately after release. No market state is replaced by stale data.
if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('seiltanzer:3d-idle', scheduleFlush);
  window.addEventListener('focus', scheduleFlush, { passive: true });
}
if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) scheduleFlush();
  }, { passive: true });
}
