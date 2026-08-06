import { $ } from './util.js';

let containerEl;
let statusEl;
let emptyEl;
let currentMode = 'SPECTROGRAM';
let waveletData = null;
let resizeObserver = null;
let refreshTimer = null;

export function initWavelet() {
  containerEl = $('#wavelet-canvas-holder');
  statusEl = $('#wavelet-status');
  emptyEl = $('#wavelet-empty');

  $('#btn-wavelet-spectrogram')?.addEventListener('click', () => setMode('SPECTROGRAM'));
  $('#btn-wavelet-energy')?.addEventListener('click', () => setMode('ENERGY'));

  if (containerEl && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderWavelet());
    resizeObserver.observe(containerEl);
  }
  fetchWaveletData();
  refreshTimer = setInterval(fetchWaveletData, 300000);
}

function setMode(mode) {
  currentMode = mode;
  $('#btn-wavelet-spectrogram')?.classList.toggle('active', mode === 'SPECTROGRAM');
  $('#btn-wavelet-energy')?.classList.toggle('active', mode === 'ENERGY');
  renderWavelet();
}

export async function fetchWaveletData() {
  try {
    const res = await fetch('/api/analytics/wavelet', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    waveletData = await res.json();
    renderWavelet();
  } catch (err) {
    console.warn('Wavelet fetch error:', err);
    if (statusEl) statusEl.textContent = '○ WAVELET OFFLINE';
  }
}

function ensureExtraMetrics(summary) {
  const card = $('#wavelet-summary-card');
  if (!card) return;
  let extra = $('#wavelet-extra-metrics');
  if (!extra) {
    extra = document.createElement('div');
    extra.id = 'wavelet-extra-metrics';
    extra.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:8px;margin-top:4px;line-height:1.8';
    card.appendChild(extra);
  }
  const shift = summary.cycle_shift || '—';
  const concentration = summary.spectral_concentration == null ? '—' : `${(summary.spectral_concentration * 100).toFixed(0)}%`;
  const hours = summary.history_hours_trading == null ? '—' : `${summary.history_hours_trading.toFixed(1)}h`;
  const maxP = summary.period_max_hours == null ? '—' : `${summary.period_max_hours}h`;
  const source = summary.source?.source || '—';
  extra.innerHTML = `
    <div>CYCLE SHIFT: <b>${shift}</b></div>
    <div>SPECTRAL CONC.: <b>${concentration}</b></div>
    <div>REAL HISTORY: <b>${hours}</b></div>
    <div>MAX RESOLVED: <b>${maxP}</b></div>
    <div style="margin-top:6px;color:#777;font-size:10px">${source}</div>`;
}

function updateSummary(summary) {
  const dom = Number(summary.dominant_period_hours || 0);
  if ($('#wavelet-val-dom')) $('#wavelet-val-dom').textContent = `${dom.toFixed(1)}h`;
  if ($('#wavelet-val-micro')) $('#wavelet-val-micro').textContent = `${Number(summary.micro_energy_pct || 0).toFixed(1)}%`;
  if ($('#wavelet-val-intra')) $('#wavelet-val-intra').textContent = `${Number(summary.intraday_energy_pct || 0).toFixed(1)}%`;
  if ($('#wavelet-val-macro')) $('#wavelet-val-macro').textContent = `${Number(summary.macro_energy_pct || 0).toFixed(1)}%`;
  if ($('#wavelet-val-persist')) $('#wavelet-val-persist').textContent = `${(Number(summary.persistence || 0) * 100).toFixed(0)}%`;
  if (statusEl) {
    const suffix = summary.cycle_shift && summary.cycle_shift !== 'STABLE' ? ` · ${summary.cycle_shift}` : '';
    statusEl.textContent = `● DOMINANT ${dom.toFixed(1)}H${suffix}`;
  }
  ensureExtraMetrics(summary);
}

function createCanvas() {
  let cv = containerEl?.querySelector('canvas');
  if (!cv && containerEl) {
    const oldEmpty = emptyEl;
    cv = document.createElement('canvas');
    cv.style.cssText = 'width:100%;height:100%;display:block;';
    containerEl.insertBefore(cv, oldEmpty || null);
  }
  return cv;
}

function palette(v) {
  const x = Math.max(0, Math.min(1, Number(v) || 0));
  // Paper-terminal palette with real contrast: navy -> blue -> cyan -> amber.
  const stops = [
    [0.00, [247, 248, 250]],
    [0.18, [217, 225, 244]],
    [0.42, [111, 145, 214]],
    [0.68, [29, 100, 165]],
    [0.84, [22, 160, 171]],
    [1.00, [235, 164, 52]],
  ];
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [aPos, a] = stops[i - 1];
      const [bPos, b] = stops[i];
      const t = (x - aPos) / (bPos - aPos || 1);
      const c = a.map((n, j) => Math.round(n + (b[j] - n) * t));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  return 'rgb(235,164,52)';
}

function fmtTime(ts, withDate = false) {
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return withDate
    ? `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
    : `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

function renderWavelet() {
  if (!containerEl) return;
  if (!waveletData?.available || !waveletData.spectrogram?.length) {
    if (emptyEl) {
      emptyEl.style.display = 'flex';
      emptyEl.textContent = `○ ${waveletData?.reason || 'WAVELET SPECTRUM UNAVAILABLE'}`;
    }
    if (statusEl) statusEl.textContent = '○ НЕДОСТАТОЧНО РЕАЛЬНОЙ ИСТОРИИ';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';

  const summary = waveletData.summary || {};
  updateSummary(summary);

  const cv = createCanvas();
  if (!cv) return;
  const rect = containerEl.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(520, Math.floor(rect.width || 850));
  const height = Math.max(330, Math.floor(rect.height || 420));
  cv.width = Math.floor(width * dpr);
  cv.height = Math.floor(height * dpr);
  cv.style.width = `${width}px`;
  cv.style.height = `${height}px`;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const margin = { left: 68, right: 24, top: 18, bottom: 38 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const grid = waveletData.period_grid_hours || [];
  const timestamps = waveletData.timestamps || [];
  const spec = waveletData.spectrogram || [];
  const ridge = waveletData.dominant_ridge || [];

  if (currentMode === 'ENERGY') {
    const bars = [
      ['MICRO <4h', Number(summary.micro_energy_pct || 0)],
      ['INTRADAY 4–24h', Number(summary.intraday_energy_pct || 0)],
      ['MACRO >24h', Number(summary.macro_energy_pct || 0)],
    ];
    ctx.font = '11px IBM Plex Mono, monospace';
    bars.forEach(([label, val], idx) => {
      const y = margin.top + 45 + idx * 82;
      ctx.fillStyle = '#eceae4';
      ctx.fillRect(margin.left, y, plotW, 32);
      ctx.fillStyle = palette(Math.max(0.25, val / 100));
      ctx.fillRect(margin.left, y, plotW * val / 100, 32);
      ctx.fillStyle = '#282721';
      ctx.textAlign = 'left';
      ctx.fillText(label, margin.left, y - 10);
      ctx.textAlign = 'right';
      ctx.font = 'bold 14px IBM Plex Mono, monospace';
      ctx.fillText(`${val.toFixed(1)}%`, margin.left + plotW, y + 22);
      ctx.font = '11px IBM Plex Mono, monospace';
    });
    ctx.fillStyle = '#6f6c64';
    ctx.textAlign = 'left';
    ctx.fillText(`DOMINANT ${Number(summary.dominant_period_hours || 0).toFixed(1)}h · PERSISTENCE ${(Number(summary.persistence || 0) * 100).toFixed(0)}% · ${summary.cycle_shift || '—'}`,
                 margin.left, height - 24);
    return;
  }

  const cols = timestamps.length;
  const rows = grid.length;
  const cellW = plotW / Math.max(cols, 1);
  const cellH = plotH / Math.max(rows, 1);

  // Heatmap already arrives robustly normalized to [0,1].
  for (let r = 0; r < rows; r++) {
    const row = spec[r] || [];
    const y = margin.top + plotH - (r + 1) * cellH;
    for (let c = 0; c < cols; c++) {
      const x = margin.left + c * cellW;
      ctx.fillStyle = palette(row[c]);
      ctx.fillRect(x, y, Math.ceil(cellW) + 1, Math.ceil(cellH) + 1);
    }
  }

  // Grid / period labels.
  ctx.strokeStyle = 'rgba(90,88,80,.18)';
  ctx.lineWidth = 1;
  ctx.font = '9px IBM Plex Mono, monospace';
  ctx.fillStyle = '#6f6c64';
  ctx.textBaseline = 'middle';
  grid.forEach((period, idx) => {
    const y = margin.top + plotH - (idx + 0.5) * cellH;
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(margin.left + plotW, y);
    ctx.stroke();
    ctx.textAlign = 'right';
    ctx.fillText(`${period}h`, margin.left - 7, y);
  });

  // Time labels show actual UTC timestamps instead of raw epoch suffixes.
  const labels = Math.min(5, cols);
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  for (let i = 0; i < labels; i++) {
    const idx = labels === 1 ? 0 : Math.round(i * (cols - 1) / (labels - 1));
    const x = margin.left + (idx + 0.5) * cellW;
    ctx.fillStyle = '#6f6c64';
    ctx.fillText(fmtTime(timestamps[idx], true), x, margin.top + plotH + 8);
  }

  // Dominant ridge with dark halo so it remains visible on hot cells.
  if (ridge.length) {
    const periodIndex = new Map(grid.map((p, i) => [Number(p), i]));
    const draw = (stroke, widthLine) => {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = widthLine;
      ctx.beginPath();
      let started = false;
      ridge.forEach((pt, c) => {
        const row = periodIndex.get(Number(pt.period_hours));
        if (row == null) return;
        const x = margin.left + (c + 0.5) * cellW;
        const y = margin.top + plotH - (row + 0.5) * cellH;
        if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
      });
      ctx.stroke();
    };
    draw('rgba(20,24,28,.7)', 5);
    draw('#17d7df', 2.2);
  }

  // Current-period marker and compact legend.
  const dom = Number(summary.dominant_period_hours || 0);
  const domIdx = grid.findIndex((p) => Number(p) === dom || Math.abs(Number(p) - dom) < 1e-6);
  if (domIdx >= 0) {
    const y = margin.top + plotH - (domIdx + 0.5) * cellH;
    ctx.fillStyle = '#111';
    ctx.font = 'bold 10px IBM Plex Mono, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(`NOW ${dom.toFixed(1)}h`, margin.left + plotW - 6, y - 5);
  }
  ctx.strokeStyle = '#bdb9af';
  ctx.strokeRect(margin.left, margin.top, plotW, plotH);
}
