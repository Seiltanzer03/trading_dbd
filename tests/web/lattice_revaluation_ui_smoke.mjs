import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice.js', import.meta.url),
  'utf8',
);

for (const text of [
  'ПУНКТИР — ВХОД',
  'СЕРАЯ — СРЕДНЕЕ',
  'ОРАНЖЕВАЯ — СЕЙЧАС',
  'ФАКТИЧЕСКИЙ ПЕРЕТОК',
  'flowPlan',
  'lattice_visual_history',
  'Δ МАССЫ К ТЕЙКУ',
  'Δ МАССЫ К СТОПУ',
]) {
  assert.ok(source.includes(text), `direct lattice canvas must mention ${text}`);
}
assert.ok(!source.includes('spawnBall'), 'random Galton sampling must be removed');
assert.ok(!source.includes('Math.random'), 'mass-flow animation must be deterministic');
console.log('direct lattice revaluation visual contract ok');
