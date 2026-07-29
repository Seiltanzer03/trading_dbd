// CROSS-ASSET REGIME MATRIX.
//
// Upper triangle: rolling 5-minute return correlation (last 96 observations).
// Lower triangle: change versus the 3-month daily baseline. This makes regime
// breaks visible instead of animating a static monthly matrix.

import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let payload = null;
const state = { short: [], delta: [] };
const WATCHED = [
  [0, 1, 'NAS↔VXN', 'spot-vol'],
  [2, 3, 'SP500↔VIX', 'spot-vol'],
  [4, 5, 'GOLD↔GVZ', 'spot-vol'],
  [6, 7, 'OIL↔OVX', 'spot-vol'],
  [0, 2, 'NAS↔SP500', 'cross-index'],
];

export function initCorrelation() {
  canvas = $('#corr-canvas');
  emptyEl = $('#corr-empty');
  statusEl = $('#corr-status');
  if (!canvas) return;
  requestAnimationFrame(renderLoop);
}

function finite(v) {
  return Number.isFinite(Number(v));
}

function regimeShift(p) {
  const short = p.matrix_short || p.matrix;
  const base = p.matrix_baseline;
  const delta = p.matrix_delta;
  if (!short || !base || !delta) return null;
  const candidates = WATCHED.map(([i, j, label, kind]) => {
    const s = short?.[i]?.[j], b = base?.[i]?.[j], d = delta?.[i]?.[j];
    return finite(s) && finite(b) && finite(d)
      ? { i, j, label, kind, short: Number(s), base: Number(b), delta: Number(d) }
      : null;
  }).filter(Boolean).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
  const top = candidates[0];
  if (!top || Math.abs(top.delta) < 0.18) return null;
  let meaning = 'связь сменила режим — подтверждение движения слабее';
  if (top.kind === 'spot-vol') {
    if (top.base < -0.2 && top.short > top.base + 0.25)
      meaning = 'обычная обратная spot-vol защита ослабла';
    else if (top.short < top.base - 0.25)
      meaning = 'обратная spot-vol связь усилилась';
  } else if (top.short < top.base - 0.25) {
    meaning = 'индексы расходятся — меньше cross-market подтверждения';
  } else if (top.short > top.base + 0.25) {
    meaning = 'индексы синхронизировались — больше общего beta-риска';
  }
  return { ...top, meaning };
}

export function updateCorrelation(p) {
  const matrix = p?.matrix_short || p?.matrix;
  if (!p || !matrix || matrix.length === 0) {
    payload = null;
    if (emptyEl) emptyEl.style.display = 'flex';
    if (canvas) canvas.style.display = 'none';
    if (statusEl) statusEl.textContent = '○ НЕТ ДАННЫХ';
    return;
  }
  payload = p;
  if (emptyEl) emptyEl.style.display = 'none';
  if (canvas) canvas.style.display = 'block';
  const n = matrix.length;
  if (state.short.length !== n) {
    const effective = p.matrix || matrix;
    state.short = Array.from({ length: n }, (_, i) =>
      Array.from({ length: n }, (_, j) =>
        finite(matrix?.[i]?.[j]) ? Number(matrix[i][j]) : Number(effective?.[i]?.[j] || 0)));
    state.delta = Array.from({ length: n }, (_, i) =>
      Array.from({ length: n }, (_, j) =>
        finite(p.matrix_delta?.[i]?.[j]) ? Number(p.matrix_delta[i][j]) : 0));
  }
  const shift = regimeShift(p);
  if (statusEl) {
    if (shift) {
      statusEl.textContent = `⚠ Δρ ${shift.label} ${shift.delta >= 0 ? '+' : ''}${shift.delta.toFixed(2)}`;
      statusEl.className = 'badge warn';
      statusEl.title = `${shift.label}: baseline ${shift.base.toFixed(2)} → rolling ${shift.short.toFixed(2)}. ${shift.meaning}`;
    } else {
      statusEl.textContent = `● ROLLING 5M · ${p.dynamic_pairs || '—'} ПАР`;
      statusEl.className = 'badge live';
      statusEl.title = 'Rolling 5m correlations versus a 3-month daily baseline. Refresh: 5 minutes.';
    }
  }
}

function colorFor(v, alphaBase = 0.14) {
  const a = alphaBase + Math.min(1, Math.abs(v)) * 0.72;
  if (v >= 0) return `rgba(232,98,42,${a})`;
  return `rgba(62,118,186,${a})`;
}

function signalLine(p) {
  const shift = regimeShift(p);
  if (!shift) {
    return {
      text: 'РЕЖИМ СТАБИЛЕН · смотрите верх: rolling ρ · низ: Δρ к baseline',
      color: '#2E7D4F', bg: 'rgba(46,125,79,0.07)',
    };
  }
  return {
    text: `${shift.label}: ${shift.base >= 0 ? '+' : ''}${shift.base.toFixed(2)} → `
      + `${shift.short >= 0 ? '+' : ''}${shift.short.toFixed(2)} · ${shift.meaning}`,
    color: Math.abs(shift.delta) >= 0.35 ? '#C6373C' : '#A86E00',
    bg: Math.abs(shift.delta) >= 0.35 ? 'rgba(198,55,60,0.09)' : 'rgba(232,160,42,0.09)',
  };
}

