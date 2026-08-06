import { $ } from './util.js';

let plotEl;
let statusEl;
let emptyEl;
let currentHorizon = '6H'; // '6H' | '24H' | '3D'
let lastCamera = null;
let isUserHoldingCamera = false;
let regimeData = null;

export function initRegimePhase() {
  plotEl = $('#regime-phase-plot');
  statusEl = $('#regime-status');
  emptyEl = $('#regime-phase-empty');

  const btn6h = $('#btn-regime-6h');
  const btn24h = $('#btn-regime-24h');
  const btn3d = $('#btn-regime-3d');

  if (btn6h) btn6h.addEventListener('click', () => setHorizon('6H'));
  if (btn24h) btn24h.addEventListener('click', () => setHorizon('24H'));
  if (btn3d) btn3d.addEventListener('click', () => setHorizon('3D'));

  if (plotEl) {
    plotEl.addEventListener('pointerdown', () => { isUserHoldingCamera = true; });
    window.addEventListener('pointerup', () => { isUserHoldingCamera = false; });
  }

  fetchRegimePhase();
}

function setHorizon(horizon) {
  currentHorizon = horizon;
  const btn6h = $('#btn-regime-6h');
  const btn24h = $('#btn-regime-24h');
  const btn3d = $('#btn-regime-3d');
  if (btn6h) btn6h.classList.toggle('active', horizon === '6H');
  if (btn24h) btn24h.classList.toggle('active', horizon === '24H');
  if (btn3d) btn3d.classList.toggle('active', horizon === '3D');

  if (regimeData) renderRegimePlot(true);
}

export async function fetchRegimePhase() {
  try {
    const res = await fetch('/api/analytics/regime-phase');
    if (res.ok) {
      regimeData = await res.json();
      renderRegimePlot();
    }
  } catch (err) {
    console.warn('Regime phase fetch error:', err);
  }
}

export function updateLiveRegimePhase(live) {
  if (regimeData && !isUserHoldingCamera) {
    renderRegimePlot(false);
  }
}

function renderRegimePlot(force = false) {
  if (!plotEl || !window.Plotly) return;
  if (!regimeData || !regimeData.available) {
    if (emptyEl) emptyEl.style.display = 'flex';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  const summary = regimeData.summary || {};
  const current = regimeData.current || {};

  // Обновляем карточку справа
  const elRegime = $('#regime-val-label');
  const elX = $('#regime-val-x');
  const elY = $('#regime-val-y');
  const elZ = $('#regime-val-z');
  const elConf = $('#regime-val-conf');
  const elDist = $('#regime-val-dist');

  if (elRegime) {
    elRegime.textContent = current.regime || 'CHOP';
    elRegime.style.color = current.regime === 'VOL SHOCK' ? '#c0392b' : current.regime === 'CALM TREND' ? '#27ae60' : '#e67e22';
  }
  if (elX) elX.textContent = (current.x_trend || 0).toFixed(2);
  if (elY) elY.textContent = (current.y_vol || 0).toFixed(2);
  if (elZ) elZ.textContent = (current.z_stress || 0).toFixed(2);
  if (elConf) elConf.textContent = `${current.confidence || 85}%`;
  if (elDist) elDist.textContent = (summary.boundary_distance || 0).toFixed(2);
  if (statusEl) statusEl.textContent = `● ${current.regime || 'CHOP'}`;

  // Траектория
  const trajKey = currentHorizon === '24H' ? 'trajectory_24h' : currentHorizon === '3D' ? 'trajectory_3d' : 'trajectory_6h';
  const traj = regimeData[trajKey] || [];

  const xPts = traj.map((p) => p.x);
  const yPts = traj.map((p) => p.y);
  const zPts = traj.map((p) => p.z);

  // Сохраняем прошлую камеру Plotly
  if (plotEl._fullLayout && plotEl._fullLayout.scene && plotEl._fullLayout.scene.camera) {
    lastCamera = JSON.parse(JSON.stringify(plotEl._fullLayout.scene.camera));
  }

  const traceTrajectory = {
    type: 'scatter3d',
    mode: 'lines+markers',
    name: 'Phase Trajectory',
    x: xPts,
    y: yPts,
    z: zPts,
    line: { color: '#3498db', width: 4 },
    marker: { size: 3, color: '#2980b9' },
  };

  const traceCurrent = {
    type: 'scatter3d',
    mode: 'markers+text',
    name: 'Current State',
    x: [current.x_trend || 0],
    y: [current.y_vol || 0],
    z: [current.z_stress || 0],
    marker: { size: 10, color: '#e74c3c', symbol: 'circle' },
    text: [current.regime || 'STATE'],
    textposition: 'top center',
  };

  const layout = {
    margin: { l: 0, r: 0, b: 0, t: 0 },
    paper_bgcolor: 'transparent',
    plot_bgcolor: 'transparent',
    showlegend: false,
    scene: {
      xaxis: { title: 'Trend (X)', range: [-3, 3] },
      yaxis: { title: 'Vol (Y)', range: [-3, 3] },
      zaxis: { title: 'Stress (Z)', range: [0, 3] },
      camera: lastCamera || {
        eye: { x: 1.5, y: 1.5, z: 1.2 },
      },
    },
  };

  const config = { responsive: true, displayModeBar: false };
  window.Plotly.react(plotEl, [traceTrajectory, traceCurrent], layout, config);
}
