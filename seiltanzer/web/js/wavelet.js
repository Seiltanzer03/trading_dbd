import { $ } from './util.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';
import { subscribeMarketTick } from './market_bus.js';
import { ensurePremiumAnalyticsTheme } from './premium_analytics_theme.js';

let containerEl;
let statusEl;
let emptyEl;
let currentMode = 'SPECTROGRAM';
let waveletData = null;
let resizeObserver = null;
let refreshTimer = null;
let rafId = null;
let surfaceGuard = null;
let unsubscribeTick = null;
let liveBursts = [];
let liveTick = null;
let rendererGeneration = 0;

const SURFACE_CAM = { eye: { x: 1.55, y: -1.82, z: 1.25 }, up: { x: 0, y: 0, z: 1 } };

export function initWavelet() {
  ensurePremiumAnalyticsTheme();
  containerEl = $('#wavelet-canvas-holder');
  statusEl = $('#wavelet-status');
  emptyEl = $('#wavelet-empty');
  ensureButtons();
  unsubscribeTick = subscribeMarketTick(onMarketTick);
  if (containerEl && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderWavelet(true));
    resizeObserver.observe(containerEl);
  }
  fetchWaveletData();
  refreshTimer = setInterval(fetchWaveletData, 300000);
}

function ensureButtons() {
  const group = $('#wavelet-mode-group');
  if (!group) return;
  const energy = $('#btn-wavelet-energy');
  if (energy) energy.textContent = 'FLOW';
  let surface = $('#btn-wavelet-surface');
  if (!surface) {
    surface = document.createElement('button');
    surface.className = 'btn-toggle';
    surface.id = 'btn-wavelet-surface';
    surface.textContent = 'SURFACE 3D';
    group.appendChild(surface);
  }
  $('#btn-wavelet-spectrogram')?.addEventListener('click', () => setMode('SPECTROGRAM'));
  energy?.addEventListener('click', () => setMode('FLOW'));
  surface.addEventListener('click', () => setMode('SURFACE'));
}

function setMode(mode) {
  if (currentMode === mode) return;
  currentMode = mode;
  $('#btn-wavelet-spectrogram')?.classList.toggle('active', mode === 'SPECTROGRAM');
  $('#btn-wavelet-energy')?.classList.toggle('active', mode === 'FLOW');
  $('#btn-wavelet-surface')?.classList.toggle('active', mode === 'SURFACE');
  destroyRenderer();
  renderWavelet(true);
}

function destroyRenderer() {
  rendererGeneration += 1;
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
  if (surfaceGuard) { try { surfaceGuard.destroy?.(); } catch {} surfaceGuard = null; }
  const plot = containerEl?.querySelector('[data-renderer="wavelet-surface"]');
  if (plot && window.Plotly) { try { window.Plotly.purge(plot); } catch {} }
  containerEl?.querySelectorAll('[data-wavelet-renderer]').forEach((n) => n.remove());
}

export async function fetchWaveletData() {
  try {
    const res = await fetch('/api/analytics/wavelet', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    waveletData = await res.json();
    renderWavelet(true);
  } catch (err) {
    console.warn('Wavelet fetch error:', err);
    if (statusEl) statusEl.textContent = '○ WAVELET OFFLINE';
  }
}

function onMarketTick(tick) {
  liveTick = tick;
  if (Number(tick.impulse || 0) > .012) {
    liveBursts.push({ born: performance.now(), impulse: Number(tick.impulse || 0), direction: Number(tick.direction || 0) });
    if (liveBursts.length > 24) liveBursts.shift();
  }
  if (currentMode === 'SURFACE') updateSurfaceProbe();
  else ensureCanvasAnimation();
  updateSummary(waveletData?.summary || {});
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, Number(v))); }

