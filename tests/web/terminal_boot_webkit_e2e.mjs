import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { webkit } from 'playwright';

const web = new URL('../../seiltanzer/web/', import.meta.url);
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
const journal = [{ id: 144, setup: 7, instrument: 'SP500', direction: 'long', entry: 6000,
  stop: 5980, take: 6040, status: 'open', opened_at: 1795089600 }];
const tick = { ts: 1795099900, instrument: 'SP500', trade: journal[0], demo: false,
  account: { name: 'TERMINAL', balance: 25000, acc_size: 25000, balance_pct: 100,
    phase: 'funded', risk: { risk_pct: 1, base_risk_pct: 1, target_rr: 2, mode: 'fixture' } },
  feeds: { price: { value: 6010 }, chain: {}, vols: {} },
  state: { r: .5, T: 2, to_take_r: 1.5, to_stop_r: 1.5, headline: 'РЫНОЧНЫЕ ДАННЫЕ' } };
let stateRequests = 0;
let allowState = false;
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://127.0.0.1').pathname;
  const json = (body) => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify(body));
  };
  if (path === '/api/state') {
    stateRequests += 1;
    if (allowState) {
      // This cached state predates the authoritative journal and socket reads.
      return json({ tick: { ...tick, ts: tick.ts - 1, trade: null, state: null },
        journal: [], setups: [], ridge: null, ai_history: [] });
    }
    res.writeHead(504, { 'content-type': 'text/html' });
    return res.end('<h1>Gateway Timeout</h1>');
  }
  if (path === '/api/journal') return json(journal);
  if (path.startsWith('/api/')) return json({});
  if (path === '/static/vendor/plotly-gl3d.min.js') {
    // Charts are deliberately slow. They must not own the first journal paint.
    return;
  }
  try {
    const name = path === '/' ? 'index.html' : path.replace(/^\/static\//, '');
    const body = await readFile(fileURLToPath(new URL(name, web)));
    res.writeHead(200, { 'content-type': types[name.slice(name.lastIndexOf('.'))] || 'application/octet-stream' });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await webkit.launch();
  const page = await browser.newPage();
  await page.route(/^https:\/\/cdnjs\.cloudflare\.com\//, (route) => route.abort());
  let socket;
  await page.routeWebSocket('**/ws', (ws) => { socket = ws; });
  await page.goto(`http://127.0.0.1:${server.address().port}/`, { waitUntil: 'commit' });
  await page.locator('#journal-table tbody').getByText('144', { exact: true }).waitFor({ timeout: 4500 });
  assert.match(await page.locator('#hdr-setup').innerText(), /144.*SP500/);
  assert.equal(await page.locator('#btn-new-trade').isDisabled(), true);
  assert.equal(await page.locator('#btn-close-trade').isHidden(), true);
  assert.ok(socket, 'WebSocket opens independently of the failed full state');
  socket.send(JSON.stringify(tick));
  await page.locator('#panel-state').waitFor({ state: 'visible' });
  allowState = true;
  await page.locator('#terminal-loading').waitFor({ state: 'hidden', timeout: 6000 });
  assert.ok(stateRequests >= 2);
  assert.equal(await page.locator('#journal-table tbody tr').count(), 1);
  assert.match(await page.locator('#hdr-setup').innerText(), /SP500/);
  assert.equal(await page.locator('#panel-state').isVisible(), true);
  console.log('Terminal boots through 504 and delayed charts, preserving the newer live position and journal');
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
