import { $ } from './util.js';
import { subscribeMarketTick } from './market_bus.js';
import { ensurePremiumAnalyticsTheme } from './premium_analytics_theme.js';
import { isAnalyticsMobile, analyticsMobileDpr } from './analytics_mobile.js';

let chart = null;
let emptyEl = null;
let statusEl = null;
let payload = null;
let graphData = null;
let currentMode = 'NETWORK';
let resizeObserver = null;
let visibilityObserver = null;
let panelVisible = true;
let refreshTimer = null;
let rafId = null;
let draggedNodeId = null;
let hoveredNodeId = null;
let liveTick = null;
let unsubscribeTick = null;
let generation = 0;
let lastMatrixSig = null;
const positions = new Map();

function threeDBusy() {
  if (typeof window === 'undefined') return false;
  return Boolean(window.__seiltanzer3dBusy)
    || Boolean(document?.documentElement?.classList?.contains?.('analytics-3d-busy'));
}

function canAnimateNetwork() {
  return currentMode === 'NETWORK'
    && panelVisible
    && !(typeof document !== 'undefined' && document.hidden)
    && !threeDBusy();
}

function correlationSignature(p) {
  const matrix = p?.matrix_short || p?.matrix;
  if (!Array.isArray(matrix) || !matrix.length) return 'empty';
  const assets = p?.assets || p?.pairs || [];
  // Matrix is small (usually 6–10 assets), so a rounded signature is much cheaper
  // than recreating an ECharts instance on every websocket price tick.
  const values = matrix.map((row) => (row || []).map((v) =>
    Number.isFinite(Number(v)) ? Number(v).toFixed(4) : 'x').join(',')).join(';');
  const delta = (p?.matrix_delta || []).map((row) => (row || []).map((v) =>
    Number.isFinite(Number(v)) ? Number(v).toFixed(4) : 'x').join(',')).join(';');
  return `${p?.ts || p?.updated_at || ''}|${assets.join(',')}|${values}|${delta}`;
}

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
  const panel = $('#panel-correlation');
  if (panel && typeof IntersectionObserver !== 'undefined') {
    visibilityObserver = new IntersectionObserver((entries) => {
      const entry = entries[0];
      panelVisible = entry ? entry.isIntersecting : true;
      if (!panelVisible && rafId) {
        cancelAnimationFrame(rafId);
        rafId = null;
      } else if (panelVisible) ensureNetworkAnimation();
    }, { rootMargin: '180px 0px', threshold: 0.01 });
    visibilityObserver.observe(panel);
  }
  window.addEventListener?.('seiltanzer:analytics-mobile-resize', () => renderCorrelation(true));
  window.addEventListener?.('seiltanzer:3d-busy', () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = null;
  });
  window.addEventListener?.('seiltanzer:3d-idle', ensureNetworkAnimation);
  document?.addEventListener?.('visibilitychange', () => {
    if (document.hidden && rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    } else ensureNetworkAnimation();
  }, { passive: true });
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
    lastMatrixSig = null;
    if (currentMode === 'MATRIX' && emptyEl) emptyEl.style.display = 'flex';
    return;
  }
  const sig = correlationSignature(p);
  const changed = sig !== lastMatrixSig;
  payload = p;
  if (!changed) return;
  lastMatrixSig = sig;
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
  const mobile = isAnalyticsMobile();
  const pad = mobile ? 34 : 54;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const systemic = clamp(graphData?.summary?.systemic_coupling || 0, 0, 1);
  const fragmentation = clamp(graphData?.summary?.fragmentation || 0, 0, 1);
  nodes.forEach((n) => {
    const p = positions.get(n.id);
    if (p) { n.x = clamp(p.x, pad, width-pad); n.y = clamp(p.y, pad, height-pad); }
    else { n.x = pad + Number(n.x_norm ?? .5) * (width - pad * 2); n.y = pad + Number(n.y_norm ?? .5) * (height - pad * 2); }
  });
  const iterations = mobile ? 68 : 115;
  for (let iter = 0; iter < iterations; iter++) {
    const force = new Map(nodes.map((n) => [n.id, { x: 0, y: 0 }]));
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let dist = Math.max(mobile ? 17 : 22, Math.hypot(dx, dy));
        dx /= dist; dy /= dist;
        const rep = ((mobile ? 760 : 1350) + fragmentation * (mobile ? 700 : 1200)) / (dist * dist);
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
      const mobileBase = clamp(width * .43, 105, 155);
      const target = (mobile ? mobileBase : 218) - rho * (mobile ? 56 : 102) - systemic * (mobile ? 18 : 32);
      const spring = (dist - target) * (mobile ? .016 : .012) * (.08 + rho * .92);
      force.get(a.id).x += dx * spring; force.get(a.id).y += dy * spring;
      force.get(b.id).x -= dx * spring; force.get(b.id).y -= dy * spring;
    });
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
  const mobile = isAnalyticsMobile();
  const dpr = analyticsMobileDpr();
  const width = mobile ? Math.max(280, Math.floor(rect.width || 340)) : Math.max(560, Math.floor(rect.width || 930));
  const height = mobile ? Math.max(300, Math.floor(rect.height || 350)) : Math.max(340, Math.floor(rect.height || 390));
  const pixelWidth = Math.floor(width * dpr), pixelHeight = Math.floor(height * dpr);
  if (cv.width !== pixelWidth) cv.width = pixelWidth;
  if (cv.height !== pixelHeight) cv.height = pixelHeight;
  if (cv.style.width !== `${width}px`) cv.style.width = `${width}px`;
  if (cv.style.height !== `${height}px`) cv.style.height = `${height}px`;
  return { holder, cv, dpr, width, height, mobile };
}

