import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync('seiltanzer/web/js/plotly_terminal_toolbar.js', 'utf8');
for (const label of ['ORBIT', 'TURNTABLE', 'PAN', 'ZOOM', 'RESET', 'HOME']) {
  assert.ok(source.includes(`'${label}'`), `${label} control must remain`);
}
for (const mode of ['orbit', 'turntable', 'pan', 'zoom']) {
  assert.ok(source.includes(`setMode('${mode}')`), `${mode} must remain a user drag mode`);
}
assert.ok(!source.includes('requestAnimationFrame'), 'toolbar must not auto-rotate');
assert.ok(source.includes('rememberExternalCamera'), 'toolbar camera writes must update the camera guard owner');
assert.ok(source.includes('guard?.getDragMode'), 'toolbar must read drag mode from the guard owner');
assert.ok(source.includes('owner?.setDragMode'), 'toolbar must write drag mode through the guard owner');
assert.ok(source.includes('existing?.__terminal3dPlot === plot'), 'same plot must reuse its toolbar instance');
assert.ok(source.includes('onDragMode'), 'toolbar active state must subscribe to guard mode changes');

console.log(JSON.stringify({
  terminalToolbar: true,
  autoRotate: false,
  guardOwnedCamera: true,
  guardOwnedDragMode: true,
  stableInstance: true,
}));
