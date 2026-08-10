import { $ } from './util.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';
import { publishMarketTick } from './market_bus.js';
import { ensurePremiumAnalyticsTheme } from './premium_analytics_theme.js';
import { isAnalyticsMobile, analyticsMobileDpr } from './analytics_mobile.js';
import { attachTerminal3DToolbar } from './plotly_terminal_toolbar.js';
import { dampedMotion } from './real_market_motion.js';

let emptyEl;
let statusEl;
let containerEl;
let data = null;
let migrationData = null;
let currentMode = 'MIGRATION';
let resizeObserver = null;
let rafId = null;
let rendererGeneration = 0;
let pressureGuard = null;
let live = { price: 0, proxyPrice: 0, trade: null, tick: null };
let particles = [];
let staticCanvas = null;
let staticKey = '';
let lastSummaryUpdate = 0;
let marketMotion = { velocity: 0, phase: 0, turbulence: 0, lastFrame: 0 };

const PRESSURE_CAM = {
  eye: { x: 1.48, y: -1.72, z: 1.18 },
  up: { x: 0, y: 0, z: 1 },
};

export function initGex() {
  ensurePremiumAnalyticsTheme();
  emptyEl = $('#gex-evol-empty');
  statusEl = $('#gex-evol-status');
  containerEl = $('#gex-evol-canvas');
  ensureModeButtons();
  if (containerEl && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      staticKey = '';
      renderActive(true);
    });
    resizeObserver.observe(containerEl);
  }
  window.addEventListener?.('seiltanzer:analytics-mobile-resize', () => {
    staticKey = '';
    renderActive(true);
  });
}

function ensureModeButtons() {
  const group = $('#gex-mode-group');
  if (!group) return;
  let pressure = $('#btn-gex-pressure');
  if (!pressure) {
    pressure = document.createElement('button');
    pressure.className = 'btn-toggle';
    pressure.id = 'btn-gex-pressure';
    pressure.textContent = 'PRESSURE 3D';
    const snapshot = $('#btn-gex-snapshot');
    group.insertBefore(pressure, snapshot || null);
  }
  $('#btn-gex-migration')?.addEventListener('click', () => setMode('MIGRATION'));
  pressure.addEventListener('click', () => setMode('PRESSURE'));
  $('#btn-gex-snapshot')?.addEventListener('click', () => setMode('SNAPSHOT'));
}

function setMode(mode) {
  if (currentMode === mode) return;
  currentMode = mode;
  $('#btn-gex-migration')?.classList.toggle('active', mode === 'MIGRATION');
  $('#btn-gex-pressure')?.classList.toggle('active', mode === 'PRESSURE');
  $('#btn-gex-snapshot')?.classList.toggle('active', mode === 'SNAPSHOT');
  destroyRenderer();
  renderActive(true);
}

function destroyRenderer() {
  rendererGeneration += 1;
  if (rafId) cancelAnimationFrame(rafId);
  rafId = null;
  staticCanvas = null;
  staticKey = '';
  if (pressureGuard) {
    try { pressureGuard.destroy?.(); } catch {}
    pressureGuard = null;
  }
  if (containerEl && window.Plotly && containerEl.querySelector('.js-plotly-plot')) {
    try { window.Plotly.purge(containerEl.querySelector('.js-plotly-plot')); } catch {}
  }
  containerEl?.replaceChildren();
}

function showEmpty(message, status) {
  destroyRenderer();
  if (emptyEl) {
    emptyEl.style.display = 'flex';
    emptyEl.textContent = message;
  }
  if (statusEl) statusEl.textContent = status;
}

