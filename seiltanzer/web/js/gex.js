// OI × GAMMA CONTEXT — локальный SVG без зависимости от внешнего CDN.
// Панель остаётся рабочей даже когда ECharts/CDN недоступны.

import { $ } from './util.js';

let emptyEl;
let statusEl;
let data = null;
let liveData = { price: 0, proxyPrice: 0, trade: null };
let resizeObserver = null;
let lastRenderKey = '';

export function initGex() {
  emptyEl = $('#gex-evol-empty');
  statusEl = $('#gex-evol-status');
  const el = $('#gex-evol-canvas');
  if (el && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      if (data) renderGex(true);
    });
    resizeObserver.observe(el);
  }
}

function showEmpty(message, status) {
  data = null;
  const el = $('#gex-evol-canvas');
  if (el) el.replaceChildren();
  if (emptyEl) {
    emptyEl.style.display = 'flex';
    emptyEl.textContent = message;
  }
  if (statusEl) statusEl.textContent = status;
  const interpretEl = $('#gex-interpretation');
  if (interpretEl) interpretEl.style.display = 'none';
}

export function updateGex(ridgePayload) {
  if (!ridgePayload?.available || !ridgePayload.snapshots?.length) {
    showEmpty('○ OI × GAMMA КОНТЕКСТ НЕДОСТУПЕН', '○ GEX НЕДОСТУПЕН');
    return;
  }

  const latest = ridgePayload.snapshots.at(-1);
  if (!latest?.gex?.available || !latest.gex.strikes?.length || !latest.gex.net?.length) {
    showEmpty('○ GEX КОНТЕКСТ ОТКЛЮЧЁН ДЛЯ ЭТОГО PROXY', '○ GEX CONTEXT ONLY');
    return;
  }

  data = {
    scale: Number(ridgePayload.scale) || 1,
    price: Number(ridgePayload.price) || 0,
    proxyPrice: Number(ridgePayload.proxy_spot_current) || 0,
    transform: ridgePayload.proxy_transform || 'direct',
    instrument: ridgePayload.instrument || null,
    latest: latest.gex,
    oiWalls: ridgePayload.oi_walls || null,
    zeroFlip: Number(latest.gex.zero_flip) || null,
    top: latest.gex.top,
  };

  if (emptyEl) emptyEl.style.display = 'none';
  if (statusEl) statusEl.textContent = '● OI-GEX · LOCAL SVG';
  renderGex(true);
}

export function updateLiveGex(live) {
  if (live.price !== undefined) liveData.price = Number(live.price) || 0;
  if (live.proxyPrice !== undefined) liveData.proxyPrice = Number(live.proxyPrice) || 0;
  if (live.trade !== undefined) liveData.trade = live.trade;
  if (data) renderGex(false);
}

function fmtVal(value) {
  const v = Number(value) || 0;
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toFixed(0);
}

function escapeXml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function clamp(value, lo, hi) {
  return Math.max(lo, Math.min(hi, value));
}

function prepareVisible() {
  const instrumentFactor = liveData.price && data.price ? liveData.price / data.price : 1;
  const proxyFactor = liveData.proxyPrice && data.proxyPrice ? liveData.proxyPrice / data.proxyPrice : 1;
  const liveMap = data.transform === 'inverse'
    ? instrumentFactor * proxyFactor
    : instrumentFactor / proxyFactor;

  const strikes = data.latest.strikes.map((s) => Number(s) * data.scale * liveMap);
  const nets = data.latest.net.map(Number);
  const price = liveData.price || data.price || 0;
  const pairs = [];
  for (let i = 0; i < Math.min(strikes.length, nets.length); i++) {
    if (Number.isFinite(strikes[i]) && Number.isFinite(nets[i]) && Math.abs(nets[i]) > 0) {
      pairs.push({ strike: strikes[i], net: nets[i] });
    }
  }
  if (!pairs.length) return { pairs: [], price, liveMap };

  const absValues = pairs.map((p) => Math.abs(p.net)).sort((a, b) => a - b);
  const q25 = absValues[Math.floor((absValues.length - 1) * 0.25)] || 0;
  const q75 = absValues[Math.floor((absValues.length - 1) * 0.75)] || 1;
  const threshold = Math.max(q75 + (q75 - q25) * 3, q75 || 1);
  for (const pair of pairs) {
    pair.clamped = clamp(pair.net, -threshold, threshold);
    pair.isOutlier = Math.abs(pair.net) > threshold;
  }
  pairs.sort((a, b) => a.strike - b.strike);

  let closest = 0;
  let minDiff = Infinity;
  for (let i = 0; i < pairs.length; i++) {
    const diff = Math.abs(pairs[i].strike - price);
    if (diff < minDiff) {
      minDiff = diff;
      closest = i;
    }
  }

  const maxRows = 25;
  let start = Math.max(0, closest - Math.floor(maxRows / 2));
  let end = Math.min(pairs.length, start + maxRows);
  start = Math.max(0, end - maxRows);
  return { pairs: pairs.slice(start, end), price, liveMap };
}

