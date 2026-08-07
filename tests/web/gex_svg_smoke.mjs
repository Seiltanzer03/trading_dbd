import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../../seiltanzer/web/js/gex.js', import.meta.url), 'utf8');

// GEX no longer uses the legacy LOCAL SVG renderer. The current production
// architecture is Canvas for MIGRATION/SNAPSHOT plus Plotly gl3d for PRESSURE.
assert.ok(source.includes("pressure.textContent = 'PRESSURE 3D'"));
assert.ok(source.includes("cv.dataset.renderer = 'migration'"));
assert.ok(source.includes("currentMode === 'SNAPSHOT'"));
assert.ok(source.includes('createPlotlyCameraGuard'));
assert.ok(source.includes('analyticsMobileDpr'));
assert.ok(!source.includes('window.echarts'));

console.log('gex current renderer smoke: ok');
