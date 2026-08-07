import { $ } from './util.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';

let emptyEl;
let statusEl;
let data = null;
let migrationData = null;
let liveData = { price: 0, proxyPrice: 0, trade: null };
let currentMode = 'MIGRATION';
let resizeObserver = null;
let lastSnapshotKey = '';
let pressurePlotEl = null;
let pressureCamera = null;

export function initGex() {
  emptyEl = $('#gex-evol-empty');
  statusEl = $('#gex-evol-status');
  $('#btn-gex-migration')?.addEventListener('click', () => setMode('MIGRATION'));
  $('#btn-gex-snapshot')?.addEventListener('click', () => setMode('SNAPSHOT'));
  const group = $('#gex-mode-group');
  if (group && !$('#btn-gex-pressure')) {
    const b = document.createElement('button');
    b.className = 'btn-toggle'; b.id = 'btn-gex-pressure'; b.textContent = 'PRESSURE 3D';
    b.addEventListener('click', () => setMode('PRESSURE'));
    group.insertBefore(b, $('#btn-gex-snapshot') || null);
  }
  const el = $('#gex-evol-canvas');
  if (el && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderGex(true));
    resizeObserver.observe(el);
  }
}

function setMode(mode) {
  currentMode = mode;
  $('#btn-gex-migration')?.classList.toggle('active', mode === 'MIGRATION');
  $('#btn-gex-pressure')?.classList.toggle('active', mode === 'PRESSURE');
  $('#btn-gex-snapshot')?.classList.toggle('active', mode === 'SNAPSHOT');
  renderGex(true);
}

function showEmpty(message, status) {
  const el = $('#gex-evol-canvas');
  if (el) el.replaceChildren();
  pressurePlotEl = null; pressureCamera = null;
  if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = message; }
  if (statusEl) statusEl.textContent = status;
  if ($('#gex-interpretation')) $('#gex-interpretation').style.display = 'none';
}

