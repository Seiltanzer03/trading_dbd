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
  eye: { x: 1.55, y: -1.7, z: 1.15 },
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

export function updateLiveRegimePhase() {}

function fmtAge(seconds) {
  const s = Number(seconds || 0);
  if (s < 3600) return `${Math.max(0, Math.round(s / 60))}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function regimeColor(regime, alpha = 1) {
  const hex = {
    'VOL SHOCK': [198, 55, 60],
    'TREND EXPANSION': [229, 138, 43],
    'CALM TREND': [46, 125, 79],
    'COMPRESSION': [70, 112, 166],
    'RECOVERY': [123, 90, 166],
    'CHOP': [111, 108, 100],
  }[regime] || [111, 108, 100];
  return alpha >= 1 ? `rgb(${hex.join(',')})` : `rgba(${hex.join(',')},${alpha})`;
}

function rangeFor(values, fallback, minSpan = 1.2, lowerBound = null) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return fallback;
  let lo = Math.min(...finite), hi = Math.max(...finite);
  const centre = (lo + hi) / 2;
  const span = Math.max(hi - lo, minSpan);
  lo = centre - span * 0.72;
  hi = centre + span * 0.72;
  if (lowerBound != null) lo = Math.max(lowerBound, lo);
  return [Math.max(fallback[0], lo), Math.min(fallback[1], hi)];
}

function stressBar(label, value, max = 3) {
  const v = Math.max(0, Math.min(max, Number(value || 0)));
  const pct = Math.round(v / max * 100);
  return `<div style="display:grid;grid-template-columns:88px 1fr 34px;gap:6px;align-items:center;margin:3px 0">
    <span>${label}</span><span style="height:5px;background:#e7e4dd;display:block;position:relative"><i style="display:block;height:100%;width:${pct}%;background:${pct > 65 ? '#c6373c' : pct > 35 ? '#d79031' : '#5477a8'}"></i></span><b>${v.toFixed(2)}</b></div>`;
}

function buildTimeline(traj) {
  if (!traj?.length) return '';
  const segments = [];
  let last = null;
  for (const p of traj) {
    if (!last || last.regime !== p.regime) {
      last = { regime: p.regime || 'CHOP', count: 1 };
      segments.push(last);
    } else last.count++;
  }
  const total = Math.max(1, traj.length);
  return `<div style="margin-top:9px;border-top:1px solid #d9d6ce;padding-top:8px">
    <div style="font-size:9px;color:#777;margin-bottom:5px">REGIME TRANSITION · 24H</div>
    <div style="height:11px;display:flex;overflow:hidden;border-radius:2px;background:#eee">${segments.map((s) => `<span title="${s.regime}" style="width:${100 * s.count / total}%;background:${regimeColor(s.regime)}"></span>`).join('')}</div>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:#777;margin-top:4px"><span>−24H</span><span>NOW</span></div></div>`;
}

function ensureExtraSummary(summary) {
  const card = $('#regime-summary-card');
  if (!card) return;
  let extra = $('#regime-extra-metrics');
  if (!extra) {
    extra = document.createElement('div');
    extra.id = 'regime-extra-metrics';
    extra.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:8px;margin-top:4px;line-height:1.65';
    card.appendChild(extra);
  }
  const vol = summary.vol_index || {};
  const c = summary.stress_components || {};
  extra.innerHTML = `
    <div>REGIME AGE: <b>${fmtAge(summary.regime_age_seconds)}</b></div>
    <div>VELOCITY: <b>${Number(summary.transition_velocity || 0).toFixed(2)}/h</b></div>
    <div>ACCELERATION: <b>${Number(summary.transition_acceleration || 0).toFixed(2)}/h²</b></div>
    <div>VOL INDEX: <b>${vol.key ? vol.key.toUpperCase() : '—'} ${vol.value == null ? '—' : Number(vol.value).toFixed(1)}</b></div>
    <div style="margin-top:7px;font-size:9px;color:#777">STRESS DECOMPOSITION</div>
    ${stressBar('CROSS-ASSET', c.cross_asset)}
    ${stressBar('VOL IMPULSE', c.realized_impulse)}
    ${stressBar('SHOCK', c.shock)}
    ${stressBar('DISLOCATION', c.trend_dislocation)}
    ${buildTimeline(regimeData?.trajectory_24h || [])}
    <div style="margin-top:7px;color:#777;font-size:9px">${summary.source?.source || '—'} · ${summary.stress_source || '—'}</div>`;
}

