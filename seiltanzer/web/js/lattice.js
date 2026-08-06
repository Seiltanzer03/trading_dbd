// Probability Lattice v25 — live distribution revaluation, not random sampling.
// The canvas compares ENTRY / TIME-AVERAGE / CURRENT shapes and animates only
// probability mass that actually moved between consecutive server snapshots.
import { COLORS, setupCanvas } from './util.js';

const BINS = 24;
const DOMAIN_STEP_R = 0.25;
const STORAGE_VERSION = 2;
const STORAGE_PREFIX = 'seiltanzer:lattice:v2:';

function finite(value, fallback = null) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}
function clamp(value, lo, hi) { return Math.max(lo, Math.min(hi, value)); }
function floorDomain(value, step = DOMAIN_STEP_R) {
  return Math.floor((value + 1e-9) / step) * step;
}
function ceilDomain(value, step = DOMAIN_STEP_R) {
  return Math.ceil((value - 1e-9) / step) * step;
}
function fmtPct(value, digits = 1) {
  const n = finite(value);
  return n == null ? '—' : `${(n * 100).toFixed(digits)}%`;
}
function fmtPp(value, digits = 1) {
  const n = finite(value);
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${(n * 100).toFixed(digits)} пп`;
}
function fmtR(value, digits = 2) {
  const n = finite(value);
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}R`;
}
function normalise(values, length = null) {
  if (!Array.isArray(values) || (length != null && values.length !== length)) return null;
  const out = values.map((value) => Math.max(0, finite(value, 0)));
  const total = out.reduce((sum, value) => sum + value, 0);
  return total > 0 ? out.map((value) => value / total) : null;
}
function sameGrid(a, b) {
  return Array.isArray(a) && Array.isArray(b) && a.length === b.length
    && a.every((value, index) => Math.abs(value - b[index]) < 1e-7);
}

/** Stable actionable window retained for old callers/tests. */
export function computeFocusDomain({ edges, T = 2.5, r = 0 }) {
  const rawLo = Array.isArray(edges) && Number.isFinite(Number(edges[0]))
    ? Number(edges[0]) : -2;
  const rawHi = Array.isArray(edges) && Number.isFinite(Number(edges.at(-1)))
    ? Number(edges.at(-1)) : finite(T, 2.5) + 1;
  const take = Math.max(0.25, finite(T, 2.5));
  const current = finite(r, 0);
  let lo = Math.min(-2, current - 0.5);
  let hi = Math.max(take + 1, current + 0.5);
  lo = Math.max(rawLo, floorDomain(lo));
  hi = Math.min(rawHi, ceilDomain(hi));
  if (!(hi > lo)) return { lo: rawLo, hi: rawHi, rawLo, rawHi, compressed: false };
  return { lo, hi, rawLo, rawHi, compressed: lo > rawLo + 1e-9 || hi < rawHi - 1e-9 };
}

