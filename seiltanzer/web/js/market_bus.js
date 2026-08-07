// Lightweight live-tick bus for advanced analytics modules.
// app.js already calls updateLiveGex() on every WS price tick; gex.js publishes
// that packet here so other analytics can react without opening duplicate WS feeds.

const listeners = new Set();
let lastTs = 0;
let lastPrice = null;
let impulse = 0;
let seq = 0;
let lastEmitAt = 0;
let pendingPacket = null;
let pendingTimer = null;

function isMobile() {
  if (typeof window === 'undefined') return false;
  if (typeof window.matchMedia === 'function') return window.matchMedia('(max-width: 760px)').matches;
  return Number(window.innerWidth || 9999) <= 760;
}

function emit(packet) {
  lastEmitAt = performance.now();
  pendingPacket = null;
  listeners.forEach((fn) => {
    try { fn(packet); } catch (err) { console.warn('market tick listener failed', err); }
  });
}

function scheduleMobileEmit(packet) {
  pendingPacket = packet;
  // During an active 3D gesture the single-owner Plotly camera controller is
  // authoritative. Keep only the newest tick and release it when the main
  // thread has room again instead of making subscribers fight the gesture.
  const busy = typeof window !== 'undefined' && Boolean(window.__seiltanzer3dBusy);
  const minInterval = busy ? 120 : 50; // ~8 Hz while touching, 20 Hz otherwise.
  const elapsed = performance.now() - lastEmitAt;
  if (elapsed >= minInterval && !busy) {
    if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null; }
    emit(packet);
    return;
  }
  if (pendingTimer) return;
  pendingTimer = setTimeout(() => {
    pendingTimer = null;
    if (!pendingPacket) return;
    emit(pendingPacket);
  }, Math.max(16, minInterval - elapsed));
}

export function subscribeMarketTick(fn) {
  if (typeof fn !== 'function') return () => {};
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function publishMarketTick(raw = {}) {
  const price = Number(raw.price);
  if (!Number.isFinite(price) || price <= 0) return;
  const now = performance.now();
  const dtSec = lastTs ? Math.max(0.016, (now - lastTs) / 1000) : 0;
  const retBp = lastPrice && lastPrice > 0 ? Math.log(price / lastPrice) * 1e4 : 0;
  const speedBpSec = dtSec ? retBp / dtSec : 0;
  const shock = Math.min(1, Math.abs(retBp) / 3.0);
  impulse = impulse * 0.86 + shock * 0.14;
  seq += 1;
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
  };
  lastTs = now;
  lastPrice = price;
  if (isMobile()) scheduleMobileEmit(packet);
  else emit(packet);
}
