import { $ } from './util.js';

let emptyEl;
let statusEl;
let data = null;
let migrationData = null;
let liveData = { price: 0, proxyPrice: 0, trade: null };
let currentMode = 'MIGRATION'; // 'MIGRATION' | 'SNAPSHOT'
let resizeObserver = null;
let lastRenderKey = '';

export function initGex() {
  emptyEl = $('#gex-evol-empty');
  statusEl = $('#gex-evol-status');

  const btnMig = $('#btn-gex-migration');
  const btnSnap = $('#btn-gex-snapshot');

  if (btnMig) {
    btnMig.addEventListener('click', () => {
      setMode('MIGRATION');
    });
  }
  if (btnSnap) {
    btnSnap.addEventListener('click', () => {
      setMode('SNAPSHOT');
    });
  }

  const el = $('#gex-evol-canvas');
  if (el && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      if (data || migrationData) renderGex(true);
    });
    resizeObserver.observe(el);
  }
}

function setMode(mode) {
  currentMode = mode;
  const btnMig = $('#btn-gex-migration');
  const btnSnap = $('#btn-gex-snapshot');
  if (btnMig) btnMig.classList.toggle('active', mode === 'MIGRATION');
  if (btnSnap) btnSnap.classList.toggle('active', mode === 'SNAPSHOT');

  renderGex(true);
}

