import { $ } from './util.js';

let emptyEl;
let statusEl;
let data = null;
let migrationData = null;
let liveData = { price: 0, proxyPrice: 0, trade: null };
let currentMode = 'MIGRATION';
let resizeObserver = null;
let lastSnapshotKey = '';

export function initGex() {
  emptyEl = $('#gex-evol-empty');
  statusEl = $('#gex-evol-status');
  $('#btn-gex-migration')?.addEventListener('click', () => setMode('MIGRATION'));
  $('#btn-gex-snapshot')?.addEventListener('click', () => setMode('SNAPSHOT'));
  const el = $('#gex-evol-canvas');
  if (el && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => renderGex(true));
    resizeObserver.observe(el);
  }
}

function setMode(mode) {
  currentMode = mode;
  $('#btn-gex-migration')?.classList.toggle('active', mode === 'MIGRATION');
  $('#btn-gex-snapshot')?.classList.toggle('active', mode === 'SNAPSHOT');
  renderGex(true);
}

function showEmpty(message, status) {
  const el = $('#gex-evol-canvas');
  if (el) el.replaceChildren();
  if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = message; }
  if (statusEl) statusEl.textContent = status;
  $('#gex-interpretation') && ($('#gex-interpretation').style.display = 'none');
}

export async function updateGex(ridgePayload) {
  if (!ridgePayload?.available || !ridgePayload.snapshots?.length) {
    data = null; migrationData = null;
    showEmpty('○ OI × GAMMA КОНТЕКСТ НЕДОСТУПЕН', '○ GEX НЕДОСТУПЕН');
    return;
  }
  const latest = ridgePayload.snapshots.at(-1);
  if (!latest?.gex?.available || !latest.gex.strikes?.length || !latest.gex.net?.length) {
    data = null; migrationData = null;
    showEmpty('○ GEX КОНТЕКСТ ОТКЛЮЧЁН ДЛЯ ЭТОГО PROXY', '○ GEX CONTEXT ONLY');
    return;
  }
  data = {
    scale: Number(ridgePayload.scale) || 1,
    price: Number(ridgePayload.price) || 0,
    proxyPrice: Number(ridgePayload.proxy_spot_current) || 0,
    transform: ridgePayload.proxy_transform || 'direct',
    latest: latest.gex,
    oiWalls: ridgePayload.oi_walls || null,
    zeroFlip: Number(latest.gex.zero_flip) || null,
  };
  liveData.trade = ridgePayload.trade || liveData.trade;

  try {
    const res = await fetch('/api/analytics/gex-migration', { cache: 'no-store' });
    migrationData = res.ok ? await res.json() : null;
  } catch (err) {
    console.warn('GEX migration fetch failed:', err);
    migrationData = null;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  renderGex(true);
}

export function updateLiveGex(live) {
  if (live.price !== undefined) liveData.price = Number(live.price) || 0;
  if (live.proxyPrice !== undefined) liveData.proxyPrice = Number(live.proxyPrice) || 0;
  if (live.trade !== undefined) liveData.trade = live.trade;
  renderGex(false);
}

function renderGex(force = false) {
  if (currentMode === 'MIGRATION' && migrationData?.available) renderMigrationMap();
  else renderSnapshotBarChart(force);
}

function fmtVal(value) {
  const v = Number(value) || 0;
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toFixed(0);
}

function fmtMigration(v, suffix = '/6h') {
  if (v == null || !Number.isFinite(Number(v))) return '— BUILDING';
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${n.toFixed(1)} ${suffix}`;
}

function fmtUtc(ts) {
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

function percentile(values, q) {
  const a = values.filter((v) => Number.isFinite(v)).sort((x, y) => x - y);
  if (!a.length) return 1;
  const pos = Math.max(0, Math.min(a.length - 1, Math.round((a.length - 1) * q)));
  return a[pos] || 1;
}

function updateMigrationSummary(summary) {
  if ($('#gex-sum-regime')) $('#gex-sum-regime').textContent = summary.gamma_regime || 'UNKNOWN';
  if ($('#gex-sum-flip')) $('#gex-sum-flip').textContent = summary.flip?.price == null ? '—' : Number(summary.flip.price).toFixed(1);
  if ($('#gex-sum-flip-mig')) $('#gex-sum-flip-mig').textContent = fmtMigration(summary.flip?.migration_6h);
  if ($('#gex-sum-call')) $('#gex-sum-call').textContent = summary.call_wall?.price == null ? '—' : Number(summary.call_wall.price).toFixed(1);
  if ($('#gex-sum-call-mig')) $('#gex-sum-call-mig').textContent = fmtMigration(summary.call_wall?.migration_6h);
  if ($('#gex-sum-put')) $('#gex-sum-put').textContent = summary.put_wall?.price == null ? '—' : Number(summary.put_wall.price).toFixed(1);
  if ($('#gex-sum-put-mig')) $('#gex-sum-put-mig').textContent = fmtMigration(summary.put_wall?.migration_6h);

  const path = $('#gex-sum-path');
  if (path) {
    path.textContent = summary.take_path || 'NO DATA';
    const obstructed = String(summary.take_path || '').includes('OBSTRUCTED');
    const mixed = String(summary.take_path || '').includes('MIXED');
    path.style.background = obstructed ? '#c6373c' : mixed ? '#b47c2d' : '#2e7d4f';
    path.style.color = '#fff';
  }
  const pressure = $('#gex-sum-pressure');
  if (pressure) {
    const p = Number(summary.path_pressure || 0);
    pressure.textContent = `${p > 0 ? '+' : ''}${p.toFixed(2)}`;
    pressure.style.color = p > 0 ? '#2e7d4f' : p < 0 ? '#c6373c' : '#45433e';
  }
  if (statusEl) {
    const n = summary.snapshot_count ?? migrationData?.timestamps?.length ?? 0;
    const h = summary.history_hours == null ? '' : ` · ${Number(summary.history_hours).toFixed(1)}H`;
    statusEl.textContent = `● GEX MIGRATION · ${n} SNAP${h}`;
  }

  const card = $('#gex-summary-card');
  if (card && typeof document.createElement === 'function') {
    let meta = $('#gex-migration-meta');
    if (!meta) {
      meta = document.createElement('div');
      meta.id = 'gex-migration-meta';
      meta.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:7px;margin-top:7px;font-size:10px;color:#706d65;line-height:1.7';
      card.appendChild?.(meta);
    }
    if (meta) {
      const cp = Number(summary.call_wall?.persistence || 0) * 100;
      const pp = Number(summary.put_wall?.persistence || 0) * 100;
      meta.innerHTML = `SNAPSHOTS ${summary.snapshot_count ?? '—'} · HISTORY ${summary.history_hours == null ? '—' : `${Number(summary.history_hours).toFixed(1)}h`}<br>WALL PERSISTENCE · CALL ${cp.toFixed(0)}% · PUT ${pp.toFixed(0)}%`;
    }
  }
}

function renderMigrationMap() {
  const el = $('#gex-evol-canvas');
  if (!el || !migrationData?.price_grid?.length) return;
  if (emptyEl) emptyEl.style.display = 'none';
  const summary = migrationData.summary || {};
  updateMigrationSummary(summary);

  let cv = el.querySelector('canvas');
  if (!cv) {
    el.innerHTML = '<canvas style="width:100%;height:100%;display:block"></canvas>';
    cv = el.querySelector('canvas');
  }
  const rect = el.getBoundingClientRect();
  const width = Math.max(520, Math.floor(rect.width || 850));
  const height = Math.max(340, Math.floor(rect.height || 420));
  cv.width = width; cv.height = height;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, width, height);

  const margin = { left: 70, right: 105, top: 22, bottom: 42 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const prices = migrationData.price_grid;
  const timestamps = migrationData.timestamps || [];
  const heat = migrationData.heatmap || [];
  if (!timestamps.length || !heat.length) return;

  const range = migrationData.plot_range || [prices[0], prices.at(-1)];
  const pMin = Number(range[0]);
  const pMax = Number(range[1]);
  const tMin = Number(timestamps[0]);
  const tMax = Number(timestamps.at(-1));
  const X = (ts) => margin.left + ((Number(ts) - tMin) / Math.max(1, tMax - tMin)) * plotW;
  const Y = (p) => margin.top + plotH - ((Number(p) - pMin) / Math.max(1e-9, pMax - pMin)) * plotH;

  const abs = [];
  heat.forEach((row) => row.forEach((v) => { if (Math.abs(Number(v)) > 0) abs.push(Math.abs(Number(v))); }));
  const scale = Math.max(percentile(abs, 0.98), 1e-12);
  const cellW = Math.max(2, plotW / Math.max(1, timestamps.length));
  const cellH = plotH / Math.max(1, prices.length);
  for (let r = 0; r < prices.length; r++) {
    const y = Y(prices[r]);
    for (let c = 0; c < timestamps.length; c++) {
      const v = Number(heat[r]?.[c] || 0);
      if (!v) continue;
      const norm = Math.sqrt(Math.min(1, Math.abs(v) / scale));
      const alpha = 0.08 + 0.82 * norm;
      ctx.fillStyle = v > 0 ? `rgba(46,125,79,${alpha})` : `rgba(198,55,60,${alpha})`;
      ctx.fillRect(X(timestamps[c]) - cellW / 2, y - cellH / 2, cellW + 1, Math.max(2, cellH + 1));
    }
  }

  ctx.font = '9px IBM Plex Mono, monospace';
  ctx.strokeStyle = 'rgba(80,78,72,.16)'; ctx.fillStyle = '#6f6c64';
  for (let i = 0; i <= 6; i++) {
    const p = pMin + i * (pMax - pMin) / 6;
    const y = Y(p);
    ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(margin.left + plotW, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(p.toFixed(1), margin.left - 7, y);
  }
  const xLabels = Math.min(5, timestamps.length);
  for (let i = 0; i < xLabels; i++) {
    const idx = xLabels === 1 ? 0 : Math.round(i * (timestamps.length - 1) / (xLabels - 1));
    const x = X(timestamps[idx]);
    ctx.beginPath(); ctx.moveTo(x, margin.top); ctx.lineTo(x, margin.top + plotH); ctx.stroke();
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillText(fmtUtc(timestamps[idx]), x, margin.top + plotH + 8);
  }

  function drawTrajectory(points, color, dash, label) {
    const valid = (points || []).filter((p) => p.price != null && Number.isFinite(Number(p.price)) && Number(p.price) >= pMin && Number(p.price) <= pMax);
    if (!valid.length) return;
    const strokePath = (stroke, w) => {
      ctx.save(); ctx.strokeStyle = stroke; ctx.lineWidth = w; ctx.setLineDash(dash || []); ctx.beginPath();
      valid.forEach((p, i) => { const x = X(p.ts), y = Y(p.price); if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y); });
      ctx.stroke(); ctx.restore();
    };
    strokePath('rgba(255,255,255,.8)', 5);
    strokePath(color, 2.2);
    const last = valid.at(-1);
    ctx.fillStyle = color; ctx.font = 'bold 9px IBM Plex Mono, monospace'; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
    ctx.fillText(`${label} ${Number(last.price).toFixed(1)}`, Math.min(X(last.ts) + 5, margin.left + plotW - 110), Y(last.price) - 3);
  }
  const traj = migrationData.trajectories || {};
  drawTrajectory(traj.call_wall, '#2e7d4f', [], 'CALL');
  drawTrajectory(traj.put_wall, '#c6373c', [], 'PUT');
  drawTrajectory(traj.flip, '#7c4d9e', [5, 4], 'FLIP');

  function marker(value, label, color, dash = []) {
    const v = Number(value);
    if (!Number.isFinite(v) || v < pMin || v > pMax) return;
    const y = Y(v);
    ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = label === 'PRICE' ? 2.3 : 1.25; ctx.setLineDash(dash);
    ctx.beginPath(); ctx.moveTo(margin.left, y); ctx.lineTo(margin.left + plotW, y); ctx.stroke(); ctx.restore();
    ctx.fillStyle = color; ctx.font = 'bold 9px IBM Plex Mono, monospace'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(`${label} ${v.toFixed(1)}`, margin.left + plotW + 6, y);
  }
  const currentPrice = liveData.price || summary.current_price || data?.price;
  marker(currentPrice, 'PRICE', '#e8622a');
  marker(liveData.trade?.entry, 'ENTRY', '#77736c', [3, 3]);
  marker(liveData.trade?.stop, 'STOP', '#c6373c');
  marker(liveData.trade?.take, 'TAKE', '#2e7d4f');
  ctx.strokeStyle = '#bdb9af'; ctx.strokeRect(margin.left, margin.top, plotW, plotH);
}

function prepareVisible() {
  if (!data) return { pairs: [], price: 0, liveMap: 1 };
  const instrumentFactor = liveData.price && data.price ? liveData.price / data.price : 1;
  const proxyFactor = liveData.proxyPrice && data.proxyPrice ? liveData.proxyPrice / data.proxyPrice : 1;
  const liveMap = data.transform === 'inverse' ? instrumentFactor * proxyFactor : instrumentFactor / proxyFactor;
  const strikes = data.latest.strikes.map((s) => Number(s) * data.scale * liveMap);
  const nets = data.latest.net.map(Number);
  const price = liveData.price || data.price || 0;
  const pairs = strikes.map((strike, i) => ({ strike, net: nets[i] }))
    .filter((p) => Number.isFinite(p.strike) && Number.isFinite(p.net) && p.net !== 0)
    .sort((a, b) => a.strike - b.strike);
  if (!pairs.length) return { pairs: [], price, liveMap };
  const av = pairs.map((p) => Math.abs(p.net)).sort((a, b) => a - b);
  const q25 = av[Math.floor((av.length - 1) * .25)] || 0;
  const q75 = av[Math.floor((av.length - 1) * .75)] || 1;
  const threshold = Math.max(q75 + 3 * (q75 - q25), q75, 1);
  pairs.forEach((p) => { p.clamped = clamp(p.net, -threshold, threshold); p.isOutlier = Math.abs(p.net) > threshold; });
  let closest = 0;
  pairs.forEach((p, i) => { if (Math.abs(p.strike - price) < Math.abs(pairs[closest].strike - price)) closest = i; });
  const maxRows = 25;
  let start = Math.max(0, closest - Math.floor(maxRows / 2));
  let end = Math.min(pairs.length, start + maxRows); start = Math.max(0, end - maxRows);
  return { pairs: pairs.slice(start, end), price, liveMap };
}

function renderSnapshotBarChart(force = false) {
  const el = $('#gex-evol-canvas');
  if (!el || !data) return;
  const { pairs: visible, price, liveMap } = prepareVisible();
  if (!visible.length) { showEmpty('○ GEX: НУЛЕВОЙ ПРОФИЛЬ', '○ GEX CONTEXT'); return; }
  if (emptyEl) emptyEl.style.display = 'none';
  if (statusEl) statusEl.textContent = '● OI-GEX SNAPSHOT';

  const width = Math.max(520, Math.round(el.clientWidth || el.getBoundingClientRect().width || 820));
  const height = Math.max(360, Math.round(el.clientHeight || el.getBoundingClientRect().height || 420));
  const key = [width, height, price.toFixed(2), liveMap.toFixed(5), visible.map((p) => `${p.strike}:${p.net}`).join('|')].join(':');
  if (!force && key === lastSnapshotKey) return;
  lastSnapshotKey = key;

  const margin = { left: 74, right: 90, top: 24, bottom: 32 };
  const plotW = width - margin.left - margin.right, plotH = height - margin.top - margin.bottom;
  const cx = margin.left + plotW / 2;
  const maxAbs = Math.max(...visible.map((p) => Math.abs(p.clamped)), 1);
  const xScale = (v) => cx + v / maxAbs * (plotW / 2 - 12);
  const rowH = plotH / visible.length;
  const strongest = [...visible].sort((a, b) => Math.abs(b.net) - Math.abs(a.net)).slice(0, 5);
  const threshold = Math.abs(strongest.at(-1)?.net || Infinity);
  const svg = [`<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="OI Gamma snapshot"><rect width="100%" height="100%" fill="#fff"/>`];
  for (let i = -2; i <= 2; i++) {
    const x = cx + i * plotW / 4;
    svg.push(`<line x1="${x}" y1="${margin.top}" x2="${x}" y2="${height - margin.bottom}" stroke="${i ? '#eeeae2' : '#8a877d'}"/>`);
  }
  visible.forEach((p, idx) => {
    const y = margin.top + plotH - (idx + .5) * rowH;
    const x = xScale(p.clamped), bx = Math.min(cx, x), bw = Math.max(1, Math.abs(x - cx));
    const color = p.net > 0 ? '#2e7d4f' : '#c6373c';
    svg.push(`<text x="${margin.left - 7}" y="${y + 3}" text-anchor="end" font-size="9" fill="#6f6c64" font-family="IBM Plex Mono,monospace">${p.strike.toFixed(1)}</text>`);
    svg.push(`<rect x="${bx}" y="${y - Math.max(2, rowH*.3)}" width="${bw}" height="${Math.max(4,rowH*.6)}" fill="${color}" fill-opacity=".75"><title>Strike ${p.strike.toFixed(1)} · Net GEX ${escapeXml(fmtVal(p.net))}</title></rect>`);
    if (Math.abs(p.net) >= threshold) svg.push(`<text x="${p.net > 0 ? x+5 : x-5}" y="${y+3}" text-anchor="${p.net > 0 ? 'start':'end'}" font-size="9" font-weight="600" fill="#45433e" font-family="IBM Plex Mono,monospace">${escapeXml(fmtVal(p.net))}</text>`);
  });
  const markerY = (v) => {
    if (!Number.isFinite(v) || v < visible[0].strike || v > visible.at(-1).strike) return null;
    return margin.top + plotH - (v - visible[0].strike) / Math.max(1e-9, visible.at(-1).strike - visible[0].strike) * plotH;
  };
  const markers = [
    [price, `PRICE ${price.toFixed(1)}`, '#e8622a', ''],
    [data.zeroFlip ? data.zeroFlip * data.scale * liveMap : NaN, 'FLIP', '#7c4d9e', '5 3'],
    [Number(liveData.trade?.entry), 'ENTRY', '#77736c', '3 3'],
    [Number(liveData.trade?.stop), 'STOP', '#c6373c', ''],
    [Number(liveData.trade?.take), 'TAKE', '#2e7d4f', ''],
  ];
  markers.forEach(([v,label,color,dash]) => {
    const y = markerY(Number(v)); if (y == null) return;
    svg.push(`<line x1="${margin.left}" y1="${y}" x2="${width-margin.right}" y2="${y}" stroke="${color}" stroke-width="${String(label).startsWith('PRICE') ? 2:1.3}" ${dash ? `stroke-dasharray="${dash}"`:''}/>`);
    svg.push(`<text x="${width-margin.right+5}" y="${y+3}" font-size="9" font-weight="600" fill="${color}" font-family="IBM Plex Mono,monospace">${escapeXml(label)}</text>`);
  });
  svg.push(`<text x="${margin.left}" y="14" font-size="9" fill="#8a877d" font-family="IBM Plex Mono,monospace">NEGATIVE GEX</text><text x="${width-margin.right}" y="14" text-anchor="end" font-size="9" fill="#8a877d" font-family="IBM Plex Mono,monospace">POSITIVE GEX</text></svg>`);
  el.innerHTML = svg.join('');
}

function escapeXml(value) {
  return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&apos;');
}
function clamp(value, lo, hi) { return Math.max(lo, Math.min(hi, value)); }
