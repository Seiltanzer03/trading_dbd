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
<style>html,body{margin:0}#plot{width:390px;height:430px;touch-action:none}</style>
<div id="plot"></div>
<script src="/seiltanzer/web/vendor/plotly-gl3d.min.js"></script>
<script type="module">
  import { createPlotlyCameraGuard } from '/seiltanzer/web/js/plotly_camera_guard.js';
  const INIT = { eye:{x:0.15,y:2.3,z:0.65}, center:{x:0,y:0,z:0}, up:{x:0,y:0,z:1} };
  const el = document.getElementById('plot');
  const guard = createPlotlyCameraGuard(el, INIT);
  const z = Array.from({length:12}, (_,r) => Array.from({length:12}, (_,c) =>
    Math.exp(-((c-5.5)*(c-5.5)+(r-5.5)*(r-5.5))/20)));
  const traces = [{type:'surface', z, showscale:false}];
  const layout = {margin:{l:0,r:0,t:0,b:0},uirevision:'e2e-ui',scene:{
    camera:INIT,uirevision:'e2e-camera',dragmode:'orbit',aspectmode:'cube'}};
  await Plotly.newPlot(el, traces, layout, {responsive:true,scrollZoom:true,displayModeBar:false});
  guard.arm();
  window.__fixture = {el,guard,INIT,traces,layout};
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
    const make = (p) => new Touch({
      identifier: p.id,
      target: el,
      clientX: p.x,
      clientY: p.y,
      pageX: p.x,
      pageY: p.y,
      screenX: p.x,
      screenY: p.y,
      radiusX: 8,
      radiusY: 8,
      force: 0.5,
    });
    const touches = points.map(make);
    const changedTouches = changed.map(make);
    el.dispatchEvent(new TouchEvent(type, {
      bubbles: true,
      cancelable: true,
      composed: true,
      touches,
      targetTouches: touches,
      changedTouches,
    }));
  }, { type, points, changed });
}

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
  busy: window.__seiltanzer3dBusy,
}));
assert.notDeepEqual(rotated.rendered.eye, initial.eye, 'one-finger iOS gesture must rotate camera');
assert.deepEqual(rotated.rendered, rotated.saved, 'rendered and saved camera must agree after touchend');
assert.equal(rotated.state, 'idle');
assert.equal(rotated.busy, false);

// Reproduce the old rollback: a structural react explicitly supplies INIT_CAM.
await page.evaluate(async () => {
  const { el, traces, INIT } = window.__fixture;
  await Plotly.react(el, traces, {
    margin:{l:0,r:0,t:0,b:0},
    uirevision:'e2e-ui',
    scene:{camera:INIT,uirevision:'e2e-camera',dragmode:'orbit',aspectmode:'cube'},
  }, {responsive:true,scrollZoom:true,displayModeBar:false});
});
await page.waitForTimeout(250);
const afterReact = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
assert.deepEqual(afterReact, rotated.saved, 'Plotly.react must not restore the initial camera');

const radius = (camera) => {
  const c = camera.center || {x:0,y:0,z:0};
  return Math.hypot(camera.eye.x-c.x, camera.eye.y-c.y, camera.eye.z-c.z);
};
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
const zoomed = await page.evaluate(() => ({
  rendered: structuredClone(window.__fixture.el._fullLayout.scene.camera),
  saved: window.__fixture.guard.getSavedCamera(),
}));
assert(radius(zoomed.rendered) < radius(afterReact) * 0.8, 'two-finger spread must zoom in');
assert.deepEqual(zoomed.rendered, zoomed.saved, 'pinch zoom must persist after touchend');

// A real container resize is allowed, but it must carry the saved camera.
await page.setViewportSize({ width: 430, height: 844 });
await page.waitForTimeout(500);
const afterResize = await page.evaluate(() => structuredClone(window.__fixture.el._fullLayout.scene.camera));
assert.deepEqual(afterResize, zoomed.saved, 'ResizeObserver resize must preserve camera');

console.log(JSON.stringify({
  realWebKit: true,
  customIOSTouch: await page.evaluate(() => window.__fixture.guard.usesCustomIOSTouch()),
  rotationRetainedAfterReact: true,
  pinchZoomRetained: true,
  resizeRetained: true,
}));

await context.close();
await browser.close();
await new Promise((resolve) => server.close(resolve));
