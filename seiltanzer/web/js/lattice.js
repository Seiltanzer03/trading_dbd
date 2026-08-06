// Probability Lattice v26 — live Galton board backed by option-implied mass.
// Falling balls are a deterministic low-discrepancy sample of CURRENT RND mass.
// ENTRY / TIME-AVERAGE / CURRENT and mass deltas remain analytical overlays.
import { COLORS, setupCanvas } from './util.js';

const ROWS = 9;
const BINS = 24;
const MAX_SAMPLES = 320;
const DOMAIN_STEP_R = 0.25;
const GOLDEN = 0.6180339887498949;

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

export function computeFocusDomain({ edges, T = 2.5, r = 0 }) {
  const rawLo = Array.isArray(edges) && Number.isFinite(Number(edges[0]))
    ? Number(edges[0]) : -2;
  const rawHi = Array.isArray(edges) && Number.isFinite(Number(edges.at(-1)))
    ? Number(edges.at(-1]) : finite(T, 2.5) + 1;
  const take = Math.max(0.25, finite(T, 2.5));
  const current = finite(r, 0);
  let lo = Math.min(-2, current - 0.5);
  let hi = Math.max(take + 1, current + 0.5);
  lo = Math.max(rawLo, floorDomain(lo));
  hi = Math.min(rawHi, ceilDomain(hi));
  if (!(hi > lo)) return { lo: rawLo, hi: rawHi, rawLo, rawHi, compressed: false };
  return { lo, hi, rawLo, rawHi, compressed: lo > rawLo + 1e-9 || hi < rawHi - 1e-9 };
}

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
export function deterministicTarget(probs, edges, sequenceIndex) {
  if (!Array.isArray(probs) || !Array.isArray(edges) || edges.length !== probs.length + 1) return null;
  const u = ((sequenceIndex + 0.5) * GOLDEN) % 1;
  const within = ((sequenceIndex + 0.5) * GOLDEN * GOLDEN) % 1;
  let acc = 0;
  let bin = probs.length - 1;
  for (let i = 0; i < probs.length; i++) {
    acc += Math.max(0, finite(probs[i], 0));
    if (u <= acc + 1e-12) { bin = i; break; }
  }
  return edges[bin] + within * (edges[bin + 1] - edges[bin]);
}

