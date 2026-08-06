import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice.js', import.meta.url),
  'utf8',
);

for (const text of [
  'const ROWS = 10',
  'const BINS = ROWS + 1',
  'LIVE GALTON · ТИК → СНИМОК → ФИКСИРОВАННЫЙ ШАРИК',
  'ТИКИ МЕНЯЮТ ТОЛЬКО НОВЫЕ ШАРИКИ',
  'CURRENT RND — live black bell',
  'TIME-AVERAGED SNAPSHOTS',
  'buildGaltonDistribution',
  'deterministicBin',
  'accumulateSnapshotMass',
  'advanceBallKinematics',
  'snapshotProbs = state.model.probs.slice()',
  'state.counts[ball.bin] += 1',
  'state.expectedMass = accumulateSnapshotMass',
  'domainOverride: state.domain',
  'pegX',
  'stackPoint',
  'requestAnimationFrame',
]) {
  assert.ok(source.includes(text), `live snapshot Galton board must mention ${text}`);
}
assert.ok(!source.includes('new WebSocket'),
  'lattice must not own a second websocket competing with app.js');
assert.ok(!source.includes("fetch('/api/state'"),
  'lattice must receive one canonical data stream through setData');
assert.ok(!source.includes('Math.random'),
  'ball targets and paths must remain deterministic for reproducible checks');
assert.ok(!source.includes('reset({ clearStorage: true })'),
  'market ticks must never clear landed balls');
assert.ok(source.includes('if (state.active && state.tradeId != null) return false'),
  'manual reset must be blocked while a trade is active');
assert.ok(source.includes('if (nextTradeId == null) clearStored(previousTradeId, storage)'),
  'persistent balls must be deleted when the trade closes');
console.log('live probability snapshot and persistence contract ok');
