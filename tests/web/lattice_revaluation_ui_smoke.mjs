import assert from 'node:assert/strict';
import fs from 'node:fs';

const wrapper = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice.js', import.meta.url),
  'utf8',
);
const source = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice_core.js', import.meta.url),
  'utf8',
);

for (const text of [
  'const ROWS = 10',
  'const BINS = ROWS + 1',
  'LIVE GALTON · ШАРИК → ВКЛАД → РОСТ КОЛОНКИ',
  'ОСЕВШИХ ТОЧЕК НЕТ: ИХ МАССА ВНУТРИ КОЛОНОК',
  'buildGaltonDistribution',
  'deterministicBin',
  'accumulateSnapshotMass',
  'columnSharesFromCounts',
  'columnHeight',
  'advanceBallKinematics',
  'snapshotProbs = state.model.probs.slice()',
  'state.counts[ball.bin] += 1',
  'state.expectedMass = accumulateSnapshotMass',
  'state.displayCounts',
  'ctx.fillRect(x + 2, g.baseY - hgt',
  'domainOverride: state.domain',
  'pegX',
  'requestAnimationFrame',
]) {
  assert.ok(source.includes(text), `absorbing-column Galton board must mention ${text}`);
}
assert.ok(!source.includes('new WebSocket'),
  'lattice must not own a second websocket competing with app.js');
assert.ok(!source.includes("fetch('/api/state'"),
  'lattice must receive one canonical data stream through setData');
assert.ok(!source.includes('Math.random'),
  'ball targets and paths must remain deterministic for reproducible checks');
assert.ok(!source.includes('stackPoint'),
  'landed balls must not remain as a separate decorative dot stack');
assert.ok(!source.includes('ctx.arc(point.x, point.y, 2.15'),
  'settled dots must be replaced by filled empirical columns');
assert.ok(source.includes('if (state.active && state.tradeId != null) return false'),
  'manual reset must be blocked while a trade is active');
assert.ok(source.includes('if (nextTradeId == null) clearStored(previousTradeId, storage)'),
  'persistent columns must be deleted only when the trade closes');
const ballDraw = source.indexOf('for (const ball of state.balls)');
const columnDraw = source.indexOf('ctx.fillRect(x + 2, g.baseY - hgt');
assert.ok(ballDraw >= 0 && columnDraw > ballDraw,
  'columns must draw over the landing phase so they visually absorb the ball');

for (const text of [
  "export * from './lattice_core.js'",
  "const STABLE_IDS = ['lat-balls', 'lat-green', 'lat-conv', 'lat-calib', 'lat-read']",
  'sink.hidden = true',
  "mirror.id = `${id}-stable`",
  'КОЛОНКИ ${dropped} · +R',
  'LIVE ${fmtPct(live)} · ИСТ ${fmtPct(history)} · Δ ${fmtPct(shift)}',
  "read.style.whiteSpace = 'pre-line'",
]) {
  assert.ok(wrapper.includes(text), `stable DOM owner must mention ${text}`);
}
assert.ok(!wrapper.includes("document.getElementById('lat-read').textContent"),
  'wrapper must render only through isolated visible mirrors');
console.log('absorbing empirical-column and stable DOM ownership contract ok');
