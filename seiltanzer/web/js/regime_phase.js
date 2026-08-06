import { $ } from './util.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';

let plotEl;
let statusEl;
let emptyEl;
let currentHorizon = '6H';
let regimeData = null;
let cameraGuard = null;
let refreshTimer = null;

const INIT_CAM = {
  eye: { x: 1.45, y: -1.55, z: 1.05 },
  up: { x: 0, y: 0, z: 1 },
};

export function initRegimePhase() {
  plotEl = $('#regime-phase-plot');
  statusEl = $('#regime-status');
  emptyEl = $('#regime-phase-empty');
  if (plotEl) cameraGuard = createPlotlyCameraGuard(plotEl, INIT_CAM);

  $('#btn-regime-6h')?.addEventListener('click', () => setHorizon('6H'));
  $('#btn-regime-24h')?.addEventListener('click', () => setHorizon('24H'));
  $('#btn-regime-3d')?.addEventListener('click', () => setHorizon('3D'));

  fetchRegimePhase();
  refreshTimer = setInterval(fetchRegimePhase, 300000);
}

function setHorizon(horizon) {
  currentHorizon = horizon;
  $('#btn-regime-6h')?.classList.toggle('active', horizon === '6H');
  $('#btn-regime-24h')?.classList.toggle('active', horizon === '24H');
  $('#btn-regime-3d')?.classList.toggle('active', horizon === '3D');
  renderRegimePlot();
}