function boxMesh(cx, cy, cz, sx, sy, sz, color, name) {
  const x = [], y = [], z = [];
  for (const dx of [-1, 1]) for (const dy of [-1, 1]) for (const dz of [-1, 1]) {
    x.push(cx + dx * sx / 2); y.push(cy + dy * sy / 2); z.push(Math.max(0, cz + dz * sz / 2));
  }
  // Vertex ordering from nested loops above.
  const faces = [
    [0,1,3],[0,3,2],[4,6,7],[4,7,5],
    [0,4,5],[0,5,1],[2,3,7],[2,7,6],
    [0,2,6],[0,6,4],[1,5,7],[1,7,3],
  ];
  return {
    type: 'mesh3d', name, x, y, z,
    i: faces.map((f) => f[0]), j: faces.map((f) => f[1]), k: faces.map((f) => f[2]),
    color, opacity: 0.065, hoverinfo: 'skip', showlegend: false, flatshading: true,
  };
}

function renderRegimePlot() {
  if (!plotEl || !window.Plotly) return;
  if (!regimeData?.available) {
    if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = `○ ${regimeData?.reason || 'PHASE SPACE UNAVAILABLE'}`; }
    if (statusEl) statusEl.textContent = '○ НЕДОСТАТОЧНО РЕАЛЬНОЙ ИСТОРИИ';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  const summary = regimeData.summary || {};
  const current = regimeData.current || {};
  if ($('#regime-val-label')) { $('#regime-val-label').textContent = current.regime || 'CHOP'; $('#regime-val-label').style.color = regimeColor(current.regime); }
  if ($('#regime-val-x')) $('#regime-val-x').textContent = Number(current.x_trend || 0).toFixed(2);
  if ($('#regime-val-y')) $('#regime-val-y').textContent = Number(current.y_vol || 0).toFixed(2);
  if ($('#regime-val-z')) $('#regime-val-z').textContent = Number(current.z_stress || 0).toFixed(2);
  if ($('#regime-val-conf')) $('#regime-val-conf').textContent = `${Number(current.confidence || 0).toFixed(0)}%`;
  if ($('#regime-val-dist')) $('#regime-val-dist').textContent = Number(summary.boundary_distance || 0).toFixed(2);
  if (statusEl) statusEl.textContent = `● ${current.regime || 'CHOP'} · Z ${Number(current.z_stress || 0).toFixed(2)} · ${currentHorizon}`;
  ensureExtraSummary(summary);

  const key = currentHorizon === '24H' ? 'trajectory_24h' : currentHorizon === '3D' ? 'trajectory_3d' : 'trajectory_6h';
  const traj = regimeData[key] || [];
  const x = traj.map((p) => Number(p.x));
  const y = traj.map((p) => Number(p.y));
  const z = traj.map((p) => Number(p.z));
  const labels = traj.map((p) => `${p.regime || '—'} · ${new Date(Number(p.ts) * 1000).toISOString().slice(5,16).replace('T',' ')} UTC`);
  const speeds = traj.map((p, i) => {
    if (!i) return 0;
    const a = traj[i - 1], dt = Math.max((Number(p.ts) - Number(a.ts)) / 3600, 1 / 12);
    return Math.hypot(Number(p.x)-Number(a.x), Number(p.y)-Number(a.y), Number(p.z)-Number(a.z)) / dt;
  });
  const maxSpeed = Math.max(0.001, ...speeds);

  const traces = [
    boxMesh(1.55, 0.0, 0.35, 1.35, 1.45, 0.7, '#2e7d4f', 'CALM +'),
    boxMesh(-1.55, 0.0, 0.35, 1.35, 1.45, 0.7, '#2e7d4f', 'CALM -'),
    boxMesh(0.0, -1.35, 0.28, 1.45, 1.0, 0.55, '#5477a8', 'COMPRESSION'),
    boxMesh(1.55, 1.05, 0.8, 1.5, 1.15, 1.0, '#e58a2b', 'TREND EXP +'),
    boxMesh(-1.55, 1.05, 0.8, 1.5, 1.15, 1.0, '#e58a2b', 'TREND EXP -'),
    boxMesh(0.0, 1.9, 2.0, 2.6, 1.1, 1.7, '#c6373c', 'VOL SHOCK'),
    {
      type: 'scatter3d', mode: 'lines+markers', name: 'REAL TRAJECTORY', x, y, z,
      text: labels, hovertemplate: '%{text}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>',
      line: { color: '#183f68', width: 6 },
      marker: { size: speeds.map((s) => 2.5 + 4 * Math.min(1, s / maxSpeed)), color: z,
                colorscale: [[0,'#3e7896'],[0.45,'#49a2a0'],[0.72,'#e0a13e'],[1,'#c6373c']], cmin: 0, cmax: Math.max(1, ...z), showscale: false,
                line: { color: 'rgba(255,255,255,.55)', width: 0.5 } },
    },
    {
      type: 'scatter3d', mode: 'lines', hoverinfo: 'skip', showlegend: false,
      x, y, z: z.map(() => 0), line: { color: 'rgba(45,60,75,.22)', width: 2, dash: 'dot' },
    },
    {
      type: 'scatter3d', mode: 'lines', hoverinfo: 'skip', showlegend: false,
      x: [Number(current.x_trend || 0), Number(current.x_trend || 0)],
      y: [Number(current.y_vol || 0), Number(current.y_vol || 0)],
      z: [0, Number(current.z_stress || 0)], line: { color: 'rgba(198,55,60,.38)', width: 4, dash: 'dot' },
    },
    {
      type: 'scatter3d', mode: 'markers+text', name: 'CURRENT',
      x: [Number(current.x_trend || 0)], y: [Number(current.y_vol || 0)], z: [Number(current.z_stress || 0)],
      marker: { size: 10, color: regimeColor(current.regime), line: { color: '#fff', width: 1.8 } },
      text: [current.regime || 'STATE'], textposition: 'top center',
      hovertemplate: `CURRENT ${current.regime || '—'}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>`,
    },
  ];

  const vv = current.velocity_vector || summary.velocity_vector || {};
  const speed = Number(vv.speed || 0);
  if (speed > 0.001) {
    const s = 0.75 / speed;
    traces.push({
      type: 'cone', showlegend: false, hoverinfo: 'skip', anchor: 'tail', sizemode: 'absolute', sizeref: 0.32,
      x: [Number(current.x_trend || 0)], y: [Number(current.y_vol || 0)], z: [Number(current.z_stress || 0)],
      u: [Number(vv.x || 0) * s], v: [Number(vv.y || 0) * s], w: [Number(vv.z || 0) * s],
      colorscale: [[0, regimeColor(current.regime)], [1, regimeColor(current.regime)]], showscale: false,
    });
  }

  const xr = rangeFor([...x, Number(current.x_trend || 0)], [-3, 3], 2.2);
  const yr = rangeFor([...y, Number(current.y_vol || 0)], [-3, 3], 2.2);
  const maxZ = Math.max(0, ...z.filter(Number.isFinite), Number(current.z_stress || 0));
  const zr = [0, Math.min(3, Math.max(1.25, maxZ + 0.55))];
  const layout = {
    margin: { l: 0, r: 0, b: 0, t: 0 }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', showlegend: false,
    uirevision: 'macro-phase-camera-v3',
    scene: {
      xaxis: { title: 'TREND · X', range: xr, gridcolor: '#dedbd3', zerolinecolor: '#8a877d', showspikes: false },
      yaxis: { title: 'VOL REGIME · Y', range: yr, gridcolor: '#dedbd3', zerolinecolor: '#8a877d', showspikes: false },
      zaxis: { title: 'FRAGILITY / STRESS · Z', range: zr, gridcolor: '#dedbd3', zerolinecolor: '#8a877d', showspikes: false },
      bgcolor: 'rgba(255,255,255,0)', aspectmode: 'cube',
    },
  };
  cameraGuard?.beforeWrite();
  window.Plotly.react(plotEl, traces, layout, { responsive: true, displayModeBar: false });
  cameraGuard?.afterWrite();
}
