import { $ } from './util.js';
import { subscribeMarketTick } from './market_bus.js';
import { ensurePremiumAnalyticsTheme } from './premium_analytics_theme.js';

let chart = null;
let emptyEl = null;
let statusEl = null;
let payload = null;
let graphData = null;
let currentMode = 'NETWORK';
let resizeObserver = null;
let refreshTimer = null;
let rafId = null;
let draggedNodeId = null;
let hoveredNodeId = null;
let liveTick = null;
let unsubscribeTick = null;
let generation = 0;
const positions = new Map();

export function initCorrelation() {
  ensurePremiumAnalyticsTheme();
  emptyEl = $('#corr-empty');
  statusEl = $('#corr-status');
  $('#btn-corr-network')?.addEventListener('click', () => setMode('NETWORK'));
  $('#btn-corr-matrix')?.addEventListener('click', () => setMode('MATRIX'));
  const holder = $('#corr-chart');
  if (holder && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderCorrelation(true));
    resizeObserver.observe(holder);
  }
  unsubscribeTick = subscribeMarketTick((tick) => { liveTick = tick; ensureNetworkAnimation(); });
  fetchGraphData();
  refreshTimer = setInterval(fetchGraphData, 300000);
}

function setMode(mode) {
  if (currentMode === mode) return;
  currentMode = mode;
  $('#btn-corr-network')?.classList.toggle('active', mode === 'NETWORK');
  $('#btn-corr-matrix')?.classList.toggle('active', mode === 'MATRIX');
  destroyRenderer();
  renderCorrelation(true);
}

function destroyRenderer() {
  generation += 1;
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
  if (chart) { try { chart.dispose(); } catch {} chart = null; }
  const holder = $('#corr-chart');
  holder?.querySelectorAll('[data-corr-renderer]').forEach((n) => n.remove());
}

export async function fetchGraphData() {
  try {
    const res = await fetch('/api/analytics/correlation-graph', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    graphData = await res.json();
    renderCorrelation(true);
  } catch (err) {
    console.warn('Correlation graph fetch error:', err);
    if (statusEl) statusEl.textContent = '○ NETWORK OFFLINE';
  }
}

export function updateCorrelation(p) {
  const matrix = p?.matrix_short || p?.matrix;
  if (!p || !matrix?.length) {
    payload = null;
    if (currentMode === 'MATRIX' && emptyEl) emptyEl.style.display = 'flex';
    return;
  }
  payload = p;
  if (currentMode === 'MATRIX') renderMatrixChart(true);
}

function renderCorrelation(force = false) {
  if (currentMode === 'NETWORK') renderForceGraph(force);
  else renderMatrixChart(force);
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, Number(v))); }
function groupColor(group) { return ({ equity:'#3b82b8', volatility:'#b44f71', metals:'#c38b2d', energy:'#9b7143', fx:'#4e9b7d', crypto:'#7d6bb5', other:'#647687' })[group] || '#647687'; }
function edgeColor(rho, alpha) { return rho >= 0 ? `rgba(69,211,153,${alpha})` : `rgba(244,82,107,${alpha})`; }

function aliasForInstrument(instrument) {
  const a = String(instrument || '').toUpperCase();
  if (a.includes('NAS')) return 'NAS';
  if (a.includes('SP500') || a.includes('SPX')) return 'SP500';
  if (a.includes('XAU') || a.includes('GOLD')) return 'GOLD';
  if (a.includes('OIL') || a.includes('WTI') || a.includes('BRENT')) return 'OIL';
  if (a.includes('US30')) return 'US30';
  if (a.includes('GER')) return 'GER40';
  if (a.includes('UK')) return 'UK100';
  return a;
}