function renderForceGraph(force = false) {
  if (!graphData?.available || !graphData.nodes?.length) {
    if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = `○ ${graphData?.reason || 'НЕТ РЕАЛЬНОЙ CROSS-ASSET МАТРИЦЫ'}`; }
    if (statusEl) statusEl.textContent = '○ NO REAL NETWORK DATA';
    return;
  }
  if (!panelVisible && !force) return;
  if (emptyEl) emptyEl.style.display = 'none';
  if (chart) { try { chart.dispose(); } catch {} chart = null; }
  const { cv, dpr, width, height, mobile } = networkCanvas();
  const nodes = graphData.nodes.map((n) => ({ ...n }));
  const links = (graphData.links || []).map((l) => ({ ...l }));
  settleLayout(nodes, links, width, height);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const generationNow = generation;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const activeInstrument = aliasForInstrument(liveTick?.trade?.instrument);
  const allActive = links.filter((l) => Math.abs(Number(l.correlation || 0)) >= .22 || Number(l.tension || 0) >= .08 || l.status === 'BREAK_ALERT');
  const linkDynamics = allActive.some((l) => Number(l.velocity_magnitude || 0) > .01 || l.status === 'BREAK_ALERT');
  let lastDraw = 0;
  const activeFrame = mobile ? 32 : 16;
  const idleFrame = mobile ? 110 : 80;

  const draw = (now) => {
    if (generationNow !== generation || currentMode !== 'NETWORK') return;
    if (!canAnimateNetwork()) { rafId = null; return; }
    const liveImpulseNow = clamp(Number(liveTick?.impulse || 0), 0, 1);
    const dynamicNow = linkDynamics || liveImpulseNow > .015 || draggedNodeId || hoveredNodeId;
    const minFrame = dynamicNow ? activeFrame : idleFrame;
    if (now - lastDraw < minFrame) { rafId = requestAnimationFrame(draw); return; }
    lastDraw = now;
    ctx.clearRect(0, 0, width, height);
    drawNetworkBackground(ctx, width, height, mobile);

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

    allActive.forEach((l, li) => {
      const a = byId.get(l.source), b = byId.get(l.target); if (!a || !b) return;
      const rho = Number(l.correlation || 0), absR = Math.abs(rho);
      const tension = clamp(l.tension || 0, 0, 1.5);
      const alert = l.status === 'BREAK_ALERT';
      ctx.strokeStyle = alert ? 'rgba(255,82,105,.82)' : edgeColor(rho, .26 + absR * .54);
      ctx.lineWidth = (mobile ? .9 : 1.1) + absR * (mobile ? 2.2 : 3.0) + tension * (mobile ? .9 : 1.5);
      ctx.setLineDash(alert ? [7, 5] : []);
      ctx.shadowColor = alert ? '#ff5269' : (rho >= 0 ? '#43d79f' : '#ff5e78');
      ctx.shadowBlur = mobile ? 3 + tension * 4 : 4 + tension * 8;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      ctx.shadowBlur = 0; ctx.setLineDash([]);

      const velocity = clamp(Number(l.velocity_magnitude || 0) / .35, 0, 1);
      if (velocity > .03 || alert) {
        const speed = .035 + velocity * .22;
        const phase = (now / 1000 * speed + li * .173) % 1;
        const packets = mobile ? [phase] : [phase, (phase + .5) % 1];
        for (const t of packets) {
          const x = a.x + (b.x - a.x) * t, y = a.y + (b.y - a.y) * t;
          const r = (mobile ? 1.5 : 2) + (mobile ? 3 : 4.5) * Math.max(velocity, tension * .5);
          const grad = ctx.createRadialGradient(x, y, 0, x, y, r * 2.4);
          grad.addColorStop(0, alert ? 'rgba(255,99,115,.96)' : (rho >= 0 ? 'rgba(74,232,177,.92)' : 'rgba(255,103,124,.92)'));
          grad.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(x, y, r * 2.4, 0, Math.PI * 2); ctx.fill();
        }
      }
      if ((!mobile && absR >= .48) || alert) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        const text = `${rho >= 0 ? '+' : ''}${rho.toFixed(2)}`;
        ctx.font = `${mobile ? 7 : 9}px IBM Plex Mono,monospace`; const tw = ctx.measureText(text).width + 7;
        ctx.fillStyle = 'rgba(7,17,29,.82)'; ctx.fillRect(mx - tw / 2, my - 7, tw, 13);
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
      const liveImpulse = isLive ? liveImpulseNow : 0;
      const r = (mobile ? 12 : 15) + coupling * (mobile ? 7 : 10);

      if (stress > .03 || liveImpulse > .02) {
        const haloR = r + (mobile ? 12 : 20) + stress * (mobile ? 10 : 16) + liveImpulse * (mobile ? 10 : 18);
        const halo = ctx.createRadialGradient(n.x, n.y, r * .6, n.x, n.y, haloR);
        halo.addColorStop(0, isLive ? `rgba(255,185,77,${.10 + liveImpulse * .22})` : `rgba(238,79,101,${.06 + stress * .18})`);
        halo.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = halo; ctx.beginPath(); ctx.arc(n.x, n.y, haloR, 0, Math.PI * 2); ctx.fill();
      }

      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = noDynamics ? '#314355' : groupColor(n.group); ctx.fill();
      ctx.strokeStyle = n.break_count ? '#ff5b72' : isLive ? '#ffbf55' : 'rgba(238,245,249,.88)';
      ctx.lineWidth = n.break_count ? (mobile ? 2 : 3) : isLive ? (mobile ? 2 : 2.8) : 1.3; ctx.stroke();
      if (noDynamics && !mobile) { ctx.setLineDash([3,3]); ctx.strokeStyle = 'rgba(190,206,216,.45)'; ctx.beginPath(); ctx.arc(n.x,n.y,r+4,0,Math.PI*2);ctx.stroke();ctx.setLineDash([]); }

      ctx.beginPath(); ctx.arc(n.x, n.y, r + (mobile ? 4 : 5), -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * stress);
      ctx.strokeStyle = stress > .65 ? '#ff5b72' : stress > .35 ? '#e2aa45' : '#48c7b0'; ctx.lineWidth = mobile ? 2 : 3; ctx.stroke();
      if (liveImpulse > .02) {
        const rr = r + 8 + ((now / 14) % (mobile ? 14 : 20));
        ctx.beginPath(); ctx.arc(n.x, n.y, rr, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,191,85,${Math.max(0, .45 - (rr-r-8)/38) * liveImpulse})`; ctx.lineWidth = 1.2; ctx.stroke();
      }
      ctx.fillStyle = '#fff'; ctx.font = `bold ${mobile ? 8 : 9}px IBM Plex Mono,monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(n.id, n.x, n.y);
      if (!mobile) {
        ctx.fillStyle = 'rgba(201,216,226,.66)'; ctx.font = '8px IBM Plex Mono,monospace';
        ctx.fillText(noDynamics ? 'NO PAIR DYN' : `σ ${Number(n.stress_pressure || 0).toFixed(2)}`, n.x, n.y + r + 15);
      }
    });

    drawHud(ctx, width, height, systemic, frag, links.length, allActive.length, mobile);
    if (!mobile) drawHover(ctx, byId, links, width, height);
    rafId = requestAnimationFrame(draw);
  };

  bindNetworkPointer(cv, nodes, byId, width, height, mobile);
  if (rafId) cancelAnimationFrame(rafId);
  if (canAnimateNetwork()) rafId = requestAnimationFrame(draw);
  updateNetworkText(links, allActive);
}