function markerY(value, visible, top, plotH) {
  if (!Number.isFinite(value) || !visible.length) return null;
  const lo = visible[0].strike;
  const hi = visible.at(-1).strike;
  if (value < lo || value > hi || hi === lo) return null;
  return top + plotH - ((value - lo) / (hi - lo)) * plotH;
}

function renderGex(force = false) {
  const el = $('#gex-evol-canvas');
  if (!el || !data) return;

  const { pairs: visible, price, liveMap } = prepareVisible();
  if (!visible.length) {
    el.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#8A877D;font:11px IBM Plex Mono,monospace;letter-spacing:.08em">GEX: все значения равны нулю</div>';
    return;
  }

  const width = Math.max(520, Math.round(el.clientWidth || 820));
  const height = Math.max(360, Math.round(el.clientHeight || 420));
  const renderKey = [width, height, price.toFixed(3), liveMap.toFixed(6),
    visible[0].strike.toFixed(3), visible.at(-1).strike.toFixed(3),
    visible.map((p) => p.net.toFixed(0)).join(',')].join('|');
  if (!force && renderKey === lastRenderKey) return;
  lastRenderKey = renderKey;

  const margin = { left: 74, right: 82, top: 22, bottom: 32 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const centerX = margin.left + plotW / 2;
  const maxAbs = Math.max(...visible.map((p) => Math.abs(p.clamped)), 1);
  const xScale = (value) => centerX + (value / maxAbs) * (plotW / 2 - 12);
  const rowH = plotH / visible.length;
  const keyLevels = [...visible]
    .sort((a, b) => Math.abs(b.net) - Math.abs(a.net))
    .slice(0, 5);
  const keyThreshold = Math.abs(keyLevels.at(-1)?.net || Infinity);

  const svg = [];
  svg.push(`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="OI × Gamma context">`);
  svg.push('<rect width="100%" height="100%" fill="#fff"/>');

  for (let i = -2; i <= 2; i++) {
    const x = centerX + i * plotW / 4;
    svg.push(`<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${height - margin.bottom}" stroke="${i === 0 ? '#8A877D' : '#EEECE4'}" stroke-width="${i === 0 ? 1.3 : 1}"/>`);
    const val = maxAbs * i / 2;
    svg.push(`<text x="${x}" y="${height - 10}" text-anchor="middle" font-size="9" fill="#8A877D" font-family="IBM Plex Mono,monospace">${escapeXml(fmtVal(val))}</text>`);
  }

  visible.forEach((p, index) => {
    const y = margin.top + plotH - (index + 0.5) * rowH;
    const x = xScale(p.clamped);
    const barX = Math.min(centerX, x);
    const barW = Math.max(1, Math.abs(x - centerX));
    const color = p.net > 0 ? '#2E7D4F' : '#C6373C';
    const opacity = p.isOutlier ? 0.95 : 0.72;
    svg.push(`<text x="${margin.left - 8}" y="${y + 3}" text-anchor="end" font-size="9" fill="#6F6C64" font-family="IBM Plex Mono,monospace">${p.strike.toFixed(1)}</text>`);
    svg.push(`<rect x="${barX}" y="${y - Math.max(2, rowH * 0.32)}" width="${barW}" height="${Math.max(4, rowH * 0.64)}" rx="1" fill="${color}" fill-opacity="${opacity}"${p.isOutlier ? ` stroke="${color}" stroke-width="2"` : ''}>`);
    const distance = price ? ((p.strike - price) / price * 100).toFixed(2) : '—';
    svg.push(`<title>Strike ${p.strike.toFixed(1)} · Net GEX ${escapeXml(fmtVal(p.net))} · до цены ${distance}%</title></rect>`);
    if (Math.abs(p.net) >= keyThreshold) {
      const labelX = p.net > 0 ? x + 5 : x - 5;
      const anchor = p.net > 0 ? 'start' : 'end';
      svg.push(`<text x="${labelX}" y="${y + 3}" text-anchor="${anchor}" font-size="9" font-weight="600" fill="#45433E" font-family="IBM Plex Mono,monospace">${escapeXml(fmtVal(p.net))}${p.isOutlier ? ' ⚠' : ''}</text>`);
    }
  });

  const markers = [];
  if (price > 0) markers.push({ value: price, label: `PRICE ${price.toFixed(1)}`, color: '#E8622A', dash: '' });
  if (data.zeroFlip) markers.push({ value: data.zeroFlip * data.scale * liveMap, label: 'FLIP', color: '#7C4D9E', dash: '5 3' });
  const trade = liveData.trade;
  if (trade?.entry) markers.push({ value: Number(trade.entry), label: 'ENTRY', color: '#8A877D', dash: '3 3' });
  if (trade?.stop) markers.push({ value: Number(trade.stop), label: 'STOP', color: '#C6373C', dash: '' });
  if (trade?.take) markers.push({ value: Number(trade.take), label: 'TAKE', color: '#2E7D4F', dash: '' });
  for (const marker of markers) {
    const y = markerY(marker.value, visible, margin.top, plotH);
    if (y == null) continue;
    svg.push(`<line x1="${margin.left}" y1="${y}" x2="${width - margin.right}" y2="${y}" stroke="${marker.color}" stroke-width="${marker.label.startsWith('PRICE') ? 2 : 1.4}"${marker.dash ? ` stroke-dasharray="${marker.dash}"` : ''}/>`);
    svg.push(`<text x="${width - margin.right + 6}" y="${y + 3}" text-anchor="start" font-size="9" font-weight="600" fill="${marker.color}" font-family="IBM Plex Mono,monospace">${escapeXml(marker.label)}</text>`);
  }

  svg.push(`<text x="${margin.left}" y="13" text-anchor="start" font-size="9" fill="#8A877D" font-family="IBM Plex Mono,monospace">PUT / NEGATIVE GEX</text>`);
  svg.push(`<text x="${width - margin.right}" y="13" text-anchor="end" font-size="9" fill="#8A877D" font-family="IBM Plex Mono,monospace">CALL / POSITIVE GEX</text>`);
  svg.push('</svg>');
  el.innerHTML = svg.join('');

  const sorted = [...visible].sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
  const biggestCall = sorted.find((p) => p.net > 0);
  const biggestPut = sorted.find((p) => p.net < 0);
  const interpretEl = $('#gex-interpretation');
  if (interpretEl) {
    const parts = [];
    const flip = data.zeroFlip ? data.zeroFlip * data.scale * liveMap : null;
    if (flip) parts.push(`<b>Цена ${price > flip ? 'выше' : 'ниже'} gamma flip ${flip.toFixed(1)}</b>`);
    if (biggestCall) parts.push(`🟢 CALL GEX: ${biggestCall.strike.toFixed(1)} (${fmtVal(biggestCall.net)})`);
    if (biggestPut) parts.push(`🔴 PUT GEX: ${biggestPut.strike.toFixed(1)} (${fmtVal(biggestPut.net)})`);
    const callWall = Number(data.oiWalls?.call_wall);
    const putWall = Number(data.oiWalls?.put_wall);
    const walls = [];
    if (Number.isFinite(callWall)) walls.push(`CALL ${(callWall * data.scale * liveMap).toFixed(1)}`);
    if (Number.isFinite(putWall)) walls.push(`PUT ${(putWall * data.scale * liveMap).toFixed(1)}`);
    if (walls.length) parts.push(`📌 MAX OI: ${walls.join(' · ')}`);
    interpretEl.innerHTML = parts.join('<br>');
    interpretEl.style.display = parts.length ? 'block' : 'none';
  }
}
