import fs from 'node:fs';

const html = fs.readFileSync('seiltanzer/web/universe.html', 'utf8');
const js = fs.readFileSync('seiltanzer/web/js/llm_edge_researcher_ui.js', 'utf8');

for (const id of [
  'llm-edge-researcher-summary',
  'llm-edge-researcher-candidates',
]) {
  if (!html.includes(`id="${id}"`)) throw new Error(`missing ${id}`);
}
if (!html.includes('/static/js/llm_edge_researcher_ui.js')) {
  throw new Error('Researcher UI module is not loaded by Universe');
}
if (!js.includes('/api/research/g1s/edge-researcher/lifecycle')) {
  throw new Error('Researcher UI must read the materialized lifecycle endpoint');
}
for (const label of ['COLLECTING', 'CONFIRMED', 'ACTIVE', 'REJECTED']) {
  if (!js.includes(label)) throw new Error(`missing lifecycle label ${label}`);
}
for (const forbidden of ['/propose', '/evaluate', 'POST']) {
  if (js.includes(forbidden)) throw new Error(`UI must remain read-only: ${forbidden}`);
}
console.log('llm edge researcher UI smoke: ok');
