import assert from 'node:assert/strict';
import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { webkit } from 'playwright';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};
const baseTs = 2_000_000_000;

function waveletPayload() {
  const timestamps = Array.from({ length: 24 }, (_, i) => baseTs - (23-i)*300);
  const periods = [.25,.5,1,2,4,8];
  const spectrogram = periods.map((_, r) => timestamps.map((_, c) =>
    Math.max(0.02, Math.min(1, .18 + .62*Math.exp(-((r-3)**2)/3) + .12*Math.sin(c/3+r)))));
  return {
    available: true,
    timestamps,
    period_grid_hours: periods,
    spectrogram,
    dominant_ridge: timestamps.map((ts, i) => ({
      ts, period_hours: i < 12 ? 2 : 4,
      secondary_period_hours: 1, power: .72 + .08*Math.sin(i/4),
    })),
    energy_flow: timestamps.slice(-8).map((ts, i) => ({
      ts, micro: 24-i*.7, intraday: 42+i*.9, macro: 34-i*.2,
    })),
    summary: {
      dominant_period_hours: 4, secondary_period_hours: 1,
      secondary_power_ratio: .48, micro_energy_pct: 19,
      intraday_energy_pct: 49, macro_energy_pct: 32,
      persistence: .71, phase_stability: .68, spectral_concentration: .59,
      cycle_shift: 'LONGER', ridge_velocity_log_per_hour: .025,
      decay_half_life_estimate_hours: 3.5,
    },
  };
}

function regimePayload() {
  const trajectory = Array.from({ length: 18 }, (_, i) => ({
    ts: baseTs-(17-i)*300,
    x: -.3+i*.035, y: .3+.08*Math.sin(i/3), z: .25+i*.018,
    regime: i < 10 ? 'CALM TREND' : 'TREND EXPANSION',
  }));
  const velocity = { x:.10, y:.04, z:.06, speed:.123 };
  const acceleration = { x:.02, y:.01, z:.015, magnitude:.027 };
  return {
    available: true,
    current: {
      x_trend:.31, y_vol:.37, z_stress:.56, regime:'TREND EXPANSION',
      confidence:82, velocity_vector:velocity, acceleration_vector:acceleration,
    },
    summary: {
      boundary_distance:.42, regime_age_seconds:2700,
      transition_velocity:.123, transition_acceleration:.027,
      velocity_vector:velocity, acceleration_vector:acceleration,
      stress_components:{cross_asset:.35,realized_impulse:.28,shock:.12,trend_dislocation:.31},
      vol_index:{key:'VXN',value:19.5}, source:{source:'e2e'}, stress_source:'observed',
    },
    trajectory_6h: trajectory,
    trajectory_24h: trajectory,
    trajectory_3d: trajectory,
    reference_points:{entry:{x:-.1,y:.31,z:.35},previous_ai_review:{x:.15,y:.34,z:.48}},
  };
}

function gexMigrationPayload() {
  const timestamps = Array.from({ length: 16 }, (_, i) => baseTs-(15-i)*300);
  const priceGrid = [96,98,100,102,104];
  const heatmap = priceGrid.map((p, r) => timestamps.map((_, c) =>
    (r-2)*(.6+.02*c) + .15*Math.sin(c/2)));
  const trajectory = (base, slope) => timestamps.map((ts, i) => ({ts,price:base+slope*i}));
  return {
    available:true, timestamps, price_grid:priceGrid, heatmap, plot_range:[96,104],
    trajectories:{
      call_wall:trajectory(103.0,-.015),
      put_wall:trajectory(97.2,.012),
      flip:trajectory(99.3,.025),
    },
    path_pressure_history:timestamps.map((ts,i)=>({ts,obstruction:.25+.01*i})),
    summary:{
      current_price:100.2, gamma_regime:'POSITIVE', snapshot_count:16,
      history_hours:1.25, obstruction_score:.38, corridor_state:'MATERIAL FRICTION',
      flip:{price:99.7,migration_6h:.4,persistence:.8,strength:.7},
      call_wall:{price:102.8,migration_6h:-.3,persistence:.82,strength:.75},
      put_wall:{price:97.4,migration_6h:.2,persistence:.79,strength:.70},
    },
  };
}