function fmtTime(ts, withDate = false) {
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return withDate
    ? `${String(d.getUTCDate()).padStart(2,'0')}.${String(d.getUTCMonth()+1).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`
    : `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}

function palette(v) {
  const x = clamp(v || 0, 0, 1);
  const stops = [
    [0,[5,14,24]], [.16,[13,35,58]], [.34,[28,72,108]], [.52,[26,135,153]],
    [.70,[51,201,164]], [.84,[236,187,70]], [1,[239,82,104]],
  ];
  for (let i=1;i<stops.length;i++) if (x<=stops[i][0]) {
    const [pa,a]=stops[i-1], [pb,b]=stops[i], t=(x-pa)/(pb-pa||1);
    const c=a.map((n,j)=>Math.round(n+(b[j]-n)*t)); return `rgb(${c.join(',')})`;
  }
  return 'rgb(239,82,104)';
}

function updateSummary(summary) {
  const dom = Number(summary.dominant_period_hours || 0);
  const set=(id,text)=>{const el=$(id);if(el)el.textContent=text;};
  set('#wavelet-val-dom', `${dom.toFixed(1)}h`);
  set('#wavelet-val-micro', `${Number(summary.micro_energy_pct || 0).toFixed(1)}%`);
  set('#wavelet-val-intra', `${Number(summary.intraday_energy_pct || 0).toFixed(1)}%`);
  set('#wavelet-val-macro', `${Number(summary.macro_energy_pct || 0).toFixed(1)}%`);
  set('#wavelet-val-persist', `${Math.round(Number(summary.persistence || 0) * 100)}%`);
  if (statusEl) statusEl.textContent = `● ${dom.toFixed(1)}H · ${summary.cycle_shift || '—'} · PHASE ${Math.round(Number(summary.phase_stability || 0)*100)}%`;
  const card=$('#wavelet-summary-card'); if(!card)return;
  let extra=$('#wavelet-extra-metrics');
  if(!extra){extra=document.createElement('div');extra.id='wavelet-extra-metrics';extra.style.cssText='border-top:1px solid #d9d6ce;padding-top:8px;margin-top:4px;line-height:1.7';card.appendChild(extra);}
  const rv=Number(summary.ridge_velocity_log_per_hour || 0);
  const half=summary.decay_half_life_estimate_hours;
  const tick=liveTick||{};
  extra.innerHTML=`
    <div class="analytics-metric-grid">
      <div class="analytics-metric-tile"><small>SECONDARY</small><b>${Number(summary.secondary_period_hours||0).toFixed(1)}h · ${Number(summary.secondary_power_ratio||0).toFixed(2)}</b></div>
      <div class="analytics-metric-tile"><small>PHASE STABILITY</small><b>${Math.round(Number(summary.phase_stability||0)*100)}%</b></div>
      <div class="analytics-metric-tile"><small>SPECTRAL CONC.</small><b>${Math.round(Number(summary.spectral_concentration||0)*100)}%</b></div>
      <div class="analytics-metric-tile"><small>RIDGE DRIFT</small><b>${Math.abs(rv)<.002?'FLAT':rv>0?'LONGER':'SHORTER'} ${rv.toFixed(3)}/h</b></div>
      <div class="analytics-metric-tile"><small>POWER HALF-LIFE*</small><b>${half==null?'—':Number(half).toFixed(1)+'h'}</b></div>
      <div class="analytics-metric-tile"><small>LIVE TICK</small><b>${Number(tick.retBp||0)>=0?'+':''}${Number(tick.retBp||0).toFixed(2)}bp</b></div>
    </div>
    <div style="margin-top:7px;color:#777;font-size:9px">LIVE tick probe is an intrabar broadband impulse overlay; CWT itself refreshes from real 5m history.</div>`;
}

function renderWavelet(force=false) {
  if (!containerEl) return;
  if (!waveletData?.available || !waveletData.spectrogram?.length) {
    if (emptyEl) { emptyEl.style.display='flex'; emptyEl.textContent=`○ ${waveletData?.reason || 'WAVELET SPECTRUM UNAVAILABLE'}`; }
    if (statusEl) statusEl.textContent='○ НЕДОСТАТОЧНО РЕАЛЬНОЙ ИСТОРИИ';
    return;
  }
  if (emptyEl) emptyEl.style.display='none';
  updateSummary(waveletData.summary||{});
  if(currentMode==='SURFACE') renderSurface(force);
  else renderCanvasMode(force);
}

function ensureCanvas() {
  let cv=containerEl.querySelector('canvas[data-wavelet-renderer]');
  if(!cv){destroyRenderer();cv=document.createElement('canvas');cv.dataset.waveletRenderer='canvas';cv.style.cssText='width:100%;height:100%;display:block';containerEl.insertBefore(cv,emptyEl||null);}
  const rect=containerEl.getBoundingClientRect();const dpr=Math.min(window.devicePixelRatio||1,2);const width=Math.max(540,Math.floor(rect.width||850)),height=Math.max(340,Math.floor(rect.height||420));
  cv.width=Math.floor(width*dpr);cv.height=Math.floor(height*dpr);cv.style.width=`${width}px`;cv.style.height=`${height}px`;
  return {cv,dpr,width,height};
}

function renderCanvasMode(force=false){
  const state=ensureCanvas();
  const generation=rendererGeneration;
  const draw=(now)=>{
    if(generation!==rendererGeneration||currentMode==='SURFACE')return;
    if(currentMode==='FLOW')drawFlow(state,now);else drawSpectrogram(state,now);
    liveBursts=liveBursts.filter((b)=>now-b.born<2400);
    rafId=requestAnimationFrame(draw);
  };
  if(rafId)cancelAnimationFrame(rafId);rafId=requestAnimationFrame(draw);
}

function ensureCanvasAnimation(){if(currentMode!=='SURFACE'&&!rafId)renderCanvasMode(false);}

function drawBackground(ctx,width,height){
  const g=ctx.createLinearGradient(0,0,0,height);g.addColorStop(0,'#06101b');g.addColorStop(1,'#0a1724');ctx.fillStyle=g;ctx.fillRect(0,0,width,height);
}

function drawSpectrogram({cv,dpr,width,height},now){
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);drawBackground(ctx,width,height);
  const margin={left:64,right:22,top:22,bottom:38};const plotW=width-margin.left-margin.right,plotH=height-margin.top-margin.bottom;
  const grid=waveletData.period_grid_hours||[],timestamps=waveletData.timestamps||[],spec=waveletData.spectrogram||[],ridge=waveletData.dominant_ridge||[];
  const cols=timestamps.length,rows=grid.length,cellW=plotW/Math.max(cols,1),cellH=plotH/Math.max(rows,1);
  for(let r=0;r<rows;r++){const y=margin.top+plotH-(r+1)*cellH;for(let c=0;c<cols;c++){const v=Number(spec[r]?.[c]||0);ctx.fillStyle=palette(v);ctx.fillRect(margin.left+c*cellW,y,Math.ceil(cellW)+1,Math.ceil(cellH)+1);}}
  // Contour islands: outline local high-energy cells, giving actual spectral topology.
  ctx.strokeStyle='rgba(244,225,151,.45)';ctx.lineWidth=.8;
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const v=Number(spec[r]?.[c]||0);if(v<.72)continue;const left=Number(spec[r]?.[Math.max(0,c-1)]||0),right=Number(spec[r]?.[Math.min(cols-1,c+1)]||0),up=Number(spec[Math.min(rows-1,r+1)]?.[c]||0),down=Number(spec[Math.max(0,r-1)]?.[c]||0);if(Math.min(left,right,up,down)<.62)ctx.strokeRect(margin.left+c*cellW,margin.top+plotH-(r+1)*cellH,Math.ceil(cellW)+1,Math.ceil(cellH)+1);}
  ctx.font='9px IBM Plex Mono,monospace';ctx.fillStyle='rgba(206,220,230,.72)';ctx.strokeStyle='rgba(191,210,223,.13)';
  grid.forEach((p,r)=>{const y=margin.top+plotH-(r+.5)*cellH;ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(margin.left+plotW,y);ctx.stroke();ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillText(`${p}h`,margin.left-7,y);});
  for(let i=0;i<5;i++){const idx=Math.round(i*(cols-1)/4);const x=margin.left+(idx+.5)*cellW;ctx.textAlign='center';ctx.textBaseline='top';ctx.fillText(fmtTime(timestamps[idx],true),x,margin.top+plotH+8);}
  const periodIndex=new Map(grid.map((p,i)=>[Number(p),i]));
  const drawRidge=(field,color,widthLine,dash=[])=>{ctx.save();ctx.setLineDash(dash);ctx.strokeStyle='rgba(3,9,14,.78)';ctx.lineWidth=widthLine+4;ctx.beginPath();let started=false;ridge.forEach((pt,c)=>{const row=periodIndex.get(Number(pt[field]));if(row==null)return;const x=margin.left+(c+.5)*cellW,y=margin.top+plotH-(row+.5)*cellH;if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);});ctx.stroke();ctx.strokeStyle=color;ctx.shadowColor=color;ctx.shadowBlur=8;ctx.lineWidth=widthLine;ctx.stroke();ctx.restore();};
  drawRidge('period_hours','#55e4f1',2.4);drawRidge('secondary_period_hours','rgba(241,187,74,.72)',1.2,[4,4]);
  // Live broadband tick bursts arrive on the right edge. They do not alter the historical CWT.
  liveBursts.forEach((b)=>{const age=(now-b.born)/2400,alpha=(1-age)*b.impulse;const x=margin.left+plotW-age*plotW*.055;const grad=ctx.createLinearGradient(0,margin.top,0,margin.top+plotH);grad.addColorStop(0,`rgba(255,93,116,${alpha*.15})`);grad.addColorStop(.72,`rgba(84,222,224,${alpha*.22})`);grad.addColorStop(1,`rgba(84,222,224,${alpha*.75})`);ctx.strokeStyle=grad;ctx.lineWidth=1+5*alpha;ctx.shadowColor=b.direction>=0?'#55e4f1':'#ff5d74';ctx.shadowBlur=12*alpha;ctx.beginPath();ctx.moveTo(x,margin.top);ctx.lineTo(x,margin.top+plotH);ctx.stroke();ctx.shadowBlur=0;});
  const dom=Number(waveletData.summary?.dominant_period_hours||0),di=grid.findIndex(p=>Number(p)===dom);if(di>=0){const y=margin.top+plotH-(di+.5)*cellH;ctx.fillStyle='#f3c36a';ctx.font='bold 9px IBM Plex Mono,monospace';ctx.textAlign='right';ctx.fillText(`NOW ${dom.toFixed(1)}h`,margin.left+plotW-6,y-6);}
  ctx.strokeStyle='rgba(203,219,229,.25)';ctx.strokeRect(margin.left,margin.top,plotW,plotH);
}

function flowDerivatives(flow){
  if(!flow?.length)return {micro:0,intraday:0,macro:0};const a=flow[Math.max(0,flow.length-7)],b=flow.at(-1);return {micro:Number(b.micro||0)-Number(a.micro||0),intraday:Number(b.intraday||0)-Number(a.intraday||0),macro:Number(b.macro||0)-Number(a.macro||0)};
}

function drawFlow({cv,dpr,width,height},now){
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);drawBackground(ctx,width,height);
  const flow=waveletData.energy_flow||[];if(!flow.length)return;const margin={left:58,right:24,top:28,bottom:42},plotW=width-margin.left-margin.right,plotH=height-margin.top-margin.bottom;
  const bands=[['MACRO','macro','#e5ad46'],['INTRADAY','intraday','#36baa8'],['MICRO','micro','#438cd1']];
  const baseY={MACRO:margin.top+plotH*.18,INTRADAY:margin.top+plotH*.50,MICRO:margin.top+plotH*.82};
  const xAt=i=>margin.left+i/Math.max(1,flow.length-1)*plotW;
  bands.forEach(([label,key,color])=>{const y0=baseY[label];ctx.strokeStyle='rgba(194,211,223,.12)';ctx.beginPath();ctx.moveTo(margin.left,y0);ctx.lineTo(margin.left+plotW,y0);ctx.stroke();ctx.fillStyle='rgba(214,225,233,.72)';ctx.font='9px IBM Plex Mono,monospace';ctx.textAlign='right';ctx.fillText(label,margin.left-8,y0+3);ctx.beginPath();flow.forEach((p,i)=>{const y=y0-(Number(p[key]||0)-33)*1.4;const x=xAt(i);if(!i)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.strokeStyle=color;ctx.shadowColor=color;ctx.shadowBlur=7;ctx.lineWidth=2.3;ctx.stroke();ctx.shadowBlur=0;});
  // Actual energy transfer arrows, derived from recent Δ family shares.
  const d=flowDerivatives(flow);const entries=[['MICRO',d.micro],['INTRADAY',d.intraday],['MACRO',d.macro]];const sources=entries.filter(e=>e[1]<-1).sort((a,b)=>a[1]-b[1]),sinks=entries.filter(e=>e[1]>1).sort((a,b)=>b[1]-a[1]);
  if(sources.length&&sinks.length){const src=sources[0],dst=sinks[0],mag=Math.min(1,Math.min(-src[1],dst[1])/14);const x=margin.left+plotW*.83,y1=baseY[src[0]],y2=baseY[dst[0]];ctx.strokeStyle=`rgba(255,198,91,${.35+.55*mag})`;ctx.lineWidth=2+5*mag;ctx.shadowColor='#ffc65b';ctx.shadowBlur=10*mag;ctx.beginPath();ctx.moveTo(x,y1);ctx.bezierCurveTo(x+35,y1,x+35,y2,x,y2);ctx.stroke();ctx.shadowBlur=0;const phase=(now/900)%1;for(let k=0;k<5;k++){const t=(phase+k/5)%1;const omt=1-t;const px=omt*omt*omt*x+3*omt*omt*t*(x+35)+3*omt*t*t*(x+35)+t*t*t*x;const py=omt*omt*omt*y1+3*omt*omt*t*y1+3*omt*t*t*y2+t*t*t*y2;ctx.fillStyle='#ffd47a';ctx.beginPath();ctx.arc(px,py,1.8+2.2*mag,0,Math.PI*2);ctx.fill();}ctx.fillStyle='rgba(224,232,237,.8)';ctx.font='bold 9px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.fillText(`${src[0]} → ${dst[0]} · Δ ${Math.min(-src[1],dst[1]).toFixed(1)}pp`,x-70,Math.min(y1,y2)-12);}
  // Time axis and current family cards.
  ctx.fillStyle='rgba(208,222,231,.65)';ctx.font='9px IBM Plex Mono,monospace';for(let i=0;i<4;i++){const idx=Math.round(i*(flow.length-1)/3);ctx.textAlign='center';ctx.fillText(fmtTime(flow[idx].ts,true),xAt(idx),height-18);}
  const last=flow.at(-1);ctx.textAlign='left';ctx.fillStyle='rgba(224,232,237,.85)';ctx.fillText(`NOW · MICRO ${Number(last.micro||0).toFixed(1)}%   INTRA ${Number(last.intraday||0).toFixed(1)}%   MACRO ${Number(last.macro||0).toFixed(1)}%`,margin.left,16);
}

function renderSurface(force=false){
  if(!window.Plotly)return;
  if(containerEl.querySelector('[data-renderer="wavelet-surface"]')&&!force)return;
  destroyRenderer();
  const plot=document.createElement('div');plot.dataset.waveletRenderer='plot';plot.dataset.renderer='wavelet-surface';plot.style.cssText='width:100%;height:100%';containerEl.insertBefore(plot,emptyEl||null);surfaceGuard=createPlotlyCameraGuard(plot,SURFACE_CAM);
  const periods=waveletData.period_grid_hours||[],ts=waveletData.timestamps||[],spec=waveletData.spectrogram||[],ridge=waveletData.dominant_ridge||[];const latest=Number(ts.at(-1)||0);const x=ts.map(t=>(Number(t)-latest)/3600);
  const traces=[{type:'surface',x,y:periods,z:spec,colorscale:[[0,'#07101b'],[.2,'#123a5b'],[.42,'#1e8798'],[.64,'#36c9a7'],[.82,'#e2b348'],[1,'#ef5268']],cmin:0,cmax:1,showscale:false,opacity:.94,hovertemplate:'t=%{x:.2f}h<br>period=%{y:.1f}h<br>power=%{z:.2f}<extra></extra>',contours:{z:{show:true,color:'rgba(232,240,245,.18)',project:{z:true}}},lighting:{ambient:.36,diffuse:.78,roughness:.76,specular:.25},lightposition:{x:70,y:-80,z:120}},
    {type:'scatter3d',mode:'lines',name:'DOMINANT RIDGE',x:ridge.map(p=>(Number(p.ts)-latest)/3600),y:ridge.map(p=>Number(p.period_hours)),z:ridge.map(p=>1.035+Number(p.power||0)*.045),line:{color:'#59eff2',width:7},hoverinfo:'skip',showlegend:false},
    {type:'scatter3d',mode:'lines',name:'SECONDARY RIDGE',x:ridge.map(p=>(Number(p.ts)-latest)/3600),y:ridge.map(p=>Number(p.secondary_period_hours)),z:ridge.map(p=>1.02),line:{color:'rgba(244,192,79,.72)',width:4,dash:'dot'},hoverinfo:'skip',showlegend:false},
    {type:'scatter3d',mode:'markers+text',name:'LIVE TICK PROBE',x:[0],y:[Number(waveletData.summary?.dominant_period_hours||periods.at(-1)||1)],z:[1.08],marker:{size:8,color:'#ffbf55',line:{color:'#fff',width:1.2}},text:['LIVE'],textposition:'top center',hoverinfo:'skip',showlegend:false}];
  const layout={margin:{l:0,r:0,t:0,b:0},paper_bgcolor:'transparent',plot_bgcolor:'transparent',showlegend:false,uirevision:'wavelet-surface-premium-v3',scene:{xaxis:{title:'TIME · HOURS TO NOW',gridcolor:'rgba(188,208,222,.14)',color:'#b8cad5',zerolinecolor:'#f2b956'},yaxis:{title:'PERIOD · HOURS',type:'log',gridcolor:'rgba(188,208,222,.14)',color:'#b8cad5'},zaxis:{title:'SPECTRAL POWER',range:[0,1.16],gridcolor:'rgba(188,208,222,.12)',color:'#b8cad5'},bgcolor:'rgba(4,12,20,0)',aspectmode:'manual',aspectratio:{x:1.55,y:1.08,z:.72}}};
  surfaceGuard?.beforeWrite?.();window.Plotly.newPlot(plot,traces,layout,{responsive:true,displayModeBar:false,scrollZoom:true});surfaceGuard?.afterWrite?.();updateSurfaceProbe();
}

function updateSurfaceProbe(){
  if(currentMode!=='SURFACE'||!window.Plotly||!containerEl)return;const plot=containerEl.querySelector('[data-renderer="wavelet-surface"]');if(!plot?.data)return;const idx=plot.data.findIndex(t=>t.name==='LIVE TICK PROBE');if(idx<0)return;const imp=clamp(Number(liveTick?.impulse||0),0,1),dom=Number(waveletData?.summary?.dominant_period_hours||1);window.Plotly.restyle(plot,{y:[[dom]],z:[[1.06+.14*imp]],'marker.size':[[7+11*imp]]},[idx]);
}
