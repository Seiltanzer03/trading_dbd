import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(
  new URL('../../seiltanzer/web/js/lattice_revaluation_ui.js', import.meta.url),
  'utf8',
);

for (const text of [
  'ВХОД → СРЕДНЕЕ → СЕЙЧАС',
  'Переток массы',
  'option_distribution',
  'confidence_weight',
]) {
  assert.ok(source.includes(text), `UI contract must mention ${text}`);
}
assert.ok(source.includes('new WebSocket'));
assert.ok(source.includes('lattice_revaluation'));
console.log('lattice revaluation UI contract ok');