let last = performance.now();
function renderLoop(now) {
  requestAnimationFrame(renderLoop);
  const dt = Math.min((now - last) / 1000, 0.05);
  last = now;
  if (!payload || !canvas || canvas.style.display === 'none') return;

  const { ctx, w, h } = setupCanvas(canvas, 290);
  ctx.clearRect(0, 0, w, h);
  const targetShort = payload.matrix_short || payload.matrix;
  const targetDelta = payload.matrix_delta || [];
  const effective = payload.matrix || targetShort;
  const n = targetShort.length;
  const assets = payload.assets;
  const signal = signalLine(payload);

  ctx.fillStyle = signal.bg;
  ctx.beginPath(); ctx.roundRect(4, 4, w - 8, 24, 4); ctx.fill();
  ctx.fillStyle = signal.color;
  ctx.font = 'bold 8.5px "IBM Plex Mono",monospace';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(signal.text, w / 2, 16);

  const padX = 48, padY = 42, legH = 24;
  const gridH = h - padY - legH - 2;
  const cellW = (w - padX) / n, cellH = gridH / n;
  ctx.textBaseline = 'middle';

  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const targetS = finite(targetShort?.[i]?.[j])
        ? Number(targetShort[i][j]) : Number(effective?.[i]?.[j] || 0);
      const targetD = finite(targetDelta?.[i]?.[j]) ? Number(targetDelta[i][j]) : 0;
      state.short[i][j] = approach(state.short[i][j], targetS, dt, 4);
      state.delta[i][j] = approach(state.delta[i][j], targetD, dt, 4);
      const x = padX + j * cellW, y = padY + i * cellH;

      if (i === j) {
        ctx.fillStyle = 'rgba(20,20,15,0.86)';
        ctx.beginPath(); ctx.roundRect(x + 2, y + 2, cellW - 4, cellH - 4, 3); ctx.fill();
        ctx.fillStyle = '#fff'; ctx.font = 'bold 8px "IBM Plex Mono",monospace';
        ctx.textAlign = 'center'; ctx.fillText(assets[i], x + cellW / 2, y + cellH / 2 - 3);
        const obs = payload.observations_short?.[i];
        if (obs != null) {
          ctx.fillStyle = 'rgba(255,255,255,0.58)';
          ctx.font = '6.5px "IBM Plex Mono",monospace';
          ctx.fillText(`n${obs}`, x + cellW / 2, y + cellH / 2 + 7);
        }
        continue;
      }

      const upper = i < j;
      const available = upper ? finite(targetShort?.[i]?.[j]) : finite(targetDelta?.[i]?.[j]);
      const val = upper ? state.short[i][j] : state.delta[i][j];
      ctx.fillStyle = available ? colorFor(val, upper ? 0.12 : 0.08) : 'rgba(138,135,125,0.10)';
      ctx.beginPath(); ctx.roundRect(x + 2, y + 2, cellW - 4, cellH - 4, 3); ctx.fill();
      if (!upper && available && Math.abs(val) >= 0.25) {
        ctx.strokeStyle = 'rgba(222,170,30,0.9)'; ctx.lineWidth = 1.4;
        ctx.strokeRect(x + 2.5, y + 2.5, cellW - 5, cellH - 5);
      }
      ctx.fillStyle = available
        ? (Math.abs(val) > 0.35 ? 'rgba(255,255,255,0.94)' : '#2A2925')
        : '#8A877D';
      ctx.textAlign = 'center';
      ctx.font = `${Math.abs(val) > 0.45 ? 'bold ' : ''}7.5px "IBM Plex Mono",monospace`;
      ctx.fillText(available ? `${val >= 0 ? '+' : ''}${val.toFixed(2)}` : '—',
        x + cellW / 2, y + cellH / 2 + 2);
      ctx.font = '6px "IBM Plex Mono",monospace';
      ctx.fillStyle = available ? 'rgba(20,20,15,0.58)' : '#8A877D';
      ctx.fillText(upper ? 'ρ5m' : 'Δρ', x + cellW / 2, y + 8);
    }

    ctx.fillStyle = '#6F6C65'; ctx.font = '8px "IBM Plex Mono",monospace';
    ctx.textAlign = 'right';
    ctx.fillText(assets[i], padX - 5, padY + i * cellH + cellH / 2);
    ctx.save();
    ctx.translate(padX + i * cellW + cellW / 2, padY - 5);
    ctx.rotate(-Math.PI / 5);
    ctx.textAlign = 'left'; ctx.fillText(assets[i], 0, 0);
    ctx.restore();
  }

  const ly = h - legH + 2;
  ctx.font = '7px "IBM Plex Mono",monospace'; ctx.textAlign = 'left';
  ctx.fillStyle = '#14140F';
  ctx.fillText('ВЕРХ: ρ5m rolling', padX, ly + 6);
  ctx.fillText('НИЗ: Δρ = rolling − 3mo baseline', padX + 112, ly + 6);
  ctx.fillStyle = '#A86E00';
  ctx.fillText('рамка: |Δρ| ≥ 0.25', padX + 310, ly + 6);
  const grad = ctx.createLinearGradient(padX, 0, padX + 100, 0);
  grad.addColorStop(0, 'rgba(62,118,186,0.85)');
  grad.addColorStop(0.5, 'rgba(138,135,125,0.12)');
  grad.addColorStop(1, 'rgba(232,98,42,0.85)');
  ctx.fillStyle = grad; ctx.fillRect(padX, ly + 12, 100, 7);
  ctx.fillStyle = '#8A877D';
  ctx.fillText('− обратная', padX + 106, ly + 16);
  ctx.fillText('+ прямая / рост Δ', padX + 176, ly + 16);
}
