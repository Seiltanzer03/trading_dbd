import { $ } from './util.js';

let chart = null;
let emptyEl = null;
let statusEl = null;
let payload = null;
let graphData = null;
let currentMode = 'NETWORK';
let resizeObserver = null;
let refreshTimer = null;
const positions = new Map();
let draggedNodeId = null;

export function initCorrelation() {
  emptyEl = $('#corr-empty');
  statusEl = $('#corr-status');
  $('#btn-corr-network')?.addEventListener('click', () => setMode('NETWORK'));
  $('#btn-corr-matrix')?.addEventListener('click', () => setMode('MATRIX'));

  const holder = $('#corr-chart');
  if (holder && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderCorrelation());
    resizeObserver.observe(holder);
  }
  fetchGraphData();
  refreshTimer = setInterval(fetchGraphData, 300000);
}

function setMode(mode) {
  currentMode = mode;
  $('#btn-corr-network')?.classList.toggle('active', mode === 'NETWORK');
  $('#btn-corr-matrix')?.classList.toggle('active', mode === 'MATRIX');
  renderCorrelation();
}

export async function fetchGraphData() {
  try {
    const res = await fetch('/api/analytics/correlation-graph', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    graphData = await res.json();
    renderCorrelation();
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
  if (currentMode === 'MATRIX') renderMatrixChart();
}

function renderCorrelation() {
  if (currentMode === 'NETWORK') renderForceGraph();
  else renderMatrixChart();
}

function groupColor(group) {
  return {
    equity: '#2f5d86', volatility: '#8d4f62', metals: '#9a762e',
    energy: '#7b623f', fx: '#4f7b68', other: '#59616a',
  }[group] || '#59616a';
}

function lineColor(rho, alpha) {
  return rho >= 0
    ? `rgba(43,122,82,${alpha})`
    : `rgba(187,69,73,${alpha})`;
}

function settleLayout(nodes, links, width, height) {
  const pad = 52;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  nodes.forEach((n) => {
    const existing = positions.get(n.id);
    if (existing) {
      n.x = existing.x; n.y = existing.y;
    } else {
      n.x = pad + Number(n.x_norm ?? 0.5) * (width - pad * 2);
      n.y = pad + Number(n.y_norm ?? 0.5) * (height - pad * 2);
    }
  });

  // Short deterministic settle. It runs only on fresh data/resize, not as an
  // endless animation, so the graph remains a stable instrument panel.
  for (let iter = 0; iter < 70; iter++) {
    const force = new Map(nodes.map((n) => [n.id, { x: 0, y: 0 }]));
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.max(20, Math.hypot(dx, dy));
        const rep = 900 / (dist * dist);
        dx /= dist; dy /= dist;
        force.get(a.id).x -= dx * rep; force.get(a.id).y -= dy * rep;
        force.get(b.id).x += dx * rep; force.get(b.id).y += dy * rep;
      }
    }
    links.forEach((l) => {
      const a = byId.get(l.source), b = byId.get(l.target);
      if (!a || !b) return;
      let dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.max(1, Math.hypot(dx, dy));
      dx /= dist; dy /= dist;
      const target = 190 - Math.abs(Number(l.correlation || 0)) * 105;
      const spring = (dist - target) * 0.012 * (0.4 + Math.abs(Number(l.correlation || 0)));
      force.get(a.id).x += dx * spring; force.get(a.id).y += dy * spring;
      force.get(b.id).x -= dx * spring; force.get(b.id).y -= dy * spring;
    });
    nodes.forEach((n) => {
      if (n.id === draggedNodeId) return;
      const f = force.get(n.id);
      n.x = Math.max(pad, Math.min(width - pad, n.x + f.x));
      n.y = Math.max(pad, Math.min(height - pad, n.y + f.y));
    });
  }
  nodes.forEach((n) => positions.set(n.id, { x: n.x, y: n.y }));
}