export async function fetchRegimePhase() {
  try {
    const res = await fetch('/api/analytics/regime-phase', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    regimeData = await res.json();
    renderRegimePlot();
  } catch (err) {
    console.warn('Regime phase fetch error:', err);
    if (statusEl) statusEl.textContent = '○ MACRO REGIME OFFLINE';
  }
}

// The analytical path is sampled on a slower cadence. A raw price WebSocket
// tick must not redraw the same 3D scene and must never reset the user camera.
export function updateLiveRegimePhase() {}

function fmtAge(seconds) {
  const s = Number(seconds || 0);
  if (s < 3600) return `${Math.max(0, Math.round(s / 60))}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function ensureExtraSummary(summary) {
  const card = $('#regime-summary-card');
  if (!card) return;
  let extra = $('#regime-extra-metrics');
  if (!extra) {
    extra = document.createElement('div');
    extra.id = 'regime-extra-metrics';
    extra.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:8px;margin-top:4px;line-height:1.8';
    card.appendChild(extra);
  }
  const vol = summary.vol_index || {};
  const source = summary.source?.source || '—';
  extra.innerHTML = `
    <div>REGIME AGE: <b>${fmtAge(summary.regime_age_seconds)}</b></div>
    <div>TRANSITION V: <b>${Number(summary.transition_velocity || 0).toFixed(2)}/h</b></div>
    <div>VOL INDEX: <b>${vol.key ? vol.key.toUpperCase() : '—'} ${vol.value == null ? '—' : Number(vol.value).toFixed(1)}</b></div>
    <div>REAL POINTS: <b>${summary.points ?? '—'}</b></div>
    <div style="margin-top:6px;color:#777;font-size:10px">${source}</div>`;
}

function rangeFor(values, fallback, minSpan = 1.2, lowerBound = null) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return fallback;
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  const centre = (lo + hi) / 2;
  const span = Math.max(hi - lo, minSpan);
  lo = centre - span * 0.65;
  hi = centre + span * 0.65;
  if (lowerBound != null) lo = Math.max(lowerBound, lo);
  return [Math.max(fallback[0], lo), Math.min(fallback[1], hi)];
}

function regimeColor(regime) {
  if (regime === 'VOL SHOCK') return '#c6373c';
  if (regime === 'TREND EXPANSION') return '#e58a2b';
  if (regime === 'CALM TREND') return '#2e7d4f';
  if (regime === 'COMPRESSION') return '#5477a8';
  if (regime === 'RECOVERY') return '#7b5aa6';
  return '#6f6c64';
}

function renderRegimePlot() {
  if (!plotEl || !window.Plotly) return;
  if (!regimeData?.available) {
    if (emptyEl) {
      emptyEl.style.display = 'flex';
      emptyEl.textContent = `○ ${regimeData?.reason || 'PHASE SPACE UNAVAILABLE'}`;
    }
    if (statusEl) statusEl.textContent = '○ НЕДОСТАТОЧНО РЕАЛЬНОЙ ИСТОРИИ';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  const summary = regimeData.summary || {};
  const current = regimeData.current || {};
  if ($('#regime-val-label')) {
    $('#regime-val-label').textContent = current.regime || 'CHOP';
    $('#regime-val-label').style.color = regimeColor(current.regime);
  }
  if ($('#regime-val-x')) $('#regime-val-x').textContent = Number(current.x_trend || 0).toFixed(2);
  if ($('#regime-val-y')) $('#regime-val-y').textContent = Number(current.y_vol || 0).toFixed(2);
  if ($('#regime-val-z')) $('#regime-val-z').textContent = Number(current.z_stress || 0).toFixed(2);
  if ($('#regime-val-conf')) $('#regime-val-conf').textContent = `${Number(current.confidence || 0).toFixed(0)}%`;
  if ($('#regime-val-dist')) $('#regime-val-dist').textContent = Number(summary.boundary_distance || 0).toFixed(2);
  if (statusEl) statusEl.textContent = `● ${current.regime || 'CHOP'} · ${currentHorizon}`;
  ensureExtraSummary(summary);

  const key = currentHorizon === '24H' ? 'trajectory_24h' : currentHorizon === '3D' ? 'trajectory_3d' : 'trajectory_6h';
  const traj = regimeData[key] || [];
  const x = traj.map((p) => Number(p.x));
  const y = traj.map((p) => Number(p.y));
  const z = traj.map((p) => Number(p.z));
  const colors = traj.map((_, i) => i / Math.max(1, traj.length - 1));
  const labels = traj.map((p) => {
    const d = new Date(Number(p.ts) * 1000);
    return `${p.regime || '—'} · ${d.toISOString().slice(5, 16).replace('T', ' ')} UTC`;
  });

  const traces = [
    {
      type: 'scatter3d', mode: 'lines+markers', name: 'REAL TRAJECTORY',
      x, y, z,
      text: labels, hovertemplate: '%{text}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>',
      line: { color: '#345f8c', width: 5 },
      marker: {
        size: 3.5,
        color: colors,
        colorscale: [[0, '#b9c5d3'], [0.55, '#5d85ad'], [1, '#173f68']],
        showscale: false,
      },
    },
    // XY floor projection makes a low-stress regime visually readable instead
    // of collapsing into an apparently empty 3D box.
    {
      type: 'scatter3d', mode: 'lines', hoverinfo: 'skip', showlegend: false,
      x, y, z: z.map(() => 0),
      line: { color: 'rgba(70,80,90,.22)', width: 2, dash: 'dot' },
    },
    {
      type: 'scatter3d', mode: 'markers+text', name: 'CURRENT',
      x: [Number(current.x_trend || 0)],
      y: [Number(current.y_vol || 0)],
      z: [Number(current.z_stress || 0)],
      marker: { size: 9, color: regimeColor(current.regime), line: { color: '#fff', width: 1.5 } },
      text: [current.regime || 'STATE'], textposition: 'top center',
      hovertemplate: `CURRENT ${current.regime || '—'}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>`,
    },
  ];

  // Reference anchors label the semantic regions without pretending that they
  // are hard geometric attractors.
  traces.push({
    type: 'scatter3d', mode: 'text', hoverinfo: 'skip', showlegend: false,
    x: [1.5, 1.4, 0, 0, 0, -1.5],
    y: [0, 1.2, -1.3, 1.8, 1.0, 0],
    z: [0.15, 0.35, 0.1, 1.7, 0.35, 0.15],
    text: ['CALM TREND', 'TREND EXP.', 'COMPRESSION', 'VOL SHOCK', 'RECOVERY', 'CALM ↓'],
    textfont: { size: 9, color: '#9a978e' },
  });

  const xr = rangeFor([...x, Number(current.x_trend || 0)], [-3, 3], 1.6);
  const yr = rangeFor([...y, Number(current.y_vol || 0)], [-3, 3], 1.6);
  const maxZ = Math.max(0, ...z.filter(Number.isFinite), Number(current.z_stress || 0));
  const zr = [0, Math.min(3, Math.max(0.8, maxZ + 0.45))];

  const layout = {
    margin: { l: 0, r: 0, b: 0, t: 0 },
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', showlegend: false,
    uirevision: 'macro-phase-camera-v2',
    scene: {
      xaxis: { title: 'TREND · X', range: xr, gridcolor: '#dedbd3', zerolinecolor: '#8a877d' },
      yaxis: { title: 'VOL · Y', range: yr, gridcolor: '#dedbd3', zerolinecolor: '#8a877d' },
      zaxis: { title: 'CROSS-STRESS · Z', range: zr, gridcolor: '#dedbd3', zerolinecolor: '#8a877d' },
      bgcolor: 'rgba(255,255,255,0)',
    },
  };

  cameraGuard?.beforeWrite();
  window.Plotly.react(plotEl, traces, layout, { responsive: true, displayModeBar: false });
  cameraGuard?.afterWrite();
}
