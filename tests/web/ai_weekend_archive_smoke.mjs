import fs from 'node:fs';
import assert from 'node:assert/strict';

// Exercise the actual modal callback, including its request and trade guards.
const source = fs.readFileSync('seiltanzer/web/js/app.js', 'utf8');
const callback = source.slice(source.indexOf("$('#btn-ai-verdict').addEventListener"),
  source.indexOf('\nasync function apiPost(', source.indexOf("$('#btn-ai-verdict').addEventListener")));
class Element {
  constructor() { this.children = []; this.listeners = {}; this.textContent = ''; }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
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
  const archives = [];
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
    assert.equal(url, '/api/ai/history?include_management=true');
    beforeHistory?.(state, nodes);
    if (historyError) throw new Error('history unavailable');
    return history ?? {trade_id: 7, items: [
      {ts: 1_700_000_000, verdict: 'older'},
      {ts: 1_700_000_100, verdict: 'HOLD ПОДТВЕРЖДЁН <img src=x onerror=alert(1)>', model: 'saved-model',
        management_archive: {available: true, execution_allowed: false, captured_ts: 1_700_000_000,
          decision: {policy: 'HOLD'}, unified_edge_ensemble: {selected_policy: 'HOLD'}}}]};
  };
  const noDecision = () => { throw new Error('archive cannot mount execution controls'); };
  new Function('$','openModal','closeModal','fetchStructured','mountArmedShadowActions',
    'mountEdgeManagement','mountManagementDecision','mountShadowWorkingAction','apiPost',
    'refreshJournalAndSetups','refreshAiHistory','S','document','mountArchivedManagement',callback)(
    (id) => nodes.get(id), openModal, () => {}, fetchStructured,
    (container, actions) => {
      for (const action of actions || []) {
        const control = new Element(); control.textContent = action.action_id;
        container.appendChild(control);
      }
    }, noDecision, noDecision, noDecision, noDecision, noDecision, noDecision,
    state, {createElement: () => new Element()}, (container, archive) => { archives.push(archive); });
  await nodes.get('#btn-ai-verdict').click();
  return {nodes, state, calls, archives, out: nodes.get('#ai-verdict-text'),
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
assert.deepEqual(available.calls.at(-1), {url: '/api/ai/history?include_management=true', method: 'GET'});
assert.equal(available.archives.length, 1);
assert.equal(available.archives[0].decision.policy, 'HOLD');

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
// The normal history entry point must expose the same paired saved context.
const historyCallback = source.slice(source.indexOf("$('#btn-ai-history').addEventListener"),
  source.indexOf("$('#btn-ai-verdict').addEventListener"));
async function historyFixture({wrongTrade=false, replaced=false}={}) {
  const nodes = new Map([['#btn-ai-history',new Element()]]);
  const archives = [];
  const calls = [];
  const state = {tick:{trade:{id:7}},aiHistory:[{ts:1700000000,verdict:'old cached text'}]};
  const open = () => { for (const id of ['#ai-history-list','#ai-history-close']) nodes.set(id,new Element()); };
  new Function('$','openModal','closeModal','S','document','fetchStructured','mountArchivedManagement',historyCallback)(
    (id)=>nodes.get(id),open,()=>{},state,{createElement:()=>new Element()},async(url)=>{
      calls.push(url);
      if(replaced) nodes.set('#ai-history-list',new Element());
      return {trade_id:wrongTrade?8:7,items:[{ts:1700000100,verdict:'paired saved text',
        management_archive:{available:true,execution_allowed:false,decision:{policy:'CLOSE_25'}}}]};
    },(_container,archive)=>archives.push(archive));
  await nodes.get('#btn-ai-history').click();
  return {nodes,archives,calls};
}
const normalHistory=await historyFixture();
assert.deepEqual(normalHistory.calls,['/api/ai/history?include_management=true']);
assert.equal(normalHistory.archives[0].decision.policy,'CLOSE_25');
for(const opts of [{wrongTrade:true},{replaced:true}]) {
  const invalid=await historyFixture(opts);
  assert.equal(invalid.archives.length,0);
}
console.log('Weekend archive modal smoke: PASS');
