// IV Surface (3D): ECharts-GL (Hedge-Fund Grade Visualization)
// Cinematic rendering with realistic lighting, shadows, and smooth 60FPS WebGL.

import { approach } from './anim.js';

const ORANGE = '#E8622A';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';

function interp(xs, ys, x) {
  if (!xs || !xs.length) return 0;
  if (x <= xs[0]) return ys[0];
  if (x >= xs[xs.length - 1]) return ys[ys.length - 1];
  let hi = 1;
  while (hi < xs.length && xs[hi] < x) hi++;
  if (hi >= xs.length) return ys[ys.length - 1];
  const lo = hi - 1;
  const span = xs[hi] - xs[lo];
  if (!span) return ys[lo];
  const f = (x - xs[lo]) / span;
  return ys[lo] + (ys[hi] - ys[lo]) * f;
}

export function initIVSurface(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let chart = null;
  let model = null;
  let targetLiveX = 0, displayLiveX = 0;
  let lastFrame = performance.now();
  const emptyEl = document.getElementById('iv-surface-empty');
  
  // Track trade state for Volatility Delta
  let initialTradeIV = null;

  if (typeof ResizeObserver !== 'undefined' && el) {
    new ResizeObserver(() => {
      if (chart) chart.resize();
    }).observe(el);
  }

  function ready() {
    return !!window.echarts;
  }

  function normalizePayload(surfacePayload) {
    return Array.isArray(surfacePayload)
      ? { value: surfacePayload, status: 'delayed' }
      : (surfacePayload || {});
  }

  function payloadSignature(payload) {
    const rows = payload.value || [];
    const compact = rows.map((r) => {
      const strikes = r.strikes || [], ivs = r.ivs || [];
      return [
        Number(r.days || 0).toFixed(4),
        strikes.length,
        Number(strikes[0] || 0).toFixed(4),
        Number(strikes[strikes.length - 1] || 0).toFixed(4),
        Number(ivs[0] || 0).toFixed(5),
        Number(ivs[ivs.length - 1] || 0).toFixed(5),
      ].join(':');
    }).join('|');
    return `${payload.ts || ''}|${compact}`;
  }

  function buildModel(payload) {
    const surfaceData = payload.value || [];
    const firstStrikes = surfaceData[0]?.strikes || [];
    const snapshotSpot = Number(surfaceData[0]?.spot_at_snapshot)
      || Number(firstStrikes[Math.floor(firstStrikes.length / 2)]);
    if (!(snapshotSpot > 0) || !firstStrikes.length) return null;

    const rows = surfaceData.map((row) => {
      const rowSpot = Number(row.spot_at_snapshot) || snapshotSpot;
      const pairs = (row.strikes || []).map((strike, i) => ({
        x: (Number(strike) / rowSpot - 1) * 100,
        iv: Number(row.ivs?.[i]) * 100,
      })).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.iv)
        && p.iv > 0 && p.iv < 200).sort((a, b) => a.x - b.x);
      return { row, pairs };
    }).filter((r) => r.pairs.length >= 3);
    if (!rows.length) return null;

    let xLo = Math.max(-20, ...rows.map((r) => r.pairs[0].x));
    let xHi = Math.min(20, ...rows.map((r) => r.pairs[r.pairs.length - 1].x));
    if (!(xHi > xLo + 1)) return null;
    
    const moneyPct = Array.from({ length: 41 }, (_, i) => +(xLo + (xHi - xLo) * i / 40).toFixed(3));
    const zIvs = rows.map(({ pairs }) => {
      const xs = pairs.map((p) => p.x), ys = pairs.map((p) => p.iv);
      return moneyPct.map((x) => interp(xs, ys, x));
    });
    const yDte = rows.map((r) => Number(r.row.days));
    const yTickText = rows.map((r) => {
      const d = Number(r.row.days);
      if (d < 1) return `${(d * 24).toFixed(1)}h`;
      if (d < 7) return `${Math.round(d)}d`;
      if (d < 28) return `${Math.round(d / 7)}W`;
      return `${Math.round(d / 30)}M`;
    });
    
    const allZ = zIvs.flat().filter(Number.isFinite);
    if (!allZ.length) return null;
    const zMin = Math.min(...allZ);
    const rawMax = Math.max(...allZ);
    const zMax = rawMax > zMin ? rawMax : zMin + 0.01;
    
    // Build ECharts surface data array [[x, y, z], ...]
    const surfData = [];
    const rvData = [];
    const rvBaseZ = zMin + Math.max((zMax - zMin) * 0.15, 2.0);
    
    for (let r = 0; r < yDte.length; r++) {
      for (let c = 0; c < moneyPct.length; c++) {
        const val = zIvs[r][c];
        surfData.push([moneyPct[c], yDte[r], val]);
        rvData.push([moneyPct[c], yDte[r], rvBaseZ + (val - zMin) * 0.2]);
      }
    }

    return {
      payload, snapshotSpot, moneyPct, zIvs, yDte, yTickText, zMin, zMax,
      xLo, xHi, surfData, rvData
    };
  }

  function getOptionForModel(m, liveX, liveZ) {
    // Ridge line (Live Slice)
    const ridgeData = [];
    for (let r = 0; r < m.yDte.length; r++) {
      ridgeData.push([liveX, m.yDte[r], liveZ[r]]);
    }
    
    const dotZ = liveZ[0] + (m.zMax - m.zMin) * 0.05;
    
    return {
      tooltip: { show: true, formatter: (p) => {
          if (p.seriesName === 'IV Surface') return `Moneyness: ${p.data[0].toFixed(2)}%<br>DTE: ${p.data[1].toFixed(2)}<br>IV: ${p.data[2].toFixed(2)}%`;
          return '';
      }},
      visualMap: {
        show: false, dimension: 2, min: m.zMin, max: m.zMax,
        inRange: { color: ['#1A1F3A', '#1B6CA8', '#2ECC71', '#F4CE14', '#E8622A', '#C6373C'] }
      },
      xAxis3D: {
        type: 'value', name: 'MONEYNESS %', 
        nameTextStyle: { color: '#888' }, axisLabel: { textStyle: { color: '#666' } },
        splitLine: { lineStyle: { color: 'rgba(180,180,180,0.3)' } }
      },
      yAxis3D: {
        type: 'value', name: 'DTE',
        nameTextStyle: { color: '#888' }, axisLabel: { textStyle: { color: '#666' } },
        splitLine: { lineStyle: { color: 'rgba(180,180,180,0.3)' } }
      },
      zAxis3D: {
        type: 'value', name: 'IV %',
        nameTextStyle: { color: '#888' }, axisLabel: { textStyle: { color: '#666' } },
        splitLine: { lineStyle: { color: 'rgba(180,180,180,0.3)' } },
        min: m.zMin, max: m.zMax + (m.zMax - m.zMin)*0.1
      },
      grid3D: {
        viewControl: {
          projection: 'perspective', autoRotate: false,
          distance: 250, alpha: 25, beta: -35
        },
        boxWidth: 100, boxHeight: 60, boxDepth: 80,
        light: {
          main: { intensity: 1.2, shadow: true },
          ambient: { intensity: 0.5 }
        },
        environment: 'rgba(0,0,0,0)' // transparent
      },
      series: [
        {
          name: 'IV Surface', type: 'surface',
          wireframe: { show: true, lineStyle: { color: 'rgba(255,255,255,0.15)', width: 1 } },
          shading: 'realistic', itemStyle: { opacity: 0.95 },
          realisticMaterial: { roughness: 0.4, metalness: 0.1 },
          data: m.surfData
        },
        {
          name: 'CFD RV', type: 'surface',
          wireframe: { show: true, lineStyle: { color: 'rgba(46, 204, 113, 0.5)', width: 1 } },
          shading: 'color', itemStyle: { color: 'rgba(46, 204, 113, 0.1)' },
          data: m.rvData
        },
        {
          name: 'Live Ridge', type: 'line3D',
          lineStyle: { width: 6, color: ORANGE },
          data: ridgeData
        },
        {
          name: 'Live Dot', type: 'scatter3D',
          symbol: 'circle', symbolSize: 15,
          itemStyle: { color: '#FFF', borderColor: ORANGE, borderWidth: 3 },
          data: [[liveX, m.yDte[0], dotZ]]
        },
        {
          name: 'Halo', type: 'scatter3D',
          symbol: 'circle', symbolSize: 40,
          itemStyle: { color: 'rgba(232, 98, 42, 0.3)' },
          data: [[liveX, m.yDte[0], dotZ - 0.01]]
        }
      ]
    };
  }

  function updateStatus(payload, x, z) {
    const status = document.getElementById('iv-surface-status');
    const skewEl = document.getElementById('iv-skew-momentum');
    if (!model || !z.length) return;
    
    // In-Trade Volatility Delta
    const near = model.zIvs[0];
    const atm = interp(model.moneyPct, near, x);
    if (atm != null && initialTradeIV === null) {
      initialTradeIV = atm; // First seen IV becomes baseline for trade
    }
    
    const deltaIV = atm != null && initialTradeIV != null ? atm - initialTradeIV : 0;
    const volBadge = deltaIV < -2.0 ? '🔥 SQUEEZE' : (deltaIV > 2.0 ? '🌊 EXPANSION' : 'STABLE');

    if (status) {
      status.innerText = `● ECHARTS-GL ENGINE · LIVE SLICE · VOL DELTA: ${deltaIV > 0 ? '+' : ''}${deltaIV.toFixed(2)}% [${volBadge}]`;
      status.className = `badge live`;
      if (Math.abs(deltaIV) > 2.0) status.style.color = '#FFF';
      if (deltaIV < -2.0) status.style.backgroundColor = '#C6373C'; // Squeeze danger
      if (deltaIV > 2.0) status.style.backgroundColor = '#2E7D4F'; // Expansion
    }
    if (skewEl) {
      skewEl.style.display = 'none'; // Replaced by vol badge
    }
  }

  function applyLiveGeometry() {
    if (!ready() || !model) return;
    const x = Math.max(model.xLo, Math.min(model.xHi, displayLiveX));
    const z = model.zIvs.map((row) => {
      const v = interp(model.moneyPct, row, x);
      return v == null ? model.zMin : v;
    });
    
    if (!chart) {
      chart = window.echarts.init(el);
    }
    
    const option = getOptionForModel(model, x, z);
    chart.setOption(option, { replaceMerge: ['series'] });
    
    updateStatus(model.payload, x, z);
  }

  function setLive(payload) {
    if (!model) return;
    model.payload = payload;
    const spot = Number(payload.spot_current) > 0 ? Number(payload.spot_current) : model.snapshotSpot;
    targetLiveX = (spot / model.snapshotSpot - 1) * 100;
  }

  function resetTradeState() {
    initialTradeIV = null; // Reset when trade is closed
  }

  function renderLoop() {
    if (!ready()) { requestAnimationFrame(renderLoop); return; }
    const now = performance.now();
    const dt = Math.min(now - lastFrame, 100) / 1000;
    lastFrame = now;
    
    if (model && Math.abs(displayLiveX - targetLiveX) > 0.001) {
      displayLiveX = approach(displayLiveX, targetLiveX, 0.15, dt);
      applyLiveGeometry();
    }
    requestAnimationFrame(renderLoop);
  }

  const ro = new ResizeObserver(() => {
    if (chart) {
      chart.resize();
    }
  });
  ro.observe(el);

  window.addEventListener('resize', () => { if (chart) chart.resize(); });
  requestAnimationFrame(renderLoop);

  let lastPayloadSig = null;

  return {
    render: (state, payload) => {
      const p = normalizePayload(payload);
      if (!p.value || p.value.length === 0) {
        if (emptyEl) emptyEl.style.display = 'flex';
        return;
      }
      
      if (emptyEl) emptyEl.style.display = 'none';
      
      const sig = payloadSignature(p);
      if (sig !== lastPayloadSig) {
        lastPayloadSig = sig;
        model = buildModel(p);
      }
      
      if (model) {
        setLive(p);
        applyLiveGeometry();
      }
    },
    updateLive: setLive,
    setLive,
    resetTradeState
  };
}
