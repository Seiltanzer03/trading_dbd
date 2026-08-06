import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice.js', import.meta.url),
  'utf8',
);

for (const text of [
  'ПУНКТИР — ВХОД',
  'СЕРАЯ — СРЕДНЕЕ',
  'ОРАНЖЕВАЯ — CURRENT RND',
  '1 ПРИЗЕМЛЕНИЕ = 1 ВКЛАД',
  'ЧЁРНАЯ — ЭМПИРИКА ШАРИКОВ',
  'deterministicTarget',
  'empiricalKernelDistribution',
  'advanceBallKinematics',
  'spawnBall',
  's.samples.push(ball.targetR)',
  'IMPACT_HOLD_MS',
  'landingPoint',
  'lattice_visual_history',
  'НЕ СЛОЖЕНЫ В КРАЙНИЕ КОРЗИНЫ',
]) {
  assert.ok(source.includes(text), `real-contribution Galton board must mention ${text}`);
}
assert.ok(!source.includes('Math.random'), 'Galton paths and target bins must remain deterministic');
assert.ok(source.includes('requestAnimationFrame'), 'balls must remain a live animated board');
console.log('real landed-ball Galton contract ok');
