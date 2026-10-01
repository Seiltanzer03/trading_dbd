import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { webkit } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/fixture') {
      res.writeHead(200, {'content-type':'text/html; charset=utf-8'});
      res.end(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<div id="edge"></div><div id="execution"></div><div id="shadow"></div>
<div id="extended-execution"></div><div id="extended-shadow"></div><div id="armed"></div><div id="repeat"></div>
<div id="journal-management"></div><div id="closed-execution"></div>
<script type="module">
import {mountEdgeManagement,mountManagementDecision,mountShadowWorkingAction,mountArmedShadowActions} from '/seiltanzer/web/js/management_ui.js';
import {mountJournalManagement,totalAfterRemainingExit} from '/seiltanzer/web/js/journal_management.js';
const decision={
  trade_id:7,decision_id:'decision-e2e-close25',policy:'CLOSE_25',
  execution_status:'pending_execution',manual_execution_required:true,
  incremental_close_fraction:.25,remaining_fraction_before_action:1,
  remaining_fraction_after_action:.75,
  instruction_ru:'Закрыть 25% текущего остатка позиции.'
};
const edge={available:true,action_now:'CLOSE_25',direction_ru:'поддерживает более раннюю частичную фиксацию',weights:{combined:.40},counterfactual:{raw_policy_without_edge:'HOLD',raw_policy_with_edge:'CLOSE_25',raw_policy_changed:true},top_signals:[{target_family:'RETURN',horizon_minutes:30,status:'EARLY_ADVANTAGE',position_relation:'OPPOSES_POSITION',prediction_shift:{candidate_minus_structural_baseline:-.12,unit:'sigma'},selected_effective_n:24,positive_fold_count:2,evaluated_fold_count:3}],hard_risk_cvar_preserved:true,measurement_note_ru:'Улучшение прогноза — не доходность сделки. Вес не отменяет hard-risk/CVaR.'};
const shadow={working_action:{action_id:'shadow-action-e2e',trade_id:7,policy:'TIGHTEN_STOP',execution_status:'pending_execution',manual_execution_required:true,instruction_ru:'ВРУЧНУЮ ПОДТЯНУТЬ СТОП К ZERO_GAMMA: 107'}};
window.__calls=[];
window.__applied=null;
const post=async (url,payload)=>{
  window.__calls.push({url,payload});
  if(url==='/api/ai/shadow-action/ack') return {ok:true,action_id:payload.action_id,execution_status:'executed',position_state:{active_stop_price:107,take:130}};
  return {ok:true,decision_id:payload.decision_id,
    execution_status:payload.executed?'executed':'recommended_not_executed',
    position_state:{remaining_position_fraction:payload.executed?(payload.decision_id==='decision-repeat-close50'?.25:.75):1}};
};
mountEdgeManagement(document.querySelector('#edge'),edge);
mountManagementDecision(document.querySelector('#execution'),decision,post,
  result=>{window.__applied=result});
mountManagementDecision(document.querySelector('#repeat'),{...decision,
  decision_id:'decision-repeat-close50',policy:'CLOSE_50',incremental_close_fraction:.5,
  remaining_fraction_before_action:.5,remaining_fraction_after_action:.25,
  repeat_reduction:true,instruction_ru:'Закрыть 50% текущего остатка позиции.'},post);
mountShadowWorkingAction(document.querySelector('#shadow'),shadow,post);
const extended={...decision,decision_id:'management-action-e2e',policy:'TIGHTEN_STOP',
  instruction_ru:'Подтянуть стоп к 107',quant_baseline_policy:'HOLD',
  authority:'AI_RISK_OVERLAY_EXTENDED'};
mountManagementDecision(document.querySelector('#extended-execution'),extended,post);
mountShadowWorkingAction(document.querySelector('#extended-shadow'),{
  working_action:{...shadow.working_action,action_id:'management-action-e2e'},
},post,()=>{},extended);
mountArmedShadowActions(document.querySelector('#armed'),[
  {action_id:'shadow-action-armed',trade_id:7,policy:'SCALE_OUT_ON_SPIKE',status:'armed'},
],post);
mountManagementDecision(document.querySelector('#closed-execution'),{...decision,
  decision_id:'final-exit',policy:'EXIT',instruction_ru:'Закрыть остаток'},async()=>({
  trade_closed:true,position_state:{remaining_position_fraction:0}}));
const trade={entry:30500,stop:30396,direction:'long',result_r:1.37019231,result_basis:'ledger_weighted',result_status:'ESTIMATED'};
window.__totalPreview=totalAfterRemainingExit(trade,{realized_r_weighted:-.23076923,remaining_position_fraction:.5},30833);
window.__missingPreview=totalAfterRemainingExit(trade,{realized_r_weighted:null,remaining_position_fraction:.5},30833);
mountJournalManagement(document.querySelector('#journal-management'),{trade,
  summary:{remaining_position_fraction:0,fill_count:2},events:[
  {event_type:'AI_CLOSE_50',timestamp:1000,fraction_closed:.5,execution_price:30452,execution_r:-48/104,active_stop:30396,take:30770,metadata:{execution_price_source:'user_supplied_broker_fill'}},
  {event_type:'TAKE_EXIT',timestamp:2000,fraction_closed:.5,execution_price:30833,execution_r:333/104,active_stop:30500,take:30770,metadata:{}},
  ]});
</script>`);
      return;
    }
    const relative=decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file=path.resolve(ROOT,relative);
    if(!file.startsWith(ROOT)||(await stat(file)).isFile()===false) throw new Error();
    res.writeHead(200,{'content-type':'text/javascript; charset=utf-8'});
    res.end(await readFile(file));
  } catch {
    res.writeHead(404);res.end('not found');
  }
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await webkit.launch({headless:true});
const context=await browser.newContext({
  viewport:{width:390,height:844},screen:{width:390,height:844},
  deviceScaleFactor:3,isMobile:true,hasTouch:true,
});
const page=await context.newPage();
await page.goto(`http://127.0.0.1:${server.address().port}/fixture`,
  {waitUntil:'networkidle'});
