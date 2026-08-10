import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { webkit } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const graph = {
  available: true,
  nodes: [
    { id: 'NAS', group: 'equity', x_norm: .2, y_norm: .4, coupling: .7 },
    { id: 'SP500', group: 'equity', x_norm: .5, y_norm: .3, coupling: .6 },
    { id: 'VIX', group: 'volatility', x_norm: .8, y_norm: .6, coupling: .5 },
  ],
  links: [
    { source: 'NAS', target: 'SP500', correlation: .72, tension: .01, status: 'STABLE' },
    { source: 'NAS', target: 'VIX', correlation: -.10, tension: .01, status: 'STABLE' },
    { source: 'SP500', target: 'VIX', correlation: -.16, tension: .21, status: 'BREAK_ALERT' },
  ],
  break_alerts: [],
  summary: { observed_pairs: 3, systemic_coupling: .33, network_tension: .08, fragmentation: .2 },
};

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/fixture') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      res.end(`<!doctype html><meta name="viewport" content="width=device-width">
        <section id="panel-correlation">
          <button id="btn-corr-full" class="active">FULL</button>
          <button id="btn-corr-material">MATERIAL</button>
          <button id="btn-corr-stress">STRESS</button>
          <button id="btn-corr-network" class="active">NETWORK</button>
          <button id="btn-corr-matrix">MATRIX</button>
          <span id="corr-status"></span><div id="corr-human-line"></div>
          <div id="corr-chart" style="width:760px;height:350px"></div>
          <div id="corr-empty"></div><div id="corr-interpretation"></div>
        </section>
        <script type="module">
          window.fetch = async () => new Response(${JSON.stringify(JSON.stringify(graph))},
            {status:200,headers:{'content-type':'application/json'}});
          window.IntersectionObserver = class { constructor(cb){this.cb=cb} observe(){this.cb([{isIntersecting:true}])} };
          const { initCorrelation } = await import('/seiltanzer/web/js/correlation.js');
          initCorrelation();
          window.__ready = true;
        </script>`);
      return;
    }
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file = path.resolve(ROOT, relative);
    if (!file.startsWith(ROOT) || !(await stat(file)).isFile()) throw new Error('not found');
    res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript' : 'text/plain' });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404); res.end('not found');
  }
});

await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const browser = await webkit.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
await page.goto(`http://127.0.0.1:${server.address().port}/fixture`);
await page.waitForFunction(() => window.__ready && document.querySelector('#corr-status').textContent.includes('3 / OBSERVED 3'));

await page.click('#btn-corr-matrix');
await page.click('#btn-corr-material');
await page.waitForFunction(() => document.querySelector('#corr-status').textContent.includes('MATERIAL · SHOWN LINKS 2 / OBSERVED 3'));
assert.equal(await page.locator('#btn-corr-network').getAttribute('aria-pressed'), 'true',
  'a link filter selected from MATRIX must open NETWORK');
assert.equal(await page.locator('#btn-corr-material').getAttribute('aria-pressed'), 'true');

await page.click('#btn-corr-stress');
await page.waitForFunction(() => document.querySelector('#corr-status').textContent.includes('STRESS · SHOWN LINKS 1 / OBSERVED 3'));
assert.equal(await page.locator('#btn-corr-stress').getAttribute('aria-pressed'), 'true');

console.log(JSON.stringify({ realWebKit: true, matrixToMaterial: '2/3', stress: '1/3' }));
await browser.close();
await new Promise((resolve) => server.close(resolve));
