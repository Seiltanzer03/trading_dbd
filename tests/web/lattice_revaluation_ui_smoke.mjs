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
  'ШАРИКИ — CURRENT RND',
  'deterministicTarget',
  'spawnBall',
  'lattice_visual_history',
  'Δ МАССЫ К ТЕЙКУ',
  'Δ МАССЫ К СТОПУ',
  'НЕ СЛОЖЕНЫ В КРАЙНИЕ КОРЗИНЫ',
]) {
  assert.ok(source.includes(text), `live Galton board must mention ${text}`);
}
assert.ok(!source.includes('Math.random'), 'Galton paths and target bins must remain deterministic');
assert.ok(source.includes('requestAnimationFrame'), 'balls must remain a live animated board');
console.log('live deterministic Galton revaluation contract ok');
