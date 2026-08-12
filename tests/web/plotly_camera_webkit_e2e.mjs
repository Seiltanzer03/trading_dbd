import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { webkit } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/fixture') {
      res.writeHead(200, { 'content-type': MIME['.html'], 'cache-control': 'no-store' });
      res.end(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>html,body{margin:0}#holder{width:390px;height:430px;position:relative}#plot{width:100%;height:100%;touch-action:none}</style>
<div id="holder"><div id="plot"></div></div>
<script src="/seiltanzer/web/vendor/plotly-gl3d.min.js"></script>
<script type="module">
  import { createPlotlyCameraGuard } from '/seiltanzer/web/js/plotly_camera_guard.js';
  import { attachTerminal3DToolbar } from '/seiltanzer/web/js/plotly_terminal_toolbar.js';
  const INIT = { eye:{x:0.15,y:2.3,z:0.65}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1} };
  const el = document.getElementById('plot');
  const holder = document.getElementById('holder');
  const guard = createPlotlyCameraGuard(el, INIT);
  const z = Array.from({length:12}, (_,r) => Array.from({length:12}, (_,c) =>
    Math.exp(-((c-5.5)*(c-5.5)+(r-5.5)*(r-5.5))/20)));
  const traces = [{type:'surface', z, showscale:false}];
  const layout = {margin:{l:0,r:0,t:0,b:0},uirevision:'e2e-ui',scene:{
    camera:INIT,uirevision:'e2e-camera',dragmode:'orbit',aspectmode:'cube'}};
  await Plotly.newPlot(el, traces, layout, {responsive:true,scrollZoom:true,displayModeBar:false});
  guard.arm();
  const toolbar = attachTerminal3DToolbar({plot:el,container:holder,guard,homeCamera:INIT,key:'e2e'});
  window.__fixture = {el,holder,guard,toolbar,INIT,traces,layout,attachTerminal3DToolbar};
</script>`);
      return;
    }
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file = path.resolve(ROOT, relative);
    if (!file.startsWith(ROOT) || !(await stat(file)).isFile()) throw new Error('not found');
    res.writeHead(200, {
      'content-type': MIME[path.extname(file)] || 'application/octet-stream',
      'cache-control': 'no-store',
    });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404); res.end('not found');
  }
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const port = server.address().port;
const browser = await webkit.launch({ headless: true });
const context = await browser.newContext({
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0 Mobile/15E148 Safari/604.1',
  viewport: { width: 390, height: 844 },
  screen: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  isMobile: true,
  hasTouch: true,
});
const page = await context.newPage();
page.on('console', (msg) => {
  if (msg.type() === 'error') console.error('[webkit console]', msg.text());
});
await page.goto(`http://127.0.0.1:${port}/fixture`, { waitUntil: 'networkidle' });
await page.waitForFunction(() => window.__fixture?.el?._fullLayout?.scene?.camera);

async function dispatchTouch(type, points, changed = points) {
  await page.evaluate(({ type, points, changed }) => {
    const { el } = window.__fixture;
    const make = (p) => ({
      identifier: p.id, target: el,
      clientX: p.x, clientY: p.y, pageX: p.x, pageY: p.y,
      screenX: p.x, screenY: p.y, radiusX: 8, radiusY: 8, force: 0.5,
    });
    const touches = points.map(make);
    const changedTouches = changed.map(make);
    const event = new Event(type, { bubbles: true, cancelable: true, composed: true });
    Object.defineProperties(event, {
      touches: { value: touches, enumerable: true },
      targetTouches: { value: touches, enumerable: true },
      changedTouches: { value: changedTouches, enumerable: true },
    });
    el.dispatchEvent(event);
  }, { type, points, changed });
}

const radius = (camera) => {
  const c = camera.center || {x:0,y:0,z:0};
  return Math.hypot(camera.eye.x-c.x, camera.eye.y-c.y, camera.eye.z-c.z);
};
const vector = (camera) => {
  const c = camera.center || {x:0,y:0,z:0};
  return {x:camera.eye.x-c.x,y:camera.eye.y-c.y,z:camera.eye.z-c.z};
};
const distance = (a, b) => Math.hypot(a.x-b.x, a.y-b.y, a.z-b.z);
const cameraGeometry = (camera) => ({
  eye: camera.eye,
  center: camera.center || {x:0,y:0,z:0},
  up: camera.up || {x:0,y:0,z:1},
});

