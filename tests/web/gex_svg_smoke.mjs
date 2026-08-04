import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../../seiltanzer/web/js/gex.js', import.meta.url), 'utf8');
assert.ok(source.includes('LOCAL SVG'));
assert.ok(!source.includes('window.echarts'));
assert.ok(source.includes('<svg'));
console.log('gex local SVG smoke: ok');