assert.match(await page.locator('#journal-management').innerText(),/Сумма результатов закрытых долей/);
assert.match(await page.locator('#journal-management').innerText(),/Часть цен исполнения оценочная/);
assert.equal(await page.locator('#journal-management tr').count(),3);
assert.match(await page.locator('#journal-management').innerText(),/Фактическая/);
assert.match(await page.locator('#journal-management').innerText(),/Оценка/);
const previews=await page.evaluate(()=>({total:window.__totalPreview,missing:window.__missingPreview}));
assert.ok(Math.abs(previews.total-1.37019231)<1e-7);
assert.equal(previews.missing,null);
await page.locator('#closed-execution').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).tap();
await page.locator('#closed-execution').getByText('Сделка закрыта. Все фиксации учтены в журнале; повторно закрывать её не нужно.').waitFor();
await page.locator('#execution').getByText('ФАКТИЧЕСКОЕ ИСПОЛНЕНИЕ').waitFor();
await page.getByText('ПЕРЕВЕСЫ В РЕШЕНИИ').waitFor();
assert.match(await page.locator('#edge').innerText(),/Без перевеса: HOLD → с перевесом: CLOSE_25/);
assert.match(await page.locator('#edge').innerText(),/перевес/);
assert.match(await page.locator('#execution .ai-execution-instruction').innerText(),
  /Закрыть 25% текущего остатка/);
assert.equal(await page.locator('#execution').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).count(),1);
assert.equal(await page.locator('#execution').getByRole('button',{name:'НЕ ВЫПОЛНЕНО',exact:true}).count(),1);
await page.locator('#execution').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).tap();
await page.getByText('Исполнение записано. Остаток: 75.0%.').waitFor();
const state=await page.evaluate(()=>({calls:window.__calls,applied:window.__applied}));
assert.equal(state.calls.length,1);
assert.equal(state.calls[0].url,'/api/ai/decision/ack');
assert.deepEqual(state.calls[0].payload,{
  decision_id:'decision-e2e-close25',trade_id:7,executed:true});
