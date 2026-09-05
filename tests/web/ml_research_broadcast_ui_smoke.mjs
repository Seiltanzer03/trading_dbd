import fs from 'node:fs';

const html = fs.readFileSync('seiltanzer/web/ml_research.html', 'utf8');
const js = fs.readFileSync('seiltanzer/web/js/ml_research_broadcast.js', 'utf8');
for (const id of ['freshness-banner', 'worker-state', 'pipeline', 'training-horizons', 'hypotheses', 'recent-runs']) {
  if (!html.includes(`id="${id}"`)) throw new Error(`missing ${id}`);
}
if (!html.includes('/static/js/ml_research_broadcast.js')) throw new Error('module not mounted');
if (!js.includes('/api/research/ml-broadcast')) throw new Error('read-only API not used');
for (const forbidden of ['Math.random', 'WebSocket(']) {
  if (js.includes(forbidden)) throw new Error(`forbidden simulated/mutating path: ${forbidden}`);
}
console.log('ml research broadcast UI smoke: ok');