function renderForceGraph() {
  const holder = $('#corr-chart');
  if (!holder) return;
  if (chart) { chart.dispose(); chart = null; }

  if (!graphData?.available || !graphData.nodes?.length) {
    if (emptyEl) {
      emptyEl.style.display = 'flex';
      emptyEl.textContent = `○ ${graphData?.reason || 'НЕТ РЕАЛЬНОЙ CROSS-ASSET МАТРИЦЫ'}`;
    }
    if (statusEl) statusEl.textContent = '○ NO REAL NETWORK DATA';
    holder.querySelector('canvas')?.remove();
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  let cv = holder.querySelector('canvas');
  if (!cv) {
    cv = document.createElement('canvas');
    cv.style.cssText = 'width:100%;height:100%;display:block;cursor:grab;touch-action:none';
    holder.appendChild(cv);
  }
  const rect = holder.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(520, Math.floor(rect.width || 900));
  const height = Math.max(330, Math.floor(rect.height || 380));
  cv.width = width * dpr; cv.height = height * dpr;
  cv.style.width = `${width}px`; cv.style.height = `${height}px`;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const nodes = graphData.nodes.map((n) => ({ ...n }));
  const links = graphData.links || [];
  settleLayout(nodes, links, width, height);
  const byId = new Map(nodes.map((n) => [n.id, n]));

  function draw() {
    ctx.clearRect(0, 0, width, height);

    // Links with rho labels. Weak links remain visible but quiet; regime breaks
    // are dashed and annotated instead of flashing the whole panel.
    links.forEach((l) => {
      const a = byId.get(l.source), b = byId.get(l.target);
      if (!a || !b) return;
      const rho = Number(l.correlation || 0);
      const alert = l.status === 'BREAK_ALERT';
      const alpha = alert ? 0.9 : 0.18 + Math.abs(rho) * 0.55;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = alert ? '#c6373c' : lineColor(rho, alpha);
      ctx.lineWidth = alert ? 3 : 0.8 + Math.abs(rho) * 3;
      ctx.setLineDash(alert ? [7, 4] : []); ctx.stroke(); ctx.setLineDash([]);

      if (Math.abs(rho) >= 0.45 || alert) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        ctx.font = '9px IBM Plex Mono, monospace';
        const txt = `${rho >= 0 ? '+' : ''}${rho.toFixed(2)}`;
        const tw = ctx.measureText(txt).width + 8;
        ctx.fillStyle = 'rgba(255,255,255,.88)'; ctx.fillRect(mx - tw / 2, my - 8, tw, 15);
        ctx.fillStyle = alert ? '#b52c31' : '#5d5a53'; ctx.textAlign = 'center';
        ctx.fillText(txt, mx, my + 3);
      }
    });

    nodes.forEach((n) => {
      const r = 19;
      ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
      ctx.fillStyle = groupColor(n.group); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.font = 'bold 9px IBM Plex Mono, monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(n.id, n.x, n.y);
    });
  }

  draw();

  function pointerPos(e) {
    const r = cv.getBoundingClientRect();
    return { x: (e.clientX - r.left) * width / r.width, y: (e.clientY - r.top) * height / r.height };
  }
  cv.onpointerdown = (e) => {
    const p = pointerPos(e);
    const hit = nodes.find((n) => Math.hypot(n.x - p.x, n.y - p.y) <= 24);
    if (!hit) return;
    draggedNodeId = hit.id; cv.setPointerCapture?.(e.pointerId); cv.style.cursor = 'grabbing';
  };
  cv.onpointermove = (e) => {
    if (!draggedNodeId) return;
    const p = pointerPos(e), n = byId.get(draggedNodeId);
    if (!n) return;
    n.x = Math.max(30, Math.min(width - 30, p.x));
    n.y = Math.max(30, Math.min(height - 30, p.y));
    positions.set(n.id, { x: n.x, y: n.y }); draw();
  };
  const release = (e) => {
    if (!draggedNodeId) return;
    draggedNodeId = null; cv.releasePointerCapture?.(e.pointerId); cv.style.cursor = 'grab';
  };
  cv.onpointerup = release; cv.onpointercancel = release;

  const summary = graphData.summary || {};
  if (statusEl) {
    statusEl.textContent = summary.active_breaks_count
      ? `⚠ ${summary.active_breaks_count} REGIME BREAK${summary.velocity_ready ? '' : ' · ΔV BUILDING'}`
      : `● ${summary.observed_pairs || links.length} REAL PAIRS${summary.velocity_ready ? '' : ' · ΔV BUILDING'}`;
  }
  const interpret = $('#corr-interpretation');
  if (interpret) {
    const top = (graphData.break_alerts || [])[0];
    if (top) {
      const vel = top.delta_15m == null ? '—' : `${top.delta_15m >= 0 ? '+' : ''}${top.delta_15m.toFixed(2)}`;
      interpret.innerHTML = `<b>РЕЖИМНЫЙ СДВИГ:</b> ${top.source}↔${top.target} · ρ ${top.correlation >= 0 ? '+' : ''}${top.correlation.toFixed(2)} · baseline ${top.baseline == null ? '—' : top.baseline.toFixed(2)} · Δρ ${top.delta_baseline == null ? '—' : top.delta_baseline.toFixed(2)} · Δ15m ${vel}`;
    } else {
      const src = summary.source?.source || 'rolling 5m correlation';
      interpret.innerHTML = `<b>NETWORK:</b> ${summary.observed_pairs || links.length} реальных пар. Толщина = |ρ|, знак связи = цвет, пунктир = режимный сдвиг. Источник: ${src}.`;
    }
    interpret.style.display = 'block';
  }
}

function renderMatrixChart() {
  const holder = $('#corr-chart');
  if (!holder || !payload || !window.echarts) return;
  if (emptyEl) emptyEl.style.display = 'none';
  holder.querySelector('canvas')?.remove();
  if (chart) chart.dispose();
  chart = window.echarts.init(holder);

  const matrix = payload.matrix_short || payload.matrix;
  const assets = payload.assets || payload.pairs || [];
  const delta = payload.matrix_delta || [];
  const points = [];
  for (let i = 0; i < matrix.length; i++) {
    for (let j = 0; j < matrix[i].length; j++) {
      const v = Number(matrix[i][j]);
      if (Number.isFinite(v)) points.push([j, i, v, Number(delta?.[i]?.[j])]);
    }
  }
  chart.setOption({
    animation: false,
    tooltip: { formatter: (p) => {
      const [j, i, rho, d] = p.data;
      return `<b>${assets[i]} ↔ ${assets[j]}</b><br>rolling ρ: ${rho.toFixed(2)}<br>Δ vs baseline: ${Number.isFinite(d) ? d.toFixed(2) : '—'}`;
    }},
    grid: { left: 70, right: 30, top: 20, bottom: 45 },
    xAxis: { type: 'category', data: assets, axisLabel: { rotate: -25, fontSize: 10 } },
    yAxis: { type: 'category', data: assets, inverse: true, axisLabel: { fontSize: 10 } },
    visualMap: { min: -1, max: 1, show: false, inRange: { color: ['#bd4549', '#f4f2ec', '#2b7a52'] } },
    series: [{ type: 'heatmap', data: points, label: { show: true, formatter: (p) => Number(p.data[2]).toFixed(2), fontSize: 9 }, itemStyle: { borderColor: '#fff', borderWidth: 1 } }],
  });
}