function settleLayout(nodes, links, width, height) {
  const pad = 54;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const systemic = clamp(graphData?.summary?.systemic_coupling || 0, 0, 1);
  const fragmentation = clamp(graphData?.summary?.fragmentation || 0, 0, 1);
  nodes.forEach((n) => {
    const p = positions.get(n.id);
    if (p) { n.x = p.x; n.y = p.y; }
    else { n.x = pad + Number(n.x_norm ?? .5) * (width - pad * 2); n.y = pad + Number(n.y_norm ?? .5) * (height - pad * 2); }
  });
  for (let iter = 0; iter < 115; iter++) {
    const force = new Map(nodes.map((n) => [n.id, { x: 0, y: 0 }]));
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let dist = Math.max(22, Math.hypot(dx, dy));
        dx /= dist; dy /= dist;
        const rep = (1350 + fragmentation * 1200) / (dist * dist);
        force.get(a.id).x -= dx * rep; force.get(a.id).y -= dy * rep;
        force.get(b.id).x += dx * rep; force.get(b.id).y += dy * rep;
      }
    }
    links.forEach((l) => {
      const a = byId.get(l.source), b = byId.get(l.target);
      if (!a || !b) return;
      const rho = Math.abs(Number(l.correlation || 0));
      let dx = b.x - a.x, dy = b.y - a.y, dist = Math.max(1, Math.hypot(dx, dy));
      dx /= dist; dy /= dist;
      const target = 218 - rho * 102 - systemic * 32;
      const spring = (dist - target) * .012 * (.08 + rho * .92);
      force.get(a.id).x += dx * spring; force.get(a.id).y += dy * spring;
      force.get(b.id).x -= dx * spring; force.get(b.id).y -= dy * spring;
    });
    // Systemic coupling contracts the network; fragmentation expands it.
    const cx = width / 2, cy = height / 2;
    nodes.forEach((n) => {
      if (n.id === draggedNodeId) return;
      const f = force.get(n.id);
      const inward = .0018 * systemic;
      const outward = .0016 * fragmentation;
      f.x += (cx - n.x) * inward - (cx - n.x) * outward;
      f.y += (cy - n.y) * inward - (cy - n.y) * outward;
      n.x = Math.max(pad, Math.min(width - pad, n.x + f.x));
      n.y = Math.max(pad, Math.min(height - pad, n.y + f.y));
    });
  }
  nodes.forEach((n) => positions.set(n.id, { x: n.x, y: n.y }));
}

function networkCanvas() {
  const holder = $('#corr-chart');
  let cv = holder?.querySelector('canvas[data-corr-renderer="network"]');
  if (!cv && holder) {
    cv = document.createElement('canvas');
    cv.dataset.corrRenderer = 'network';
    cv.style.cssText = 'width:100%;height:100%;display:block;cursor:grab;touch-action:none';
    holder.appendChild(cv);
  }
  const rect = holder.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(560, Math.floor(rect.width || 930));
  const height = Math.max(340, Math.floor(rect.height || 390));
  cv.width = Math.floor(width * dpr); cv.height = Math.floor(height * dpr);
  cv.style.width = `${width}px`; cv.style.height = `${height}px`;
  return { holder, cv, dpr, width, height };
}

