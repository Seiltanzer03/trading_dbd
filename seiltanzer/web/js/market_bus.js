// Lightweight live-tick bus for advanced analytics modules.
// app.js already calls updateLiveGex() on every WS price tick; gex.js publishes
// that packet here so other analytics can react without opening duplicate WS feeds.

const listeners = new Set();
let lastTs = 0;
let lastPrice = null;
let impulse = 0;
let seq = 0;

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
  listeners.forEach((fn) => {
    try { fn(packet); } catch (err) { console.warn('market tick listener failed', err); }
  });
}
