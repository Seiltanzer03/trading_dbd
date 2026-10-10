import fs from 'node:fs';
import assert from 'node:assert/strict';

// Exercise the actual modal callback, including its request and trade guards.
const source = fs.readFileSync('seiltanzer/web/js/app.js', 'utf8');
const callback = source.slice(source.indexOf("$('#btn-ai-verdict').addEventListener"),
  source.indexOf('\nasync function apiPost(', source.indexOf("$('#btn-ai-verdict').addEventListener")));
class Element {
  constructor() { this.children = []; this.listeners = {}; this.textContent = ''; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  prepend(child) { child.parentElement = this; this.children.unshift(child); }
  replaceChildren() { this.children = []; this.textContent = ''; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  click() { return this.listeners.click(); }
}
async function fixture({code = 'authoritative_price_unavailable', history, historyError, beforeHistory,
  reopenDuringFirstPosition = false} = {}) {
  const nodes = new Map([['#btn-ai-verdict', new Element()]]);
  const state = {tick: {trade: {id: 7}}};
  const calls = [];
  const openModal = () => {
    for (const id of ['ai-verdict-text','ai-edge-management','ai-management-execution',
      'ai-shadow-action','ai-armed-actions','ai-close']) nodes.set('#' + id, new Element());
    new Element().appendChild(nodes.get('#ai-close'));
  };
  const fetchStructured = async (url, init) => {
    calls.push({url, method: init?.method || 'GET'});
    if (url === '/api/position') {
      if (reopenDuringFirstPosition && calls.filter((call) => call.url === url).length === 1) {
        await nodes.get('#btn-ai-verdict').click();
        await nodes.get('#ai-close').parentElement.children[0].click();
        return {shadow_actions: [{action_id: 'old-modal-action'}]};
      }
      return {shadow_actions: []};
    }
    if (url === '/api/ai/verdict') throw Object.assign(new Error('Нет авторитетной цены'),
      {status: 503, code, requestId: 'ai-weekend'});
    assert.equal(url, '/api/ai/history');
    beforeHistory?.(state, nodes);
    if (historyError) throw new Error('history unavailable');
    return history ?? {trade_id: 7, items: [
      {ts: 1_700_000_000, verdict: 'older'},
      {ts: 1_700_000_100, verdict: 'HOLD ПОДТВЕРЖДЁН <img src=x onerror=alert(1)>', model: 'saved-model'}]};
  };
  const noDecision = () => { throw new Error('archive cannot mount execution controls'); };
  new Function('$','openModal','closeModal','fetchStructured','mountArmedShadowActions',
    'mountEdgeManagement','mountManagementDecision','mountShadowWorkingAction','apiPost',
    'refreshJournalAndSetups','refreshAiHistory','S','document',callback)(
    (id) => nodes.get(id), openModal, () => {}, fetchStructured,
    (container, actions) => {
      for (const action of actions || []) {
        const control = new Element(); control.textContent = action.action_id;
        container.appendChild(control);
      }
    }, noDecision, noDecision, noDecision, noDecision, noDecision, noDecision,
    state, {createElement: () => new Element()});
  await nodes.get('#btn-ai-verdict').click();
  return {nodes, state, calls, out: nodes.get('#ai-verdict-text'),
    button: nodes.get('#ai-close').parentElement.children.find((node) => node !== nodes.get('#ai-close'))};
}

const available = await fixture();
assert.ok(available.button, 'quote rejection must offer the existing saved review');
assert.match(available.button.textContent, /СОХРАНЁННЫЙ РАЗБОР/);
assert.equal(available.calls.filter((call) => call.method === 'POST').length, 1);
await available.button.click();
assert.match(available.out.textContent, /АРХИВНЫЙ РАЗБОР/);
assert.match(available.out.textContent, /НЕ НОВОЕ РЕШЕНИЕ/);
assert.match(available.out.textContent, /Сохранён:/);
assert.match(available.out.textContent, /ai-weekend/);
assert.ok(available.out.textContent.includes('HOLD ПОДТВЕРЖДЁН <img src=x onerror=alert(1)>'));
assert.equal(available.calls.filter((call) => call.method === 'POST').length, 1);
assert.deepEqual(available.calls.at(-1), {url: '/api/ai/history', method: 'GET'});

for (const options of [
  {history: {trade_id: 8, items: [{ts: 1_700_000_100, verdict: 'other trade'}]}},
  {history: {trade_id: 7, items: []}},
  {history: {trade_id: 7, items: [{ts: null, verdict: 'invalid date'}]}},
  {historyError: true},
  {beforeHistory: (state) => { state.tick.trade.id = 8; }},
]) {
  const test = await fixture(options);
  await test.button.click();
  assert.doesNotMatch(test.out.textContent, /АРХИВНЫЙ РАЗБОР/);
  assert.match(test.out.textContent, /HTTP 503/);
}
const unrelated = await fixture({code: 'some_other_error'});
assert.equal(unrelated.button, undefined);
const dismissed = await fixture({beforeHistory: (_state, nodes) => {
  nodes.set('#ai-verdict-text', new Element());
}});
await dismissed.button.click();
assert.equal(dismissed.nodes.get('#ai-verdict-text').textContent, '');
const racing = await fixture({reopenDuringFirstPosition: true});
assert.match(racing.out.textContent, /АРХИВНЫЙ РАЗБОР/);
assert.equal(racing.nodes.get('#ai-armed-actions').children.length, 0,
  'a delayed response from a replaced modal must not restore archive ACK controls');
assert.equal(racing.calls.filter((call) => call.method === 'POST').length, 1,
  'a replaced modal must stop before issuing another live AI request');
console.log('Weekend archive modal smoke: PASS');
