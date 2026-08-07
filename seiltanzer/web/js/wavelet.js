import { $ } from './util.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';

let containerEl;
let statusEl;
let emptyEl;
let currentMode = 'SPECTROGRAM';
let waveletData = null;
let resizeObserver = null;
let refreshTimer = null;
let surfaceEl = null;
let surfaceCamera = null;

export function initWavelet() {
  containerEl = $('#wavelet-canvas-holder');
  statusEl = $('#wavelet-status');
  emptyEl = $('#wavelet-empty');
  $('#btn-wavelet-spectrogram')?.addEventListener('click', () => setMode('SPECTROGRAM'));
  const energyBtn = $('#btn-wavelet-energy');
  if (energyBtn) {
    energyBtn.textContent = 'FLOW';
    energyBtn.addEventListener('click', () => setMode('FLOW'));
  }
  const group = $('#wavelet-mode-group');
  if (group && !$('#btn-wavelet-surface')) {
    const b = document.createElement('button');
    b.className = 'btn-toggle'; b.id = 'btn-wavelet-surface'; b.textContent = 'SURFACE 3D';
    b.addEventListener('click', () => setMode('SURFACE'));
    group.appendChild(b);
  }
  if (containerEl && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderWavelet());
    resizeObserver.observe(containerEl);
  }
  fetchWaveletData();
  refreshTimer = setInterval(fetchWaveletData, 300000);
}

function setMode(mode) {
  currentMode = mode;
  $('#btn-wavelet-spectrogram')?.classList.toggle('active', mode === 'SPECTROGRAM');
  $('#btn-wavelet-energy')?.classList.toggle('active', mode === 'FLOW');
  $('#btn-wavelet-surface')?.classList.toggle('active', mode === 'SURFACE');
  renderWavelet();
}