export async function updateGex(ridgePayload) {
  if (!ridgePayload?.available || !ridgePayload.snapshots?.length) {
    data = null; migrationData = null;
    showEmpty('○ OI × GAMMA КОНТЕКСТ НЕДОСТУПЕН', '○ GEX НЕДОСТУПЕН');
    return;
  }
  const latest = ridgePayload.snapshots.at(-1);
  if (!latest?.gex?.available || !latest.gex.strikes?.length || !latest.gex.net?.length) {
    data = null; migrationData = null;
    showEmpty('○ GEX КОНТЕКСТ ОТКЛЮЧЁН ДЛЯ ЭТОГО PROXY', '○ GEX CONTEXT ONLY');
    return;
  }
  data = {
    scale: Number(ridgePayload.scale) || 1,
    price: Number(ridgePayload.price) || 0,
    proxyPrice: Number(ridgePayload.proxy_spot_current) || 0,
    transform: ridgePayload.proxy_transform || 'direct',
    latest: latest.gex,
    oiWalls: ridgePayload.oi_walls || null,
    zeroFlip: Number(latest.gex.zero_flip) || null,
  };
  liveData.trade = ridgePayload.trade || liveData.trade;
  try {
    const res = await fetch('/api/analytics/gex-migration', { cache: 'no-store' });
    migrationData = res.ok ? await res.json() : null;
  } catch (err) {
    console.warn('GEX migration fetch failed:', err); migrationData = null;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  renderGex(true);
}

export function updateLiveGex(live) {
  if (live.price !== undefined) liveData.price = Number(live.price) || 0;
  if (live.proxyPrice !== undefined) liveData.proxyPrice = Number(live.proxyPrice) || 0;
  if (live.trade !== undefined) liveData.trade = live.trade;
  if (currentMode !== 'PRESSURE') renderGex(false);
}

function renderGex(force = false) {
  if (currentMode === 'PRESSURE' && migrationData?.available) renderPressureSurface();
  else if (currentMode === 'MIGRATION' && migrationData?.available) renderMigrationMap();
  else renderSnapshotBarChart(force);
}

function fmtVal(value) {
  const v = Number(value) || 0, a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toFixed(0);
}
function fmtMigration(v, suffix = '/6h') { if (v == null || !Number.isFinite(Number(v))) return '— BUILDING'; const n=Number(v); return `${n>0?'+':''}${n.toFixed(1)} ${suffix}`; }
function fmtUtc(ts) { const d=new Date(Number(ts)*1000); if(Number.isNaN(d.getTime()))return'—'; return `${String(d.getUTCDate()).padStart(2,'0')}.${String(d.getUTCMonth()+1).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`; }
function percentile(values,q){const a=values.filter(Number.isFinite).sort((x,y)=>x-y);if(!a.length)return 1;return a[Math.max(0,Math.min(a.length-1,Math.round((a.length-1)*q)))]||1;}

function updateMigrationSummary(summary) {
  if ($('#gex-sum-regime')) $('#gex-sum-regime').textContent = summary.gamma_regime || 'UNKNOWN';
  if ($('#gex-sum-flip')) $('#gex-sum-flip').textContent = summary.flip?.price == null ? '—' : Number(summary.flip.price).toFixed(1);
  if ($('#gex-sum-flip-mig')) $('#gex-sum-flip-mig').textContent = fmtMigration(summary.flip?.migration_6h);
  if ($('#gex-sum-call')) $('#gex-sum-call').textContent = summary.call_wall?.price == null ? '—' : Number(summary.call_wall.price).toFixed(1);
  if ($('#gex-sum-call-mig')) $('#gex-sum-call-mig').textContent = fmtMigration(summary.call_wall?.migration_6h);
  if ($('#gex-sum-put')) $('#gex-sum-put').textContent = summary.put_wall?.price == null ? '—' : Number(summary.put_wall.price).toFixed(1);
  if ($('#gex-sum-put-mig')) $('#gex-sum-put-mig').textContent = fmtMigration(summary.put_wall?.migration_6h);
  const path=$('#gex-sum-path');if(path){path.textContent=summary.corridor_state&&summary.corridor_state!=='NO DATA'?summary.corridor_state:(summary.take_path||'NO DATA');const s=String(path.textContent);path.style.background=s.includes('HARD')||s.includes('OBSTRUCTED')?'#c6373c':s.includes('THIN')||s.includes('MIXED')?'#b47c2d':'#2e7d4f';path.style.color='#fff';}
  const pressure=$('#gex-sum-pressure');if(pressure){const p=summary.obstruction_score==null?Number(summary.path_pressure||0):Number(summary.obstruction_score);pressure.textContent=summary.obstruction_score==null?`${p>0?'+':''}${p.toFixed(2)}`:`${(p*100).toFixed(0)}% OBSTRUCTION`;pressure.style.color=p>.55?'#c6373c':p>.28?'#b47c2d':'#2e7d4f';}
  if(statusEl){const n=summary.snapshot_count??migrationData?.timestamps?.length??0,h=summary.history_hours==null?'':` · ${Number(summary.history_hours).toFixed(1)}H`;statusEl.textContent=`● GEX FIELD · ${n} SNAP${h}`;}
  const card=$('#gex-summary-card');if(card&&typeof document.createElement==='function'){let meta=$('#gex-migration-meta');if(!meta){meta=document.createElement('div');meta.id='gex-migration-meta';meta.style.cssText='border-top:1px solid #d9d6ce;padding-top:7px;margin-top:7px;font-size:10px;color:#706d65;line-height:1.7';card.appendChild?.(meta);}if(meta){const cp=Number(summary.call_wall?.persistence||0)*100,pp=Number(summary.put_wall?.persistence||0)*100,cs=Number(summary.call_wall?.strength||0),ps=Number(summary.put_wall?.strength||0);meta.innerHTML=`WALL PERSISTENCE · CALL ${cp.toFixed(0)}% · PUT ${pp.toFixed(0)}%<br>REL STRENGTH · CALL ${cs.toFixed(2)} · PUT ${ps.toFixed(2)}<br>TAKE GEOMETRY · ${summary.take_path||'—'}`;}}
}

function ensurePressurePlot(){
  const el=$('#gex-evol-canvas');if(!el)return null;
  if(!pressurePlotEl){pressurePlotEl=document.createElement('div');pressurePlotEl.id='gex-pressure-3d';pressurePlotEl.style.cssText='width:100%;height:100%;display:none';el.appendChild(pressurePlotEl);pressureCamera=createPlotlyCameraGuard(pressurePlotEl,{eye:{x:1.45,y:-1.65,z:1.08},up:{x:0,y:0,z:1}});}
  return pressurePlotEl;
}

function renderPressureSurface(){
  if(!window.Plotly||!migrationData?.price_grid?.length){renderMigrationMap();return;}
  const el=$('#gex-evol-canvas'),plot=ensurePressurePlot();if(!el||!plot)return;el.querySelector('canvas')?.style.setProperty('display','none');plot.style.display='block';if(emptyEl)emptyEl.style.display='none';
  const summary=migrationData.summary||{};updateMigrationSummary(summary);
  const prices=migrationData.price_grid,times=migrationData.timestamps||[],heat=migrationData.heatmap||[];const abs=[];heat.forEach(row=>row.forEach(v=>{if(Number(v))abs.push(Math.abs(Number(v)));}));const scale=Math.max(percentile(abs,.98),1e-12);const last=Number(times.at(-1));const x=times.map(t=>(Number(t)-last)/3600);
  const z=heat.map(row=>row.map(v=>Math.sqrt(Math.min(1,Math.abs(Number(v)||0)/scale))));const surfaceColor=heat.map(row=>row.map(v=>Math.max(-1,Math.min(1,(Number(v)||0)/scale))));
  const colorscale=[[0,'#b92f39'],[.38,'#efb3a9'],[.5,'#f7f5ef'],[.62,'#a8d9bc'],[1,'#207b4b']];
  const traces=[{type:'surface',x,y:prices,z,surfacecolor:surfaceColor,cmin:-1,cmax:1,colorscale,showscale:false,opacity:.94,hovertemplate:'t %{x:.1f}h<br>price %{y:.1f}<br>|GEX| %{z:.2f}<extra></extra>',lighting:{ambient:.7,diffuse:.75,roughness:.9,specular:.18}}];
  const traj=migrationData.trajectories||{};const addTrail=(pts,color,name,dash='solid')=>{const v=(pts||[]).filter(p=>p.price!=null);if(!v.length)return;traces.push({type:'scatter3d',mode:'lines',x:v.map(p=>(Number(p.ts)-last)/3600),y:v.map(p=>Number(p.price)),z:v.map(()=>1.035),line:{color,width:5,dash},name,showlegend:false,hovertemplate:`${name} %{y:.1f}<extra></extra>`});};
  addTrail(traj.call_wall,'#1d7d49','CALL WALL');addTrail(traj.put_wall,'#c6373c','PUT WALL');addTrail(traj.flip,'#7c4d9e','GAMMA FLIP','dash');
  const current=Number(liveData.price||summary.current_price||data?.price),take=Number(liveData.trade?.take);if(Number.isFinite(current))traces.push({type:'scatter3d',mode:'lines',x:[x[0],0],y:[current,current],z:[1.07,1.07],line:{color:'#e8622a',width:6},showlegend:false,hoverinfo:'skip'});if(Number.isFinite(take))traces.push({type:'scatter3d',mode:'lines',x:[x[0],0],y:[take,take],z:[1.065,1.065],line:{color:'#2e7d4f',width:5,dash:'dot'},showlegend:false,hoverinfo:'skip'});
  const layout={margin:{l:0,r:0,b:0,t:0},paper_bgcolor:'transparent',showlegend:false,uirevision:'gex-pressure-v1',scene:{xaxis:{title:'HOURS → NOW',gridcolor:'#ddd9d0'},yaxis:{title:'PRICE / STRIKE',gridcolor:'#ddd9d0'},zaxis:{title:'|GEX PRESSURE|',range:[0,1.1],gridcolor:'#ddd9d0'},bgcolor:'rgba(255,255,255,0)',aspectratio:{x:1.75,y:1,z:.75}}};pressureCamera?.beforeWrite();window.Plotly.react(plot,traces,layout,{responsive:true,displayModeBar:false});pressureCamera?.afterWrite();
}

function renderMigrationMap() {
  const el=$('#gex-evol-canvas');if(!el||!migrationData?.price_grid?.length)return;if(pressurePlotEl)pressurePlotEl.style.display='none';if(emptyEl)emptyEl.style.display='none';const summary=migrationData.summary||{};updateMigrationSummary(summary);
  let cv=el.querySelector('canvas');if(!cv){cv=document.createElement('canvas');cv.style.cssText='width:100%;height:100%;display:block';el.insertBefore(cv,pressurePlotEl||null);}cv.style.display='block';const rect=el.getBoundingClientRect(),width=Math.max(520,Math.floor(rect.width||850)),height=Math.max(340,Math.floor(rect.height||420));cv.width=width;cv.height=height;const ctx=cv.getContext('2d');ctx.clearRect(0,0,width,height);
  const margin={left:70,right:105,top:56,bottom:42},plotW=width-margin.left-margin.right,plotH=height-margin.top-margin.bottom,prices=migrationData.price_grid,timestamps=migrationData.timestamps||[],heat=migrationData.heatmap||[];if(!timestamps.length||!heat.length)return;
  const range=migrationData.plot_range||[prices[0],prices.at(-1)],pMin=Number(range[0]),pMax=Number(range[1]),tMin=Number(timestamps[0]),tMax=Number(timestamps.at(-1));const X=ts=>margin.left+((Number(ts)-tMin)/Math.max(1,tMax-tMin))*plotW,Y=p=>margin.top+plotH-((Number(p)-pMin)/Math.max(1e-9,pMax-pMin))*plotH;
  // Corridor is a decision field: shade only the actual path between price and take.
  const corridor=migrationData.corridor;if(corridor){const y1=Y(corridor.lo),y2=Y(corridor.hi);const obs=Number(summary.obstruction_score||0);ctx.fillStyle=obs>.55?'rgba(198,55,60,.09)':obs>.28?'rgba(215,144,49,.08)':'rgba(46,125,79,.07)';ctx.fillRect(margin.left,Math.min(y1,y2),plotW,Math.abs(y2-y1));}
  const abs=[];heat.forEach(row=>row.forEach(v=>{if(Math.abs(Number(v))>0)abs.push(Math.abs(Number(v)));}));const scale=Math.max(percentile(abs,.98),1e-12),cellW=Math.max(2,plotW/Math.max(1,timestamps.length)),cellH=plotH/Math.max(1,prices.length);
  for(let r=0;r<prices.length;r++){const y=Y(prices[r]);for(let c=0;c<timestamps.length;c++){const v=Number(heat[r]?.[c]||0);if(!v)continue;const norm=Math.sqrt(Math.min(1,Math.abs(v)/scale)),alpha=.06+.86*norm;ctx.fillStyle=v>0?`rgba(46,125,79,${alpha})`:`rgba(198,55,60,${alpha})`;ctx.fillRect(X(timestamps[c])-cellW/2,y-cellH/2,cellW+1,Math.max(2,cellH+1));}}
  // Pressure-strip: the animation is the history itself, not a decorative pulse.
  const ph=migrationData.path_pressure_history||[];if(ph.length){const top=12,h=30;ctx.fillStyle='#f3f1eb';ctx.fillRect(margin.left,top,plotW,h);ctx.beginPath();ph.forEach((p,i)=>{const xx=X(p.ts),yy=top+h-(Number(p.obstruction||0))*h;if(!i)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);});ctx.strokeStyle='#c85a43';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#6d6962';ctx.font='bold 9px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.fillText('TAKE-PATH FRICTION',margin.left,top-2);ctx.textAlign='right';ctx.fillText(`${(Number(ph.at(-1).obstruction||0)*100).toFixed(0)}%`,margin.left+plotW,top-2);}
  ctx.font='9px IBM Plex Mono,monospace';ctx.strokeStyle='rgba(80,78,72,.16)';ctx.fillStyle='#6f6c64';for(let i=0;i<=6;i++){const p=pMin+i*(pMax-pMin)/6,y=Y(p);ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(margin.left+plotW,y);ctx.stroke();ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillText(p.toFixed(1),margin.left-7,y);}const xLabels=Math.min(5,timestamps.length);for(let i=0;i<xLabels;i++){const idx=xLabels===1?0:Math.round(i*(timestamps.length-1)/(xLabels-1)),xv=X(timestamps[idx]);ctx.beginPath();ctx.moveTo(xv,margin.top);ctx.lineTo(xv,margin.top+plotH);ctx.stroke();ctx.textAlign='center';ctx.textBaseline='top';ctx.fillText(fmtUtc(timestamps[idx]),xv,margin.top+plotH+8);}
  function drawTrajectory(points,color,dash,label){const valid=(points||[]).filter(p=>p.price!=null&&Number.isFinite(Number(p.price))&&Number(p.price)>=pMin&&Number(p.price)<=pMax);if(!valid.length)return;const stroke=(col,w)=>{ctx.save();ctx.strokeStyle=col;ctx.lineWidth=w;ctx.setLineDash(dash||[]);ctx.beginPath();valid.forEach((p,i)=>{const xv=X(p.ts),yv=Y(p.price);if(!i)ctx.moveTo(xv,yv);else ctx.lineTo(xv,yv);});ctx.stroke();ctx.restore();};stroke('rgba(255,255,255,.82)',5);stroke(color,2.2);const last=valid.at(-1);ctx.fillStyle=color;ctx.font='bold 9px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.textBaseline='bottom';ctx.fillText(`${label} ${Number(last.price).toFixed(1)}`,Math.min(X(last.ts)+5,margin.left+plotW-110),Y(last.price)-3);}
  const traj=migrationData.trajectories||{};drawTrajectory(traj.call_wall,'#2e7d4f',[],'CALL');drawTrajectory(traj.put_wall,'#c6373c',[],'PUT');drawTrajectory(traj.flip,'#7c4d9e',[5,4],'FLIP');
  function marker(value,label,color,dash=[]){const v=Number(value);if(!Number.isFinite(v)||v<pMin||v>pMax)return;const y=Y(v);ctx.save();ctx.strokeStyle=color;ctx.lineWidth=label==='PRICE'?2.3:1.25;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(margin.left+plotW,y);ctx.stroke();ctx.restore();ctx.fillStyle=color;ctx.font='bold 9px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(`${label} ${v.toFixed(1)}`,margin.left+plotW+6,y);}
  const currentPrice=liveData.price||summary.current_price||data?.price;marker(currentPrice,'PRICE','#e8622a');marker(liveData.trade?.entry,'ENTRY','#77736c',[3,3]);marker(liveData.trade?.stop,'STOP','#c6373c');marker(liveData.trade?.take,'TAKE','#2e7d4f');ctx.strokeStyle='#bdb9af';ctx.strokeRect(margin.left,margin.top,plotW,plotH);
}

function prepareVisible() {
  if (!data) return { pairs: [], price: 0, liveMap: 1 };
  const instrumentFactor=liveData.price&&data.price?liveData.price/data.price:1,proxyFactor=liveData.proxyPrice&&data.proxyPrice?liveData.proxyPrice/data.proxyPrice:1,liveMap=data.transform==='inverse'?instrumentFactor*proxyFactor:instrumentFactor/proxyFactor;
  const strikes=data.latest.strikes.map(s=>Number(s)*data.scale*liveMap),nets=data.latest.net.map(Number),price=liveData.price||data.price||0,pairs=strikes.map((strike,i)=>({strike,net:nets[i]})).filter(p=>Number.isFinite(p.strike)&&Number.isFinite(p.net)&&p.net!==0).sort((a,b)=>a.strike-b.strike);if(!pairs.length)return{pairs:[],price,liveMap};
  const av=pairs.map(p=>Math.abs(p.net)).sort((a,b)=>a-b),q25=av[Math.floor((av.length-1)*.25)]||0,q75=av[Math.floor((av.length-1)*.75)]||1,threshold=Math.max(q75+3*(q75-q25),q75,1);pairs.forEach(p=>{p.clamped=clamp(p.net,-threshold,threshold);p.isOutlier=Math.abs(p.net)>threshold;});let closest=0;pairs.forEach((p,i)=>{if(Math.abs(p.strike-price)<Math.abs(pairs[closest].strike-price))closest=i;});const maxRows=25;let start=Math.max(0,closest-Math.floor(maxRows/2)),end=Math.min(pairs.length,start+maxRows);start=Math.max(0,end-maxRows);return{pairs:pairs.slice(start,end),price,liveMap};
}

function renderSnapshotBarChart(force=false) {
  const el=$('#gex-evol-canvas');if(!el||!data)return;if(pressurePlotEl)pressurePlotEl.style.display='none';const oldCanvas=el.querySelector('canvas');if(oldCanvas)oldCanvas.style.display='none';const {pairs:visible,price,liveMap}=prepareVisible();if(!visible.length){showEmpty('○ GEX: НУЛЕВОЙ ПРОФИЛЬ','○ GEX CONTEXT');return;}if(emptyEl)emptyEl.style.display='none';if(statusEl)statusEl.textContent='● OI-GEX SNAPSHOT';
  const width=Math.max(520,Math.round(el.clientWidth||el.getBoundingClientRect().width||820)),height=Math.max(360,Math.round(el.clientHeight||el.getBoundingClientRect().height||420)),key=[width,height,price.toFixed(2),liveMap.toFixed(5),visible.map(p=>`${p.strike}:${p.net}`).join('|')].join(':');if(!force&&key===lastSnapshotKey)return;lastSnapshotKey=key;
  const margin={left:74,right:90,top:24,bottom:32},plotW=width-margin.left-margin.right,plotH=height-margin.top-margin.bottom,cx=margin.left+plotW/2,maxAbs=Math.max(...visible.map(p=>Math.abs(p.clamped)),1),xScale=v=>cx+v/maxAbs*(plotW/2-12),rowH=plotH/visible.length,strongest=[...visible].sort((a,b)=>Math.abs(b.net)-Math.abs(a.net)).slice(0,5),threshold=Math.abs(strongest.at(-1)?.net||Infinity),svg=[`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="OI Gamma snapshot"><rect width="100%" height="100%" fill="#fff"/>`];
  for(let i=-2;i<=2;i++){const x=cx+i*plotW/4;svg.push(`<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${height-margin.bottom}" stroke="${i?'#eeeae2':'#8a877d'}"/>`);}visible.forEach((p,idx)=>{const y=margin.top+plotH-(idx+.5)*rowH,x=xScale(p.clamped),bx=Math.min(cx,x),bw=Math.max(1,Math.abs(x-cx)),color=p.net>0?'#2e7d4f':'#c6373c';svg.push(`<text x="${margin.left-7}" y="${y+3}" text-anchor="end" font-size="9" fill="#6f6c64" font-family="IBM Plex Mono,monospace">${p.strike.toFixed(1)}</text>`);svg.push(`<rect x="${bx}" y="${y-Math.max(2,rowH*.3)}" width="${bw}" height="${Math.max(4,rowH*.6)}" fill="${color}" fill-opacity=".75"><title>Strike ${p.strike.toFixed(1)} · Net GEX ${escapeXml(fmtVal(p.net))}</title></rect>`);if(Math.abs(p.net)>=threshold)svg.push(`<text x="${p.net>0?x+5:x-5}" y="${y+3}" text-anchor="${p.net>0?'start':'end'}" font-size="9" font-weight="600" fill="#45433e" font-family="IBM Plex Mono,monospace">${escapeXml(fmtVal(p.net))}</text>`);});
  const markerY=v=>{if(!Number.isFinite(v)||v<visible[0].strike||v>visible.at(-1).strike)return null;return margin.top+plotH-(v-visible[0].strike)/Math.max(1e-9,visible.at(-1).strike-visible[0].strike)*plotH;};const markers=[[price,`PRICE ${price.toFixed(1)}`,'#e8622a',''],[data.zeroFlip?data.zeroFlip*data.scale*liveMap:NaN,'FLIP','#7c4d9e','5 3'],[Number(liveData.trade?.entry),'ENTRY','#77736c','3 3'],[Number(liveData.trade?.stop),'STOP','#c6373c',''],[Number(liveData.trade?.take),'TAKE','#2e7d4f','']];markers.forEach(([v,label,color,dash])=>{const y=markerY(Number(v));if(y==null)return;svg.push(`<line x1="${margin.left}" y1="${y}" x2="${width-margin.right}" y2="${y}" stroke="${color}" stroke-width="${String(label).startsWith('PRICE')?2:1.3}" ${dash?`stroke-dasharray="${dash}"`:''}/>`);svg.push(`<text x="${width-margin.right+5}" y="${y+3}" font-size="9" font-weight="600" fill="${color}" font-family="IBM Plex Mono,monospace">${escapeXml(label)}</text>`);});svg.push(`<text x="${margin.left}" y="14" font-size="9" fill="#8a877d" font-family="IBM Plex Mono,monospace">NEGATIVE GEX</text><text x="${width-margin.right}" y="14" text-anchor="end" font-size="9" fill="#8a877d" font-family="IBM Plex Mono,monospace">POSITIVE GEX</text></svg>`);
  // Replace only the display layer; pressure div is recreated lazily when needed.
  el.innerHTML=svg.join(''); pressurePlotEl=null; pressureCamera=null;
}

function escapeXml(value){return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&apos;');}
function clamp(value,lo,hi){return Math.max(lo,Math.min(hi,value));}