function drawNetworkBackground(ctx, width, height, mobile) {
  const g = ctx.createLinearGradient(0, 0, 0, height); g.addColorStop(0, '#06101b'); g.addColorStop(1, '#0a1724'); ctx.fillStyle = g; ctx.fillRect(0,0,width,height);
  const glow = ctx.createRadialGradient(width*.5,height*.46,10,width*.5,height*.46,width*.55); glow.addColorStop(0,'rgba(37,99,132,.12)'); glow.addColorStop(1,'rgba(0,0,0,0)'); ctx.fillStyle=glow;ctx.fillRect(0,0,width,height);
  ctx.strokeStyle='rgba(183,207,222,.055)';ctx.lineWidth=1;const gx=mobile?48:60,gy=mobile?44:50;for(let x=gx;x<width;x+=gx){ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,height);ctx.stroke();}for(let y=gy;y<height;y+=gy){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(width,y);ctx.stroke();}
}

function drawHud(ctx, width, height, systemic, frag, allPairs, activePairs, mobile) {
  const s = graphData.summary || {};
  const boxW=mobile?190:245,boxH=mobile?46:55;
  ctx.fillStyle = 'rgba(4,11,18,.72)'; ctx.fillRect(8, 8, boxW, boxH);
  ctx.strokeStyle='rgba(184,207,222,.14)';ctx.strokeRect(8,8,boxW,boxH);
  ctx.fillStyle='#cbdce5';ctx.font=`${mobile?7:9}px IBM Plex Mono,monospace`;ctx.textAlign='left';
  ctx.fillText(`COUP ${systemic.toFixed(2)}  TENS ${Number(s.network_tension||0).toFixed(2)}`,14,mobile?23:26);
  ctx.fillText(`FRAG ${frag.toFixed(2)}  LINKS ${allPairs}/${activePairs}`,14,mobile?37:42);
  if(!mobile){ctx.fillStyle='#f0bd58';ctx.fillText(`STRESS NODE ${s.dominant_stress_node||'—'}`,18,57);}
}