export async function fetchWaveletData() {
  try {
    const res = await fetch('/api/analytics/wavelet', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    waveletData = await res.json();
    renderWavelet();
  } catch (err) {
    console.warn('Wavelet fetch error:', err);
    if (statusEl) statusEl.textContent = '○ WAVELET OFFLINE';
  }
}

function ensureExtraMetrics(summary) {
  const card = $('#wavelet-summary-card');
  if (!card) return;
  let extra = $('#wavelet-extra-metrics');
  if (!extra) {
    extra = document.createElement('div'); extra.id = 'wavelet-extra-metrics';
    extra.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:8px;margin-top:4px;line-height:1.75';
    card.appendChild(extra);
  }
  const concentration = summary.spectral_concentration == null ? '—' : `${(summary.spectral_concentration * 100).toFixed(0)}%`;
  const phase = summary.phase_stability == null ? '—' : `${(summary.phase_stability * 100).toFixed(0)}%`;
  const half = summary.decay_half_life_estimate_hours == null ? '—' : `${Number(summary.decay_half_life_estimate_hours).toFixed(1)}h`;
  const sec = summary.secondary_period_hours == null ? '—' : `${Number(summary.secondary_period_hours).toFixed(1)}h`;
  const vel = Number(summary.ridge_velocity_log_per_hour || 0);
  extra.innerHTML = `
    <div>CYCLE STATE: <b>${summary.cycle_shift || '—'}</b></div>
    <div>SECONDARY: <b>${sec}</b> · ratio <b>${Number(summary.secondary_power_ratio || 0).toFixed(2)}</b></div>
    <div>PHASE STABILITY: <b>${phase}</b></div>
    <div>SPECTRAL CONC.: <b>${concentration}</b></div>
    <div>RIDGE DRIFT: <b>${vel > 0 ? 'LONGER' : vel < 0 ? 'SHORTER' : 'FLAT'} ${Math.abs(vel).toFixed(3)}/h</b></div>
    <div>POWER HALF-LIFE*: <b>${half}</b></div>
    <div style="margin-top:6px;color:#777;font-size:9px">*только при наблюдаемом распаде мощности ridge; не прогноз разворота<br>${summary.source?.source || '—'}</div>`;
}

function updateSummary(summary) {
  const dom = Number(summary.dominant_period_hours || 0);
  if ($('#wavelet-val-dom')) $('#wavelet-val-dom').textContent = `${dom.toFixed(1)}h`;
  if ($('#wavelet-val-micro')) $('#wavelet-val-micro').textContent = `${Number(summary.micro_energy_pct || 0).toFixed(1)}%`;
  if ($('#wavelet-val-intra')) $('#wavelet-val-intra').textContent = `${Number(summary.intraday_energy_pct || 0).toFixed(1)}%`;
  if ($('#wavelet-val-macro')) $('#wavelet-val-macro').textContent = `${Number(summary.macro_energy_pct || 0).toFixed(1)}%`;
  if ($('#wavelet-val-persist')) $('#wavelet-val-persist').textContent = `${(Number(summary.persistence || 0) * 100).toFixed(0)}%`;
  if (statusEl) statusEl.textContent = `● ${dom.toFixed(1)}H · ${summary.cycle_shift || 'STABLE'} · PHASE ${(Number(summary.phase_stability || 0) * 100).toFixed(0)}%`;
  ensureExtraMetrics(summary);
}

function createCanvas() {
  let cv = containerEl?.querySelector('canvas');
  if (!cv && containerEl) {
    cv = document.createElement('canvas');
    cv.style.cssText = 'width:100%;height:100%;display:block;';
    containerEl.insertBefore(cv, emptyEl || null);
  }
  return cv;
}

function ensureSurface() {
  if (!containerEl) return null;
  if (!surfaceEl) {
    surfaceEl = document.createElement('div');
    surfaceEl.id = 'wavelet-surface-3d';
    surfaceEl.style.cssText = 'width:100%;height:100%;display:none;';
    containerEl.insertBefore(surfaceEl, emptyEl || null);
    surfaceCamera = createPlotlyCameraGuard(surfaceEl, { eye: { x: 1.45, y: -1.65, z: 1.15 }, up: { x: 0, y: 0, z: 1 } });
  }
  return surfaceEl;
}

function palette(v) {
  const x = Math.max(0, Math.min(1, Number(v) || 0));
  const stops = [
    [0.00, [247,248,250]], [0.14, [221,228,244]], [0.35, [106,139,205]],
    [0.58, [28,91,151]], [0.76, [20,170,170]], [0.90, [239,190,48]], [1.00, [204,64,45]],
  ];
  for (let i = 1; i < stops.length; i++) if (x <= stops[i][0]) {
    const [ap,a] = stops[i-1], [bp,b] = stops[i]; const t = (x-ap)/(bp-ap||1);
    const c = a.map((n,j) => Math.round(n + (b[j]-n)*t)); return `rgb(${c.join(',')})`;
  }
  return 'rgb(204,64,45)';
}

function fmtTime(ts, withDate = false) {
  const d = new Date(Number(ts) * 1000); if (Number.isNaN(d.getTime())) return '—';
  return withDate
    ? `${String(d.getUTCDate()).padStart(2,'0')}.${String(d.getUTCMonth()+1).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`
    : `${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`;
}

function canvasContext() {
  const cv = createCanvas(); if (!cv) return null;
  cv.style.display = 'block'; if (surfaceEl) surfaceEl.style.display = 'none';
  const rect = containerEl.getBoundingClientRect(); const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(520, Math.floor(rect.width || 850)); const height = Math.max(330, Math.floor(rect.height || 420));
  cv.width = Math.floor(width*dpr); cv.height = Math.floor(height*dpr); cv.style.width=`${width}px`; cv.style.height=`${height}px`;
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,width,height);
  return { cv, ctx, width, height };
}

