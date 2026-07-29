// IV Surface (3D) — Plotly GL3D Engine (Reliable, High-End Hedge Fund Visualization)
// Uses offline-capable Plotly GL3D renderer for maximum stability and cinematic 3D surfaces.

import { $ } from './util.js';

const ORANGE = '#E8622A';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
const PAPER = 'rgba(255,255,255,0)';
const RULE = 'rgba(180,180,180,0.3)';

const SURF_SCALE = [
  [0.0, '#1A1F3A'],
  [0.2, '#1B6CA8'],
  [0.4, '#2ECC71'],
  [0.6, '#F4CE14'],
  [0.8, '#E8622A'],
  [1.0, '#C6373C']
];

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
  const emptyEl = document.getElementById('iv-surface-empty');
  const statusEl = document.getElementById('iv-surface-status');
  
  let hasPlot = false;
  let currentCam = { eye: { x: 1.5, y: -1.5, z: 1.1 } };
  let initialTradeIV = null;

  function grabCam() {
    if (el && el._fullLayout && el._fullLayout.scene && el._fullLayout.scene._scene) {
      currentCam = el._fullLayout.scene._scene.getCamera();
    }
  }

  function attachListeners() {
    if (!el || el._iv_attached) return;
    el.on('plotly_relayout', () => grabCam());
    el._iv_attached = true;
  }

  function normalizePayload(surfacePayload) {
    if (!surfacePayload) return {};
    return Array.isArray(surfacePayload)
      ? { value: surfacePayload, status: 'delayed' }
      : surfacePayload;
  }

  function render(state, payload) {
    if (!window.Plotly) return;

    const p = normalizePayload(payload);
    const surfaceData = p.value || [];
    
    if (!surfaceData || surfaceData.length === 0) {
      if (emptyEl) emptyEl.style.display = 'flex';
      if (statusEl) {
        statusEl.textContent = '○ НЕТ ДАННЫХ ОПЦИОНОВ';
        statusEl.className = 'badge';
      }
      return;
    }

    const firstStrikes = surfaceData[0]?.strikes || [];
    const snapshotSpot = Number(surfaceData[0]?.spot_at_snapshot)
      || Number(firstStrikes[Math.floor(firstStrikes.length / 2)]);
    if (!(snapshotSpot > 0) || !firstStrikes.length) {
      if (emptyEl) emptyEl.style.display = 'flex';
      return;
    }

    if (emptyEl) emptyEl.style.display = 'none';

    const rows = surfaceData.map((row) => {
      const rowSpot = Number(row.spot_at_snapshot) || snapshotSpot;
      const pairs = (row.strikes || []).map((strike, i) => ({
        x: (Number(strike) / rowSpot - 1) * 100,
        iv: Number(row.ivs?.[i]) * 100,
      })).filter((item) => Number.isFinite(item.x) && Number.isFinite(item.iv)
        && item.iv > 0 && item.iv < 300).sort((a, b) => a.x - b.x);
      return { row, pairs };
    }).filter((r) => r.pairs.length >= 3);

    if (!rows.length) {
      if (emptyEl) emptyEl.style.display = 'flex';
      return;
    }

    let xLo = Math.max(-20, ...rows.map((r) => r.pairs[0].x));
    let xHi = Math.min(20, ...rows.map((r) => r.pairs[r.pairs.length - 1].x));
    if (!(xHi > xLo + 1)) {
      xLo = -15; xHi = 15;
    }

    const moneyPct = Array.from({ length: 41 }, (_, i) => +(xLo + (xHi - xLo) * i / 40).toFixed(2));
    const zIvs = rows.map(({ pairs }) => {
      const xs = pairs.map((item) => item.x), ys = pairs.map((item) => item.iv);
      return moneyPct.map((x) => +interp(xs, ys, x).toFixed(2));
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
    if (!allZ.length) return;
    const zMin = Math.min(...allZ);
    const rawMax = Math.max(...allZ);
    const zMax = rawMax > zMin ? rawMax : zMin + 0.01;

    // Volatility Delta status update
    const nearRow = zIvs[0];
    const atmIndex = Math.floor(moneyPct.length / 2);
    const currentATM = nearRow[atmIndex] || zMin;
    if (initialTradeIV === null) initialTradeIV = currentATM;
    const deltaIV = currentATM - initialTradeIV;

    if (statusEl) {
      const volBadge = deltaIV < -2.0 ? 'SQUEEZE' : (deltaIV > 2.0 ? 'EXPANSION' : 'STABLE');
      statusEl.textContent = `● PLOTLY 3D ENGINE · LIVE SLICE · VOL DELTA: ${deltaIV >= 0 ? '+' : ''}${deltaIV.toFixed(2)}% [${volBadge}]`;
      statusEl.className = 'badge live';
    }

    // 3D Surface Trace
    const surfaceTrace = {
      type: 'surface',
      x: moneyPct,
      y: yDte,
      z: zIvs,
      colorscale: SURF_SCALE,
      cmin: zMin,
      cmax: zMax,
      showscale: true,
      colorbar: {
        thickness: 14, len: 0.8, x: 1.02,
        bgcolor: 'rgba(255,255,255,0.9)',
        bordercolor: 'rgba(200,200,200,0.5)',
        borderwidth: 1,
        tickfont: { family: FONT, size: 10, color: '#333' },
        title: { text: 'IV %', side: 'right', font: { family: FONT, size: 11, color: '#333' } },
        ticksuffix: '%'
      },
      contours: {
        x: { show: true, color: 'rgba(255,255,255,0.2)', width: 1 },
        y: { show: true, color: 'rgba(255,255,255,0.2)', width: 1 },
        z: { show: true, usecolormap: true, project: { z: false }, width: 2 }
      },
      lighting: { ambient: 0.75, diffuse: 0.7, specular: 0.25, roughness: 0.5 },
      opacity: 0.94,
      name: 'IV Surface',
      hovertemplate: '<b>Moneyness:</b> %{x:.1f}%<br><b>DTE:</b> %{y}<br><b>IV:</b> %{z:.1f}%<extra></extra>'
    };

    // ATM Ridge Trace
    const atmTrace = {
      type: 'scatter3d',
      mode: 'lines',
      x: moneyPct.map(() => 0),
      y: yDte,
      z: zIvs.map((row) => row[atmIndex]),
      line: { color: ORANGE, width: 6 },
      name: 'ATM Ridge',
      hoverinfo: 'skip'
    };

    if (hasPlot) grabCam();

    const layout = {
      autosize: true,
      height: 340,
      margin: { l: 0, r: 40, t: 10, b: 10 },
      uirevision: 'iv-surface-v4',
      paper_bgcolor: PAPER,
      plot_bgcolor: PAPER,
      showlegend: false,
      scene: {
        camera: currentCam,
        uirevision: 'iv-surface-cam-v4',
        dragmode: 'orbit',
        bgcolor: 'rgba(250,250,250,0.5)',
        aspectmode: 'manual',
        aspectratio: { x: 1.4, y: 1.0, z: 0.7 },
        xaxis: {
          title: { text: 'MONEYNESS %', font: { family: FONT, size: 11, color: '#666' } },
          tickfont: { family: FONT, size: 9, color: '#666' },
          gridcolor: RULE, zerolinecolor: ORANGE, zerolinewidth: 2,
          ticksuffix: '%'
        },
        yaxis: {
          title: { text: 'DTE', font: { family: FONT, size: 11, color: '#666' } },
          tickfont: { family: FONT, size: 9, color: '#666' },
          gridcolor: RULE, zeroline: false,
          tickvals: yDte, ticktext: yTickText
        },
        zaxis: {
          title: { text: 'IV %', font: { family: FONT, size: 11, color: '#666' } },
          tickfont: { family: FONT, size: 9, color: '#666' },
          gridcolor: RULE, zeroline: false,
          ticksuffix: '%'
        }
      }
    };

    const config = {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['toImage', 'sendDataToCloud'],
      scrollZoom: true
    };

    const P = window.Plotly;
    if (!hasPlot) {
      P.newPlot(el, [surfaceTrace, atmTrace], layout, config).then(() => {
        hasPlot = true;
        attachListeners();
      });
    } else {
      P.react(el, [surfaceTrace, atmTrace], layout, config);
    }
  }

  function resetTradeState() {
    initialTradeIV = null;
  }

  return {
    render,
    updateLive: () => {},
    setLive: () => {},
    resetTradeState
  };
}
