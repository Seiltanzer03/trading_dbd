import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { webkit } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const MIME = { '.html':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8' };

const rates = {
  available:true, curve_available:true, curve_state:'SHORT_10Y_POSITIVE', asof:2_000_000_000,
  source:'fixture', production_authority:false,
  semantics:{synthetic_fallback:false,interpolation:false},
  series:[
    {id:'UST_13W',ticker:'^IRX',label:'13W',maturity_years:.25,available:true,yield_pct:3.7,change_bps:-.8},
    {id:'UST_5Y',ticker:'^FVX',label:'5Y',maturity_years:5,available:true,yield_pct:4.36,change_bps:4.9},
    {id:'UST_10Y',ticker:'^TNX',label:'10Y',maturity_years:10,available:true,yield_pct:4.69,change_bps:5.5},
    {id:'UST_30Y',ticker:'^TYX',label:'30Y',maturity_years:30,available:true,yield_pct:5.26,change_bps:5.2},
  ],
  spreads:[{from:'UST_13W',to:'UST_10Y',spread_bps:99.9,change_bps:6.3}],
};
const edge = {
  instrument:'NAS100', production_authority:false, visualization_only:true,
  active_edge:{available:true,matched_structured_signal_n:0,supporting_position_n:0,opposing_position_n:0,matched_groups:[]},
  production_weight:{weight_fraction:0,max_weight_fraction:.30,high_risk_only_cap:.30,direction_score:0,strict_directional_share:0,independent_bucket_n:0,hard_risk_override:false,cvar_override:false,may_widen_stop:false,automatic_execution:false},
  canonical_features:{available_n:6,total_n:8,items:{
    'price.ret_15m':{feature_id:'price.ret_15m',value:.003,available:true,stale:false},
    'price.trend_efficiency_60':{feature_id:'price.trend_efficiency_60',value:.22,available:true,stale:false},
    'vol.rv15_over_rv60':{feature_id:'vol.rv15_over_rv60',value:.75,available:true,stale:false},
    'option.iv':{feature_id:'option.iv',value:.105,available:true,stale:false},
    'option_dynamics.gex_velocity':{feature_id:'option_dynamics.gex_velocity',value:-.12,available:true,stale:false},
    'regime.wavelet_phase':{feature_id:'regime.wavelet_phase',value:1,available:true,stale:false},
    'cross.confirmation':{feature_id:'cross.confirmation',value:null,available:false,stale:false},
    'cross.correlation':{feature_id:'cross.correlation',value:null,available:false,stale:false},
  }},
  g1s:{horizons:[{horizon_minutes:15,effective_n:425},{horizon_minutes:60,effective_n:254}]},
  management_attribution:{status:{},edge:{}},
  cross_asset:{summary:{systemic_coupling:.327,network_tension:.139,fragmentation:.714}},
};

const server = http.createServer(async (req,res)=>{
  try {
    const u=new URL(req.url,'http://127.0.0.1');
    if(u.pathname==='/api/visual/rates-orbit'){res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(rates));return;}
    if(u.pathname==='/api/visual/edge-universe'){res.writeHead(200,{'content-type':'application/json'});res.end(JSON.stringify(edge));return;}
    let rel;
    if(u.pathname==='/universe') rel='seiltanzer/web/universe.html';
    else if(u.pathname.startsWith('/static/')) rel='seiltanzer/web/'+u.pathname.slice('/static/'.length);
    else rel=decodeURIComponent(u.pathname).replace(/^\/+/, '');
    const file=path.resolve(ROOT,rel);
    if(!file.startsWith(ROOT)||!(await stat(file)).isFile()) throw new Error('not found');
    res.writeHead(200,{'content-type':MIME[path.extname(file)]||'application/octet-stream','cache-control':'no-store'});
    res.end(await readFile(file));
  } catch(e){res.writeHead(404);res.end(String(e));}
});
await new Promise((resolve)=>server.listen(0,'127.0.0.1',resolve));
const port=server.address().port;
const browser=await webkit.launch({headless:true});
const page=await browser.newPage({viewport:{width:1280,height:900}});
await page.addInitScript(()=>{
  class FakeWebSocket {
    constructor(){
      setTimeout(()=>{
        this.onopen?.();
        this.onmessage?.({data:JSON.stringify({
          instrument:'NAS100',feeds:{price:{value:30046.141},vols:{vix:{value:14.97},vxn:{value:20.59},gvz:{value:23.92}}},
          sigma:{sigma_implied:.177,sigma_baseline:.237,ratio:.75,phase:'flat'},atr:{ratio:.56,phase:'flat'},vrp:{iv_rv_ratio:.75},regime:{phase:'flat'},
          options_summary:{skew:{rr:0},term:{slope:0},implied_move_frac:.016,implied_move_abs_instr:469.35},gamma:{},market:{},prob:{},mc:{},verdict:{}
        })});
      },10);
    }
    close(){this.onclose?.();}
  }
  Object.defineProperty(window,'WebSocket',{value:FakeWebSocket,writable:true});
});
page.on('console',(msg)=>{if(msg.type()==='error') console.error('[universe webkit]',msg.text());});
await page.goto(`http://127.0.0.1:${port}/universe`,{waitUntil:'networkidle'});
await page.waitForFunction(()=>document.querySelector('#rates-orbit-chart')?._fullLayout?.scene?.camera && document.querySelector('#edge-universe-chart')?._fullLayout?.scene?.camera);
const state=await page.evaluate(()=>({
  ratesButton:document.querySelector('#rates-toggle')?.textContent,
  edgeButton:document.querySelector('#edge-toggle')?.textContent,
  ratesTraces:document.querySelector('#rates-orbit-chart')?.data?.length||0,
  edgeTraces:document.querySelector('#edge-universe-chart')?.data?.length||0,
  edgeEmpty:getComputedStyle(document.querySelector('#edge-empty')).display,
  edgeStatus:document.querySelector('#edge-status')?.textContent||'',
  featureIds:(document.querySelector('#edge-universe-chart')?.data||[]).flatMap((t)=>Array.isArray(t.customdata)?t.customdata.map((v)=>Array.isArray(v)?v[0]:null):[]).filter(Boolean),
}));
assert.equal(state.ratesButton,'ON','rates must start ON on every page load');
assert.equal(state.edgeButton,'ON','edge must start ON on every page load');
assert(state.ratesTraces>=4,'rates 3D must render observed curve and nodes');
assert(state.edgeTraces>=5,'edge 3D must render canonical feature topology even with NO MATCH');
assert.equal(state.edgeEmpty,'none','NO MATCH must not blank the Edge Universe when canonical T0 exists');
assert(state.edgeStatus.includes('T0 FEATURES · NO MATCH'),'status must distinguish feature topology from active match');
assert(state.featureIds.includes('vol.rv15_over_rv60'),'exact canonical feature IDs must be present in 3D trace data');
assert(state.featureIds.includes('option_dynamics.gex_velocity'),'option dynamics must be present in 3D trace data');

const html=await readFile(path.join(ROOT,'seiltanzer/web/universe.html'),'utf8');
assert(html.includes('/static/vendor/plotly-gl3d.min.js'),'Universe must use repository-local Plotly');
assert(!html.includes('cdnjs.cloudflare.com'),'Universe must not depend on external chart CDN');

await browser.close();
await new Promise((resolve)=>server.close(resolve));
console.log(JSON.stringify({ratesVisible:true,edgeVisibleWithoutMatch:true,localPlotly:true,canonicalFeatureTopology:true}));