const initial = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
await dispatchTouch('touchstart', [{ id: 1, x: 170, y: 240 }]);
assert.equal(await page.evaluate(() => window.__seiltanzer3dBusy), true);
await dispatchTouch('touchmove', [{ id: 1, x: 285, y: 165 }]);
await page.waitForTimeout(100);
await dispatchTouch('touchend', [], [{ id: 1, x: 285, y: 165 }]);
await page.waitForTimeout(650);

const rotated = await page.evaluate(() => ({
  rendered: structuredClone(window.__fixture.el._fullLayout.scene.camera),
  saved: window.__fixture.guard.getSavedCamera(),
  state: window.__fixture.guard.getState(),
  mode: window.__fixture.guard.getDragMode(),
  busy: window.__seiltanzer3dBusy,
}));
assert.notDeepEqual(rotated.rendered.eye, initial.eye, 'ORBIT one-finger iOS gesture must rotate camera');
assert.deepEqual(rotated.rendered, rotated.saved, 'rendered and saved camera must agree after touchend');
assert.equal(rotated.mode, 'orbit');
assert.equal(rotated.state, 'idle');
assert.equal(rotated.busy, false);

await page.getByRole('button', { name: 'Pan drag mode' }).click();
assert.equal(await page.evaluate(() => window.__fixture.guard.getDragMode()), 'pan');
const beforePan = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
await dispatchTouch('touchstart', [{ id: 1, x: 180, y: 235 }]);
await dispatchTouch('touchmove', [{ id: 1, x: 270, y: 180 }]);
await page.waitForTimeout(100);
await dispatchTouch('touchend', [], [{ id: 1, x: 270, y: 180 }]);
await page.waitForTimeout(600);
const afterPan = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
assert(distance(afterPan.center, beforePan.center) > 0.05, 'PAN must move scene.camera.center');
assert(distance(vector(afterPan), vector(beforePan)) < 1e-5, 'PAN must preserve the eye-center vector');

const toolbarIdentity = await page.evaluate(async () => {
  const { el, holder, traces, INIT, toolbar, guard, attachTerminal3DToolbar } = window.__fixture;
  await Plotly.react(el, traces, {
    margin:{l:0,r:0,t:0,b:0}, uirevision:'e2e-ui',
    scene:{camera:INIT,uirevision:'e2e-camera',dragmode:'orbit',aspectmode:'cube'},
  }, {responsive:true,scrollZoom:true,displayModeBar:false});
  const again = attachTerminal3DToolbar({plot:el,container:holder,guard,homeCamera:INIT,key:'e2e'});
  return {
    same: toolbar === again,
    count: holder.querySelectorAll('[data-terminal-3d-toolbar="e2e"]').length,
    mode: guard.getDragMode(),
    active: again.querySelector('button.active')?.dataset.dragMode,
    camera: structuredClone(el._fullLayout.scene.camera),
  };
});
assert.equal(toolbarIdentity.same, true, 're-attach must reuse the existing toolbar');
assert.equal(toolbarIdentity.count, 1, 'toolbar must never duplicate');
assert.equal(toolbarIdentity.mode, 'pan', 'react must not reset selected drag mode');
assert.equal(toolbarIdentity.active, 'pan', 'toolbar active state must follow guard owner');
assert.deepEqual(toolbarIdentity.camera, afterPan, 'react must preserve the panned camera');

await page.getByRole('button', { name: 'Zoom drag mode' }).click();
const beforeOneFingerZoom = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
await dispatchTouch('touchstart', [{ id: 1, x: 210, y: 280 }]);
await dispatchTouch('touchmove', [{ id: 1, x: 210, y: 150 }]);
await page.waitForTimeout(100);
await dispatchTouch('touchend', [], [{ id: 1, x: 210, y: 150 }]);
await page.waitForTimeout(600);
const afterOneFingerZoom = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
assert(radius(afterOneFingerZoom) < radius(beforeOneFingerZoom) * 0.8,
  'ZOOM one-finger upward drag must reduce eye radius');