function renderForceGraph(force = false) {
  if (!graphData?.available || !graphData.nodes?.length) {
    if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = `○ ${graphData?.reason || 'НЕТ РЕАЛЬНОЙ CROSS-ASSET МАТРИЦЫ'}`; }
    if (statusEl) statusEl.textContent = '○ NO REAL NETWORK DATA';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  if (chart) { try { chart.dispose(); } catch {} chart = null; }
  const { cv, dpr, width, height } = networkCanvas();
  const nodes = graphData.nodes.map((n) => ({ ...n }));
  const links = (graphData.links || []).map((l) => ({ ...l }));
  settleLayout(nodes, links, width, height);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const generationNow = generation;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const activeInstrument = aliasForInstrument(liveTick?.trade?.instrument);
  const allActive = links.filter((l) => Math.abs(Number(l.correlation || 0)) >= .22 || Number(l.tension || 0) >= .08 || l.status === 'BREAK_ALERT');

  const draw = (now) => {
    if (generationNow !== generation || currentMode !== 'NETWORK') return;
    ctx.clearRect(0, 0, width, height);
    drawNetworkBackground(ctx, width, height);

    // Layer 1: complete observed topology. Every real pair is visible, including weak relationships.
    links.forEach((l) => {
      const a = byId.get(l.source), b = byId.get(l.target); if (!a || !b) return;
      const rho = Number(l.correlation || 0), absR = Math.abs(rho);
      const alpha = .045 + absR * .17;
      ctx.strokeStyle = edgeColor(rho, alpha);
      ctx.lineWidth = .55 + absR * .8;
      ctx.setLineDash(absR < .08 ? [2, 5] : []);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      ctx.setLineDash([]);
    });

    // Layer 2: strong or fast-changing relationships.
    allActive.forEach((l, li) => {
      const a = byId.get(l.source), b = byId.get(l.target); if (!a || !b) return;
      const rho = Number(l.correlation || 0), absR = Math.abs(rho);
      const tension = clamp(l.tension || 0, 0, 1.5);
      const alert = l.status === 'BREAK_ALERT';
      ctx.strokeStyle = alert ? 'rgba(255,82,105,.82)' : edgeColor(rho, .26 + absR * .54);
      ctx.lineWidth = 1.1 + absR * 3.0 + tension * 1.5;
      ctx.setLineDash(alert ? [8, 5] : []);
      ctx.shadowColor = alert ? '#ff5269' : (rho >= 0 ? '#43d79f' : '#ff5e78');
      ctx.shadowBlur = 4 + tension * 8;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      ctx.shadowBlur = 0; ctx.setLineDash([]);

      // Packets move only when the observed relationship itself changes.
      const velocity = clamp(Number(l.velocity_magnitude || 0) / .35, 0, 1);
      if (velocity > .03 || alert) {
        const speed = .035 + velocity * .22;
        const phase = (now / 1000 * speed + li * .173) % 1;
        for (const t of [phase, (phase + .5) % 1]) {
          const x = a.x + (b.x - a.x) * t, y = a.y + (b.y - a.y) * t;
          const r = 2 + 4.5 * Math.max(velocity, tension * .5);
          const grad = ctx.createRadialGradient(x, y, 0, x, y, r * 2.6);
          grad.addColorStop(0, alert ? 'rgba(255,99,115,.96)' : (rho >= 0 ? 'rgba(74,232,177,.92)' : 'rgba(255,103,124,.92)'));
          grad.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(x, y, r * 2.6, 0, Math.PI * 2); ctx.fill();
        }
      }
      if (absR >= .48 || alert) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const text = `${rho >= 0 ? '+' : ''}${rho.toFixed(2)}`;
        ctx.font = '9px IBM Plex Mono,monospace'; const tw = ctx.measureText(text).width + 8;
        ctx.fillStyle = 'rgba(7,17,29,.82)'; ctx.fillRect(mx - tw / 2, my - 8, tw, 15);
        ctx.fillStyle = alert ? '#ff6b7d' : '#c9d9e3'; ctx.textAlign = 'center'; ctx.fillText(text, mx, my + 3);
      }
    });

    const systemic = clamp(graphData.summary?.systemic_coupling || 0, 0, 1);
    const frag = clamp(graphData.summary?.fragmentation || 0, 0, 1);
    nodes.forEach((n) => {
      const stress = clamp(n.stress_normalized || 0, 0, 1);
      const coupling = clamp(n.coupling || 0, 0, 1);
      const incident = links.filter((l) => l.source === n.id || l.target === n.id);
      const noDynamics = incident.every((l) => Math.abs(Number(l.correlation || 0)) < .015 && Number(l.tension || 0) < .015);
      const isLive = activeInstrument && (n.id === activeInstrument || (activeInstrument === 'XAU' && n.id === 'GOLD'));
      const liveImpulse = isLive ? clamp(Number(liveTick?.impulse || 0), 0, 1) : 0;
      const r = 15 + coupling * 10;

      if (stress > .03 || liveImpulse > .02) {
        const haloR = r + 20 + stress * 16 + liveImpulse * 18;
        const halo = ctx.createRadialGradient(n.x, n.y, r * .6, n.x, n.y, haloR);
        halo.addColorStop(0, isLive ? `rgba(255,185,77,${.10 + liveImpulse * .22})` : `rgba(238,79,101,${.06 + stress * .18})`);
        halo.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(n.x, n.y, haloR, 0, Math.PI * 2); ctx.fill();
      }

      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = noDynamics ? '#314355' : groupColor(n.group); ctx.fill();
      ctx.strokeStyle = n.break_count ? '#ff5b72' : isLive ? '#ffbf55' : 'rgba(238,245,249,.88)';
      ctx.lineWidth = n.break_count ? 3 : isLive ? 2.8 : 1.5; ctx.stroke();
      if (noDynamics) { ctx.setLineDash([3,3]); ctx.strokeStyle = 'rgba(190,206,216,.45)'; ctx.beginPath(); ctx.arc(n.x,n.y,r+4,0,Math.PI*2);ctx.stroke();ctx.setLineDash([]); }

      // Quantitative stress ring.
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * stress);
      ctx.strokeStyle = stress > .65 ? '#ff5b72' : stress > .35 ? '#e2aa45' : '#48c7b0'; ctx.lineWidth = 3; ctx.stroke();
      if (liveImpulse > .02) {
        const rr = r + 10 + ((now / 14) % 20);
        ctx.beginPath(); ctx.arc(n.x, n.y, rr, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,191,85,${Math.max(0, .45 - (rr-r-10)/45) * liveImpulse})`; ctx.lineWidth = 1.5; ctx.stroke();
      }
      ctx.fillStyle = '#fff'; ctx.font = 'bold 9px IBM Plex Mono,monospace'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(n.id, n.x, n.y);
      ctx.fillStyle = 'rgba(201,216,226,.66)'; ctx.font = '8px IBM Plex Mono,monospace';
      ctx.fillText(noDynamics ? 'NO PAIR DYN' : `σ ${Number(n.stress_pressure || 0).toFixed(2)}`, n.x, n.y + r + 15);
    });

    drawHud(ctx, width, height, systemic, frag, links.length, allActive.length);
    drawHover(ctx, byId, links, width, height);
    rafId = requestAnimationFrame(draw);
  };

  bindNetworkPointer(cv, nodes, byId, width, height);
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(draw);
  updateNetworkText(links, allActive);
}

function drawNetworkBackground(ctx, width, height) {
  const g = ctx.createLinearGradient(0, 0, 0, height); g.addColorStop(0, '#06101b'); g.addColorStop(1, '#0a1724'); ctx.fillStyle = g; ctx.fillRect(0,0,width,height);
  const glow = ctx.createRadialGradient(width*.5,height*.46,10,width*.5,height*.46,width*.55); glow.addColorStop(0,'rgba(37,99,132,.12)'); glow.addColorStop(1,'rgba(0,0,0,0)'); ctx.fillStyle=glow;ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='rgba(183,207,222,.055)';ctx.lineWidth=1;for(let x=40;x<width;x+=60){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke();}for(let y=30;y<height;y+=50){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
}

function drawHud(ctx, width, height, systemic, frag, allPairs, activePairs) {
  const s = graphData.summary || {};
  ctx.fillStyle = 'rgba(4,11,18,.72)'; ctx.fillRect(10, 10, 245, 55);
  ctx.strokeStyle='rgba(184,207,222,.14)';ctx.strokeRect(10,10,245,55);
  ctx.fillStyle='#cbdce5';ctx.font='9px IBM Plex Mono,monospace';ctx.textAlign='left';
  ctx.fillText(`SYSTEM COUPLING ${systemic.toFixed(2)}   TENSION ${Number(s.network_tension||0).toFixed(2)}`,18,28);
  ctx.fillText(`FRAGMENT ${frag.toFixed(2)}   LINKS ${allPairs} / ACTIVE ${activePairs}`,18,44);
  ctx.fillStyle='#f0bd58';ctx.fillText(`STRESS NODE ${s.dominant_stress_node||'—'}`,18,59);
}

function drawHover(ctx, byId, links, width, height) {
  if (!hoveredNodeId) return;
  const n = byId.get(hoveredNodeId); if (!n) return;
  const incident = links.filter((l) => l.source === n.id || l.target === n.id).sort((a,b)=>Math.abs(Number(b.correlation||0))-Math.abs(Number(a.correlation||0))).slice(0,4);
  const boxW=190,boxH=42+incident.length*15;let x=Math.min(width-boxW-8,n.x+22),y=Math.max(8,Math.min(height-boxH-8,n.y-boxH/2));
  ctx.fillStyle='rgba(4,12,20,.94)';ctx.fillRect(x,y,boxW,boxH);ctx.strokeStyle='rgba(214,228,236,.18)';ctx.strokeRect(x,y,boxW,boxH);ctx.fillStyle='#fff';ctx.font='bold 10px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.fillText(`${n.id} · COUPLING ${Number(n.coupling||0).toFixed(2)}`,x+9,y+16);ctx.font='9px IBM Plex Mono,monospace';ctx.fillStyle='#b9ccd7';incident.forEach((l,i)=>{const other=l.source===n.id?l.target:l.source;ctx.fillText(`${other.padEnd(7)} ρ ${Number(l.correlation||0)>=0?'+':''}${Number(l.correlation||0).toFixed(2)} · Δ ${Number(l.delta_baseline||0).toFixed(2)}`,x+9,y+34+i*15);});
}

function bindNetworkPointer(cv, nodes, byId, width, height) {
  const pointer=(e)=>{const r=cv.getBoundingClientRect();return{x:(e.clientX-r.left)*width/r.width,y:(e.clientY-r.top)*height/r.height};};
  cv.onpointerdown=(e)=>{const p=pointer(e),hit=nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<=30);if(!hit)return;draggedNodeId=hit.id;cv.setPointerCapture?.(e.pointerId);cv.style.cursor='grabbing';};
  cv.onpointermove=(e)=>{const p=pointer(e);if(draggedNodeId){const n=byId.get(draggedNodeId);if(n){n.x=Math.max(28,Math.min(width-28,p.x));n.y=Math.max(28,Math.min(height-28,p.y));positions.set(n.id,{x:n.x,y:n.y});}}else{const hit=nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<=30);hoveredNodeId=hit?.id||null;cv.style.cursor=hit?'pointer':'grab';}};
  const release=(e)=>{if(draggedNodeId){draggedNodeId=null;cv.releasePointerCapture?.(e.pointerId);cv.style.cursor='grab';}};cv.onpointerup=release;cv.onpointercancel=release;cv.onpointerleave=()=>{if(!draggedNodeId)hoveredNodeId=null;};
}

function updateNetworkText(links, activeLinks) {
  const s=graphData.summary||{};if(statusEl)statusEl.textContent=s.active_breaks_count?`⚠ ${s.active_breaks_count} BREAK · TENSION ${Number(s.network_tension||0).toFixed(2)} · ${links.length} LINKS`:`● ${links.length} LINKS · COUPLING ${Number(s.systemic_coupling||0).toFixed(2)}${s.velocity_ready?'':' · ΔV BUILDING'}`;
  const interpret=$('#corr-interpretation');if(interpret){const top=(graphData.break_alerts||[])[0];interpret.innerHTML=top?`<b>NETWORK TENSION:</b> ${top.source}↔${top.target} · ρ ${Number(top.correlation).toFixed(2)} · Δbaseline ${top.delta_baseline==null?'—':Number(top.delta_baseline).toFixed(2)} · Δ15m ${top.delta_15m==null?'—':Number(top.delta_15m).toFixed(2)}. <b>Все ${links.length} наблюдаемых связи</b> показаны фоновым слоем; активный слой выделяет ${activeLinks.length} сильных/быстро меняющихся. Светящиеся пакеты показывают перестройку связи и не означают причинность.`:`<b>FULL TOPOLOGY:</b> все ${links.length} наблюдаемых пары показаны; ${activeLinks.length} находятся в активном слое. Толщина = |ρ|, внешний круг узла = incident stress, физическое сжатие сети = systemic coupling, расширение = fragmentation.`;interpret.style.display='block';}
}

function ensureNetworkAnimation(){if(currentMode==='NETWORK'&&!rafId)renderForceGraph(false);}

function renderMatrixChart(force=false) {
  const holder=$('#corr-chart'); if(!holder||!payload||!window.echarts)return;
  if(emptyEl)emptyEl.style.display='none';
  holder.querySelectorAll('canvas[data-corr-renderer="network"]').forEach(n=>n.remove());
  if(chart)chart.dispose(); chart=window.echarts.init(holder,null,{renderer:'canvas'});
  const matrix=payload.matrix_short||payload.matrix,assets=payload.assets||payload.pairs||[],delta=payload.matrix_delta||[],points=[];
  for(let i=0;i<matrix.length;i++)for(let j=0;j<matrix[i].length;j++){const v=Number(matrix[i][j]);if(Number.isFinite(v))points.push([j,i,v,Number(delta?.[i]?.[j])]);}
  chart.setOption({animationDuration:280,backgroundColor:'#08131f',tooltip:{backgroundColor:'rgba(4,12,20,.96)',borderColor:'#2b485e',textStyle:{color:'#d6e4eb',fontFamily:'IBM Plex Mono',fontSize:10},formatter:p=>{const[j,i,rho,d]=p.data;return`<b>${assets[i]} ↔ ${assets[j]}</b><br>rolling ρ: ${rho.toFixed(2)}<br>Δ vs baseline: ${Number.isFinite(d)?d.toFixed(2):'—'}`;}},grid:{left:70,right:30,top:28,bottom:48},xAxis:{type:'category',data:assets,axisLabel:{rotate:-25,fontSize:9,color:'#a9bdc9'},axisLine:{lineStyle:{color:'#345064'}},splitLine:{show:true,lineStyle:{color:'rgba(164,190,207,.07)'}}},yAxis:{type:'category',data:assets,inverse:true,axisLabel:{fontSize:9,color:'#a9bdc9'},axisLine:{lineStyle:{color:'#345064'}},splitLine:{show:true,lineStyle:{color:'rgba(164,190,207,.07)'}}},visualMap:{min:-1,max:1,show:false,inRange:{color:['#d84861','#172839','#36c898']}},series:[{type:'heatmap',data:points,label:{show:true,formatter:p=>Number(p.data[2]).toFixed(2),fontSize:9,color:'#dce7ed'},itemStyle:{borderColor:'rgba(210,225,234,.12)',borderWidth:1}}]});
  if(statusEl)statusEl.textContent=`● MATRIX · ${assets.length} ASSETS`;
}
