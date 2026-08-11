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
<div id="execution"></div>
<script type="module">
import {mountManagementDecision} from '/seiltanzer/web/js/management_ui.js';
const decision={
  trade_id:7,decision_id:'decision-e2e-close25',policy:'CLOSE_25',
  execution_status:'pending_execution',manual_execution_required:true,
  incremental_close_fraction:.25,remaining_fraction_before_action:1,
  remaining_fraction_after_action:.75,
  instruction_ru:'Закрыть 25% текущего остатка позиции.'
};
window.__calls=[];
window.__applied=null;
const post=async (url,payload)=>{
  window.__calls.push({url,payload});
  return {ok:true,decision_id:payload.decision_id,
    execution_status:payload.executed?'executed':'recommended_not_executed',
    position_state:{remaining_position_fraction:payload.executed?.75:1}};
};
mountManagementDecision(document.querySelector('#execution'),decision,post,
  result=>{window.__applied=result});
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
await page.getByText('ФАКТИЧЕСКОЕ ИСПОЛНЕНИЕ').waitFor();
assert.match(await page.locator('.ai-execution-instruction').innerText(),
  /Закрыть 25% текущего остатка/);
assert.equal(await page.getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).count(),1);
assert.equal(await page.getByRole('button',{name:'НЕ ВЫПОЛНЕНО',exact:true}).count(),1);
await page.getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).tap();
await page.getByText('Исполнение записано. Остаток: 75.0%.').waitFor();
const state=await page.evaluate(()=>({calls:window.__calls,applied:window.__applied}));
assert.equal(state.calls.length,1);
assert.equal(state.calls[0].url,'/api/ai/decision/ack');
assert.deepEqual(state.calls[0].payload,{
  decision_id:'decision-e2e-close25',trade_id:7,executed:true});
assert.equal(state.applied.position_state.remaining_position_fraction,.75);
assert.equal(await page.getByRole('button',{name:'ВЫПОЛНЕНО',exact:true}).count(),0);
assert.equal(await page.getByRole('button',{name:'НЕ ВЫПОЛНЕНО',exact:true}).count(),0);
await browser.close();
await new Promise(resolve=>server.close(resolve));
console.log('AI management CLOSE_25 WebKit E2E: PASS');