await page.getByRole('button', { name: 'Turntable drag mode' }).click();
const beforeTurntable = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
await dispatchTouch('touchstart', [{ id: 1, x: 165, y: 240 }]);
await dispatchTouch('touchmove', [{ id: 1, x: 285, y: 145 }]);
await page.waitForTimeout(100);
await dispatchTouch('touchend', [], [{ id: 1, x: 285, y: 145 }]);
await page.waitForTimeout(600);
const afterTurntable = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
assert.notDeepEqual(afterTurntable.eye, beforeTurntable.eye, 'TURNTABLE must move the camera');
assert.equal(await page.evaluate(() => window.__fixture.guard.getDragMode()), 'turntable');

await dispatchTouch('touchstart', [
  { id: 1, x: 135, y: 230 },
  { id: 2, x: 255, y: 230 },
]);
await dispatchTouch('touchmove', [
  { id: 1, x: 80, y: 230 },
  { id: 2, x: 310, y: 230 },
]);
await page.waitForTimeout(120);
await dispatchTouch('touchend', [], [
  { id: 1, x: 80, y: 230 },
  { id: 2, x: 310, y: 230 },
]);
await page.waitForTimeout(650);
const pinch = await page.evaluate(() => ({
  rendered: structuredClone(window.__fixture.el._fullLayout.scene.camera),
  saved: window.__fixture.guard.getSavedCamera(),
}));
assert(radius(pinch.rendered) < radius(afterTurntable) * 0.8, 'two-finger spread must zoom in');
assert.deepEqual(pinch.rendered, pinch.saved, 'pinch zoom must persist after touchend');

await page.setViewportSize({ width: 430, height: 844 });
await page.waitForTimeout(500);
const afterResize = await page.evaluate(() => ({
  camera: structuredClone(window.__fixture.el._fullLayout.scene.camera),
  mode: window.__fixture.guard.getDragMode(),
  active: window.__fixture.toolbar.querySelector('button.active')?.dataset.dragMode,
}));
assert.deepEqual(afterResize.camera, pinch.saved, 'ResizeObserver resize must preserve camera');
assert.equal(afterResize.mode, 'turntable', 'resize must preserve selected drag mode');
assert.equal(afterResize.active, 'turntable', 'toolbar must still display guard mode');

await page.getByRole('button', { name: 'Return to terminal home view' }).click();
await page.waitForTimeout(120);
const home = await page.evaluate(() => ({
  camera: structuredClone(window.__fixture.el._fullLayout.scene.camera),
  saved: window.__fixture.guard.getSavedCamera(),
  init: window.__fixture.INIT,
  mode: window.__fixture.guard.getDragMode(),
}));
assert.deepEqual(cameraGeometry(home.camera), cameraGeometry(home.init),
  'HOME must restore the requested eye/center/up geometry');
assert.deepEqual(cameraGeometry(home.saved), cameraGeometry(home.init),
  'guard must own the HOME eye/center/up geometry');
assert.equal(home.mode, 'turntable');

await page.getByRole('button', { name: 'Reset to Plotly default view' }).click();
await page.waitForTimeout(120);
assert.equal(await page.evaluate(() => window.__fixture.guard.getDragMode()), 'turntable');

console.log(JSON.stringify({
  realWebKit: true,
  customIOSTouch: await page.evaluate(() => window.__fixture.guard.usesCustomIOSTouch()),
  orbit: true,
  panMovesCenter: true,
  oneFingerZoom: true,
  turntable: true,
  pinchZoomRetained: true,
  reactRetainsCameraAndMode: true,
  resizeRetainsCameraAndMode: true,
  toolbarInstanceStable: true,
  homeResetExplicitOnly: true,
}));

await context.close();
await browser.close();
await new Promise((resolve) => server.close(resolve));
