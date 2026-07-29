// IV Surface (3D) — Apache ECharts-GL Engine (Hedge-Fund Grade Visualization)
// Intraday Micro-Surface (0-24h / 1 Day DTE Horizon)

import { approach } from './anim.js';

const ORANGE = '#E8622A';

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
  const statusEl = document.getElementById('iv-surface-status');
  
  let initialTradeIV = null;

  function ready() {
    return !!(window.echarts);
  }

  function normalizePayload(surfacePayload) {
    if (!surfacePayload) return {};
    return Array.isArray(surfacePayload)
      ? { value: surfacePayload, status: 'delayed' }
      : surfacePayload;
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
    let surfaceData = payload.value || [];
    if (!surfaceData.length) return null;

    // FILTER FOR 1-DAY HORIZON: Keep intraday/near-term DTEs (DTE <= 1.5 days)
    // If all DTEs > 1.5, keep the 2 closest near-term expirations for 1-day trade context
    let intradayData = surfaceData.filter((r) => Number(r.days) <= 1.5);
    if (intradayData.length < 2) {
      intradayData = surfaceData.slice(0, Math.min(3, surfaceData.length));
    }

    const firstStrikes = intradayData[0]?.strikes || [];
    const snapshotSpot = Number(intradayData[0]?.spot_at_snapshot)
      || Number(firstStrikes[Math.floor(firstStrikes.length / 2)]);
    if (!(snapshotSpot > 0) || !firstStrikes.length) return null;

    const rows = intradayData.map((row) => {
      const rowSpot = Number(row.spot_at_snapshot) || snapshotSpot;
      const pairs = (row.strikes || []).map((strike, i) => ({
        x: (Number(strike) / rowSpot - 1) * 100,
        iv: Number(row.ivs?.[i]) * 100,
      })).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.iv)
        && p.iv > 0 && p.iv < 300).sort((a, b) => a.x - b.x);
      return { row, pairs };
    }).filter((r) => r.pairs.length >= 3);

    if (!rows.length) return null;

    let xLo = Math.max(-15, ...rows.map((r) => r.pairs[0].x));
    let xHi = Math.min(15, ...rows.map((r) => r.pairs[r.pairs.length - 1].x));
    if (!(xHi > xLo + 1)) {
      xLo = -10; xHi = 10;
    }

    const moneyPct = Array.from({ length: 31 }, (_, i) => +(xLo + (xHi - xLo) * i / 30).toFixed(3));
    const zIvs = rows.map(({ pairs }) => {
      const xs = pairs.map((p) => p.x), ys = pairs.map((p) => p.iv);
      return moneyPct.map((x) => +interp(xs, ys, x).toFixed(2));
    });

    const yDte = rows.map((r) => Number(r.row.days));
    const yTickText = rows.map((r) => {
      const d = Number(r.row.days);
      return d < 1 ? `${(d * 24).toFixed(1)}h` : `${d.toFixed(1)}d`;
    });

    const allZ = zIvs.flat().filter(Number.isFinite);
    if (!allZ.length) return null;
    const zMin = Math.min(...allZ);
    const rawMax = Math.max(...allZ);
    const zMax = rawMax > zMin ? rawMax : zMin + 0.01;

    // Complete 2D Grid Array for ECharts-GL (r = category index)
    const surfData = [];
    const rvData = [];
    const rvBaseZ = zMin + Math.max((zMax - zMin) * 0.15, 1.0);

    for (let r = 0; r < yDte.length; r++) {
      for (let c = 0; c < moneyPct.length; c++) {
        const val = zIvs[r][c];
        surfData.push([moneyPct[c], r, val]);
        rvData.push([moneyPct[c], r, rvBaseZ + (val - zMin) * 0.15]);
      }
    }

    return {
      payload, snapshotSpot, moneyPct, zIvs, yDte, yTickText, zMin, zMax,
      xLo, xHi, surfData, rvData
    };
  }

  function getOptionForModel(m, liveX, liveZ) {
    const ridgeData = [];
    for (let r = 0; r < m.yDte.length; r++) {
      ridgeData.push([liveX, r, liveZ[r]]);
    }

    const dotZ = liveZ[0] + (m.zMax - m.zMin) * 0.05;

    return {
      backgroundColor: 'transparent',
      tooltip: {
        show: true,
        formatter: (p) => {
          if (p.seriesName === 'IV Surface') {
            const dteStr = m.yTickText[p.data[1]] || `${p.data[1]}`;
            return `<b>Moneyness:</b> ${p.data[0].toFixed(2)}%<br><b>DTE:</b> ${dteStr}<br><b>IV:</b> ${p.data[2].toFixed(2)}%`;
          }
          return '';
        }
      },
      visualMap: {
        show: false,
        dimension: 2,
        min: m.zMin,
        max: m.zMax,
        inRange: { color: ['#1A1F3A', '#1B6CA8', '#2ECC71', '#F4CE14', '#E8622A', '#C6373C'] }
      },
      xAxis3D: {
        type: 'value',
        name: 'MONEYNESS %',
        nameTextStyle: { color: '#888', fontSize: 10 },
        axisLabel: { textStyle: { color: '#666', fontSize: 9 } },
        splitLine: { lineStyle: { color: 'rgba(200,200,200,0.3)' } }
      },
      yAxis3D: {
        type: 'category',
        data: m.yTickText,
        name: 'DTE (1-DAY)',
        nameTextStyle: { color: '#888', fontSize: 10 },
        axisLabel: { textStyle: { color: '#666', fontSize: 9 } },
        splitLine: { lineStyle: { color: 'rgba(200,200,200,0.3)' } }
      },
      zAxis3D: {
        type: 'value',
        name: 'IV %',
        nameTextStyle: { color: '#888', fontSize: 10 },
        axisLabel: { textStyle: { color: '#666', fontSize: 9 } },
        splitLine: { lineStyle: { color: 'rgba(200,200,200,0.3)' } },
        min: m.zMin,
        max: m.zMax + (m.zMax - m.zMin) * 0.1
      },
      grid3D: {
        viewControl: {
          projection: 'perspective',
          autoRotate: false,
          distance: 220,
          alpha: 25,
          beta: -35
        },
        boxWidth: 100,
        boxHeight: 60,
        boxDepth: 80,
        light: {
          main: { intensity: 1.2, shadow: false },
          ambient: { intensity: 0.6 }
        },
        environment: 'rgba(0,0,0,0)'
      },
      series: [
        {
          name: 'IV Surface',
          type: 'surface',
          wireframe: { show: true, lineStyle: { color: 'rgba(255,255,255,0.2)', width: 1 } },
          shading: 'lambert',
          itemStyle: { opacity: 0.95 },
          data: m.surfData
        },
        {
          name: 'CFD RV',
          type: 'surface',
          wireframe: { show: true, lineStyle: { color: 'rgba(46, 204, 113, 0.4)', width: 1 } },
          shading: 'color',
          itemStyle: { color: 'rgba(46, 204, 113, 0.15)' },
          data: m.rvData
        },
        {
          name: 'Live Ridge',
          type: 'line3D',
          lineStyle: { width: 5, color: ORANGE },
          data: ridgeData
        },
        {
          name: 'Live Dot',
          type: 'scatter3D',
          symbol: 'circle',
          symbolSize: 12,
          itemStyle: { color: '#FFF', borderColor: ORANGE, borderWidth: 2 },
          data: [[liveX, 0, dotZ]]
        }
      ]
    };
  }

  function updateStatus(payload, x, z) {
    if (!model || !z.length) return;

    const near = model.zIvs[0];
    const atm = interp(model.moneyPct, near, x);
    if (atm != null && initialTradeIV === null) {
      initialTradeIV = atm;
    }

    const deltaIV = atm != null && initialTradeIV != null ? atm - initialTradeIV : 0;
    const volBadge = deltaIV < -2.0 ? 'SQUEEZE' : (deltaIV > 2.0 ? 'EXPANSION' : 'STABLE');

    if (statusEl) {
      statusEl.textContent = `● ECHARTS-GL 3D · 1-DAY DTE · VOL DELTA: ${deltaIV >= 0 ? '+' : ''}${deltaIV.toFixed(2)}% [${volBadge}]`;
      statusEl.className = 'badge live';
    }
  }

  function applyLiveGeometry() {
    if (!ready() || !model || !el) return;
    const x = Math.max(model.xLo, Math.min(model.xHi, displayLiveX));
    const z = model.zIvs.map((row) => interp(model.moneyPct, row, x));

    if (!chart && window.echarts) {
      chart = window.echarts.init(el);
    }
    if (!chart) return;

    const option = getOptionForModel(model, x, z);
    chart.setOption(option, true);
    
    // GUARANTEE CANVAS RESIZE RIGHT AFTER SETOPTION
    setTimeout(() => {
      if (chart) chart.resize();
    }, 50);

    updateStatus(model.payload, x, z);
  }

  function setLive(payload) {
    if (!model) return;
    model.payload = payload;
    const spot = Number(payload.spot_current) > 0 ? Number(payload.spot_current) : model.snapshotSpot;
    targetLiveX = (spot / model.snapshotSpot - 1) * 100;
  }

  function resetTradeState() {
    initialTradeIV = null;
  }

  function renderLoop() {
    if (!ready()) {
      requestAnimationFrame(renderLoop);
      return;
    }
    const now = performance.now();
    const dt = Math.min(now - lastFrame, 100) / 1000;
    lastFrame = now;

    if (model && Math.abs(displayLiveX - targetLiveX) > 0.001) {
      displayLiveX = approach(displayLiveX, targetLiveX, 0.15, dt);
      applyLiveGeometry();
    }
    requestAnimationFrame(renderLoop);
  }

  if (typeof ResizeObserver !== 'undefined' && el) {
    new ResizeObserver(() => {
      if (chart) chart.resize();
    }).observe(el);
  }
  window.addEventListener('resize', () => { if (chart) chart.resize(); });

  requestAnimationFrame(renderLoop);

  let lastPayloadSig = null;

  return {
    render: (state, payload) => {
      const p = normalizePayload(payload);
      if (!p.value || p.value.length === 0) {
        if (emptyEl) emptyEl.style.display = 'flex';
        if (statusEl) {
          statusEl.textContent = '○ НЕТ ДАННЫХ ОПЦИОНОВ';
          statusEl.className = 'badge';
        }
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
