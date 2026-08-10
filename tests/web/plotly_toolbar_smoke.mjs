import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync('seiltanzer/web/js/plotly_terminal_toolbar.js', 'utf8');
for (const label of ['ORBIT', 'TURNTABLE', 'PAN', 'ZOOM', 'RESET', 'HOME']) {
  assert.ok(source.includes(`'${label}'`), `${label} control must remain`);
}
for (const mode of ['orbit', 'turntable', 'pan', 'zoom']) {
  assert.ok(source.includes(`'scene.dragmode': '${mode}'`), `${mode} must be a user drag mode`);
}
assert.ok(!source.includes('requestAnimationFrame'), 'toolbar must not auto-rotate');
assert.ok(source.includes('rememberExternalCamera'), 'toolbar camera writes must update the camera guard owner');

console.log(JSON.stringify({ terminalToolbar: true, autoRotate: false, guardOwnedCamera: true }));