function showEmpty(message, status) {
  data = null;
  migrationData = null;
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

export async function updateGex(ridgePayload) {
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

  // Получаем данные миграции через REST API
  try {
    const res = await fetch('/api/analytics/gex-migration');
    if (res.ok) {
      migrationData = await res.json();
    }
  } catch (err) {
    console.warn('GEX Migration fetch failed, falling back to local snapshots:', err);
  }

  if (emptyEl) emptyEl.style.display = 'none';
  if (statusEl) statusEl.textContent = currentMode === 'MIGRATION' ? '● GEX MIGRATION MAP' : '● OI-GEX SNAPSHOT';

  renderGex(true);
}

export function updateLiveGex(live) {
  if (live.price !== undefined) liveData.price = Number(live.price) || 0;
  if (live.proxyPrice !== undefined) liveData.proxyPrice = Number(live.proxyPrice) || 0;
  if (live.trade !== undefined) liveData.trade = live.trade;

  if (currentMode === 'MIGRATION' && migrationData) {
    // Двигаем только маркер живой цены на canvas
    renderMigrationMap(false);
  } else if (data) {
    renderGex(false);
  }
}

function fmtVal(value) {
  const v = Number(value) || 0;
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
  return v.toFixed(0);
}

function renderGex(force = false) {
  if (currentMode === 'MIGRATION') {
    renderMigrationMap(force);
  } else {
    renderSnapshotBarChart(force);
  }
}

function renderMigrationMap(force = false) {
  const el = $('#gex-evol-canvas');
  if (!el) return;

  if (!migrationData || !migrationData.available || !migrationData.price_grid?.length) {
    renderSnapshotBarChart(force);
    return;
  }

  const summary = migrationData.summary || {};

  // Обновляем карточку сводки справа
  const elRegime = $('#gex-sum-regime');
  const elFlip = $('#gex-sum-flip');
  const elFlipMig = $('#gex-sum-flip-mig');
  const elCall = $('#gex-sum-call');
  const elCallMig = $('#gex-sum-call-mig');
  const elPut = $('#gex-sum-put');
  const elPutMig = $('#gex-sum-put-mig');
  const elPath = $('#gex-sum-path');
  const elPressure = $('#gex-sum-pressure');

  if (elRegime) elRegime.textContent = summary.gamma_regime || 'UNKNOWN';
  if (elFlip && summary.flip) {
    elFlip.textContent = summary.flip.price ? summary.flip.price.toFixed(1) : '-';
    if (elFlipMig) elFlipMig.textContent = summary.flip.migration_6h ? `${summary.flip.migration_6h > 0 ? '+' : ''}${summary.flip.migration_6h.toFixed(1)} /6h` : '0.0';
  }
  if (elCall && summary.call_wall) {
    elCall.textContent = summary.call_wall.price ? summary.call_wall.price.toFixed(1) : '-';
    if (elCallMig) elCallMig.textContent = summary.call_wall.migration_6h ? `${summary.call_wall.migration_6h > 0 ? '+' : ''}${summary.call_wall.migration_6h.toFixed(1)} /6h` : '0.0';
  }
  if (elPut && summary.put_wall) {
    elPut.textContent = summary.put_wall.price ? summary.put_wall.price.toFixed(1) : '-';
    if (elPutMig) elPutMig.textContent = summary.put_wall.migration_6h ? `${summary.put_wall.migration_6h > 0 ? '+' : ''}${summary.put_wall.migration_6h.toFixed(1)} /6h` : '0.0';
  }
  if (elPath) {
    const pathText = summary.take_path || 'CLEAR';
    elPath.textContent = pathText;
    if (pathText.includes('OBSTRUCTED')) {
      elPath.style.background = '#c0392b';
      elPath.style.color = '#fff';
    } else {
      elPath.style.background = '#27ae60';
      elPath.style.color = '#fff';
    }
  }
  if (elPressure) {
    const p = summary.path_pressure || 0;
    elPressure.textContent = (p > 0 ? `+${p.toFixed(2)}` : p.toFixed(2));
    elPressure.style.color = p > 0 ? '#27ae60' : p < 0 ? '#c0392b' : '#333';
  }

  // Отрисовка Canvas Heatmap + Trajectories
  let cv = el.querySelector('canvas');
  if (!cv) {
    el.innerHTML = '<canvas style="width:100%;height:100%;display:block;"></canvas>';
    cv = el.querySelector('canvas');
  }

  const rect = el.getBoundingClientRect();
  const width = Math.max(400, Math.floor(rect.width || 800));
  const height = Math.max(300, Math.floor(rect.height || 420));
  cv.width = width;
  cv.height = height;

  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, width, height);

  const margin = { left: 60, right: 60, top: 20, bottom: 30 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;

  const priceGrid = migrationData.price_grid;
  const timestamps = migrationData.timestamps;
  const timesIso = migrationData.times_iso || [];
  const heatmap = migrationData.heatmap;

  if (!priceGrid.length || !timestamps.length || !heatmap.length) {
    ctx.fillStyle = '#8A877D';
    ctx.font = '11px IBM Plex Mono, monospace';
    ctx.fillText('Снимки миграции отсутствуют', width / 2 - 80, height / 2);
    return;
  }

  const pMin = priceGrid[0];
  const pMax = priceGrid[priceGrid.length - 1];
  const tMin = timestamps[0];
  const tMax = timestamps[timestamps.length - 1] || (tMin + 3600);

  const X = (ts) => margin.left + ((ts - tMin) / (tMax - tMin || 1)) * plotW;
  const Y = (p) => margin.top + plotH - ((p - pMin) / (pMax - pMin || 1)) * plotH;

  // 1. Отрисовка Heatmap Ячеек
  let maxAbsGex = 1;
  for (let r = 0; r < heatmap.length; r++) {
    for (let c = 0; c < heatmap[r].length; c++) {
      maxAbsGex = Math.max(maxAbsGex, Math.abs(heatmap[r][c]));
    }
  }

  const cellW = Math.max(2, plotW / timestamps.length);
  const cellH = Math.max(2, plotH / priceGrid.length);

  for (let r = 0; r < priceGrid.length; r++) {
    const p = priceGrid[r];
    const py = Y(p);
    for (let c = 0; c < timestamps.length; c++) {
      const ts = timestamps[c];
      const px = X(ts);
      const val = heatmap[r][c];

      if (Math.abs(val) > 0.01) {
        const norm = Math.min(1.0, Math.abs(val) / maxAbsGex);
        const alpha = Math.max(0.1, norm * 0.75);
        if (val > 0) {
          ctx.fillStyle = `rgba(39, 174, 96, ${alpha})`; // Call / Positive GEX
        } else {
          ctx.fillStyle = `rgba(192, 57, 43, ${alpha})`; // Put / Negative GEX
        }
        ctx.fillRect(px - cellW / 2, py - cellH / 2, cellW + 0.5, cellH + 0.5);
      }
    }
  }

  // 2. Отрисовка Сетки и Осей
  ctx.strokeStyle = '#eee';
  ctx.lineWidth = 1;
  ctx.strokeRect(margin.left, margin.top, plotW, plotH);

  ctx.fillStyle = '#666';
  ctx.font = '9px IBM Plex Mono, monospace';
  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  const numY = 5;
  for (let i = 0; i <= numY; i++) {
    const pVal = pMin + (i / numY) * (pMax - pMin);
    const py = Y(pVal);
    ctx.fillText(pVal.toFixed(1), margin.left - 6, py);
  }

  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const numX = Math.min(4, timestamps.length - 1);
  for (let i = 0; i <= numX; i++) {
    const idx = Math.floor((i / numX) * (timestamps.length - 1));
    const ts = timestamps[idx];
    const px = X(ts);
    const timeStr = timesIso[idx] ? timesIso[idx].substring(11, 16) : '';
    ctx.fillText(timeStr, px, margin.top + plotH + 6);
  }

  // 3. Отрисовка Траекторий (Trajectories)
  const trajs = migrationData.trajectories || {};

  function drawTrajectory(pts, color, dash = [], label = '') {
    if (!pts || !pts.length) return;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    if (dash.length) ctx.setLineDash(dash);

    ctx.beginPath();
    let started = false;
    for (const pt of pts) {
      if (pt.price != null && Number.isFinite(pt.price)) {
        const px = X(pt.ts);
        const py = Y(pt.price);
        if (!started) {
          ctx.moveTo(px, py);
          started = true;
        } else {
          ctx.lineTo(px, py);
        }
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  drawTrajectory(trajs.flip, '#9b59b6', [4, 4], 'FLIP');
  drawTrajectory(trajs.call_wall, '#27ae60', [], 'CALL WALL');
  drawTrajectory(trajs.put_wall, '#c0392b', [], 'PUT WALL');

  // 4. Отрисовка Текущих Живых Маркеров (Price, Entry, Stop, Take)
  const currentP = liveData.price || migrationData.summary?.call_wall?.price || pMin;
  if (currentP >= pMin && currentP <= pMax) {
    const py = Y(currentP);
    ctx.strokeStyle = '#e67e22';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(margin.left, py);
    ctx.lineTo(margin.left + plotW, py);
    ctx.stroke();

    ctx.fillStyle = '#e67e22';
    ctx.font = 'bold 9px IBM Plex Mono, monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`PRICE ${currentP.toFixed(1)}`, margin.left + plotW + 4, py - 4);
  }

  const trade = liveData.trade;
  if (trade) {
    if (trade.entry) {
      const ey = Y(Number(trade.entry));
      ctx.strokeStyle = '#8A877D';
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(margin.left, ey);
      ctx.lineTo(margin.left + plotW, ey);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (trade.stop) {
      const sy = Y(Number(trade.stop));
      ctx.strokeStyle = '#c0392b';
      ctx.beginPath();
      ctx.moveTo(margin.left, sy);
      ctx.lineTo(margin.left + plotW, sy);
      ctx.stroke();
    }
    if (trade.take) {
      const ty = Y(Number(trade.take));
      ctx.strokeStyle = '#27ae60';
      ctx.beginPath();
      ctx.moveTo(margin.left, ty);
      ctx.lineTo(margin.left + plotW, ty);
      ctx.stroke();
    }
  }
}

function renderSnapshotBarChart(force = false) {
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