/** Rebin without folding true tails into visible endpoint bins. */
export function rebinDistribution(probs, edges, lo, hi, bins = BINS) {
  const out = new Array(bins).fill(0);
  const outEdges = Array.from({ length: bins + 1 }, (_, i) => lo + (hi - lo) * i / bins);
  if (!Array.isArray(probs) || !Array.isArray(edges)
      || edges.length !== probs.length + 1 || !(hi > lo)) {
    return { probs: out, absoluteProbs: out.slice(), edges: outEdges,
      leftTail: 0, rightTail: 0, visibleMass: 0, totalMass: 0 };
  }
  let leftTail = 0;
  let rightTail = 0;
  let totalMass = 0;
  for (let i = 0; i < probs.length; i++) {
    const p = Math.max(0, finite(probs[i], 0));
    const a = finite(edges[i]);
    const b = finite(edges[i + 1]);
    if (!p || a == null || b == null || b <= a) continue;
    totalMass += p;
    const width = b - a;
    if (b <= lo) { leftTail += p; continue; }
    if (a >= hi) { rightTail += p; continue; }
    if (a < lo) leftTail += p * (lo - a) / width;
    if (b > hi) rightTail += p * (b - hi) / width;
    const ca = Math.max(a, lo);
    const cb = Math.min(b, hi);
    for (let j = 0; j < bins && cb > ca; j++) {
      const overlap = Math.max(0, Math.min(cb, outEdges[j + 1]) - Math.max(ca, outEdges[j]));
      if (overlap) out[j] += p * overlap / width;
    }
  }
  if (totalMass > 0 && Math.abs(totalMass - 1) > 1e-12) {
    leftTail /= totalMass;
    rightTail /= totalMass;
    for (let i = 0; i < out.length; i++) out[i] /= totalMass;
    totalMass = 1;
  }
  const absoluteProbs = out.slice();
  const visibleMass = out.reduce((sum, value) => sum + value, 0);
  if (visibleMass > 0) for (let i = 0; i < out.length; i++) out[i] /= visibleMass;
  return { probs: out, absoluteProbs, edges: outEdges, leftTail, rightTail, visibleMass, totalMass };
}

export function binIndexForR(value, edges) {
  if (!Array.isArray(edges) || edges.length < 2) return -1;
  if (value < edges[0] || value > edges.at(-1)) return -1;
  if (value === edges.at(-1)) return edges.length - 2;
  for (let i = 0; i < edges.length - 1; i++) if (value < edges[i + 1]) return i;
  return -1;
}
export function empiricalCounts(samples, edges) {
  const counts = new Array(Math.max(0, (edges?.length || 1) - 1)).fill(0);
  for (const value of samples || []) {
    const index = binIndexForR(value, edges);
    if (index >= 0) counts[index]++;
  }
  return counts;
}
function safeStorage() {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch (_) { return null; }
}
function storageKey(tradeId) { return `${STORAGE_PREFIX}${encodeURIComponent(String(tradeId))}`; }
export function loadPersistedLattice(tradeId, storage = safeStorage()) {
  if (!storage || tradeId == null) return null;
  try {
    const parsed = JSON.parse(storage.getItem(storageKey(tradeId)) || 'null');
    if (!parsed || parsed.version !== STORAGE_VERSION || String(parsed.tradeId) !== String(tradeId)) return null;
    const samples = Array.isArray(parsed.samples)
      ? parsed.samples.map(Number).filter(Number.isFinite).slice(-1600) : [];
    return {
      samples,
      dropped: Math.max(samples.length, finite(parsed.dropped, samples.length)),
      green: clamp(finite(parsed.green, samples.filter((x) => x > 0).length), 0,
        Math.max(samples.length, finite(parsed.dropped, samples.length))),
      leftTailDropped: Math.max(0, Math.floor(finite(parsed.leftTailDropped, 0))),
      rightTailDropped: Math.max(0, Math.floor(finite(parsed.rightTailDropped, 0))),
    };
  } catch (_) { return null; }
}
export function savePersistedLattice(tradeId, state, storage = safeStorage()) {
  if (!storage || tradeId == null) return false;
  try {
    storage.setItem(storageKey(tradeId), JSON.stringify({
      version: STORAGE_VERSION,
      tradeId: String(tradeId),
      samples: (state.samples || []).slice(-1600),
      dropped: Math.max(0, Math.floor(finite(state.dropped, 0))),
      green: Math.max(0, Math.floor(finite(state.green, 0))),
      leftTailDropped: Math.max(0, Math.floor(finite(state.leftTailDropped, 0))),
      rightTailDropped: Math.max(0, Math.floor(finite(state.rightTailDropped, 0))),
    }));
    return true;
  } catch (_) { return false; }
}
export function clearPersistedLattice(tradeId, storage = safeStorage()) {
  if (!storage || tradeId == null) return false;
  try { storage.removeItem(storageKey(tradeId)); return true; } catch (_) { return false; }
}