function drawHover(ctx, byId, links, width, height) {
  if (!hoveredNodeId) return;
  const n = byId.get(hoveredNodeId); if (!n) return;
  const incident = links.filter((l) => l.source === n.id || l.target === n.id).sort((a,b)=>Math.abs(Number(b.correlation||0))-Math.abs(Number(a.correlation||0))).slice(0,4);
  const boxW=190,boxH=42+incident.length*15;let x=Math.min(width-boxW-8,n.x+22),y=Math.max(8,Math.min(height-boxH-8,n.y-boxH/2));
  ctx.fillStyle='rgba(4,12,20,.94)';ctx.fillRect(x,y,boxW,boxH);ctx.strokeStyle='rgba(214,228,236,.18)';ctx.strokeRect(x,y,boxW,boxH);ctx.fillStyle='#fff';ctx.font='bold 10px IBM Plex Mono,monospace';ctx.textAlign='left';ctx.fillText(`${n.id} · COUPLING ${Number(n.coupling||0).toFixed(2)}`,x+9,y+16);ctx.font='9px IBM Plex Mono,monospace';ctx.fillStyle='#b9ccd7';incident.forEach((l,i)=>{const other=l.source===n.id?l.target:l.source;ctx.fillText(`${other.padEnd(7)} ρ ${Number(l.correlation||0)>=0?'+':''}${Number(l.correlation||0).toFixed(2)} · Δ ${Number(l.delta_baseline||0).toFixed(2)}`,x+9,y+34+i*15);});
}

function bindNetworkPointer(cv, nodes, byId, width, height, mobile) {
  const pointer=(e)=>{const r=cv.getBoundingClientRect();return{x:(e.clientX-r.left)*width/r.width,y:(e.clientY-r.top)*height/r.height};};
  cv.onpointerdown=(e)=>{const p=pointer(e),hit=nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<=(mobile?25:30));if(!hit)return;draggedNodeId=hit.id;cv.setPointerCapture?.(e.pointerId);cv.style.cursor='grabbing';ensureNetworkAnimation();};
  cv.onpointermove=(e)=>{const p=pointer(e);if(draggedNodeId){const n=byId.get(draggedNodeId);if(n){n.x=Math.max(24,Math.min(width-24,p.x));n.y=Math.max(24,Math.min(height-24,p.y));positions.set(n.id,{x:n.x,y:n.y});}}else if(!mobile){const hit=nodes.find(n=>Math.hypot(n.x-p.x,n.y-p.y)<=30);hoveredNodeId=hit?.id||null;cv.style.cursor=hit?'pointer':'grab';}ensureNetworkAnimation();};
  const release=(e)=>{if(draggedNodeId){draggedNodeId=null;cv.releasePointerCapture?.(e.pointerId);cv.style.cursor='grab';ensureNetworkAnimation();}};cv.onpointerup=release;cv.onpointercancel=release;cv.onpointerleave=()=>{if(!draggedNodeId)hoveredNodeId=null;};
}

