import assert from 'node:assert/strict';
import fs from 'node:fs';

// ---------------------------------------------------------------- market bus
// Multiple raw WS ticks inside one browser frame must become one visual packet,
// while the newest price and cumulative tick count are preserved. Crucially, the
// analytics listener may NOT run inside rAF: independently animated canvases such
// as Probability Lattice need the pre-paint frame budget for uninterrupted motion.
const windowHandlers = new Map();
const documentHandlers = new Map();
const rafQueue = [];
const timerQueue = [];

globalThis.window = {
  __seiltanzer3dBusy: false,
  addEventListener(type, fn) { windowHandlers.set(type, fn); },
};
globalThis.document = {
  hidden: false,
  documentElement: { classList: { contains: () => false } },
  addEventListener(type, fn) { documentHandlers.set(type, fn); },
};
globalThis.performance = { now: () => 1000 };
globalThis.requestAnimationFrame = (fn) => { rafQueue.push(fn); return rafQueue.length; };
globalThis.cancelAnimationFrame = () => {};
globalThis.setTimeout = (fn) => { timerQueue.push(fn); return timerQueue.length; };

const bus = await import('../../seiltanzer/web/js/market_bus.js?render-budget-smoke');
const packets = [];
bus.subscribeMarketTick((packet) => packets.push(packet));

bus.publishMarketTick({ price: 100, trade: { instrument: 'XAU' } });
bus.publishMarketTick({ price: 100.1, trade: { instrument: 'XAU' } });
bus.publishMarketTick({ price: 100.2, trade: { instrument: 'XAU' } });
assert.equal(packets.length, 0, 'market bus should not synchronously render every raw tick');
assert.equal(rafQueue.length, 1, 'one browser frame should be scheduled for a burst');

// rAF is pre-paint: firing it must only queue a post-paint task, never listeners.
rafQueue.shift()(1016);
assert.equal(packets.length, 0, 'analytics listeners must not execute inside requestAnimationFrame');
assert.equal(timerQueue.length, 1, 'analytics flush must be deferred until after the paint boundary');
timerQueue.shift()();
assert.equal(packets.length, 1);
assert.equal(packets[0].price, 100.2);
assert.equal(packets[0].coalescedTicks, 3);
assert.ok(Number.isFinite(packets[0].impulse));

// During a 3D gesture the visual packet is held, not dropped; on idle the newest
// state is delivered after a paint boundary. This protects Plotly/WebGL gestures
// without reintroducing a periodic hitch into Canvas animations.
window.__seiltanzer3dBusy = true;
bus.publishMarketTick({ price: 100.3, trade: { instrument: 'XAU' } });
bus.publishMarketTick({ price: 100.5, trade: { instrument: 'XAU' } });
assert.equal(packets.length, 1);
window.__seiltanzer3dBusy = false;
assert.equal(typeof windowHandlers.get('seiltanzer:3d-idle'), 'function');
windowHandlers.get('seiltanzer:3d-idle')();
assert.equal(rafQueue.length, 1);
rafQueue.shift()(1032);
assert.equal(packets.length, 1, '3D idle flush must also stay out of the rAF callback');
assert.equal(timerQueue.length, 1);
timerQueue.shift()();
assert.equal(packets.length, 2);
assert.equal(packets[1].price, 100.5);
assert.equal(packets[1].coalescedTicks, 2);

// -------------------------------------------------------------- architecture
// Guard the exact product characteristics that must not regress while optimizing.
const iv = fs.readFileSync('seiltanzer/web/js/iv_surface.js', 'utf8');
assert.ok(iv.includes('createPlotlyCameraGuard'), 'IV camera guard must remain');
assert.ok(iv.includes('snapshotSignature'), 'IV mesh reuse must be snapshot-aware');
assert.ok(iv.includes('core.updateLive(payload)'), 'IV live ribbon/curtain animation must remain');
assert.ok(iv.includes('core.render(state, surfacePayload, force)'), 'full IV render path must remain');
assert.ok(iv.includes('core.setMode'), 'IV mode switching must remain');

const corr = fs.readFileSync('seiltanzer/web/js/correlation.js', 'utf8');
assert.ok(corr.includes('IntersectionObserver'), 'network should pause only when offscreen');
assert.ok(corr.includes('canAnimateNetwork'), 'network visibility/gesture budget must exist');
assert.ok(corr.includes('const packets = mobile ? [phase] : [phase, (phase + .5) % 1]'), 'animated correlation packets must remain');
assert.ok(corr.includes('drawNetworkBackground'), 'premium network canvas must remain');
assert.ok(corr.includes('FULL TOPOLOGY'), 'all observed links must remain visible');
assert.ok(corr.includes("if (!chart) chart=window.echarts.init"), 'matrix renderer should be reused');
assert.ok(corr.includes('lazyUpdate:true'), 'matrix updates should remain incremental');

console.log(JSON.stringify({
  marketBusCoalescing: true,
  analyticsFlushAfterPaint: true,
  heldDuring3dGesture: true,
  ivLiveAnimationPreserved: true,
  correlationPhysicsPreserved: true,
  correlationMatrixReused: true,
}));