function renderFlow(summary) {
  const pack = canvasContext(); if (!pack) return;
  const { ctx, width, height } = pack;
  const flow = waveletData.energy_flow || []; if (!flow.length) return;
  const m = { left:58,right:70,top:24,bottom:42 }, w=width-m.left-m.right, h=height-m.top-m.bottom;
  const X = (i) => m.left + i/Math.max(1,flow.length-1)*w;
  const Y = (pct) => m.top + h - pct/100*h;
  const bands = [
    { key:'macro', color:'rgba(185,105,45,.72)', label:'MACRO >24h' },
    { key:'intraday', color:'rgba(73,112,171,.72)', label:'INTRADAY 4–24h' },
    { key:'micro', color:'rgba(35,164,160,.78)', label:'MICRO <4h' },
  ];
  let lower = flow.map(() => 0);
  for (const band of bands) {
    const upper = flow.map((p,i) => lower[i] + Number(p[band.key]||0));
    ctx.beginPath(); upper.forEach((v,i)=>{ const x=X(i),y=Y(v); if(!i)ctx.moveTo(x,y);else ctx.lineTo(x,y); });
    for(let i=flow.length-1;i>=0;i--) ctx.lineTo(X(i),Y(lower[i])); ctx.closePath(); ctx.fillStyle=band.color; ctx.fill();
    lower = upper;
  }
  ctx.strokeStyle='rgba(45,45,40,.16)'; for(let p=0;p<=100;p+=25){const y=Y(p);ctx.beginPath();ctx.moveTo(m.left,y);ctx.lineTo(m.left+w,y);ctx.stroke();ctx.fillStyle='#777';ctx.font='9px IBM Plex Mono,monospace';ctx.textAlign='right';ctx.fillText(`${p}%`,m.left-6,y+3);}
  const now=flow.at(-1); const legend=[['MICRO',now.micro,'#23a4a0'],['INTRADAY',now.intraday,'#4970ab'],['MACRO',now.macro,'#b9692d']];
  legend.forEach((r,i)=>{ctx.fillStyle=r[2];ctx.fillRect(width-62,25+i*19,8,8);ctx.fillStyle='#4d4a44';ctx.textAlign='right';ctx.fillText(`${r[0]} ${Number(r[1]).toFixed(0)}%`,width-8,33+i*19);});
  ctx.strokeStyle='#111';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(m.left+w,m.top);ctx.lineTo(m.left+w,m.top+h);ctx.stroke();
  ctx.fillStyle='#333';ctx.font='bold 10px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.fillText(`ENERGY MIGRATION · ${summary.cycle_shift || 'STABLE'}`,m.left,14);
  ctx.fillStyle='#777';ctx.font='9px IBM Plex Mono,monospace';ctx.fillText(fmtTime(flow[0].ts,true),m.left,height-18);ctx.textAlign='right';ctx.fillText(fmtTime(now.ts,true),m.left+w,height-18);
}

function renderSurface() {
  const el = ensureSurface(); if (!el || !window.Plotly) return;
  const cv = containerEl.querySelector('canvas'); if (cv) cv.style.display='none'; el.style.display='block';
  const grid=waveletData.period_grid_hours||[], ts=waveletData.timestamps||[], spec=waveletData.spectrogram||[], ridge=waveletData.dominant_ridge||[];
  if (!grid.length || !ts.length || !spec.length) return;
  const last=Number(ts.at(-1)); const x=ts.map((t)=>(Number(t)-last)/3600);
  const ridgeZ=ridge.map((r,c)=>{const ri=grid.findIndex((p)=>Number(p)===Number(r.period_hours));return ri>=0?Math.min(1.08,Number(spec[ri]?.[c]||0)+0.055):1.02;});
  const colorscale=[[0,'#f7f8fa'],[0.2,'#d7e0f3'],[0.45,'#557ebd'],[0.68,'#1aa6a4'],[0.86,'#f0bc32'],[1,'#c83d32']];
  const traces=[{
    type:'surface',x,y:grid,z:spec,surfacecolor:spec,colorscale,cmin:0,cmax:1,showscale:false,opacity:0.96,
    contours:{z:{show:true,usecolormap:true,highlightcolor:'#333',project:{z:true}}},hovertemplate:'t %{x:.1f}h<br>period %{y:.1f}h<br>power %{z:.2f}<extra></extra>',
    lighting:{ambient:0.65,diffuse:0.75,roughness:0.85,specular:0.25},
  },{
    type:'scatter3d',mode:'lines',x,y:ridge.map((r)=>r.period_hours),z:ridgeZ,line:{color:'#15e2e1',width:7},showlegend:false,
    hovertemplate:'DOMINANT %{y:.1f}h<extra></extra>',
  }];
  const layout={margin:{l:0,r:0,b:0,t:0},paper_bgcolor:'transparent',showlegend:false,uirevision:'wavelet-surface-v1',scene:{
    xaxis:{title:'TRADING HOURS → NOW',gridcolor:'#ddd9d0'},yaxis:{title:'PERIOD · H',gridcolor:'#ddd9d0'},zaxis:{title:'SPECTRAL POWER',range:[0,1.12],gridcolor:'#ddd9d0'},
    bgcolor:'rgba(255,255,255,0)',aspectratio:{x:1.8,y:1,z:0.7},
  }};
  surfaceCamera?.beforeWrite(); window.Plotly.react(el,traces,layout,{responsive:true,displayModeBar:false}); surfaceCamera?.afterWrite();
}

