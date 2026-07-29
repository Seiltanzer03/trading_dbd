// Session Volume/TPO profile.
//
// The grid is anchored by the backend to a stable ATR-based bin size. The
// frontend keeps an expanding session domain and a smoothed 95th-percentile
// scale, so a new extreme or one oversized POC bin cannot make every bar jump.

import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl;
let payload = null;
const state = {
  bins: new Map(),
  poc: null,
  isTpo: false,
  valueAreaLow: null,
  valueAreaHigh: null,
  domainLo: null,
  domainHi: null,
  targetScale: 1,
  currentScale: 1,
  binSize: null,
};

function key(price) {
  return Number(price).toFixed(8);
}

export function initVp() {
  canvas = $('#vp-canvas');
  emptyEl = $('#vp-empty');
  if (!canvas) return;
  requestAnimationFrame(renderLoop);
}

function resetState() {
  state.bins = new Map();
  state.domainLo = null;
  state.domainHi = null;
  state.currentScale = 1;
  state.targetScale = 1;
}

export function updateVp(p) {
  if (!p || !p.bins || p.bins.length === 0) {
    payload = null;
    if (emptyEl) emptyEl.style.display = 'flex';
    if (canvas) canvas.style.display = 'none';
    return;
  }
  payload = p;
  if (emptyEl) emptyEl.style.display = 'none';
  if (canvas) canvas.style.display = 'block';

  const prices = p.bins.map((b) => Number(b.price)).filter(Number.isFinite);
  const step = Number(p.bin_size) > 0
    ? Number(p.bin_size)
    : Math.max((Math.max(...prices) - Math.min(...prices)) / Math.max(prices.length, 1), 1e-9);
  const desiredLo = Math.min(...prices) - step / 2;
  const desiredHi = Math.max(...prices) + step / 2;
  const disjoint = state.domainLo != null
    && (desiredHi < state.domainLo || desiredLo > state.domainHi);
  const gridChanged = state.binSize != null
    && Math.abs(step - state.binSize) > Math.max(step, state.binSize) * 0.01;
  if (disjoint || gridChanged) resetState();
  state.binSize = step;
  state.domainLo = state.domainLo == null ? desiredLo : Math.min(state.domainLo, desiredLo);
  state.domainHi = state.domainHi == null ? desiredHi : Math.max(state.domainHi, desiredHi);

  const seen = new Set();
  for (const b of p.bins) {
    const k = key(b.price);
    seen.add(k);
    const volume = Math.max(0, Number(b.volume) || 0);
    const existing = state.bins.get(k);
    if (existing) {
      existing.targetVol = volume;
      existing.targetBidVol = Number(b.bid_vol) || (volume * 0.5);
      existing.targetAskVol = Number(b.ask_vol) || (volume * 0.5);
      existing.price = Number(b.price);
    } else {
      state.bins.set(k, {
        price: Number(b.price), 
        targetVol: volume,
        targetBidVol: Number(b.bid_vol) || (volume * 0.5),
        targetAskVol: Number(b.ask_vol) || (volume * 0.5),
        currentVol: volume * 0.65,
        currentBidVol: (Number(b.bid_vol) || (volume * 0.5)) * 0.65,
        currentAskVol: (Number(b.ask_vol) || (volume * 0.5)) * 0.65,
      });
    }
  }
  // Retain missing bins briefly and fade them instead of tearing down the grid.
  for (const [k, b] of state.bins.entries()) {
    if (!seen.has(k)) b.targetVol = 0;
  }

  const vols = p.bins.map((b) => Math.max(0, Number(b.volume) || 0))
    .sort((a, b) => a - b);
  const q95 = vols[Math.min(vols.length - 1, Math.floor((vols.length - 1) * 0.95))] || 1;
  state.targetScale = Math.max(q95, 1e-9);
  if (!(state.currentScale > 0)) state.currentScale = state.targetScale;
  state.poc = Number(p.poc);
  state.isTpo = !!p.is_tpo;
  state.valueAreaLow = Number.isFinite(Number(p.value_area_low)) ? Number(p.value_area_low) : null;
  state.valueAreaHigh = Number.isFinite(Number(p.value_area_high)) ? Number(p.value_area_high) : null;
}

