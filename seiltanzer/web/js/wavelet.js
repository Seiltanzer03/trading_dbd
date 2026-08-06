import { $ } from './util.js';

let containerEl;
let statusEl;
let emptyEl;
let currentMode = 'SPECTROGRAM'; // 'SPECTROGRAM' | 'ENERGY'
let waveletData = null;

export function initWavelet() {
  containerEl = $('#wavelet-canvas-holder');
  statusEl = $('#wavelet-status');
  emptyEl = $('#wavelet-empty');

  const btnSpec = $('#btn-wavelet-spectrogram');
  const btnEnergy = $('#btn-wavelet-energy');

  if (btnSpec) btnSpec.addEventListener('click', () => setMode('SPECTROGRAM'));
  if (btnEnergy) btnEnergy.addEventListener('click', () => setMode('ENERGY'));

  fetchWaveletData();
}

function setMode(mode) {
  currentMode = mode;
  const btnSpec = $('#btn-wavelet-spectrogram');
  const btnEnergy = $('#btn-wavelet-energy');
  if (btnSpec) btnSpec.classList.toggle('active', mode === 'SPECTROGRAM');
  if (btnEnergy) btnEnergy.classList.toggle('active', mode === 'ENERGY');

  if (waveletData) renderWavelet();
}

export async function fetchWaveletData() {
  try {
    const res = await fetch('/api/analytics/wavelet');
    if (res.ok) {
      waveletData = await res.json();
      renderWavelet();
    }
  } catch (err) {
    console.warn('Wavelet fetch error:', err);
  }
}

function renderWavelet() {
  if (!containerEl) return;
  if (!waveletData || !waveletData.available || !waveletData.spectrogram?.length) {
    if (emptyEl) emptyEl.style.display = 'flex';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  const summary = waveletData.summary || {};

  // Обновляем боковую карточку
  const elDom = $('#wavelet-val-dom');
  const elMicro = $('#wavelet-val-micro');
  const elIntra = $('#wavelet-val-intra');
  const elMacro = $('#wavelet-val-macro');
  const elPersist = $('#wavelet-val-persist');

  if (elDom) elDom.textContent = `${(summary.dominant_period_hours || 0).toFixed(1)}h`;
  if (elMicro) elMicro.textContent = `${(summary.micro_energy_pct || 0).toFixed(1)}%`;
  if (elIntra) elIntra.textContent = `${(summary.intraday_energy_pct || 0).toFixed(1)}%`;
  if (elMacro) elMacro.textContent = `${(summary.macro_energy_pct || 0).toFixed(1)}%`;
  if (elPersist) elPersist.textContent = `${((summary.persistence || 0) * 100).toFixed(0)}%`;
  if (statusEl) statusEl.textContent = `● DOMINANT ${(summary.dominant_period_hours || 0).toFixed(1)}H`;

  let cv = containerEl.querySelector('canvas');
  if (!cv) {
    containerEl.innerHTML = '<canvas style="width:100%;height:100%;display:block;"></canvas>';
    cv = containerEl.querySelector('canvas');
  }

  const rect = containerEl.getBoundingClientRect();
  const width = Math.max(400, Math.floor(rect.width || 800));
  const height = Math.max(300, Math.floor(rect.height || 420));
  cv.width = width;
  cv.height = height;

  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, width, height);

  const margin = { left: 60, right: 30, top: 20, bottom: 30 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;

  const grid = waveletData.period_grid_hours || [];
  const timestamps = waveletData.timestamps || [];
  const spectrogram = waveletData.spectrogram || [];
  const ridge = waveletData.dominant_ridge || [];

  if (!grid.length || !spectrogram.length || !timestamps.length) return;

  if (currentMode === 'ENERGY') {
    // Отрисовка гистограммы энергии по диапазонам
    const bars = [
      { label: 'MICRO (<4h)', val: summary.micro_energy_pct || 0, color: '#3498db' },
      { label: 'INTRADAY (4-24h)', val: summary.intraday_energy_pct || 0, color: '#9b59b6' },
      { label: 'MACRO (>24h)', val: summary.macro_energy_pct || 0, color: '#e67e22' },
    ];
    const barH = plotH / 4;
    bars.forEach((b, idx) => {
      const py = margin.top + idx * (barH + 15) + 20;
      const w = (b.val / 100.0) * plotW;
      ctx.fillStyle = '#f0f0f0';
      ctx.fillRect(margin.left, py, plotW, barH);
      ctx.fillStyle = b.color;
      ctx.fillRect(margin.left, py, w, barH);

      ctx.fillStyle = '#333';
      ctx.font = 'bold 11px IBM Plex Mono, monospace';
      ctx.textAlign = 'left';
      ctx.fillText(`${b.label}: ${b.val.toFixed(1)}%`, margin.left + 8, py + barH / 2 + 4);
    });
    return;
  }

  // Отрисовка Спектрограммы (Spectrogram Heatmap)
  let maxPower = 1e-6;
  for (let r = 0; r < spectrogram.length; r++) {
    for (let c = 0; c < spectrogram[r].length; c++) {
      maxPower = Math.max(maxPower, spectrogram[r][c]);
    }
  }

  const cellW = plotW / timestamps.length;
  const cellH = plotH / grid.length;

  for (let r = 0; r < grid.length; r++) {
    const py = margin.top + plotH - (r + 1) * cellH;
    for (let c = 0; c < timestamps.length; c++) {
      const val = spectrogram[r][c];
      const norm = Math.min(1.0, val / maxPower);
      const px = margin.left + c * cellW;

      // Colormap: viridis/plasma style (dark blue -> purple -> orange -> yellow)
      const hue = (1.0 - norm) * 240; // 240 (blue) to 0 (red)
      ctx.fillStyle = `hsla(${hue}, 85%, 50%, ${Math.max(0.2, norm * 0.85)})`;
      ctx.fillRect(px, py, cellW + 0.5, cellH + 0.5);
    }
  }

  // Отрисовка Светящегося Хребта (Dominant Ridge)
  if (ridge.length) {
    ctx.strokeStyle = '#00ffff';
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    for (let c = 0; c < ridge.length; c++) {
      const pVal = ridge[c].period_hours;
      const rIdx = grid.indexOf(pVal);
      if (rIdx >= 0) {
        const px = margin.left + (c + 0.5) * cellW;
        const py = margin.top + plotH - (rIdx + 0.5) * cellH;
        if (!started) {
          ctx.moveTo(px, py);
          started = true;
        } else {
          ctx.lineTo(px, py);
        }
      }
    }
    ctx.stroke();
  }

  // Оси и подписи
  ctx.strokeStyle = '#eee';
  ctx.strokeRect(margin.left, margin.top, plotW, plotH);

  ctx.fillStyle = '#666';
  ctx.font = '9px IBM Plex Mono, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  grid.forEach((p, idx) => {
    const py = margin.top + plotH - (idx + 0.5) * cellH;
    ctx.fillText(`${p}h`, margin.left - 6, py);
  });
}