function renderSpectrogram(summary) {
  const pack=canvasContext(); if(!pack)return; const {ctx,width,height}=pack;
  const margin={left:68,right:24,top:18,bottom:38}, plotW=width-margin.left-margin.right, plotH=height-margin.top-margin.bottom;
  const grid=waveletData.period_grid_hours||[], timestamps=waveletData.timestamps||[], spec=waveletData.spectrogram||[], ridge=waveletData.dominant_ridge||[];
  const cols=timestamps.length,rows=grid.length,cellW=plotW/Math.max(cols,1),cellH=plotH/Math.max(rows,1);
  for(let r=0;r<rows;r++){const row=spec[r]||[],y=margin.top+plotH-(r+1)*cellH;for(let c=0;c<cols;c++){ctx.fillStyle=palette(row[c]);ctx.fillRect(margin.left+c*cellW,y,Math.ceil(cellW)+1,Math.ceil(cellH)+1);}}
  // High-power contour edges expose coherent islands instead of a flat heatmap.
  ctx.strokeStyle='rgba(25,30,34,.28)';ctx.lineWidth=.7;
  for(let r=0;r<rows;r++)for(let c=0;c<cols;c++){const v=Number(spec[r]?.[c]||0);if(v<0.72)continue;const left=Number(spec[r]?.[c-1]||0),up=Number(spec[r+1]?.[c]||0);if(left<0.72||up<0.72)ctx.strokeRect(margin.left+c*cellW,margin.top+plotH-(r+1)*cellH,Math.ceil(cellW),Math.ceil(cellH));}
  ctx.strokeStyle='rgba(90,88,80,.17)';ctx.font='9px IBM Plex Mono,monospace';ctx.fillStyle='#6f6c64';ctx.textBaseline='middle';
  grid.forEach((period,idx)=>{const y=margin.top+plotH-(idx+.5)*cellH;ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(margin.left+plotW,y);ctx.stroke();ctx.textAlign='right';ctx.fillText(`${period}h`,margin.left-7,y);});
  const labels=Math.min(5,cols);ctx.textAlign='center';ctx.textBaseline='top';for(let i=0;i<labels;i++){const idx=labels===1?0:Math.round(i*(cols-1)/(labels-1));ctx.fillText(fmtTime(timestamps[idx],true),margin.left+(idx+.5)*cellW,margin.top+plotH+8);}
  if(ridge.length){const pi=new Map(grid.map((p,i)=>[Number(p),i]));const draw=(stroke,lw)=>{ctx.strokeStyle=stroke;ctx.lineWidth=lw;ctx.beginPath();let started=false;ridge.forEach((pt,c)=>{const row=pi.get(Number(pt.period_hours));if(row==null)return;const x=margin.left+(c+.5)*cellW,y=margin.top+plotH-(row+.5)*cellH;if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);});ctx.stroke();};draw('rgba(20,24,28,.72)',5.5);draw('#13e0df',2.4);
    ridge.slice(-24).forEach((pt,i)=>{if(i%4)return;const c=ridge.length-24+i;if(c<0)return;const row=pi.get(Number(pt.period_hours));if(row==null)return;const rr=2+4*Number(pt.power||0);ctx.beginPath();ctx.arc(margin.left+(c+.5)*cellW,margin.top+plotH-(row+.5)*cellH,rr,0,Math.PI*2);ctx.strokeStyle='rgba(255,255,255,.7)';ctx.stroke();});}
  const dom=Number(summary.dominant_period_hours||0),domIdx=grid.findIndex((p)=>Math.abs(Number(p)-dom)<1e-6);if(domIdx>=0){const y=margin.top+plotH-(domIdx+.5)*cellH;ctx.fillStyle='#111';ctx.font='bold 10px IBM Plex Mono,monospace';ctx.textAlign='right';ctx.fillText(`NOW ${dom.toFixed(1)}h`,margin.left+plotW-6,y-5);}
  ctx.strokeStyle='#bdb9af';ctx.strokeRect(margin.left,margin.top,plotW,plotH);
}

function renderWavelet() {
  if (!containerEl) return;
  if (!waveletData?.available || !waveletData.spectrogram?.length) {
    if (emptyEl) { emptyEl.style.display='flex'; emptyEl.textContent=`○ ${waveletData?.reason || 'WAVELET SPECTRUM UNAVAILABLE'}`; }
    if(statusEl)statusEl.textContent='○ НЕДОСТАТОЧНО РЕАЛЬНОЙ ИСТОРИИ'; return;
  }
  if(emptyEl)emptyEl.style.display='none'; const summary=waveletData.summary||{}; updateSummary(summary);
  if(currentMode==='SURFACE')renderSurface(); else if(currentMode==='FLOW')renderFlow(summary); else renderSpectrogram(summary);
}