assert.equal(state.applied.position_state.remaining_position_fraction,.75);
assert.equal(await page.locator('#execution').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).count(),0);
assert.equal(await page.locator('#execution').getByRole('button',{name:'НЕ ВЫПОЛНЕНО',exact:true}).count(),0);
await page.locator('#shadow').getByText('РАСШИРЕННЫЙ ВАРИАНТ LLM').waitFor();
assert.equal(await page.locator('#extended-shadow').getByRole('button').count(),0);
await page.locator('#extended-execution input').fill('110');
await page.locator('#extended-execution').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).tap();
const extendedCall=await page.evaluate(()=>window.__calls[1]);
assert.equal(extendedCall.url,'/api/ai/decision/ack');
assert.equal(extendedCall.payload.decision_id,'management-action-e2e');
assert.equal(extendedCall.payload.execution_price,110);
await page.locator('#shadow').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).tap();
await page.getByText('Исполнение записано. Стоп: 107. Take: 130.').waitFor();
const shadowCall=await page.evaluate(()=>window.__calls[2]);
assert.equal(shadowCall.url,'/api/ai/shadow-action/ack');
assert.deepEqual(shadowCall.payload,{action_id:'shadow-action-e2e',trade_id:7,executed:true});
await page.locator('#armed').getByRole('button',{name:'ИСПОЛНЕНО У БРОКЕРА'}).tap();
assert.equal((await page.evaluate(()=>window.__calls)).length,3);
await page.locator('#armed input').fill('120');
await page.locator('#armed').getByRole('button',{name:'ИСПОЛНЕНО У БРОКЕРА'}).tap();
const armedCall=await page.evaluate(()=>window.__calls[3]);
assert.deepEqual(armedCall.payload,{
  action_id:'shadow-action-armed',trade_id:7,executed:true,execution_price:120});
assert.match(await page.locator('#repeat').innerText(),/осталось 50.0%, закрыть 25.0%, останется 25.0%/);
assert.match(await page.locator('#repeat').innerText(),/предыдущее исполнение учтено/);
await page.locator('#repeat input').fill('30470.1');
await page.locator('#repeat').getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).tap();
const repeatCall=await page.evaluate(()=>window.__calls[4]);
assert.deepEqual(repeatCall.payload,{
  decision_id:'decision-repeat-close50',trade_id:7,executed:true,execution_price:30470.1});
assert.match(await page.locator('#repeat').innerText(),/Остаток: 25.0%/);
await page.evaluate(async () => {
  const {mountEdgeManagement} = await import('/seiltanzer/web/js/management_ui.js');
  mountEdgeManagement(document.querySelector('#edge'), {
    available:true, action_now:'HOLD', direction_ru:'нет чистого направления',
    weights:{combined:0,combined_base:0,combined_extended:.03,mathematical_base:0,mathematical_extended:.03},
    mathematical_edge:{instrument:'NAS100',role:'LOW_MOVEMENT_TIME_MANAGEMENT',horizon_minutes:30,
      probabilities:{direction:.5,movement:.2}},
    counterfactual:{extended_policy_without_mathematical_edge:'REDUCE_TAKE',extended_policy_with_mathematical_edge:'TIME_STOP'},
  });
});
const mathText = await page.locator('#edge').innerText();
assert.match(mathText,/базовый вес 0.0% · расширенный 3.0%/);
assert.match(mathText,/Математический edge · NAS100 · 30 мин/);
assert.match(mathText,/P\(move >2bp\) 20.0%/);
assert.match(mathText,/Расширенный кандидат без математического edge: REDUCE_TAKE → с ним: TIME_STOP/);
assert.equal((await page.evaluate(()=>window.__calls)).length,5);
await browser.close();
await new Promise(resolve=>server.close(resolve));
console.log('AI management CLOSE_25 WebKit E2E: PASS');
