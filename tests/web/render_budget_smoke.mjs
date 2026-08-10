import assert from 'node:assert/strict';
import fs from 'node:fs';

// ---------------------------------------------------------------- market bus
// Multiple raw WS ticks inside one browser frame must become one visual packet,
// while the newest price and cumulative tick count are preserved. Advanced visual
// listeners must then be staggered across separate painted frames, not run as one
// long post-paint burst that can freeze an independently animated Galton board.
const windowHandlers = new Map();
const documentHandlers = new Map();
const rafQueue = [];
const timerQueue = [];

globalThis.window = {
  __seiltanzer3dBusy: false,
  innerWidth: 1200,
  innerHeight: 800,
  addEventListener(type, fn) { windowHandlers.set(type, fn); },
  removeEventListener() {},
};
globalThis.document = {
  hidden: false,
  documentElement: {
    clientWidth: 1200,
    clientHeight: 800,
    classList: { contains: () => false },
  },
  addEventListener(type, fn) { documentHandlers.set(type, fn); },
  removeEventListener() {},
};
globalThis.performance = { now: () => 1000 };
globalThis.requestAnimationFrame = (fn) => { rafQueue.push(fn); return rafQueue.length; };
globalThis.cancelAnimationFrame = () => {};
globalThis.setTimeout = (fn) => { timerQueue.push(fn); return timerQueue.length; };

function runRaf() {
  assert.ok(rafQueue.length, 'expected a queued animation frame');
  rafQueue.shift()(1016);
}
function runTimer() {
  assert.ok(timerQueue.length, 'expected a queued post-paint timer');
  timerQueue.shift()();
}
function runPaintBoundary() {
  runRaf();
  runTimer();
}

const bus = await import('../../seiltanzer/web/js/market_bus.js?render-budget-smoke');
const packetsA = [];
const packetsB = [];
bus.subscribeMarketTick((packet) => packetsA.push(packet));
bus.subscribeMarketTick((packet) => packetsB.push(packet));

bus.publishMarketTick({ price: 100, trade: { instrument: 'XAU' } });
bus.publishMarketTick({ price: 100.1, trade: { instrument: 'XAU' } });
bus.publishMarketTick({ price: 100.2, trade: { instrument: 'XAU' } });
assert.equal(packetsA.length, 0, 'market bus should not synchronously render every raw tick');
assert.equal(rafQueue.length, 1, 'one browser frame should be scheduled for a raw burst');

// First boundary only prepares the canonical market packet. Listener work must not
// execute in that rAF or its market-bus post-paint callback.
runPaintBoundary();
assert.equal(packetsA.length + packetsB.length, 0, 'analytics listeners must remain outside the packet-preparation task');
assert.equal(rafQueue.length, 1, 'first analytics listener should get its own next frame');

// Exactly one listener is allowed to execute per painted frame.
runPaintBoundary();
assert.equal(packetsA.length + packetsB.length, 1, 'only one analytics listener may run in one frame budget');
assert.equal(rafQueue.length, 1, 'second listener must wait for another paint boundary');
runPaintBoundary();
assert.equal(packetsA.length, 1);
assert.equal(packetsB.length, 1);
assert.equal(packetsA[0].price, 100.2);
assert.equal(packetsA[0].coalescedTicks, 3);
assert.ok(Number.isFinite(packetsA[0].impulse));

// During a 3D gesture the visual packet is held, not dropped; on idle the newest
// state is delivered through the same staggered frame budget.
window.__seiltanzer3dBusy = true;
bus.publishMarketTick({ price: 100.3, trade: { instrument: 'XAU' } });
bus.publishMarketTick({ price: 100.5, trade: { instrument: 'XAU' } });
assert.equal(packetsA.length + packetsB.length, 2);
window.__seiltanzer3dBusy = false;
assert.equal(typeof windowHandlers.get('seiltanzer:3d-idle'), 'function');
windowHandlers.get('seiltanzer:3d-idle')();
runPaintBoundary();
assert.equal(packetsA.length + packetsB.length, 2, '3D idle packet prep must still not run listeners in the same task');
runPaintBoundary();
assert.equal(packetsA.length + packetsB.length, 3);
runPaintBoundary();
assert.equal(packetsA.length + packetsB.length, 4);
assert.equal(packetsA.at(-1).price, 100.5);
assert.equal(packetsA.at(-1).coalescedTicks, 2);

// -------------------------------------------------------------- architecture
// Guard the exact product characteristics that must not regress while optimizing.
const budget = fs.readFileSync('seiltanzer/web/js/frame_budget.js', 'utf8');
assert.ok(budget.includes('one job is executed'), 'shared frame budget must document one-job scheduling');
assert.ok(budget.includes('createLatestPanelTask'), 'viewport-aware latest-state scheduler must remain');
assert.ok(budget.includes('elementNearViewport'), 'offscreen heavy plots must be suppressible');

const cone = fs.readFileSync('seiltanzer/web/js/cone.js', 'utf8');
assert.ok(cone.includes('createPlotlyCameraGuard'), 'Cone camera guard must remain');
assert.ok(cone.includes('applyLocalTouchClock'), 'Cone local touch clock must remain');
assert.ok(cone.includes("createLatestPanelTask('cone:set-data'"), 'Cone heavy writes must respect frame budget');
assert.ok(cone.includes('core.setData(...args)'), 'full Cone render path must remain');
assert.ok(cone.includes('core.updateLive(...args)'), 'Cone live marker path must remain');

const iv = fs.readFileSync('seiltanzer/web/js/iv_surface.js', 'utf8');
assert.ok(iv.includes('createPlotlyCameraGuard'), 'IV camera guard must remain');
assert.ok(iv.includes('snapshotSignature'), 'IV mesh reuse must be snapshot-aware');
assert.ok(iv.includes('core.updateLive(payload)'), 'IV live ribbon/curtain animation must remain');
assert.ok(iv.includes('core.render(state, surfacePayload, force)'), 'full IV render path must remain');
assert.ok(iv.includes('core.setMode'), 'IV mode switching must remain');
assert.ok(iv.includes("createLatestPanelTask('iv-surface:render'"), 'IV writes must respect viewport/frame budget');

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
  analyticsListenersStaggered: true,
  heldDuring3dGesture: true,
  coneViewportBudgeted: true,
  ivViewportBudgeted: true,
  ivLiveAnimationPreserved: true,
  correlationPhysicsPreserved: true,
  correlationMatrixReused: true,
}));