export function initLattice(canvas) {
  const s = {
    active: false, tradeId: null, T: 2.5, r: 0, edges: null,
    entry: null, average: null, current: null,
    tails: { entry: {}, average: {}, current: {} },
    revaluation: null, visualHistory: null, source: null,
    samples: [], balls: [], sequence: 0, dropped: 0,
    lastSpawn: 0, nextSpawnIn: 260, reconnectTimer: null,
  };

  function resetBoard() {
    s.samples = [];
    s.balls = [];
    s.sequence = 0;
    s.dropped = 0;
    s.lastSpawn = 0;
  }
  function canvasHeight() {
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 760;
    return Math.round(Math.max(440, Math.min(590, width * 0.66)));
  }
  function geometry(w, h) {
    const padX = Math.max(42, Math.min(64, w * 0.065));
    const top = 66;
    const boardBottom = h - 214;
    const histTop = boardBottom + 16;
    const histBottom = h - 116;
    const deltaTop = histBottom + 22;
    const deltaBase = h - 58;
    const axisY = h - 25;
    return {
      w, h, padX, top, boardBottom, histTop, histBottom, deltaTop, deltaBase, axisY,
      boardH: boardBottom - top,
      histH: histBottom - histTop,
      binW: (w - 2 * padX) / Math.max(1, (s.current || []).length),
      rowH: (boardBottom - top) / (ROWS + 1),
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
  function pegX(g, row, rightCount) {
    const center = g.padX + (g.w - 2 * g.padX) / 2;
    return center + (2 * rightCount - row) * (g.binW * 0.48);
  }
  function pegY(g, row) { return g.top + row * g.rowH; }

  function spawnBall() {
    if (!s.current || !s.edges) return;
    const targetR = deterministicTarget(s.current, s.edges, s.sequence++);
    if (targetR == null) return;
    const dom = domain();
    const normalized = clamp((targetR - dom.lo) / Math.max(1e-9, dom.hi - dom.lo), 0, 1);
    const rightsNeeded = Math.round(normalized * ROWS);
    const dirs = Array.from({ length: ROWS }, (_, i) => i < rightsNeeded);
    let seed = (s.sequence * 2654435761) >>> 0;
    for (let i = dirs.length - 1; i > 0; i--) {
      seed = (1664525 * seed + 1013904223) >>> 0;
      const j = seed % (i + 1);
      [dirs[i], dirs[j]] = [dirs[j], dirs[i]];
    }
    s.balls.push({ targetR, dirs, seg: 0, t: 0, rights: 0, speed: 0.9 + (seed % 19) / 100 });
  }
  function stepBalls(dtMs) {
    for (const ball of s.balls) {
      const segmentMs = ball.seg < ROWS ? 95 : 210;
      ball.t += (dtMs / segmentMs) * ball.speed;
      while (ball.t >= 1) {
        ball.t -= 1;
        if (ball.seg < ROWS) {
          if (ball.dirs[ball.seg]) ball.rights++;
          ball.seg++;
        } else {
          s.samples.push(ball.targetR);
          if (s.samples.length > MAX_SAMPLES) s.samples.shift();
          s.dropped++;
          ball.done = true;
          break;
        }
      }
    }
    s.balls = s.balls.filter((ball) => !ball.done);
  }
  function ballPosition(g, ball, counts) {
    if (ball.seg < ROWS) {
      const t = ball.t * ball.t * (3 - 2 * ball.t);
      const x0 = pegX(g, ball.seg, ball.rights);
      const x1 = pegX(g, ball.seg + 1, ball.rights + (ball.dirs[ball.seg] ? 1 : 0));
      return { x: x0 + (x1 - x0) * t, y: pegY(g, ball.seg) + g.rowH * ball.t };
    }
    const bin = Math.max(0, binIndexForR(ball.targetR, s.edges));
    const stack = counts[bin] || 0;
    return {
      x: xOfR(g, ball.targetR),
      y: pegY(g, ROWS) + (g.boardBottom - 6 - Math.min(42, stack * 1.7) - pegY(g, ROWS)) * (ball.t * ball.t),
    };
  }

  function consumeTick(tick) {
    if (!tick) return;
    const trade = tick.trade;
    const market = tick.market;
    const history = tick.lattice_visual_history;
    const nextTradeId = trade?.id ?? null;
    if (nextTradeId !== s.tradeId) resetBoard();
    s.active = !!(trade && market);
    s.tradeId = nextTradeId;
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
        if (!sameGrid(s.edges, edges)) resetBoard();
        s.edges = edges.map(Number);
        s.entry = entry;
        s.average = average;
        s.current = current;
        s.visualHistory = history;
        s.tails = { entry: history.entry || {}, average: history.average || {}, current: history.current || {} };
      }
    } else if (Array.isArray(market?.scenario_probs) && Array.isArray(market?.scenario_edges)) {
      const focused = computeFocusDomain({ edges: market.scenario_edges, T: s.T, r: s.r });
      const rebinned = rebinDistribution(market.scenario_probs, market.scenario_edges,
        focused.lo, focused.hi, BINS);
      if (rebinned.visibleMass > 0) {
        if (!sameGrid(s.edges, rebinned.edges)) resetBoard();
        s.edges = rebinned.edges;
        s.current = rebinned.probs;
        s.entry ||= rebinned.probs.slice();
        s.average ||= rebinned.probs.slice();
        s.tails.current = { left_tail: rebinned.leftTail, right_tail: rebinned.rightTail,
          visible_mass: rebinned.visibleMass };
      }
    }
    scheduleDomUpdate();
  }

  function setData(d) {
    const nextTradeId = d.tradeId ?? s.tradeId;
    if (nextTradeId !== s.tradeId) resetBoard();
    s.active = !!d.active;
    s.tradeId = nextTradeId;
    s.T = finite(d.T, s.T);
    s.r = finite(d.r, s.r);
    if (Array.isArray(d.distributionProbs) && Array.isArray(d.edges)) {
      const focused = computeFocusDomain({ edges: d.edges, T: s.T, r: s.r });
      const rebinned = rebinDistribution(d.distributionProbs, d.edges, focused.lo, focused.hi, BINS);
      if (rebinned.visibleMass > 0) {
        if (!sameGrid(s.edges, rebinned.edges)) resetBoard();
        s.edges = rebinned.edges;
        s.current = rebinned.probs;
        s.entry ||= rebinned.probs.slice();
        s.average ||= rebinned.probs.slice();
        s.tails.current = { left_tail: rebinned.leftTail, right_tail: rebinned.rightTail,
          visible_mass: rebinned.visibleMass };
      }
    }
    scheduleDomUpdate();
  }

  function curveY(g, probs, index, maxProb) {
    return g.histBottom - (probs[index] / maxProb) * (g.histH - 8);
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
      const y = curveY(g, probs, index, maxProb);
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
    const counts = empiricalCounts(s.samples, s.edges);
    const maxProb = Math.max(...s.entry, ...s.average, ...s.current, 0.001);
    const zeroX = clamp(xOfR(g, 0), g.padX, w - g.padX);
    const takeX = clamp(xOfR(g, s.T), g.padX, w - g.padX);

    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX, g.top, w - 2 * g.padX, g.deltaBase - g.top);
    ctx.fillStyle = 'rgba(198,55,60,0.035)';
    ctx.fillRect(g.padX, g.top, Math.max(0, zeroX - g.padX), g.histBottom - g.top);
    ctx.fillStyle = 'rgba(46,125,79,0.035)';
    ctx.fillRect(zeroX, g.top, Math.max(0, takeX - zeroX), g.histBottom - g.top);
    ctx.fillStyle = 'rgba(232,98,42,0.055)';
    ctx.fillRect(takeX, g.top, Math.max(0, w - g.padX - takeX), g.histBottom - g.top);

    ctx.fillStyle = COLORS.dim;
    for (let row = 1; row <= ROWS; row++) {
      for (let i = 0; i <= row; i++) {
        ctx.beginPath();
        ctx.arc(pegX(g, row, i), pegY(g, row), 1.55, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    for (let bin = 0; bin < counts.length; bin++) {
      const center = g.padX + (bin + 0.5) * g.binW;
      const shown = Math.min(counts[bin], 24);
      for (let k = 0; k < shown; k++) {
        const col = k % 3;
        const row = Math.floor(k / 3);
        ctx.beginPath();
        ctx.arc(center + (col - 1) * 3.2, g.boardBottom - 5 - row * 5.2, 2.25, 0, Math.PI * 2);
        ctx.fillStyle = takeBin(bin) ? '#E8622A' : favorable(bin) ? COLORS.green : COLORS.red;
        ctx.globalAlpha = 0.78;
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    for (const ball of s.balls) {
      const p = ballPosition(g, ball, counts);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3.1, 0, Math.PI * 2);
      const bin = Math.max(0, binIndexForR(ball.targetR, s.edges));
      ctx.fillStyle = ball.seg < ROWS ? COLORS.ink
        : takeBin(bin) ? '#E8622A' : favorable(bin) ? COLORS.green : COLORS.red;
      ctx.fill();
    }

    for (let index = 0; index < s.current.length; index++) {
      const x = g.padX + index * g.binW;
      const y = curveY(g, s.current, index, maxProb);
      ctx.fillStyle = takeBin(index)
        ? 'rgba(232,98,42,0.26)'
        : favorable(index) ? 'rgba(46,125,79,0.18)' : 'rgba(198,55,60,0.18)';
      ctx.fillRect(x + 1.2, y, g.binW - 2.4, g.histBottom - y);
    }
    drawCurve(ctx, g, s.entry, maxProb, { color: '#1D1B18', width: 1.0, dash: [5, 4], alpha: 0.66 });
    drawCurve(ctx, g, s.average, maxProb, { color: '#7B746C', width: 1.7, alpha: 0.72 });
    drawCurve(ctx, g, s.current, maxProb, { color: '#E8622A', width: 2.4 });

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

    const [headline, headlineColor] = scoreText();
    ctx.font = '700 10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = headlineColor;
    ctx.fillText(`ЖИВАЯ ДОСКА: ${headline}`, g.padX, 17);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.dim;
    ctx.fillText('ШАРИКИ — CURRENT RND  ·  ПУНКТИР — ВХОД  ·  СЕРАЯ — СРЕДНЕЕ  ·  ОРАНЖЕВАЯ — СЕЙЧАС', g.padX, 31);
    ctx.fillText('НИЖНЯЯ ПОЛОСА — Δ МАССЫ К ВХОДУ; ХВОСТЫ ПОКАЗАНЫ ОТДЕЛЬНО И НЕ СЛОЖЕНЫ В КРАЙНИЕ КОРЗИНЫ', g.padX, 44);
    const source = s.source?.label || s.source?.mode || 'SOURCE';
    const weight = finite(s.revaluation?.score?.confidence_weight, 0);
    ctx.textAlign = 'right';
    ctx.fillStyle = COLORS.dim;
    ctx.fillText(`${source} · ДОВЕРИЕ ${(weight * 100).toFixed(0)}%`, w - g.padX, 17);
    ctx.fillText(`ШАРИКОВ ${s.dropped} · ОКНО ${domain().lo.toFixed(2)}…${domain().hi.toFixed(2)}R`, w - g.padX, 31);

    ctx.font = '9px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = COLORS.ink;
    ctx.fillText(`${domain().lo.toFixed(2)}R`, g.padX, g.axisY);
    ctx.textAlign = 'center';
    ctx.fillText('0', zeroX, g.axisY);
    ctx.fillText(`r=${fmtR(s.r)}`, currentX, g.axisY - 13);
    ctx.textAlign = 'right';
    ctx.fillText(`${domain().hi >= 0 ? '+' : ''}${domain().hi.toFixed(2)}R`, w - g.padX, g.axisY);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = COLORS.red;
    ctx.fillText(`← ВНЕ ОКНА ${fmtPct(s.tails.current?.left_tail)}`, g.padX, g.deltaTop - 6);
    ctx.textAlign = 'right';
    ctx.fillStyle = COLORS.green;
    ctx.fillText(`ВНЕ ОКНА ${fmtPct(s.tails.current?.right_tail)} →`, w - g.padX, g.deltaTop - 6);
  }

  function favorableDelta() {
    const buckets = s.revaluation?.change_from_entry?.buckets || {};
    return finite(buckets.green_zone, 0) + finite(buckets.take_tail, 0);
  }
  function adverseDelta() {
    const buckets = s.revaluation?.change_from_entry?.buckets || {};
    return finite(buckets.stop_tail, 0) + finite(buckets.red_zone, 0);
  }
  function rowLabel(id, text) {
    const value = document.getElementById(id);
    const label = value?.parentElement?.querySelector('.lbl');
    if (label) label.textContent = text;
  }
  function updateDom() {
    if (typeof document === 'undefined') return;
    const title = document.querySelector('#panel-lattice h2');
    if (title) title.textContent = 'PROBABILITY LATTICE · ЖИВАЯ ДОСКА ГАЛЬТОНА';
    const resetButton = document.getElementById('btn-lattice-reset');
    if (resetButton) {
      resetButton.textContent = 'СБРОС ШАРИКОВ';
      resetButton.dataset.tip = 'Сбросить только визуальную выборку шариков. Серверная история и option-implied расчёты не удаляются.';
    }
    rowLabel('lat-balls', 'ШАРИКОВ УПАЛО');
    rowLabel('lat-green', 'Δ МАССЫ К ТЕЙКУ');
    rowLabel('lat-conv', 'Δ МАССЫ К СТОПУ');
    rowLabel('lat-calib', 'ДОВЕРИЕ / ИСТОЧНИК');
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
    setText('lat-balls', String(s.dropped));
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
    } catch (_) { /* terminal owns offline reporting */ }
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

  let lastFrame = performance.now();
  function frame(now) {
    const dt = Math.min(50, now - lastFrame);
    lastFrame = now;
    if (s.active && s.current) {
      s.lastSpawn += dt;
      if (s.lastSpawn >= s.nextSpawnIn) {
        s.lastSpawn = 0;
        s.nextSpawnIn = 220 + (s.sequence % 7) * 22;
        spawnBall();
      }
      stepBalls(dt);
    }
    draw(now);
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
    reset() { resetBoard(); scheduleDomUpdate(); },
    get stats() {
      const visible = empiricalCounts(s.samples, s.edges).reduce((a, b) => a + b, 0);
      const green = s.samples.filter((value) => value > 0).length;
      const modelGreen = s.current && s.edges
        ? s.current.reduce((sum, p, i) => sum + (binMid(i) > 0 ? p : 0), 0) : null;
      return {
        dropped: s.dropped,
        greenShare: visible ? green / visible : null,
        pGreenModel: modelGreen,
        convergence: visible && modelGreen != null ? Math.abs(green / visible - modelGreen) : null,
        visibleMass: finite(s.tails.current?.visible_mass, 1),
        leftTail: finite(s.tails.current?.left_tail, 0),
        rightTail: finite(s.tails.current?.right_tail, 0),
        restored: false,
      };
    },
  };
}