function gexRidgePayload() {
  return {
    available:true, scale:1, price:100.2, proxy_spot_current:100.2,
    proxy_transform:'direct', trade:{entry:99,stop:96,take:104},
    snapshots:[{gex:{available:true,strikes:[96,98,100,102,104],net:[-2,-1,.3,1.4,2.2],zero_flip:99.7}}],
  };
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/api/analytics/wavelet') {
      res.writeHead(200, {'content-type':'application/json','cache-control':'no-store'});
      res.end(JSON.stringify(waveletPayload())); return;
    }
    if (url.pathname === '/api/analytics/regime-phase') {
      res.writeHead(200, {'content-type':'application/json','cache-control':'no-store'});
      res.end(JSON.stringify(regimePayload())); return;
    }
    if (url.pathname === '/api/analytics/gex-migration') {
      res.writeHead(200, {'content-type':'application/json','cache-control':'no-store'});
      res.end(JSON.stringify(gexMigrationPayload())); return;
    }
    if (url.pathname === '/fixture') {
      res.writeHead(200, { 'content-type': MIME['.html'], 'cache-control':'no-store' });
      res.end(`<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
html,body{margin:0;background:#08111b;color:#ddd;font-family:monospace}.holder{width:390px;height:350px;position:relative;margin:8px}.plot{width:100%;height:100%}.btn-group{display:flex}.btn-toggle{font-size:10px}
</style>
<div id="wavelet-mode-group"><button id="btn-wavelet-spectrogram" class="btn-toggle active">SPECTROGRAM</button><button id="btn-wavelet-energy" class="btn-toggle">FLOW</button></div>
<div class="holder" id="wavelet-canvas-holder"><div id="wavelet-empty"></div></div><span id="wavelet-status"></span>
<div id="regime-buttons"><button id="btn-regime-6h">6H</button><button id="btn-regime-24h">24H</button><button id="btn-regime-3d">3D</button></div>
<div class="holder" id="regime-holder"><div class="plot" id="regime-phase-plot"></div><div id="regime-phase-empty"></div></div><span id="regime-status"></span>
<div id="gex-mode-group"><button id="btn-gex-migration" class="btn-toggle active">MIGRATION</button><button id="btn-gex-snapshot" class="btn-toggle">SNAPSHOT</button></div>
<div class="holder" id="gex-evol-canvas"></div><div id="gex-evol-empty"></div><span id="gex-evol-status"></span>
<script src="/seiltanzer/web/vendor/plotly-gl3d.min.js"></script>
<script type="module">
  import {initWavelet,fetchWaveletData} from '/seiltanzer/web/js/wavelet.js';
  import {initRegimePhase,fetchRegimePhase} from '/seiltanzer/web/js/regime_phase.js';
  import {initGex,updateGex,updateLiveGex} from '/seiltanzer/web/js/gex.js';
  initWavelet();
  initRegimePhase();
  initGex();
  const ridge=${JSON.stringify(gexRidgePayload())};
  await updateGex(ridge);
  updateLiveGex({price:100.2,proxyPrice:100.2,trade:ridge.trade});
  window.__analytics3d={fetchWaveletData,fetchRegimePhase,updateGex,updateLiveGex,ridge};
</script>`);
      return;
    }
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const file = path.resolve(ROOT, relative);
    if (!file.startsWith(ROOT) || !(await stat(file)).isFile()) throw new Error('not found');
    res.writeHead(200, {'content-type':MIME[path.extname(file)]||'application/octet-stream','cache-control':'no-store'});
    res.end(await readFile(file));
  } catch (error) {
    res.writeHead(404); res.end(String(error));
  }
});

await new Promise((resolve)=>server.listen(0,'127.0.0.1',resolve));
const port=server.address().port;
const browser=await webkit.launch({headless:true});
const context=await browser.newContext({
  userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0 Mobile/15E148 Safari/604.1',
  viewport:{width:390,height:844},screen:{width:390,height:844},deviceScaleFactor:3,isMobile:true,hasTouch:true,
});
const page=await context.newPage();
page.on('console',(msg)=>{if(msg.type()==='error')console.error('[analytics webkit]',msg.text());});
await page.goto(`http://127.0.0.1:${port}/fixture`,{waitUntil:'networkidle'});
await page.getByRole('button',{name:'SURFACE 3D'}).click();
await page.getByRole('button',{name:'PRESSURE 3D'}).click();
await page.waitForFunction(()=>
  document.querySelector('[data-renderer="wavelet-surface"]')?._fullLayout?.scene?.camera
  && document.querySelector('#regime-phase-plot')?._fullLayout?.scene?.camera
  && document.querySelector('[data-renderer="pressure"]')?._fullLayout?.scene?.camera);

const selectors={
  wavelet:'[data-renderer="wavelet-surface"]',
  macro:'#regime-phase-plot',
  gex:'[data-renderer="pressure"]',
};
const toolbarKeys={wavelet:'wavelet-surface',macro:'macro-regime',gex:'gex-pressure'};