let last = performance.now();
function renderLoop(now) {
  requestAnimationFrame(renderLoop);
  const dt = Math.min((now - last) / 1000, 0.05);
  last = now;
  if (!payload || !canvas || canvas.style.display === 'none') return;

  const { ctx, w, h } = setupCanvas(canvas, 190);
  ctx.clearRect(0, 0, w, h);
  if (!state.bins.size || !(state.domainHi > state.domainLo)) return;

  state.currentScale = approach(state.currentScale, state.targetScale, dt, 4);
  const padY = 10;
  const getY = (price) =>
    h - padY - ((price - state.domainLo) / (state.domainHi - state.domainLo)) * (h - padY * 2);
  const binH = Math.max(2, Math.abs(getY(state.domainLo) - getY(state.domainLo + state.binSize)));
  const maxW = w - 10;

  // Accepted 70% area: stable context before individual bars.
  if (state.valueAreaLow != null && state.valueAreaHigh != null) {
    const yTop = getY(state.valueAreaHigh + state.binSize / 2);
    const yBottom = getY(state.valueAreaLow - state.binSize / 2);
    ctx.fillStyle = 'rgba(46,125,79,0.045)';
    ctx.fillRect(0, yTop, w, Math.max(1, yBottom - yTop));
    ctx.strokeStyle = 'rgba(46,125,79,0.24)';
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(0, yTop); ctx.lineTo(w, yTop);
    ctx.moveTo(0, yBottom); ctx.lineTo(w, yBottom); ctx.stroke();
    ctx.setLineDash([]);
  }

  const ordered = [...state.bins.values()].sort((a, b) => a.price - b.price);
  for (const b of ordered) {
    b.currentVol = approach(b.currentVol, b.targetVol, dt, 4.5);
    b.currentBidVol = approach(b.currentBidVol, b.targetBidVol, dt, 4.5);
    b.currentAskVol = approach(b.currentAskVol, b.targetAskVol, dt, 4.5);
    
    if (b.targetVol === 0 && b.currentVol < state.currentScale * 0.001) {
      state.bins.delete(key(b.price));
      continue;
    }
    const ratio = Math.max(0, Math.min(1, b.currentVol / Math.max(state.currentScale, 1e-9)));
    const bidRatio = Math.max(0, Math.min(1, b.currentBidVol / Math.max(state.currentScale, 1e-9)));
    const askRatio = Math.max(0, Math.min(1, b.currentAskVol / Math.max(state.currentScale, 1e-9)));
    
    const barW = Math.max(1, ratio * maxW);
    const bidW = bidRatio * maxW;
    const askW = askRatio * maxW;
    
    const y = getY(b.price);
    const isPoc = Math.abs(b.price - state.poc) <= state.binSize * 0.1;
    
    if (isPoc) {
      ctx.shadowColor = 'rgba(232,98,42,0.42)';
      ctx.shadowBlur = 7;
      ctx.fillStyle = 'rgba(232,98,42,0.84)';
      ctx.beginPath();
      ctx.roundRect(0, y - binH / 2, barW, Math.max(1, binH - 1), [0, 3, 3, 0]);
      ctx.fill();
      ctx.shadowBlur = 0;
    } else {
      if (bidW > 0) {
        ctx.fillStyle = 'rgba(198,55,60,0.52)'; // Reddish for bid volume (sellers)
        ctx.beginPath();
        ctx.rect(0, y - binH / 2, bidW, Math.max(1, binH - 1));
        ctx.fill();
      }
      if (askW > 0) {
        ctx.fillStyle = 'rgba(46,125,79,0.52)'; // Greenish for ask volume (buyers)
        ctx.beginPath();
        ctx.roundRect(bidW, y - binH / 2, askW, Math.max(1, binH - 1), [0, 3, 3, 0]);
        ctx.fill();
      }
    }
  }

  if (state.poc != null) {
    const y = getY(state.poc);
    ctx.strokeStyle = 'rgba(232,98,42,0.55)';
    ctx.setLineDash([3, 2]);
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#E8622A';
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`POC${state.isTpo ? ' · TPO' : ' · VOL'}`, 4, Math.max(9, y - 4));
  }
}
