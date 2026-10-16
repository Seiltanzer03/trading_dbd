import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Execute the actual embedded UI renderer in the existing Node/browser lane.
// A Python subprocess startup timeout is not a renderer/API latency measure.
const source = fs.readFileSync('seiltanzer/g1_intelligence_page_refinement.py', 'utf8');
const start = source.indexOf(' function renderLocal(');
const end = source.indexOf(' async function refreshTwoSpeed', start);
assert.ok(start >= 0 && end > start, 'actual local-edge renderer must exist');
const host = {};
const context = vm.createContext({
  document: {querySelector: () => host},
  fmt: n => n == null ? '—' : String(n),
});
vm.runInContext(source.slice(start, end), context, {timeout: 1000});
const cases = [
  ['READY', true, null, 'Возраст снимка'],
  ['BUILDING', true, null, 'обновляется'],
  ['BUILDING', false, null, 'ещё не построен'],
  ['BUILDING', true, 'ValueError', 'ошибкой'],
];
for (const [state, cached, error, expected] of cases) {
  context.status = {};
  context.evidence = {report_materialization: {
    presentation_state: state, cached_snapshot: cached,
    snapshot_age_sec: 5, last_error_class: error,
  }};
  vm.runInContext('renderLocal(status, evidence)', context, {timeout: 1000});
  assert.ok(host.innerHTML.includes(expected), `${state}/${cached}/${error}: ${host.innerHTML}`);
  if (!cached) {
    assert.ok(!host.innerHTML.includes('ещё созревают'), 'missing report is not an observed immature cohort');
  }
}
console.log('g1m local-edge cache UI: 4 cases PASS');