export async function updateGex(ridgePayload) {
  if (!ridgePayload?.available || !ridgePayload.snapshots?.length) {
    data = null;
    migrationData = null;
    showEmpty('○ OI × GAMMA КОНТЕКСТ НЕДОСТУПЕН', '○ GEX НЕДОСТУПЕН');
    return;
  }
  const latest = ridgePayload.snapshots.at(-1);
  if (!latest?.gex?.available || !latest.gex.strikes?.length || !latest.gex.net?.length) {
    data = null;
    migrationData = null;
    showEmpty('○ GEX КОНТЕКСТ ОТКЛЮЧЁН ДЛЯ ЭТОГО PROXY', '○ GEX CONTEXT ONLY');
    return;
  }
  data = {
    scale: Number(ridgePayload.scale) || 1,
    price: Number(ridgePayload.price) || 0,
    proxyPrice: Number(ridgePayload.proxy_spot_current) || 0,
    transform: ridgePayload.proxy_transform || 'direct',
    latest: latest.gex,
    zeroFlip: Number(latest.gex.zero_flip) || null,
  };
  live.trade = ridgePayload.trade || live.trade;
  try {
    const res = await fetch('/api/analytics/gex-migration', { cache: 'no-store' });
    migrationData = res.ok ? await res.json() : null;
  } catch (err) {
    console.warn('GEX migration fetch failed:', err);
    migrationData = null;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  resetParticles();
  staticKey = '';
  renderActive(true);
}

export function updateLiveGex(packet = {}) {
  if (packet.price !== undefined) live.price = Number(packet.price) || 0;
  if (packet.proxyPrice !== undefined) live.proxyPrice = Number(packet.proxyPrice) || 0;
  if (packet.trade !== undefined) live.trade = packet.trade;
  publishMarketTick(packet);
  const prev = live.tick?.price;
  const now = performance.now();
  const price = live.price;
  const dt = live.tick ? Math.max(0.016, (now - live.tick.now) / 1000) : 0;
  const retBp = prev && price > 0 ? Math.log(price / prev) * 1e4 : 0;
  const speed = dt ? retBp / dt : 0;
  const previousSpeed = Number(live.tick?.speed || 0);
  live.tick = {
    price,
    now,
    retBp,
    speed,
    acceleration: dt ? (speed - previousSpeed) / dt : 0,
    impulse: Math.min(1, Math.abs(retBp) / 3),
  };
  if (currentMode === 'PRESSURE') updatePressureLiveMarker();
  else if (currentMode === 'SNAPSHOT') renderSnapshot();
  else ensureAnimation();
}

function renderActive(force = false) {
  if (!containerEl || !data) return;
  if (currentMode === 'PRESSURE') renderPressure3D(force);
  else if (currentMode === 'SNAPSHOT') renderSnapshot(force);
  else renderMigration(force);
}

function fmtUtc(ts) {
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return '—';
  return `${String(d.getUTCDate()).padStart(2, '0')}.${String(d.getUTCMonth() + 1).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}

function percentile(values, q) {
  const a = values.filter(Number.isFinite).sort((x, y) => x - y);
  if (!a.length) return 1;
  return a[Math.max(0, Math.min(a.length - 1, Math.round((a.length - 1) * q)))] || 1;
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, Number(v))); }

function liveMapFactor() {
  if (!data) return 1;
  const instrumentFactor = live.price && data.price ? live.price / data.price : 1;
  const proxyFactor = live.proxyPrice && data.proxyPrice ? live.proxyPrice / data.proxyPrice : 1;
  return data.transform === 'inverse' ? instrumentFactor * proxyFactor : instrumentFactor / proxyFactor;
}

function mappedPairs() {
  if (!data) return [];
  const factor = liveMapFactor();
  return (data.latest.strikes || []).map((s, i) => ({
    strike: Number(s) * data.scale * factor,
    gex: Number(data.latest.net?.[i] || 0),
  })).filter((p) => Number.isFinite(p.strike) && Number.isFinite(p.gex) && p.gex !== 0)
    .sort((a, b) => a.strike - b.strike);
}

function liveField(price = live.price || data?.price || 0) {
  const pairs = mappedPairs();
  if (!pairs.length || !price) return { potential: 0, gradient: 0, normalized: 0, force: 0, bandwidth: 0 };
  const diffs = [];
  for (let i = 1; i < pairs.length; i++) diffs.push(Math.abs(pairs[i].strike - pairs[i - 1].strike));
  const h = Math.max(price * 0.0045, percentile(diffs, 0.5) * 1.45, 1e-6);
  const scale = Math.max(percentile(pairs.map((p) => Math.abs(p.gex)), 0.95), 1e-9);
  let potential = 0;
  let gradient = 0;
  for (const p of pairs) {
    const d = price - p.strike;
    const kernel = Math.exp(-0.5 * (d / h) ** 2);
    const g = clamp(p.gex / scale, -1.5, 1.5);
    potential += g * kernel;
    gradient += g * kernel * (-d / (h * h));
  }
  return {
    potential,
    gradient,
    normalized: Math.tanh(potential / 2),
    force: Math.tanh(-gradient * h),
    bandwidth: h,
  };
}

function updateSummary() {
  const s = migrationData?.summary || {};
  const set = (id, text) => { const el = $(id); if (el) el.textContent = text; };
  set('#gex-sum-regime', s.gamma_regime || 'UNKNOWN');
  set('#gex-sum-flip', s.flip?.price == null ? '—' : Number(s.flip.price).toFixed(1));
  set('#gex-sum-flip-mig', fmtMigration(s.flip?.migration_6h));
  set('#gex-sum-call', s.call_wall?.price == null ? '—' : Number(s.call_wall.price).toFixed(1));
  set('#gex-sum-call-mig', fmtMigration(s.call_wall?.migration_6h));
  set('#gex-sum-put', s.put_wall?.price == null ? '—' : Number(s.put_wall.price).toFixed(1));
  set('#gex-sum-put-mig', fmtMigration(s.put_wall?.migration_6h));
  const path = $('#gex-sum-path');
  if (path) {
    path.textContent = s.corridor_state || s.take_path || 'NO DATA';
    const obs = Number(s.obstruction_score);
    path.style.background = obs >= .72 ? '#b82635' : obs >= .48 ? '#d26431' : obs >= .24 ? '#c8942d' : '#267d68';
    path.style.color = '#fff';
  }
  set('#gex-sum-pressure', s.obstruction_score == null ? '—' : `${Math.round(Number(s.obstruction_score) * 100)}% OBSTRUCTION`);
  const headline = $('#gex-human-line');
  if (headline) {
    const obstruction = Number(s.obstruction_score || 0);
    const walls = [s.call_wall, s.put_wall].filter((wall) => Number.isFinite(Number(wall?.price)) && Number.isFinite(Number(wall?.migration_6h)));
    const approaching = walls.find((wall) => Math.sign(Number(wall.price) - Number(s.current_price || live.price)) === -Math.sign(Number(wall.migration_6h)));
    headline.textContent = `TAKE PATH: ${obstruction >= .58 ? 'HARDENING' : obstruction >= .28 ? 'MATERIAL FRICTION' : 'CLEAR'}${approaching ? ` · ${(approaching === s.put_wall ? 'PUT' : 'CALL')} WALL APPROACHING` : ' · WALLS STABLE/RECEDING'}`;
  }
  const card = $('#gex-summary-card');
  if (card) {
    let meta = $('#gex-migration-meta');
    if (!meta) {
      meta = document.createElement('div');
      meta.id = 'gex-migration-meta';
      meta.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:7px;margin-top:7px;font-size:9px;color:#706d65;line-height:1.65';
      card.appendChild(meta);
    }
    const f = liveField();
    const tick = live.tick || {};
    meta.innerHTML = `
      <div class="analytics-metric-grid">
        <div class="analytics-metric-tile"><small>LIVE FIELD</small><b>${f.normalized >= 0 ? '+' : ''}${f.normalized.toFixed(2)}</b></div>
        <div class="analytics-metric-tile"><small>FIELD FORCE</small><b>${f.force >= 0 ? '+' : ''}${f.force.toFixed(2)}</b></div>
        <div class="analytics-metric-tile"><small>TICK Δ</small><b>${Number(tick.retBp || 0).toFixed(2)}bp</b></div>
        <div class="analytics-metric-tile"><small>WALL PERSIST.</small><b>C ${Math.round(Number(s.call_wall?.persistence || 0) * 100)}% · P ${Math.round(Number(s.put_wall?.persistence || 0) * 100)}%</b></div>
      </div>
      <div style="margin-top:8px">REL STRENGTH · CALL ${Number(s.call_wall?.strength || 0).toFixed(2)} · PUT ${Number(s.put_wall?.strength || 0).toFixed(2)}</div>
      <div>FIELD = Σ GEXᵢ·exp(−(S−Kᵢ)²/2h²); FORCE = −∂FIELD/∂S · context-only</div>`;
  }
  if (statusEl) {
    const n = s.snapshot_count ?? migrationData?.timestamps?.length ?? 0;
    const h = s.history_hours == null ? '' : ` · ${Number(s.history_hours).toFixed(1)}H`;
    statusEl.textContent = `● GEX FIELD · ${n} SNAP${h}`;
  }
}

function maybeUpdateSummary(now = performance.now()) {
  const interval = isAnalyticsMobile() ? 300 : 140;
  if (now - lastSummaryUpdate < interval) return;
  lastSummaryUpdate = now;
  updateSummary();
}

function fmtMigration(v) {
  return v == null || !Number.isFinite(Number(v)) ? '— BUILDING' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)}/6h`;
}

function resetParticles() {
  const count = isAnalyticsMobile() ? 26 : 58;
  particles = Array.from({ length: count }, (_, i) => ({
    u: (i + .5) / count,
    lane: (((i * 17) % count) / Math.max(1, count - 1)) * 2 - 1,
    phase: (i * 2.399963229728653) % (Math.PI * 2),
    size: 1 + ((i * 7) % 11) / 10 * 1.8,
  }));
  marketMotion = { velocity: 0, phase: 0, turbulence: 0, lastFrame: 0 };
}

function chartGeometry() {
  const rect = containerEl.getBoundingClientRect();
  const mobile = isAnalyticsMobile();
  const width = mobile ? Math.max(280, Math.floor(rect.width || 340)) : Math.max(560, Math.floor(rect.width || 850));
  const height = mobile ? Math.max(280, Math.floor(rect.height || 330)) : Math.max(340, Math.floor(rect.height || 420));
  return { width, height, mobile, margin: mobile ? { left: 49, right: 50, top: 22, bottom: 34 } : { left: 68, right: 72, top: 24, bottom: 42 } };
}

function migrationScales(g) {
  const timestamps = migrationData?.timestamps || [];
  const prices = migrationData?.price_grid || [];
  const range = migrationData?.plot_range || [prices[0], prices.at(-1)];
  const pMin = Number(range[0]), pMax = Number(range[1]);
  const tMin = Number(timestamps[0]), tMax = Number(timestamps.at(-1));
  const plotW = g.width - g.margin.left - g.margin.right;
  const plotH = g.height - g.margin.top - g.margin.bottom;
  return {
    ...g, pMin, pMax, tMin, tMax, plotW, plotH,
    X: (ts) => g.margin.left + ((Number(ts) - tMin) / Math.max(1, tMax - tMin)) * plotW,
    Y: (p) => g.margin.top + plotH - ((Number(p) - pMin) / Math.max(1e-9, pMax - pMin)) * plotH,
  };
}

function buildMigrationStatic() {
  const g = migrationScales(chartGeometry());
  const cv = document.createElement('canvas');
  cv.width = g.width;
  cv.height = g.height;
  const ctx = cv.getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, g.height);
  grad.addColorStop(0, '#07111d'); grad.addColorStop(1, '#0b1724');
  ctx.fillStyle = grad; ctx.fillRect(0, 0, g.width, g.height);

  const timestamps = migrationData.timestamps || [];
  const prices = migrationData.price_grid || [];
  const heat = migrationData.heatmap || [];
  const abs = [];
  heat.forEach((row) => row.forEach((v) => { const n = Math.abs(Number(v)); if (n > 0) abs.push(n); }));
  const scale = Math.max(percentile(abs, .98), 1e-12);
  const cellW = Math.max(2, g.plotW / Math.max(1, timestamps.length));
  const cellH = g.plotH / Math.max(1, prices.length);
  for (let r = 0; r < prices.length; r++) {
    const y = g.Y(prices[r]);
    for (let c = 0; c < timestamps.length; c++) {
      const v = Number(heat[r]?.[c] || 0);
      if (!v) continue;
      const n = Math.sqrt(Math.min(1, Math.abs(v) / scale));
      const x = g.X(timestamps[c]);
      const color = v > 0 ? [52, 205, 156] : [244, 84, 111];
      ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${0.045 + n * .65})`;
      ctx.fillRect(x - cellW / 2, y - cellH / 2, cellW + 1, Math.max(2, cellH + 1));
    }
  }

  ctx.strokeStyle = 'rgba(189,211,226,.12)';
  ctx.fillStyle = 'rgba(215,226,235,.66)';
  ctx.font = `${g.mobile ? 8 : 9}px IBM Plex Mono,monospace`;
  const yTicks = g.mobile ? 4 : 6;
  for (let i = 0; i <= yTicks; i++) {
    const p = g.pMin + i * (g.pMax - g.pMin) / yTicks;
    const y = g.Y(p);
    ctx.beginPath(); ctx.moveTo(g.margin.left, y); ctx.lineTo(g.margin.left + g.plotW, y); ctx.stroke();
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(p.toFixed(1), g.margin.left - 5, y);
  }
  const xLabels = Math.min(g.mobile ? 3 : 5, timestamps.length);
  for (let i = 0; i < xLabels; i++) {
    const idx = xLabels === 1 ? 0 : Math.round(i * (timestamps.length - 1) / (xLabels - 1));
    const x = g.X(timestamps[idx]);
    ctx.beginPath(); ctx.moveTo(x, g.margin.top); ctx.lineTo(x, g.margin.top + g.plotH); ctx.stroke();
    ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillText(g.mobile ? fmtUtc(timestamps[idx]).slice(-5) : fmtUtc(timestamps[idx]), x, g.margin.top + g.plotH + 7);
  }

  const drawTrajectory = (points, color, dash, label) => {
    const valid = (points || []).filter((p) => Number.isFinite(Number(p.price)) && Number(p.price) >= g.pMin && Number(p.price) <= g.pMax);
    if (!valid.length) return;
    ctx.save(); ctx.setLineDash(dash || []); ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    ctx.shadowColor = color; ctx.shadowBlur = g.mobile ? 6 : 10; ctx.strokeStyle = color; ctx.lineWidth = g.mobile ? 1.7 : 2.1;
    ctx.beginPath(); valid.forEach((p, i) => { const x = g.X(p.ts), y = g.Y(p.price); if (!i) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.stroke();
    ctx.shadowBlur = 0; ctx.restore();
    const last = valid.at(-1); ctx.fillStyle = color; ctx.font = `bold ${g.mobile ? 8 : 9}px IBM Plex Mono,monospace`; ctx.textAlign = 'right';
    ctx.fillText(`${label} ${Number(last.price).toFixed(1)}`, g.margin.left + g.plotW - 4, g.Y(last.price) - 4);
  };
  const tr = migrationData.trajectories || {};
  drawTrajectory(tr.call_wall, '#43d79f', [], 'CALL');
  drawTrajectory(tr.put_wall, '#ff5e78', [], 'PUT');
  drawTrajectory(tr.flip, '#c49cff', [6, 4], 'FLIP');

  const ph = migrationData.path_pressure_history || [];
  if (ph.length) {
    const y = g.margin.top + 5;
    const h = 7;
    ph.forEach((p, i) => {
      const x0 = g.X(p.ts);
      const x1 = i + 1 < ph.length ? g.X(ph[i + 1].ts) : g.margin.left + g.plotW;
      const obs = clamp(p.obstruction || 0, 0, 1);
      ctx.fillStyle = `rgba(${Math.round(54 + 198 * obs)},${Math.round(190 - 125 * obs)},${Math.round(136 - 80 * obs)},${.34 + .5 * obs})`;
      ctx.fillRect(x0, y, Math.max(2, x1 - x0), h);
    });
    ctx.fillStyle = 'rgba(218,226,232,.72)'; ctx.font = `${g.mobile ? 7 : 8}px IBM Plex Mono,monospace`; ctx.textAlign = 'left';
    ctx.fillText(g.mobile ? 'PATH FRICTION' : 'TAKE-PATH FRICTION', g.margin.left, y + 18);
  }

  ctx.strokeStyle = 'rgba(202,218,230,.26)'; ctx.strokeRect(g.margin.left, g.margin.top, g.plotW, g.plotH);
  return { canvas: cv, g };
}

function ensureMigrationCanvas() {
  let cv = containerEl.querySelector('canvas[data-renderer="migration"]');
  if (!cv) {
    containerEl.replaceChildren();
    cv = document.createElement('canvas');
    cv.dataset.renderer = 'migration';
    cv.style.cssText = 'width:100%;height:100%;display:block';
    containerEl.appendChild(cv);
  }
  const g = chartGeometry();
  const dpr = analyticsMobileDpr();
  if (cv.width !== Math.floor(g.width * dpr) || cv.height !== Math.floor(g.height * dpr)) {
    cv.width = Math.floor(g.width * dpr); cv.height = Math.floor(g.height * dpr);
    cv.style.width = `${g.width}px`; cv.style.height = `${g.height}px`;
    staticKey = '';
  }
  return { cv, dpr, g };
}

function renderMigration(force = false) {
  if (!migrationData?.available) {
    if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = `○ ${migrationData?.reason || 'GEX MIGRATION UNAVAILABLE'}`; }
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  updateSummary();
  const { cv, dpr, g } = ensureMigrationCanvas();
  const key = `${g.width}:${g.height}:${migrationData.timestamps?.length}:${migrationData.summary?.snapshot_count}`;
  if (force || !staticCanvas || key !== staticKey) {
    const built = buildMigrationStatic();
    staticCanvas = built.canvas;
    staticKey = key;
  }
  const generation = rendererGeneration;
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  let lastDraw = 0;
  const minFrame = g.mobile ? 32 : 15;

  const draw = (now) => {
    if (generation !== rendererGeneration || currentMode !== 'MIGRATION') return;
    if (now - lastDraw >= minFrame) {
      lastDraw = now;
      ctx.clearRect(0, 0, g.width, g.height);
      ctx.drawImage(staticCanvas, 0, 0, g.width, g.height);
      drawLiveMigrationOverlay(ctx, migrationScales(g), now);
    }
    rafId = requestAnimationFrame(draw);
  };
  if (rafId) cancelAnimationFrame(rafId);
  rafId = requestAnimationFrame(draw);
}

function drawLiveMigrationOverlay(ctx, g, now) {
  const s = migrationData.summary || {};
  const current = live.price || Number(s.current_price || 0);
  if (!current || current < g.pMin || current > g.pMax) return;
  const yPrice = g.Y(current);
  const tickAge = live.tick ? Math.max(0, (now - Number(live.tick.now || now)) / 1000) : Infinity;
  const impulse = clamp(Number(live.tick?.impulse || 0) * Math.exp(-tickAge / 1.4), 0, 1);
  ctx.save();
  ctx.shadowColor = '#ffb24b'; ctx.shadowBlur = (g.mobile ? 3 : 5) + (g.mobile ? 5 : 9) * impulse;
  ctx.strokeStyle = `rgba(255,178,75,${.72 + .24 * impulse})`; ctx.lineWidth = (g.mobile ? 1.5 : 1.9) + impulse * .7;
  ctx.beginPath(); ctx.moveTo(g.margin.left, yPrice); ctx.lineTo(g.margin.left + g.plotW, yPrice); ctx.stroke();
  ctx.shadowBlur = 0; ctx.fillStyle = '#ffb24b'; ctx.font = `bold ${g.mobile ? 8 : 9}px IBM Plex Mono,monospace`; ctx.textAlign = 'right';
  ctx.fillText(`LIVE ${current.toFixed(1)}`, g.margin.left + g.plotW - 4, yPrice - 4);

  const take = Number(live.trade?.take);
  const field = liveField(current);
  if (Number.isFinite(take) && take >= g.pMin && take <= g.pMax && Math.abs(take - current) > 1e-9) {
    const yTake = g.Y(take);
    const obstruction = clamp(s.obstruction_score || 0, 0, 1);
    const stripX = g.margin.left + g.plotW - (g.mobile ? 18 : 27);
    const dir = Math.sign(take - current);
    ctx.fillStyle = `rgba(${Math.round(52 + 185 * obstruction)},${Math.round(190 - 115 * obstruction)},${Math.round(144 - 80 * obstruction)},.09)`;
    ctx.fillRect(stripX - (g.mobile ? 10 : 16), Math.min(yTake, yPrice), g.mobile ? 20 : 32, Math.abs(yTake - yPrice));
    ctx.strokeStyle = `rgba(223,232,239,.28)`; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(stripX, yPrice); ctx.lineTo(stripX, yTake); ctx.stroke(); ctx.setLineDash([]);

    const tick = live.tick || {};
    const towardVelocity = clamp(Number(tick.speed || 0) * dir / 6, -1, 1);
    const alignment = clamp(.55 + .45 * Number(field.force || 0) * dir, .1, 1);
    const drive = Math.max(0, towardVelocity) * (1 - obstruction) * alignment * Math.exp(-tickAge / 1.4);
    const dt = marketMotion.lastFrame ? Math.max(0, Math.min(.25, (now - marketMotion.lastFrame) / 1000)) : 0;
    marketMotion.lastFrame = now;
    marketMotion = { ...marketMotion, ...dampedMotion(marketMotion, drive, dt, { response: 8, damping: 2.8, maxSpeed: 1 }), lastFrame: now };
    const migration = Math.max(Math.abs(Number(s.call_wall?.migration_6h || 0)), Math.abs(Number(s.put_wall?.migration_6h || 0)), Math.abs(Number(s.flip?.migration_6h || 0)));
    const turbulenceTarget = clamp((Math.abs(field.force) * .38 + obstruction * .25 + Math.min(1, Math.abs(Number(tick.acceleration || 0)) / 24) * .24 + Math.min(1, migration / Math.max(current * .006, 1e-6)) * .13) * Math.exp(-tickAge / 2.0), 0, 1);
    marketMotion.turbulence += (turbulenceTarget - marketMotion.turbulence) * (1 - Math.exp(-5 * dt));
    const turbulence = marketMotion.turbulence * (g.mobile ? 9 : 13);
    particles.forEach((p) => {
      p.u += Math.max(0, marketMotion.velocity) * dt * .45;
      if (p.u > 1) p.u -= 1;
      const pp = current + (take - current) * p.u;
      const yy = g.Y(pp);
      const xx = stripX + p.lane * turbulence + Math.sin(p.phase + marketMotion.phase * Math.PI * 2) * turbulence * .35;
      const alpha = (.10 + .78 * marketMotion.energy) * (1 - obstruction * .55);
      ctx.fillStyle = obstruction > .55 ? `rgba(255,116,100,${alpha})` : `rgba(77,219,202,${alpha})`;
      ctx.beginPath(); ctx.arc(xx, yy, g.mobile ? Math.min(1.7,p.size) : p.size, 0, Math.PI * 2); ctx.fill();
    });
    ctx.fillStyle = 'rgba(214,226,235,.7)'; ctx.font = `${g.mobile ? 7 : 8}px IBM Plex Mono,monospace`; ctx.textAlign = 'center';
    ctx.fillText(`FLOW v ${marketMotion.velocity.toFixed(2)} · OBS ${Math.round(obstruction * 100)}%`, stripX, Math.min(yPrice, yTake) - 7);
  }

  const gx = g.margin.left + 8, gy = g.margin.top + g.plotH - 14, gw = g.mobile ? 72 : 105;
  ctx.fillStyle = 'rgba(5,12,20,.62)'; ctx.fillRect(gx - 5, gy - 15, gw + 10, 23);
  ctx.strokeStyle = 'rgba(220,230,238,.2)'; ctx.strokeRect(gx, gy, gw, 4);
  const centre = gx + gw / 2;
  const fw = field.force * gw / 2;
  ctx.fillStyle = field.force >= 0 ? '#43d79f' : '#ff5e78';
  ctx.fillRect(Math.min(centre, centre + fw), gy, Math.abs(fw), 4);
  ctx.fillStyle = 'rgba(221,230,237,.7)'; ctx.textAlign = 'left'; ctx.font = `${g.mobile ? 7 : 8}px IBM Plex Mono,monospace`;
  ctx.fillText(`∂FIELD ${field.force >= 0 ? '+' : ''}${field.force.toFixed(2)}`, gx, gy - 5);
  ctx.restore();
  maybeUpdateSummary(now);
}

function renderPressure3D(force = false) {
  if (!migrationData?.available || !window.Plotly) {
    currentMode = 'MIGRATION';
    renderMigration(true);
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  updateSummary();
  if (containerEl.querySelector('[data-renderer="pressure"]') && !force) return;
  destroyRenderer();
  const mobile = isAnalyticsMobile();
  const plot = document.createElement('div');
  plot.dataset.renderer = 'pressure';
  plot.style.cssText = 'width:100%;height:100%;touch-action:none';
  containerEl.appendChild(plot);
  pressureGuard = createPlotlyCameraGuard(plot, PRESSURE_CAM);
  const times0 = migrationData.timestamps || [];
  const prices0 = migrationData.price_grid || [];
  const heat0 = migrationData.heatmap || [];
  const xStride = mobile ? Math.max(1, Math.ceil(times0.length / 90)) : 1;
  const yStride = mobile ? Math.max(1, Math.ceil(prices0.length / 42)) : 1;
  const xIndices = times0.map((_,i)=>i).filter((i)=>i%xStride===0||i===times0.length-1);
  const yIndices = prices0.map((_,i)=>i).filter((i)=>i%yStride===0||i===prices0.length-1);
  const times = xIndices.map((i)=>times0[i]);
  const prices = yIndices.map((i)=>prices0[i]);
  const heat = yIndices.map((ri)=>xIndices.map((ci)=>heat0[ri]?.[ci] || 0));
  const latestTs = Number(times0.at(-1) || 0);
  const xHours = times.map((t) => (Number(t) - latestTs) / 3600);
  const abs = [];
  heat.forEach((row) => row.forEach((v) => { const n = Math.abs(Number(v)); if (n) abs.push(n); }));
  const scale = Math.max(percentile(abs, .98), 1e-12);
  const z = heat.map((row) => row.map((v) => Math.sqrt(Math.min(1, Math.abs(Number(v) || 0) / scale))));
  const surfaceColor = heat.map((row) => row.map((v) => clamp((Number(v) || 0) / scale, -1, 1)));
  const traces = [{
    type: 'surface', x: xHours, y: prices, z,
    surfacecolor: surfaceColor, cmin: -1, cmax: 1,
    colorscale: [[0,'#ff4969'],[.46,'#22364a'],[.5,'#172737'],[.54,'#1f4b51'],[1,'#35d8a1']],
    opacity: .90, showscale: false, hovertemplate: mobile ? undefined : 't=%{x:.2f}h<br>price=%{y:.1f}<br>|pressure|=%{z:.2f}<extra></extra>', hoverinfo: mobile ? 'skip' : undefined,
    contours: { z: { show: !mobile, usecolormap: false, color: 'rgba(225,235,242,.20)', project: { z: true } } },
    lighting: { ambient: .46, diffuse: .68, roughness: .82, specular: mobile ? .10 : .22, fresnel: .08 },
    lightposition: { x: 100, y: -50, z: 130 },
  }];
  const addTrajectory = (points, name, color) => {
    const valid = (points || []).filter((p) => Number.isFinite(Number(p.price)));
    if (!valid.length) return;
    traces.push({
      type: 'scatter3d', mode: 'lines', name,
      x: valid.map((p) => (Number(p.ts) - latestTs) / 3600),
      y: valid.map((p) => Number(p.price)), z: valid.map(() => 1.055),
      line: { color, width: mobile ? 4 : 6 }, hoverinfo: 'skip', showlegend: false,
    });
  };
  addTrajectory(migrationData.trajectories?.call_wall, 'CALL WALL', '#43d79f');
  addTrajectory(migrationData.trajectories?.put_wall, 'PUT WALL', '#ff5e78');
  addTrajectory(migrationData.trajectories?.flip, 'FLIP', '#c49cff');

  const latestProfile = prices.map((p, i) => z[i]?.at(-1) || 0);
  traces.push({
    type: 'scatter3d', mode: 'lines', name: 'LIVE PROFILE', x: prices.map(() => 0), y: prices, z: latestProfile,
    line: { color: '#f0c36a', width: mobile ? 3 : 5 }, hoverinfo: 'skip', showlegend: false,
  });
  traces.push({
    type: 'scatter3d', mode: 'markers+text', name: 'LIVE PRICE',
    x: [0], y: [live.price || migrationData.summary?.current_price || prices[Math.floor(prices.length / 2)]], z: [.1],
    marker: { size: mobile ? 5 : 7, color: '#ffb24b', line: { color: '#fff', width: 1 } },
    text: ['LIVE'], textposition: 'top center', hoverinfo: 'skip', showlegend: false,
  });
  const take = Number(live.trade?.take);
  if (Number.isFinite(take)) traces.push({
    type: 'scatter3d', mode: 'lines', x: [xHours[0] || -1, 0], y: [take, take], z: [0, 0],
    line: { color: '#64e2a9', width: mobile ? 3 : 4, dash: 'dash' }, hoverinfo: 'skip', showlegend: false,
  });

  const layout = {
    margin: { l: 0, r: 0, t: 2, b: 0 }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', showlegend: false, hovermode: mobile ? false : undefined,
    uirevision: 'gex-pressure-premium-v3',
    scene: {
      xaxis: { title: mobile ? 'TIME' : 'TIME · HOURS TO NOW', gridcolor: 'rgba(190,210,224,.15)', color: '#b9c9d4', zerolinecolor: '#e5aa52', tickfont:{size:mobile?8:10} },
      yaxis: { title: mobile ? 'PRICE' : 'PRICE', gridcolor: 'rgba(190,210,224,.15)', color: '#b9c9d4', tickfont:{size:mobile?8:10} },
      zaxis: { title: mobile ? 'GEX' : '|GEX PRESSURE|', range: [0, 1.12], gridcolor: 'rgba(190,210,224,.13)', color: '#b9c9d4', tickfont:{size:mobile?8:10} },
      bgcolor: 'rgba(4,12,20,.0)', aspectmode: 'manual', aspectratio: mobile ? { x: 1.22, y: 1.0, z: .82 } : { x: 1.55, y: 1.1, z: .72 },
    },
  };
  pressureGuard?.beforeWrite?.();
  window.Plotly.newPlot(plot, traces, layout, { responsive: false, displayModeBar: false, scrollZoom: !mobile });
  pressureGuard?.afterWrite?.();
  attachTerminal3DToolbar({plot,container:containerEl,guard:pressureGuard,homeCamera:PRESSURE_CAM,key:'gex-pressure'});
  updatePressureLiveMarker();
}

function updatePressureLiveMarker() {
  if (currentMode !== 'PRESSURE' || !containerEl || !window.Plotly || !migrationData?.available) return;
  const plot = containerEl.querySelector('[data-renderer="pressure"]');
  if (!plot || !plot.data?.length) return;
  const prices = migrationData.price_grid || [];
  const heat = migrationData.heatmap || [];
  if (!prices.length || !heat.length) return;
  const abs = [];
  heat.forEach((row) => row.forEach((v) => { const n = Math.abs(Number(v)); if (n) abs.push(n); }));
  const scale = Math.max(percentile(abs, .98), 1e-12);
  const p = live.price || migrationData.summary?.current_price;
  let best = 0;
  prices.forEach((v, i) => { if (Math.abs(Number(v) - p) < Math.abs(Number(prices[best]) - p)) best = i; });
  const z = Math.sqrt(Math.min(1, Math.abs(Number(heat[best]?.at(-1) || 0)) / scale));
  const idx = plot.data.findIndex((t) => t.name === 'LIVE PRICE');
  if (idx >= 0) window.Plotly.restyle(plot, { y: [[p]], z: [[Math.max(.04, z)]] }, [idx]);
  maybeUpdateSummary();
}

function renderSnapshot(force = false) {
  if (!containerEl || !data) return;
  if (emptyEl) emptyEl.style.display = 'none';
  updateSummary();
  let cv = containerEl.querySelector('canvas[data-renderer="snapshot"]');
  if (!cv) {
    destroyRenderer();
    cv = document.createElement('canvas'); cv.dataset.renderer = 'snapshot'; cv.style.cssText = 'width:100%;height:100%;display:block'; containerEl.appendChild(cv);
  }
  const g = chartGeometry(); const dpr = analyticsMobileDpr();
  cv.width = Math.floor(g.width * dpr); cv.height = Math.floor(g.height * dpr); cv.style.width = `${g.width}px`; cv.style.height = `${g.height}px`;
  const ctx = cv.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,g.width,g.height);
  const bg = ctx.createLinearGradient(0,0,0,g.height); bg.addColorStop(0,'#07111d'); bg.addColorStop(1,'#0b1724'); ctx.fillStyle=bg; ctx.fillRect(0,0,g.width,g.height);
  const pairs = mappedPairs();
  if (!pairs.length) return;
  const price = live.price || data.price;
  const closest = pairs.reduce((best,p,i)=>Math.abs(p.strike-price)<Math.abs(pairs[best].strike-price)?i:best,0);
  const maxRows=g.mobile?19:27; let start=Math.max(0,closest-Math.floor(maxRows/2)); let end=Math.min(pairs.length,start+maxRows); start=Math.max(0,end-maxRows);
  const visible=pairs.slice(start,end); const av=visible.map(p=>Math.abs(p.gex)); const maxAbs=Math.max(percentile(av,.95),1e-9);
  const margin=g.mobile?{left:48,right:54,top:22,bottom:24}:{left:68,right:78,top:24,bottom:28}; const plotW=g.width-margin.left-margin.right, plotH=g.height-margin.top-margin.bottom; const cx=margin.left+plotW/2; const rowH=plotH/visible.length;
  ctx.strokeStyle='rgba(190,210,224,.13)'; for(let i=-2;i<=2;i++){const x=cx+i*plotW/4;ctx.beginPath();ctx.moveTo(x,margin.top);ctx.lineTo(x,g.height-margin.bottom);ctx.stroke();}
  visible.forEach((p,i)=>{const y=margin.top+plotH-(i+.5)*rowH;const n=clamp(p.gex/maxAbs,-1.25,1.25);const x=cx+n*(plotW/2-12);const color=p.gex>0?'#43d79f':'#ff5e78';ctx.shadowColor=color;ctx.shadowBlur=Math.abs(n)>.65?(g.mobile?4:8):0;ctx.fillStyle=color;ctx.globalAlpha=.35+.55*Math.min(1,Math.abs(n));ctx.fillRect(Math.min(cx,x),y-rowH*.28,Math.max(1,Math.abs(x-cx)),Math.max(3,rowH*.56));ctx.shadowBlur=0;ctx.globalAlpha=1;ctx.fillStyle='rgba(211,224,233,.65)';ctx.font=`${g.mobile?7:8}px IBM Plex Mono,monospace`;ctx.textAlign='right';ctx.fillText(p.strike.toFixed(1),margin.left-5,y+3);});
  const marker=(v,label,color,dash=[])=>{if(!Number.isFinite(Number(v)))return;const lo=visible[0].strike,hi=visible.at(-1).strike;if(v<lo||v>hi)return;const y=margin.top+plotH-(v-lo)/(hi-lo)*plotH;ctx.save();ctx.strokeStyle=color;ctx.lineWidth=label==='LIVE'?(g.mobile?1.8:2.3):1.2;ctx.setLineDash(dash);ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(margin.left+plotW,y);ctx.stroke();ctx.restore();ctx.fillStyle=color;ctx.font=`bold ${g.mobile?7:9}px IBM Plex Mono,monospace`;ctx.textAlign='left';ctx.fillText(`${label} ${Number(v).toFixed(1)}`,margin.left+plotW+4,y+3);};
  marker(price,'LIVE','#ffb24b'); marker(Number(live.trade?.entry),'ENTRY','#b4b8bd',[3,3]); marker(Number(live.trade?.stop),'STOP','#ff5e78'); marker(Number(live.trade?.take),'TAKE','#43d79f');
  const f=liveField(price);ctx.fillStyle='rgba(222,230,236,.7)';ctx.font=`${g.mobile?7:9}px IBM Plex Mono,monospace`;ctx.textAlign='left';ctx.fillText(`NEG GEX`,margin.left,14);ctx.textAlign='right';ctx.fillText(`POS GEX · FIELD ${f.normalized>=0?'+':''}${f.normalized.toFixed(2)}`,margin.left+plotW,14);
  ctx.strokeStyle='rgba(203,218,228,.25)';ctx.strokeRect(margin.left,margin.top,plotW,plotH);
  if(statusEl)statusEl.textContent='● OI-GEX SNAPSHOT · LIVE MAPPING';
}

function ensureAnimation() {
  if (currentMode === 'MIGRATION' && !rafId) renderMigration(false);
}
