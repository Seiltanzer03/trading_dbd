import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice.js', import.meta.url),
  'utf8',
);

for (const text of [
  'const ROWS = 10',
  'const BINS = ROWS + 1',
  'КЛАССИЧЕСКАЯ ДОСКА · 10 РЯДОВ → 11 КОРЗИН',
  'ОДИН ЗАВЕРШЁННЫЙ ПРОХОД = ОДИН ВКЛАД',
  'buildGaltonDistribution',
  'deterministicBin',
  'advanceBallKinematics',
  'spawnBall',
  'state.counts[ball.bin] += 1',
  'pegX',
  'stackPoint',
  'requestAnimationFrame',
]) {
  assert.ok(source.includes(text), `classic Galton board must mention ${text}`);
}
assert.ok(!source.includes('new WebSocket'),
  'lattice must not own a second websocket competing with app.js');
assert.ok(!source.includes("fetch('/api/state'"),
  'lattice must receive one canonical data stream through setData');
assert.ok(!source.includes('Math.random'),
  'ball targets and paths must remain deterministic for reproducible checks');
assert.ok(source.includes('right side of the Galton bell') === false,
  'test prose must not leak into production module');
console.log('classic aligned Galton board contract ok');