async function panModel(name) {
  const selector=selectors[name],key=toolbarKeys[name];
  const before=await page.evaluate(({selector,key})=>{
    const plot=document.querySelector(selector);
    const toolbar=document.querySelector(`[data-terminal-3d-toolbar="${key}"]`);
    const pan=[...toolbar.querySelectorAll('button')].find((b)=>b.dataset.dragMode==='pan');
    pan.click();
    window.__instances ||= {};
    window.__instances[key]=plot;
    return structuredClone(plot._fullLayout.scene.camera);
  },{selector,key});
  await page.evaluate(({selector})=>{
    const el=document.querySelector(selector);
    const make=(x,y)=>({identifier:1,target:el,clientX:x,clientY:y,pageX:x,pageY:y,screenX:x,screenY:y,radiusX:8,radiusY:8,force:.5});
    const fire=(type,touches,changed=touches)=>{
      const event=new Event(type,{bubbles:true,cancelable:true,composed:true});
      Object.defineProperties(event,{touches:{value:touches},targetTouches:{value:touches},changedTouches:{value:changed}});
      el.dispatchEvent(event);
    };
    const start=make(165,220),end=make(270,165);
    fire('touchstart',[start]); fire('touchmove',[end]); fire('touchend',[],[end]);
  },{selector});
  await page.waitForTimeout(650);
  const after=await page.evaluate(({selector,key})=>{
    const plot=document.querySelector(selector);
    const toolbar=document.querySelector(`[data-terminal-3d-toolbar="${key}"]`);
    return {
      camera:structuredClone(plot._fullLayout.scene.camera),
      mode:plot._fullLayout.scene.dragmode,
      active:toolbar.querySelector('button.active')?.dataset.dragMode,
      toolbarCount:document.querySelectorAll(`[data-terminal-3d-toolbar="${key}"]`).length,
    };
  },{selector,key});
  const moved=Math.hypot(
    Number(after.camera.center?.x||0)-Number(before.center?.x||0),
    Number(after.camera.center?.y||0)-Number(before.center?.y||0),
    Number(after.camera.center?.z||0)-Number(before.center?.z||0));
  assert(moved>.03,`${name}: PAN must move camera center on real module`);
  assert.equal(after.mode,'pan',`${name}: plot dragmode must be PAN`);
  assert.equal(after.active,'pan',`${name}: toolbar active mode must be PAN`);
  assert.equal(after.toolbarCount,1,`${name}: toolbar must be unique`);
  return after.camera;
}

const cameras={};
for(const name of Object.keys(selectors)) cameras[name]=await panModel(name);

// Real module refresh paths: Wavelet/GEX must react the existing plot; Macro
// already uses react. All three must retain camera, mode and toolbar identity.
await page.evaluate(async()=>{
  const a=window.__analytics3d;
  await a.fetchWaveletData();
  await a.fetchRegimePhase();
  await a.updateGex(a.ridge);
  a.updateLiveGex({price:100.25,proxyPrice:100.25,trade:a.ridge.trade});
});
await page.waitForTimeout(650);

for(const [name,selector] of Object.entries(selectors)) {
  const key=toolbarKeys[name];
  const state=await page.evaluate(({selector,key})=>{
    const plot=document.querySelector(selector);
    return {
      same:window.__instances[key]===plot,
      camera:structuredClone(plot._fullLayout.scene.camera),
      mode:plot._fullLayout.scene.dragmode,
      toolbarCount:document.querySelectorAll(`[data-terminal-3d-toolbar="${key}"]`).length,
      active:document.querySelector(`[data-terminal-3d-toolbar="${key}"] button.active`)?.dataset.dragMode,
    };
  },{selector,key});
  assert.equal(state.same,true,`${name}: data refresh must reuse the same plot instance`);
  assert.deepEqual(state.camera,cameras[name],`${name}: data refresh must retain user camera`);
  assert.equal(state.mode,'pan',`${name}: data refresh must retain PAN`);
  assert.equal(state.active,'pan',`${name}: toolbar must still show PAN`);
  assert.equal(state.toolbarCount,1,`${name}: refresh must not duplicate toolbar`);
}

await page.evaluate(()=>{
  for(const id of ['wavelet-canvas-holder','regime-holder','gex-evol-canvas']) {
    const el=document.getElementById(id); el.style.width='360px'; el.style.height='330px';
  }
});
await page.waitForTimeout(700);
for(const [name,selector] of Object.entries(selectors)) {
  const key=toolbarKeys[name];
  const state=await page.evaluate(({selector,key})=>{
    const plot=document.querySelector(selector);
    return {
      same:window.__instances[key]===plot,
      camera:structuredClone(plot._fullLayout.scene.camera),
      mode:plot._fullLayout.scene.dragmode,
      toolbarCount:document.querySelectorAll(`[data-terminal-3d-toolbar="${key}"]`).length,
    };
  },{selector,key});
  assert.equal(state.same,true,`${name}: resize must not replace plot instance`);
  assert.deepEqual(state.camera,cameras[name],`${name}: resize must retain camera`);
  assert.equal(state.mode,'pan',`${name}: resize must retain drag mode`);
  assert.equal(state.toolbarCount,1,`${name}: resize must not duplicate toolbar`);
}

console.log(JSON.stringify({
  realWebKit:true,
  actualModules:['wavelet','macro','gex'],
  panOnEveryModel:true,
  stableAcrossDataRefresh:true,
  stableAcrossResize:true,
  cameraRetained:true,
  dragModeRetained:true,
  duplicateToolbar:false,
}));

await context.close();
await browser.close();
await new Promise((resolve)=>server.close(resolve));
