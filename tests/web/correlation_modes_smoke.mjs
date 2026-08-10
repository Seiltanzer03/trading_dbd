import assert from 'node:assert/strict';
import fs from 'node:fs';

const html = fs.readFileSync('seiltanzer/web/index.html', 'utf8');
const source = fs.readFileSync('seiltanzer/web/js/correlation.js', 'utf8');
for (const mode of ['full', 'material', 'stress']) assert.ok(html.includes(`btn-corr-${mode}`));
assert.ok(source.includes("if (mode === 'MATERIAL')"));
assert.ok(source.includes("if (mode === 'STRESS')"));
assert.ok(source.includes('return links.slice()'), 'FULL must preserve every observed link');
assert.ok(source.includes('SHOWN LINKS ${activeLinks.length} / OBSERVED ${links.length}'));
assert.ok(source.includes('const packets = [.5 + .5 * phase, .5 - .5 * phase]'), 'packets must be direction-neutral');
assert.ok(!source.includes('const speed = .035 +'), 'correlation packets must have no decorative base speed');
assert.ok(!source.includes('now / 14'), 'live-node ring must not be clock-driven');

console.log(JSON.stringify({ fullTopology: true, modes: 3, directionNeutral: true }));
