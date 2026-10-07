import fs from 'node:fs';
import assert from 'node:assert/strict';

// Exercise the real renderer without launching a server or a market worker.
class Element {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.style = {}; this.value = ''; }
  set textContent(text) { this.value = String(text); this.children = []; }
  get textContent() { return this.value + this.children.map((child) => child.textContent).join('\n'); }
  set innerHTML(_text) { throw new Error('Audit facts must use textContent'); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.value = ''; this.children = children; }
}
globalThis.document = {createElement: (tag) => new Element(tag)};
const source = fs.readFileSync('seiltanzer/web/js/management_ui.js', 'utf8')
  .replace(/^import .*;\n/gm, '').replace('mountG1SEvidencePanel();', '');
const {mountEdgeManagement} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const policies = ['HOLD','CLOSE_10','CLOSE_25','CLOSE_50','EXIT','MOVE_TO_BE',
  'TIGHTEN_STOP','TRAIL_GAMMA_FLIP','EXTEND_TAKE','REDUCE_TAKE','SCALE_OUT_ON_SPIKE','TIME_STOP'];
const candidates = policies.map((policy) => ({candidate_id:policy,policy,
  eligible:true,ranking_eligible:policy!=='EXTEND_TAKE',
  ranking_reason:policy==='EXTEND_TAKE'?'EXPECTED_SACRIFICE_EXCEEDS_BUDGET':null,
  parameters:policy==='TIGHTEN_STOP'?{new_stop:105}:{},
  expected_net_r:.12,cvar10_net_r:-.4,delta_expected_r:-.01,score:.73,
  component_contributions:[{component_id:'current_llm',contribution:.075}]}));
const audit = {available:true,scheme:'balanced',instrument:'NAS100',regime:'TREND',
  selected_candidate_id:'TIGHTEN_STOP',selected_policy:'TIGHTEN_STOP',shared_scenario_bank:true,
  scenario_bank:{bank_id:'comparison-1',source:'frozen_option_driver_comparison',
    exact_authoritative_bank:false,execution_assumption:'piecewise_linear_barrier_fill_no_slippage'},
  candidates,components:[{component_id:'current_llm',nominal_weight:.15,effective_weight:.075,
    availability:'AVAILABLE',quality:.8,age_sec:45,reason:'MATCHED_WORKING_EDGE',
    freshness_multiplier:.95,dependence_multiplier:.5,source_ids:['chain-1'],evidence_family_ids:['OPTIONS']}],
  counterfactuals:[{excluded_component_id:'current_llm',selected_candidate_id:'HOLD',selected_policy:'HOLD'}],
  scheme_comparisons:['balanced','llm20','quant100','legacy_control'].map((scheme)=>({scheme,
    selected_policy:'HOLD',expected_net_r:.13,cvar10_net_r:-.5})),
  edge_families:{order_flow:{status:'UNAVAILABLE',reason:'EXCHANGE_FLOW_FEED_UNAVAILABLE'}}};
const container = new Element();
mountEdgeManagement(container,{available:false,unified_edge_ensemble:audit});
const text = container.textContent;
assert.match(text,/15\.0% → 7\.5%/);
assert.match(text,/повторные доказательства ×0\.500/);
assert.match(text,/свежесть ×0\.950/);
assert.match(text,/Вклад в балл выбранного действия: Текущий LLM \+0\.075/);
assert.match(text,/ΔExpected к HOLD -0\.010R/);
assert.match(text,/Expected \+0\.120R/);
assert.doesNotMatch(text,/\+0\.730R/);
assert.match(text,/EXCHANGE_FLOW_FEED_UNAVAILABLE/);
assert.match(text,/историческая прибыль/i);
assert.match(text,/Δ общего сравнения не заменяет/);
assert.match(text,/Независимая консервативная проверка допуска/);
assert.match(text,/frozen_option_driver_comparison/);
assert.match(text,/исходный авторитетный банк: не использован/);
for (const scheme of ['balanced','llm20','quant100','legacy_control']) assert.ok(text.includes(scheme));
const panel = container.children[0];
const details = panel.children.find((child)=>child.tag==='details');
assert.equal(details.children[1].children[0].children[1].children.length,12);
assert.match(details.textContent,/EXTEND_TAKE[\s\S]*исключён · EXPECTED_SACRIFICE_EXCEEDS_BUDGET/);
audit.components[0].reason = '<img src=x onerror=alert(1)>';
mountEdgeManagement(container,{unified_edge_ensemble:audit});
assert.ok(container.textContent.includes(audit.components[0].reason));
console.log('Unified edge audit rendering smoke: PASS');