function updateNetworkText(links, activeLinks) {
  const s=graphData.summary||{};if(statusEl)statusEl.textContent=s.active_breaks_count?`⚠ ${s.active_breaks_count} BREAK · TENSION ${Number(s.network_tension||0).toFixed(2)} · ${links.length} LINKS`:`● ${links.length} LINKS · COUPLING ${Number(s.systemic_coupling||0).toFixed(2)}${s.velocity_ready?'':' · ΔV BUILDING'}`;
  const interpret=$('#corr-interpretation');if(interpret){const top=(graphData.break_alerts||[])[0];interpret.innerHTML=top?`<b>NETWORK TENSION:</b> ${top.source}↔${top.target} · ρ ${Number(top.correlation).toFixed(2)} · Δbaseline ${top.delta_baseline==null?'—':Number(top.delta_baseline).toFixed(2)} · Δ15m ${top.delta_15m==null?'—':Number(top.delta_15m).toFixed(2)}. <b>Все ${links.length} наблюдаемых связи</b> показаны фоновым слоем; активный слой выделяет ${activeLinks.length} сильных/быстро меняющихся.`:`<b>FULL TOPOLOGY:</b> все ${links.length} наблюдаемых пары показаны; ${activeLinks.length} находятся в активном слое.`;interpret.style.display='block';}
}

function ensureNetworkAnimation(){
  if(canAnimateNetwork()&&!rafId)renderForceGraph(false);
}

function renderMatrixChart(force=false) {
  const holder=$('#corr-chart'); if(!holder||!payload||!window.echarts)return;
  if(emptyEl)emptyEl.style.display='none';
  holder.querySelectorAll('canvas[data-corr-renderer="network"]').forEach(n=>n.remove());
  const mobile=isAnalyticsMobile();
  if (!chart) chart=window.echarts.init(holder,null,{renderer:'canvas',devicePixelRatio:analyticsMobileDpr()});
  const matrix=payload.matrix_short||payload.matrix,assets=payload.assets||payload.pairs||[],delta=payload.matrix_delta||[],points=[];
  for(let i=0;i<matrix.length;i++)for(let j=0;j<matrix[i].length;j++){const v=Number(matrix[i][j]);if(Number.isFinite(v))points.push([j,i,v,Number(delta?.[i]?.[j])]);}
  chart.setOption({animationDuration:mobile?120:280,backgroundColor:'#08131f',tooltip:{show:!mobile,backgroundColor:'rgba(4,12,20,.96)',borderColor:'#2b485e',textStyle:{color:'#d6e4eb',fontFamily:'IBM Plex Mono',fontSize:10},formatter:p=>{const[j,i,rho,d]=p.data;return`<b>${assets[i]} ↔ ${assets[j]}</b><br>rolling ρ: ${rho.toFixed(2)}<br>Δ vs baseline: ${Number.isFinite(d)?d.toFixed(2):'—'}`;}},grid:mobile?{left:45,right:8,top:20,bottom:44}:{left:70,right:30,top:28,bottom:48},xAxis:{type:'category',data:assets,axisLabel:{rotate:mobile?-45:-25,fontSize:mobile?7:9,color:'#a9bdc9',interval:0},axisLine:{lineStyle:{color:'#345064'}},splitLine:{show:true,lineStyle:{color:'rgba(164,190,207,.07)'}}},yAxis:{type:'category',data:assets,inverse:true,axisLabel:{fontSize:mobile?7:9,color:'#a9bdc9'},axisLine:{lineStyle:{color:'#345064'}},splitLine:{show:true,lineStyle:{color:'rgba(164,190,207,.07)'}}},visualMap:{min:-1,max:1,show:false,inRange:{color:['#d84861','#172839','#36c898']}},series:[{type:'heatmap',data:points,label:{show:true,formatter:p=>Number(p.data[2]).toFixed(mobile?1:2),fontSize:mobile?7:9,color:'#dce7ed'},itemStyle:{borderColor:'rgba(210,225,234,.12)',borderWidth:1}}]}, {notMerge:true,lazyUpdate:true});
  if(statusEl)statusEl.textContent=`● MATRIX · ${assets.length} ASSETS`;
}