function flowPlan(previous, current) {
  if (!previous || !current || previous.length !== current.length) return [];
  const surplus = [];
  const deficit = [];
  previous.forEach((value, index) => {
    const delta = value - current[index];
    if (delta > 0.0005) surplus.push({ index, mass: delta });
    if (delta < -0.0005) deficit.push({ index, mass: -delta });
  });
  const flows = [];
  let i = 0;
  let j = 0;
  while (i < surplus.length && j < deficit.length) {
    const mass = Math.min(surplus[i].mass, deficit[j].mass);
    if (mass > 0.0005) flows.push({ from: surplus[i].index, to: deficit[j].index, mass });
    surplus[i].mass -= mass;
    deficit[j].mass -= mass;
    if (surplus[i].mass <= 0.0005) i++;
    if (deficit[j].mass <= 0.0005) j++;
  }
  return flows;
}

export function initLattice(canvas) {
  const s = {
    active: false,
    tradeId: null,
    T: 2.5,
    r: 0,
    edges: null,
    entry: null,
    average: null,
    current: null,
    previousCurrent: null,
    tails: { entry: {}, average: {}, current: {} },
    revaluation: null,
    visualHistory: null,
    particles: [],
    source: null,
    reconnectTimer: null,
    lastTick: null,
  };

  function canvasHeight() {
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 760;
    return Math.round(Math.max(420, Math.min(560, width * 0.62)));
  }
  function geometry(w, h) {
    const padX = Math.max(42, Math.min(64, w * 0.065));
    const top = 64;
    const plotBottom = h - 132;
    const deltaTop = plotBottom + 22;
    const deltaBase = h - 68;
    const axisY = h - 30;
    return {
      w, h, padX, top, plotBottom, deltaTop, deltaBase, axisY,
      plotH: plotBottom - top,
      binW: (w - 2 * padX) / Math.max(1, (s.current || []).length),
    };
  }
  const domain = () => ({ lo: s.edges?.[0] ?? -2, hi: s.edges?.at(-1) ?? s.T + 1 });
  function xOfR(g, value) {
    const { lo, hi } = domain();
    return g.padX + (value - lo) / Math.max(1e-9, hi - lo) * (g.w - 2 * g.padX);
  }
  function binMid(index) { return (s.edges[index] + s.edges[index + 1]) / 2; }
  function favorable(index) { return binMid(index) >= 0; }
  function takeBin(index) { return binMid(index) >= s.T; }

  function makeParticles(previous, current) {
    const flows = flowPlan(previous, current);
    const now = performance.now();
    const particles = [];
    for (const flow of flows) {
      const count = clamp(Math.round(flow.mass * 180), 1, 7);
      for (let k = 0; k < count && particles.length < 52; k++) {
        particles.push({
          from: flow.from,
          to: flow.to,
          mass: flow.mass / count,
          start: now + (particles.length % 13) * 38,
          duration: 900 + ((flow.from * 37 + flow.to * 19 + k * 23) % 420),
          lane: (k - (count - 1) / 2) * 2.2,
        });
      }
    }
    s.particles = particles;
  }

  function consumeTick(tick) {
    if (!tick) return;
    s.lastTick = tick;
    const trade = tick.trade;
    const market = tick.market;
    const history = tick.lattice_visual_history;
    s.active = !!(trade && market);
    s.tradeId = trade?.id ?? null;
    s.T = finite(tick.prob?.T, s.T);
    s.r = finite(tick.prob?.r, s.r);
    s.revaluation = tick.lattice_revaluation || null;
    s.source = s.revaluation?.source_quality || null;
    if (history?.available) {
      const edges = history.current?.edges;
      const current = normalise(history.current?.probs);
      const entry = normalise(history.entry?.probs, current?.length);
      const average = normalise(history.average?.probs, current?.length);
      if (current && entry && average && Array.isArray(edges) && edges.length === current.length + 1) {
        const previous = sameGrid(s.edges, edges) ? s.current?.slice() : null;
        s.edges = edges.map(Number);
        s.previousCurrent = previous;
        s.entry = entry;
        s.average = average;
        s.current = current;
        s.visualHistory = history;
        s.tails = {
          entry: history.entry || {}, average: history.average || {}, current: history.current || {},
        };
        if (previous) makeParticles(previous, current);
      }
    } else if (Array.isArray(market?.scenario_probs) && Array.isArray(market?.scenario_edges)) {
      const focused = computeFocusDomain({ edges: market.scenario_edges, T: s.T, r: s.r });
      const rebinned = rebinDistribution(
        market.scenario_probs, market.scenario_edges, focused.lo, focused.hi, BINS,
      );
      if (rebinned.visibleMass > 0) {
        s.edges = rebinned.edges;
        s.current = rebinned.probs;
        s.entry ||= rebinned.probs.slice();
        s.average ||= rebinned.probs.slice();
      }
    }
    scheduleDomUpdate();
  }

  function setData(d) {
    s.active = !!d.active;
    s.tradeId = d.tradeId ?? s.tradeId;
    s.T = finite(d.T, s.T);
    s.r = finite(d.r, s.r);
    if (!s.current && Array.isArray(d.distributionProbs) && Array.isArray(d.edges)) {
      const focused = computeFocusDomain({ edges: d.edges, T: s.T, r: s.r });
      const rebinned = rebinDistribution(d.distributionProbs, d.edges, focused.lo, focused.hi, BINS);
      if (rebinned.visibleMass > 0) {
        s.edges = rebinned.edges;
        s.entry = rebinned.probs.slice();
        s.average = rebinned.probs.slice();
        s.current = rebinned.probs.slice();
      }
    }
    scheduleDomUpdate();
  }

  function distributionY(g, probs, index, maxProb) {
    return g.plotBottom - (probs[index] / maxProb) * (g.plotH - 16);
  }
  function drawCurve(ctx, g, probs, maxProb, style) {
    if (!probs) return;
    ctx.save();
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.width;
    ctx.setLineDash(style.dash || []);
    ctx.globalAlpha = style.alpha ?? 1;
    ctx.beginPath();
    probs.forEach((value, index) => {
      const x = g.padX + (index + 0.5) * g.binW;
      const y = distributionY(g, probs, index, maxProb);
      if (index) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.restore();
  }
  function drawMarker(ctx, g, value, label, color, dash = [4, 3]) {
    const n = finite(value);
    const { lo, hi } = domain();
    if (n == null || n < lo || n > hi) return;
    const x = xOfR(g, n);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 1.1;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x, g.top);
    ctx.lineTo(x, g.deltaBase);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText(label, x, g.top + 11);
    ctx.restore();
  }
  function scoreText() {
    const score = s.revaluation?.score || {};
    if (score.direction === 'improving') return ['УЛУЧШЕНИЕ', COLORS.green];
    if (score.direction === 'deteriorating') return ['УХУДШЕНИЕ', COLORS.red];
    return ['БЕЗ УСТОЙЧИВОГО СДВИГА', '#8B8176'];
  }

  function draw(now) {
    const { ctx, w, h } = setupCanvas(canvas, canvasHeight());
    const g = geometry(w, h);
    ctx.clearRect(0, 0, w, h);
    if (!s.active || !s.current || !s.entry || !s.average || !s.edges) return;
    const maxProb = Math.max(...s.entry, ...s.average, ...s.current, 0.001);
    const zeroX = clamp(xOfR(g, 0), g.padX, w - g.padX);
    const takeX = clamp(xOfR(g, s.T), g.padX, w - g.padX);

    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX, g.top, w - 2 * g.padX, g.deltaBase - g.top);
    ctx.fillStyle = 'rgba(198,55,60,0.035)';
    ctx.fillRect(g.padX, g.top, Math.max(0, zeroX - g.padX), g.plotBottom - g.top);
    ctx.fillStyle = 'rgba(46,125,79,0.035)';
    ctx.fillRect(zeroX, g.top, Math.max(0, takeX - zeroX), g.plotBottom - g.top);
    ctx.fillStyle = 'rgba(232,98,42,0.055)';
    ctx.fillRect(takeX, g.top, Math.max(0, w - g.padX - takeX), g.plotBottom - g.top);

    for (let index = 0; index < s.current.length; index++) {
      const x = g.padX + index * g.binW;
      const y = distributionY(g, s.current, index, maxProb);
      ctx.fillStyle = takeBin(index)
        ? 'rgba(232,98,42,0.30)'
        : favorable(index) ? 'rgba(46,125,79,0.20)' : 'rgba(198,55,60,0.20)';
      ctx.fillRect(x + 1.2, y, g.binW - 2.4, g.plotBottom - y);
    }

    drawCurve(ctx, g, s.entry, maxProb, { color: '#1D1B18', width: 1.15, dash: [5, 4], alpha: 0.72 });
    drawCurve(ctx, g, s.average, maxProb, { color: '#7B746C', width: 2.0, alpha: 0.78 });
    drawCurve(ctx, g, s.current, maxProb, { color: '#E8622A', width: 2.6 });

    const delta = s.current.map((value, index) => value - s.entry[index]);
    const maxDelta = Math.max(...delta.map(Math.abs), 0.004);
    ctx.strokeStyle = 'rgba(29,27,24,0.25)';
    ctx.beginPath();
    ctx.moveTo(g.padX, g.deltaBase);
    ctx.lineTo(w - g.padX, g.deltaBase);
    ctx.stroke();
    for (let index = 0; index < delta.length; index++) {
      const value = delta[index];
      const effect = value * (favorable(index) ? 1 : -1);
      const barH = Math.abs(value) / maxDelta * (g.deltaBase - g.deltaTop - 5);
      const x = g.padX + index * g.binW + g.binW * 0.22;
      ctx.fillStyle = effect >= 0 ? 'rgba(46,125,79,0.72)' : 'rgba(198,55,60,0.72)';
      ctx.fillRect(x, value >= 0 ? g.deltaBase - barH : g.deltaBase,
        g.binW * 0.56, value >= 0 ? barH : -barH);
    }

    drawMarker(ctx, g, -1, 'СТОП −1R', COLORS.red, [5, 3]);
    drawMarker(ctx, g, s.T, `ТЕЙК +${s.T.toFixed(2)}R`, COLORS.green, [5, 3]);
    const currentX = clamp(xOfR(g, s.r), g.padX, w - g.padX);
    ctx.strokeStyle = '#E8622A';
    ctx.lineWidth = 1.4;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(currentX, g.top - 5);
    ctx.lineTo(currentX, g.deltaBase + 4);
    ctx.stroke();
    ctx.setLineDash([]);

    const q = s.revaluation?.current || {};
    drawMarker(ctx, g, q.q10_r, 'P10', 'rgba(198,55,60,0.75)', [2, 4]);
    drawMarker(ctx, g, q.q50_r, 'P50', 'rgba(29,27,24,0.75)', [3, 3]);
    drawMarker(ctx, g, q.q90_r, 'P90', 'rgba(46,125,79,0.75)', [2, 4]);

    for (const particle of s.particles) {
      const progress = clamp((now - particle.start) / particle.duration, 0, 1);
      if (progress <= 0 || progress >= 1) continue;
      const eased = progress * progress * (3 - 2 * progress);
      const fromX = g.padX + (particle.from + 0.5) * g.binW;
      const toX = g.padX + (particle.to + 0.5) * g.binW;
      const fromY = distributionY(g, s.previousCurrent || s.current, particle.from, maxProb) - 5;
      const toY = distributionY(g, s.current, particle.to, maxProb) - 5;
      const arch = Math.sin(progress * Math.PI) * Math.min(46, Math.abs(toX - fromX) * 0.22 + 13);
      const x = fromX + (toX - fromX) * eased;
      const y = fromY + (toY - fromY) * eased - arch + particle.lane;
      ctx.beginPath();
      ctx.arc(x, y, 2.2 + Math.min(1.5, particle.mass * 40), 0, Math.PI * 2);
      ctx.fillStyle = particle.to > particle.from ? COLORS.green : COLORS.red;
      ctx.globalAlpha = 0.82;
      ctx.fill();
      ctx.globalAlpha = 1;
    }
    s.particles = s.particles.filter((particle) => now - particle.start < particle.duration + 50);

    const [headline, headlineColor] = scoreText();
    ctx.font = '700 10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = headlineColor;
    ctx.fillText(`ПЕРЕОЦЕНКА: ${headline}`, g.padX, 17);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.dim;
    ctx.fillText('ПУНКТИР — ВХОД  ·  СЕРАЯ — СРЕДНЕЕ  ·  ОРАНЖЕВАЯ — СЕЙЧАС', g.padX, 31);
    ctx.fillText('НИЖНЯЯ ПОЛОСА — Δ МАССЫ К ВХОДУ  ·  ТОЧКИ — ФАКТИЧЕСКИЙ ПЕРЕТОК МЕЖДУ СНИМКАМИ', g.padX, 44);

    const source = s.source?.label || s.source?.mode || 'SOURCE';
    const weight = finite(s.revaluation?.score?.confidence_weight, 0);
    ctx.textAlign = 'right';
    ctx.fillStyle = COLORS.dim;
    ctx.fillText(`${source} · ДОВЕРИЕ ${(weight * 100).toFixed(0)}%`, w - g.padX, 17);
    ctx.fillText(`СНИМКОВ ${s.visualHistory?.sample_count || 0} · ОКНО ${domain().lo.toFixed(2)}…${domain().hi.toFixed(2)}R`, w - g.padX, 31);

    ctx.font = '9px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = COLORS.ink;
    ctx.fillText(`${domain().lo.toFixed(2)}R`, g.padX, g.axisY);
    ctx.textAlign = 'center';
    ctx.fillText('0', zeroX, g.axisY);
    ctx.fillText(`r=${fmtR(s.r)}`, currentX, g.axisY - 13);
    ctx.textAlign = 'right';
    ctx.fillText(`+${domain().hi.toFixed(2)}R`, w - g.padX, g.axisY);

    const left = s.tails.current?.left_tail;
    const right = s.tails.current?.right_tail;
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = COLORS.red;
    ctx.fillText(`← ВНЕ ОКНА ${fmtPct(left)}`, g.padX, g.deltaTop - 6);
    ctx.textAlign = 'right';
    ctx.fillStyle = COLORS.green;
    ctx.fillText(`ВНЕ ОКНА ${fmtPct(right)} →`, w - g.padX, g.deltaTop - 6);
  }

  function rowLabel(id, text) {
    const value = document.getElementById(id);
    const label = value?.parentElement?.querySelector('.lbl');
    if (label) label.textContent = text;
  }
  function favorableDelta() {
    const buckets = s.revaluation?.change_from_entry?.buckets || {};
    return finite(buckets.green_zone, 0) + finite(buckets.take_tail, 0);
  }
  function adverseDelta() {
    const buckets = s.revaluation?.change_from_entry?.buckets || {};
    return finite(buckets.stop_tail, 0) + finite(buckets.red_zone, 0);
  }
  function updateDom() {
    if (typeof document === 'undefined') return;
    const title = document.querySelector('#panel-lattice h2');
    if (title) title.textContent = 'PROBABILITY LATTICE · ЖИВАЯ ПЕРЕОЦЕНКА МАССЫ';
    const resetButton = document.getElementById('btn-lattice-reset');
    if (resetButton) {
      resetButton.textContent = 'ПОВТОР ПЕРЕТОКА';
      resetButton.dataset.tip = 'Повторить анимацию реального изменения от распределения на входе к текущему. Серверная история не удаляется.';
    }
    rowLabel('lat-balls', 'СНИМКОВ ИСТОРИИ');
    rowLabel('lat-green', 'Δ МАССЫ К ТЕЙКУ');
    rowLabel('lat-conv', 'Δ МАССЫ К СТОПУ');
    rowLabel('lat-calib', 'ДОВЕРИЕ / ИСТОЧНИК');
    const sampleCount = s.visualHistory?.sample_count || s.revaluation?.sample_count || 0;
    const fav = favorableDelta();
    const adv = adverseDelta();
    const score = s.revaluation?.score || {};
    const source = s.source?.label || s.source?.mode || '—';
    const setText = (id, text, tone = '') => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.className = `val${tone ? ` ${tone}` : ''}`;
    };
    setText('lat-balls', String(sampleCount));
    setText('lat-green', fmtPp(fav), fav > 0.002 ? 'green' : fav < -0.002 ? 'red' : '');
    setText('lat-conv', fmtPp(adv), adv < -0.002 ? 'green' : adv > 0.002 ? 'red' : '');
    setText('lat-calib', `${(finite(score.confidence_weight, 0) * 100).toFixed(0)}% · ${source}`);
    const pAvg = document.getElementById('lat-p-avg');
    if (pAvg && s.revaluation?.entry && s.revaluation?.average) {
      pAvg.textContent = `· вход ${fmtPct(s.revaluation.entry.p_take)} · ср ${fmtPct(s.revaluation.average.p_take)}`;
    }
    const read = document.getElementById('lat-read');
    if (read) {
      const [headline] = scoreText();
      const de = s.revaluation?.change_from_entry || {};
      read.textContent = `${headline}: P тейка ${fmtPp(de.p_take)} · barrier EV ${fmtR(de.barrier_ev_r)} · P50 ${fmtR(de.q50_r)} · масса к тейку ${fmtPp(fav)} · к стопу ${fmtPp(adv)}`;
      read.className = `lat-read ${score.direction === 'improving' ? 'good' : score.direction === 'deteriorating' ? 'bad' : ''}`;
    }
  }
  function scheduleDomUpdate() {
    if (typeof queueMicrotask === 'function') queueMicrotask(updateDom);
    else setTimeout(updateDom, 0);
  }

  async function initialState() {
    try {
      const response = await fetch('/api/state', { cache: 'no-store' });
      if (!response.ok) return;
      const body = await response.json();
      consumeTick(body.tick);
    } catch (_) { /* main terminal owns offline reporting */ }
  }
  function connect() {
    if (typeof WebSocket === 'undefined' || typeof location === 'undefined') return;
    clearTimeout(s.reconnectTimer);
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/ws`);
    socket.onmessage = (event) => {
      try { consumeTick(JSON.parse(event.data)); } catch (_) { /* ignore malformed tick */ }
    };
    socket.onclose = () => { s.reconnectTimer = setTimeout(connect, 2200); };
    socket.onerror = () => socket.close();
  }

  let lastFrame = 0;
  function frame(now) {
    if (now - lastFrame > 25) {
      draw(now);
      lastFrame = now;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  if (typeof window !== 'undefined') {
    initialState();
    connect();
    setInterval(updateDom, 1200);
  }

  return {
    setData,
    reset() {
      if (s.entry && s.current) {
        s.previousCurrent = s.entry.slice();
        makeParticles(s.entry, s.current);
      }
      scheduleDomUpdate();
    },
    get stats() {
      return {
        dropped: s.visualHistory?.sample_count || s.revaluation?.sample_count || 0,
        greenShare: favorableDelta(),
        pGreenModel: null,
        convergence: adverseDelta(),
        visibleMass: finite(s.tails.current?.visible_mass, 1),
        leftTail: finite(s.tails.current?.left_tail, 0),
        rightTail: finite(s.tails.current?.right_tail, 0),
        restored: true,
      };
    },
  };
}
