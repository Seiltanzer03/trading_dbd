// Probability Lattice v28 — restored classical Galton board.
//
// Architecture intentionally follows the early working implementation:
//   app.js is the ONLY data source; this module owns no fetch/WebSocket.
//   10 peg decisions map exactly to 11 aligned physical bins.
//   Every completed fall increments one real empirical bin.
//
// The soft background bell is a moment/quantile-matched Galton projection of
// CURRENT option-implied RND into the actionable trade window. It is explicitly
// a board projection, not a replacement for barrier / RND calculations.
import { COLORS, setupCanvas } from './util.js';

const ROWS = 10;
const BINS = ROWS + 1;
const GOLDEN = 0.6180339887498949;
const DOMAIN_STEP_R = 0.25;
const STORAGE_VERSION = 4;
const STORAGE_PREFIX = 'seiltanzer:lattice:v4:';
const IMPACT_HOLD_MS = 150;

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
function normalise(values) {
  if (!Array.isArray(values) || !values.length) return null;
  const out = values.map((value) => Math.max(0, finite(value, 0)));
  const total = out.reduce((sum, value) => sum + value, 0);
  return total > 0 ? out.map((value) => value / total) : null;
}
function fmtPct(value, digits = 1) {
  const n = finite(value);
  return n == null ? '—' : `${(n * 100).toFixed(digits)}%`;
}
function fmtR(value, digits = 2) {
  const n = finite(value);
  if (n == null) return '—';
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}R`;
}
function sameDomain(a, b) {
  return !!a && !!b && Math.abs(a.lo - b.lo) < 1e-7 && Math.abs(a.hi - b.hi) < 1e-7;
}

export function computeFocusDomain({ edges, T = 2.5, r = 0 }) {
  const rawLo = Array.isArray(edges) && Number.isFinite(Number(edges[0]))
    ? Number(edges[0]) : -2;
  const rawHi = Array.isArray(edges) && Number.isFinite(Number(edges.at(-1)))
    ? Number(edges.at(-1)) : finite(T, 2.5) + 1;
  const take = Math.max(0.25, finite(T, 2.5));
  const current = finite(r, 0);
  let lo = Math.min(-2, current - 0.75);
  let hi = Math.max(take + 1, current + 0.75);
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
    return { probs: out, edges: outEdges, leftTail: 0, rightTail: 0, visibleMass: 0 };
  }
  let leftTail = 0;
  let rightTail = 0;
  let total = 0;
  for (let i = 0; i < probs.length; i++) {
    const p = Math.max(0, finite(probs[i], 0));
    const a = finite(edges[i]);
    const b = finite(edges[i + 1]);
    if (!p || a == null || b == null || b <= a) continue;
    total += p;
    const width = b - a;
    if (b <= lo) { leftTail += p; continue; }
    if (a >= hi) { rightTail += p; continue; }
    if (a < lo) leftTail += p * (lo - a) / width;
    if (b > hi) rightTail += p * (b - hi) / width;
    const ca = Math.max(a, lo);
    const cb = Math.min(b, hi);
    for (let j = 0; j < bins; j++) {
      const overlap = Math.max(0, Math.min(cb, outEdges[j + 1]) - Math.max(ca, outEdges[j]));
      if (overlap > 0) out[j] += p * overlap / width;
    }
  }
  if (total > 0 && Math.abs(total - 1) > 1e-12) {
    leftTail /= total;
    rightTail /= total;
    for (let i = 0; i < out.length; i++) out[i] /= total;
  }
  const visibleMass = out.reduce((sum, value) => sum + value, 0);
  return { probs: visibleMass > 0 ? out.map((value) => value / visibleMass) : out,
    edges: outEdges, leftTail, rightTail, visibleMass };
}

function distributionMoments(probs, edges) {
  const p = normalise(probs);
  if (!p || !Array.isArray(edges) || edges.length !== p.length + 1) {
    return { mean: null, sigma: null, skew: null };
  }
  const mids = p.map((_, i) => (Number(edges[i]) + Number(edges[i + 1])) / 2);
  const mean = p.reduce((sum, value, i) => sum + value * mids[i], 0);
  const variance = p.reduce((sum, value, i) => sum + value * (mids[i] - mean) ** 2, 0);
  const sigma = Math.sqrt(Math.max(0, variance));
  const skew = sigma > 1e-9
    ? p.reduce((sum, value, i) => sum + value * ((mids[i] - mean) / sigma) ** 3, 0)
    : 0;
  return { mean, sigma, skew };
}

function quantileFromDistribution(probs, edges, q) {
  const p = normalise(probs);
  if (!p || !Array.isArray(edges) || edges.length !== p.length + 1) return null;
  let acc = 0;
  for (let i = 0; i < p.length; i++) {
    const next = acc + p[i];
    if (q <= next + 1e-12) {
      const within = p[i] > 0 ? clamp((q - acc) / p[i], 0, 1) : 0.5;
      return Number(edges[i]) + within * (Number(edges[i + 1]) - Number(edges[i]));
    }
    acc = next;
  }
  return Number(edges.at(-1));
}

export function buildGaltonDistribution({
  probs, edges, T = 2.5, r = 0, q10 = null, q50 = null, q90 = null,
  bins = BINS,
}) {
  const domain = computeFocusDomain({ edges, T, r });
  const lo = domain.lo;
  const hi = domain.hi;
  const span = Math.max(1e-9, hi - lo);
  const raw = distributionMoments(probs, edges);
  const median = finite(q50, quantileFromDistribution(probs, edges, 0.5));
  const p10 = finite(q10, quantileFromDistribution(probs, edges, 0.1));
  const p90 = finite(q90, quantileFromDistribution(probs, edges, 0.9));

  let center = finite(median, finite(raw.mean, finite(r, 0)));
  let sigma = p10 != null && p90 != null && p90 > p10
    ? (p90 - p10) / 2.563103131
    : finite(raw.sigma, span * 0.18);

  // A Galton board must remain readable as a bell inside the trade window.
  // The centre comes from CURRENT RND, while spread is bounded only for this
  // projection; raw option tails remain in backend probabilities and labels.
  sigma = clamp(sigma, Math.max(0.30, span * 0.085), span * 0.275);
  center = clamp(center, lo + sigma * 0.55, hi - sigma * 0.55);
  const skew = clamp(finite(raw.skew, 0), -1.5, 1.5);
  const outEdges = Array.from({ length: bins + 1 }, (_, i) => lo + span * i / bins);
  const weights = [];
  for (let i = 0; i < bins; i++) {
    const mid = (outEdges[i] + outEdges[i + 1]) / 2;
    const z = (mid - center) / sigma;
    // Small exponential tilt preserves a single bell while carrying RND skew.
    weights.push(Math.exp(-0.5 * z * z + skew * 0.08 * z));
  }
  const model = normalise(weights) || new Array(bins).fill(1 / bins);
  return { probs: model, edges: outEdges, lo, hi, center, sigma, skew,
    rawMean: raw.mean, rawSigma: raw.sigma, domain };
}

export function deterministicBin(probs, sequenceIndex) {
  const p = normalise(probs);
  if (!p) return null;
  const u = ((sequenceIndex + 0.5) * GOLDEN) % 1;
  let acc = 0;
  for (let i = 0; i < p.length; i++) {
    acc += p[i];
    if (u <= acc + 1e-12) return i;
  }
  return p.length - 1;
}

export function deterministicTarget(probs, edges, sequenceIndex) {
  const bin = deterministicBin(probs, sequenceIndex);
  if (bin == null || !Array.isArray(edges) || edges.length !== probs.length + 1) return null;
  const within = ((sequenceIndex + 0.5) * GOLDEN * GOLDEN) % 1;
  return edges[bin] + within * (edges[bin + 1] - edges[bin]);
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

export function advanceBallKinematics(ball, dtMs, rows = ROWS) {
  if (!ball || !(dtMs > 0)) return { landed: false, expired: false };
  if (ball.impacted) {
    ball.impactMs = finite(ball.impactMs, 0) + dtMs;
    return { landed: false, expired: ball.impactMs >= IMPACT_HOLD_MS };
  }
  const segmentMs = ball.seg < rows ? 94 : 235;
  ball.t += dtMs / segmentMs * finite(ball.speed, 1);
  while (ball.t >= 1 && !ball.impacted) {
    if (ball.seg < rows) {
      ball.t -= 1;
      if (ball.dirs[ball.seg]) ball.rights++;
      ball.seg++;
    } else {
      ball.t = 1;
      ball.impacted = true;
      ball.impactMs = 0;
      return { landed: true, expired: false };
    }
  }
  return { landed: false, expired: false };
}

function safeStorage() {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch (_) { return null; }
}
function storageKey(tradeId) { return `${STORAGE_PREFIX}${encodeURIComponent(String(tradeId))}`; }
function loadStored(tradeId, storage) {
  if (!storage || tradeId == null) return null;
  try {
    const value = JSON.parse(storage.getItem(storageKey(tradeId)) || 'null');
    if (!value || value.version !== STORAGE_VERSION || String(value.tradeId) !== String(tradeId)) return null;
    const counts = Array.isArray(value.counts)
      ? value.counts.slice(0, BINS).map((n) => Math.max(0, Math.floor(finite(n, 0)))) : null;
    if (!counts || counts.length !== BINS) return null;
    return {
      counts,
      dropped: counts.reduce((sum, n) => sum + n, 0),
      green: Math.max(0, Math.floor(finite(value.green, 0))),
      sequence: Math.max(0, Math.floor(finite(value.sequence, 0))),
    };
  } catch (_) { return null; }
}
function saveStored(tradeId, state, storage) {
  if (!storage || tradeId == null) return;
  try {
    storage.setItem(storageKey(tradeId), JSON.stringify({
      version: STORAGE_VERSION,
      tradeId: String(tradeId),
      counts: state.counts,
      green: state.green,
      sequence: state.sequence,
    }));
  } catch (_) { /* optional */ }
}
function clearStored(tradeId, storage) {
  if (!storage || tradeId == null) return;
  try { storage.removeItem(storageKey(tradeId)); } catch (_) { /* optional */ }
}

export function initLattice(canvas) {
  const storage = safeStorage();
  const state = {
    active: false,
    tradeId: null,
    T: 2.5,
    r: 0,
    model: null,
    rawSlice: null,
    counts: new Array(BINS).fill(0),
    balls: [],
    dropped: 0,
    green: 0,
    sequence: 0,
    lastSpawn: 0,
    nextSpawnIn: 300,
    sourceLabel: null,
  };

  function reset({ clearStorage = true } = {}) {
    if (clearStorage) clearStored(state.tradeId, storage);
    state.counts = new Array(BINS).fill(0);
    state.balls = [];
    state.dropped = 0;
    state.green = 0;
    state.sequence = 0;
    state.lastSpawn = 0;
  }
  function switchTrade(tradeId) {
    if (tradeId === state.tradeId) return;
    saveStored(state.tradeId, state, storage);
    state.tradeId = tradeId;
    reset({ clearStorage: false });
    const stored = loadStored(tradeId, storage);
    if (stored) Object.assign(state, stored);
  }

  function setData(d) {
    switchTrade(d.tradeId ?? null);
    state.active = !!d.active;
    state.T = Math.max(0.25, finite(d.T, state.T));
    state.r = finite(d.r, state.r);
    state.sourceLabel = d.optionAnchored ? 'OPTIONS RND → GALTON' : 'SCENARIO → GALTON';
    if (!state.active || !Array.isArray(d.distributionProbs) || !Array.isArray(d.edges)) return;
    const nextModel = buildGaltonDistribution({
      probs: d.distributionProbs,
      edges: d.edges,
      T: state.T,
      r: state.r,
      q10: d.q10,
      q50: d.q50,
      q90: d.q90,
      bins: BINS,
    });
    if (!sameDomain(state.model, nextModel)) {
      // This happens only when the actionable window genuinely changes, not on
      // every market tick. Existing bin meanings would otherwise become wrong.
      reset({ clearStorage: true });
    }
    state.model = nextModel;
    state.rawSlice = rebinDistribution(d.distributionProbs, d.edges,
      nextModel.lo, nextModel.hi, BINS);
  }

  function canvasHeight() {
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 760;
    return Math.round(Math.max(430, Math.min(535, width * 0.62)));
  }
  function geometry(w, h) {
    const padX = Math.max(34, Math.min(54, w * 0.055));
    const top = 58;
    const axisH = 28;
    const histH = Math.max(132, Math.min(165, h * 0.32));
    const baseY = h - axisH - 7;
    const histTop = baseY - histH;
    const boardBottom = histTop - 11;
    const rowH = (boardBottom - top) / (ROWS + 0.55);
    const binW = (w - 2 * padX) / BINS;
    return { w, h, padX, top, axisH, histH, baseY, histTop, boardBottom, rowH, binW };
  }
  function binMid(bin) {
    return state.model ? (state.model.edges[bin] + state.model.edges[bin + 1]) / 2 : 0;
  }
  function binIsGreen(bin) { return binMid(bin) > 0; }
  function binIsTake(bin) { return binMid(bin) >= state.T; }
  function xOfR(g, value) {
    const lo = state.model?.lo ?? -2;
    const hi = state.model?.hi ?? state.T + 1;
    return g.padX + (value - lo) / Math.max(1e-9, hi - lo) * (g.w - 2 * g.padX);
  }
  function pegX(g, row, rights) {
    const center = g.padX + BINS * g.binW / 2;
    return center + (2 * rights - row) * g.binW / 2;
  }
  function pegY(g, row) { return g.top + row * g.rowH; }
  function binCenterX(g, bin) { return g.padX + (bin + 0.5) * g.binW; }
  function stackLayout(g) {
    const cols = clamp(Math.floor((g.binW - 4) / 5), 1, 5);
    const gapX = Math.min(4.6, (g.binW - 5) / Math.max(1, cols));
    const gapY = 4.8;
    const rows = Math.max(1, Math.floor((g.histH - 12) / gapY));
    return { cols, gapX, gapY, capacity: cols * rows };
  }
  function stackPoint(g, bin, ordinal) {
    const layout = stackLayout(g);
    const index = Math.min(Math.max(0, ordinal), layout.capacity - 1);
    const col = index % layout.cols;
    const row = Math.floor(index / layout.cols);
    return {
      x: binCenterX(g, bin) + (col - (layout.cols - 1) / 2) * layout.gapX,
      y: g.baseY - 5 - row * layout.gapY,
    };
  }

  function spawnBall() {
    if (!state.model) return;
    const bin = deterministicBin(state.model.probs, state.sequence++);
    if (bin == null) return;
    const dirs = Array.from({ length: ROWS }, (_, i) => i < bin);
    let seed = (state.sequence * 2654435761) >>> 0;
    for (let i = dirs.length - 1; i > 0; i--) {
      seed = (1664525 * seed + 1013904223) >>> 0;
      const j = seed % (i + 1);
      [dirs[i], dirs[j]] = [dirs[j], dirs[i]];
    }
    const queued = state.balls.filter((ball) => ball.bin === bin && !ball.impacted).length;
    state.balls.push({
      bin,
      dirs,
      seg: 0,
      t: 0,
      rights: 0,
      speed: 0.92 + (seed % 18) / 100,
      wobble: 2.8 + (seed % 17) / 10,
      ordinal: state.counts[bin] + queued,
      impacted: false,
      impactMs: 0,
      expired: false,
    });
  }
  function stepBalls(dtMs) {
    for (const ball of state.balls) {
      const result = advanceBallKinematics(ball, dtMs, ROWS);
      if (result.landed) {
        state.counts[ball.bin] += 1;
        state.dropped += 1;
        if (binIsGreen(ball.bin)) state.green += 1;
        saveStored(state.tradeId, state, storage);
      }
      if (result.expired) ball.expired = true;
    }
    state.balls = state.balls.filter((ball) => !ball.expired);
  }
  function ballPosition(g, ball) {
    if (ball.seg < ROWS) {
      const eased = ball.t * ball.t * (3 - 2 * ball.t);
      const x0 = pegX(g, ball.seg, ball.rights);
      const nextRights = ball.rights + (ball.dirs[ball.seg] ? 1 : 0);
      const x1 = pegX(g, ball.seg + 1, nextRights);
      const direction = ball.dirs[ball.seg] ? 1 : -1;
      return {
        x: x0 + (x1 - x0) * eased + Math.sin(ball.t * Math.PI) * ball.wobble * direction,
        y: pegY(g, ball.seg) + (pegY(g, ball.seg + 1) - pegY(g, ball.seg)) * ball.t,
      };
    }
    const target = stackPoint(g, ball.bin, ball.ordinal);
    if (ball.impacted) {
      const phase = clamp(ball.impactMs / IMPACT_HOLD_MS, 0, 1);
      return { x: target.x, y: target.y - Math.sin(phase * Math.PI) * 2.2 };
    }
    const eased = ball.t * ball.t;
    const x0 = pegX(g, ROWS, ball.rights);
    const y0 = pegY(g, ROWS);
    return { x: x0 + (target.x - x0) * eased, y: y0 + (target.y - y0) * eased };
  }

  function modelMax() { return Math.max(...(state.model?.probs || [0.001]), 0.001); }
  function empiricalShares() {
    const total = Math.max(1, state.dropped);
    return state.counts.map((count) => count / total);
  }
  function drawCurve(ctx, g, probs, max, style) {
    if (!Array.isArray(probs)) return;
    ctx.save();
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.width;
    ctx.setLineDash(style.dash || []);
    ctx.globalAlpha = style.alpha ?? 1;
    ctx.beginPath();
    probs.forEach((value, bin) => {
      const x = binCenterX(g, bin);
      const y = g.baseY - value / max * (g.histH - 12);
      if (bin) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.restore();
  }
  function drawMarker(ctx, g, value, label, color, dash = [4, 3]) {
    if (!state.model || value < state.model.lo || value > state.model.hi) return;
    const x = xOfR(g, value);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x, g.top - 8);
    ctx.lineTo(x, g.baseY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText(label, x, g.top - 12);
    ctx.restore();
  }

  function draw() {
    const { ctx, w, h } = setupCanvas(canvas, canvasHeight());
    const g = geometry(w, h);
    ctx.clearRect(0, 0, w, h);
    if (!state.active || !state.model) return;

    const zeroX = clamp(xOfR(g, 0), g.padX, w - g.padX);
    const takeX = clamp(xOfR(g, state.T), g.padX, w - g.padX);
    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 6, g.top - 24, w - 2 * g.padX + 12, g.baseY - g.top + 28);
    ctx.fillStyle = 'rgba(198,55,60,0.035)';
    ctx.fillRect(g.padX, g.top, Math.max(0, zeroX - g.padX), g.baseY - g.top);
    ctx.fillStyle = 'rgba(46,125,79,0.035)';
    ctx.fillRect(zeroX, g.top, Math.max(0, takeX - zeroX), g.baseY - g.top);
    ctx.fillStyle = 'rgba(232,98,42,0.05)';
    ctx.fillRect(takeX, g.top, Math.max(0, w - g.padX - takeX), g.baseY - g.top);

    // Classical triangular peg geometry: bottom row aligns exactly with bin centres.
    ctx.fillStyle = COLORS.dim;
    for (let row = 1; row <= ROWS; row++) {
      for (let rights = 0; rights <= row; rights++) {
        ctx.beginPath();
        ctx.arc(pegX(g, row, rights), pegY(g, row), 1.75, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Expected Galton bell from CURRENT RND moments.
    const maxModel = modelMax();
    for (let bin = 0; bin < BINS; bin++) {
      const x = g.padX + bin * g.binW;
      const height = state.model.probs[bin] / maxModel * (g.histH - 12);
      ctx.fillStyle = binIsTake(bin)
        ? 'rgba(232,98,42,0.30)'
        : binIsGreen(bin) ? COLORS.greenSoft : COLORS.redSoft;
      ctx.fillRect(x + 1.5, g.baseY - height, g.binW - 3, height);
      ctx.strokeStyle = 'rgba(20,20,15,0.16)';
      ctx.strokeRect(x + 1.5, g.histTop, g.binW - 3, g.histH);
    }
    drawCurve(ctx, g, state.model.probs, maxModel,
      { color: COLORS.ink, width: 2.1, alpha: 0.86 });

    // Real landed balls: every visible dot is one counted contribution.
    const layout = stackLayout(g);
    for (let bin = 0; bin < BINS; bin++) {
      const shown = Math.min(state.counts[bin], layout.capacity);
      for (let ordinal = 0; ordinal < shown; ordinal++) {
        const point = stackPoint(g, bin, ordinal);
        ctx.beginPath();
        ctx.arc(point.x, point.y, 2.15, 0, Math.PI * 2);
        ctx.fillStyle = binIsTake(bin) ? '#E8622A' : binIsGreen(bin) ? COLORS.green : COLORS.red;
        ctx.globalAlpha = 0.86;
        ctx.fill();
      }
      if (state.counts[bin] > layout.capacity) {
        ctx.globalAlpha = 1;
        ctx.fillStyle = COLORS.ink;
        ctx.font = '7px "IBM Plex Mono", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`+${state.counts[bin] - layout.capacity}`, binCenterX(g, bin), g.histTop + 9);
      }
    }
    ctx.globalAlpha = 1;

    // Empirical shape grows from the actual landed counts.
    if (state.dropped >= 5) {
      const empirical = empiricalShares();
      const maxEmpirical = Math.max(...empirical, 0.001);
      drawCurve(ctx, g, empirical, maxEmpirical,
        { color: '#E8622A', width: 1.8, dash: [5, 3], alpha: 0.92 });
    }

    for (const ball of state.balls) {
      const point = ballPosition(g, ball);
      ctx.beginPath();
      ctx.arc(point.x + 1, point.y + 1.5, 3.3, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(20,20,15,0.12)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(point.x, point.y, 3.25, 0, Math.PI * 2);
      ctx.fillStyle = ball.seg < ROWS ? COLORS.ink
        : binIsTake(ball.bin) ? '#E8622A' : binIsGreen(ball.bin) ? COLORS.green : COLORS.red;
      ctx.fill();
    }

    drawMarker(ctx, g, -1, 'СТОП −1R', COLORS.red);
    drawMarker(ctx, g, state.T, `ТЕЙК +${state.T.toFixed(2)}R`, COLORS.green);
    drawMarker(ctx, g, state.model.center, 'ЦЕНТР RND', '#6F685F', [2, 3]);
    const currentX = clamp(xOfR(g, state.r), g.padX, w - g.padX);
    ctx.strokeStyle = '#E8622A';
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(currentX, g.top - 8);
    ctx.lineTo(currentX, g.baseY + 3);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font = '700 10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = COLORS.green;
    ctx.fillText('КЛАССИЧЕСКАЯ ДОСКА · 10 РЯДОВ → 11 КОРЗИН', g.padX, 17);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.dim;
    ctx.fillText('МЯГКИЙ КОЛОКОЛ — GALTON-ПРОЕКЦИЯ CURRENT RND · ТОЧКИ И ПУНКТИР — РЕАЛЬНО УПАВШИЕ ШАРИКИ', g.padX, 31);
    ctx.fillText('ОДИН ЗАВЕРШЁННЫЙ ПРОХОД = ОДИН ВКЛАД В КОНКРЕТНУЮ КОРЗИНУ', g.padX, 43);
    ctx.textAlign = 'right';
    ctx.fillText(`${state.sourceLabel || 'GALTON'} · μ ${fmtR(state.model.center)} · σ ${fmtR(state.model.sigma)}`,
      w - g.padX, 17);
    ctx.fillText(`ШАРИКОВ ${state.dropped} · P(+R) ${fmtPct(stats().greenShare)} · ОШИБКА ${fmtPct(stats().convergence)}`,
      w - g.padX, 31);

    ctx.font = '9px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.ink;
    ctx.textAlign = 'left';
    ctx.fillText(`${state.model.lo.toFixed(2)}R`, g.padX, h - 8);
    ctx.textAlign = 'center';
    ctx.fillText('0', zeroX, h - 8);
    ctx.fillText(`r=${fmtR(state.r)}`, currentX, g.baseY + 20);
    ctx.textAlign = 'right';
    ctx.fillText(`${state.model.hi >= 0 ? '+' : ''}${state.model.hi.toFixed(2)}R`, w - g.padX, h - 8);
  }

  function stats() {
    const greenShare = state.dropped ? state.green / state.dropped : null;
    const pGreenModel = state.model
      ? state.model.probs.reduce((sum, p, bin) => sum + (binIsGreen(bin) ? p : 0), 0)
      : null;
    const empirical = empiricalShares();
    const shapeError = state.dropped && state.model
      ? 0.5 * empirical.reduce((sum, p, bin) => sum + Math.abs(p - state.model.probs[bin]), 0)
      : null;
    return {
      dropped: state.dropped,
      greenShare,
      pGreenModel,
      convergence: greenShare != null && pGreenModel != null
        ? Math.abs(greenShare - pGreenModel) : null,
      shapeError,
      modelMean: state.model?.center ?? null,
      modelSigma: state.model?.sigma ?? null,
    };
  }

  function updateDom() {
    if (typeof document === 'undefined') return;
    const current = stats();
    const title = document.querySelector('#panel-lattice h2');
    if (title) title.textContent = 'PROBABILITY LATTICE · ЖИВАЯ ДОСКА ГАЛЬТОНА';
    const button = document.getElementById('btn-lattice-reset');
    if (button) {
      button.textContent = 'СБРОС ШАРИКОВ';
      button.dataset.tip = 'Сбросить только накопленное распределение реально упавших шариков.';
    }
    const labels = {
      'lat-balls': 'ШАРИКОВ УПАЛО',
      'lat-green': 'ШАРИКИ В +R',
      'lat-conv': 'ОШИБКА К МОДЕЛИ',
    };
    for (const [id, text] of Object.entries(labels)) {
      const value = document.getElementById(id);
      const label = value?.parentElement?.querySelector('.lbl');
      if (label) label.textContent = text;
    }
    const balls = document.getElementById('lat-balls');
    const green = document.getElementById('lat-green');
    const conv = document.getElementById('lat-conv');
    if (balls) balls.textContent = String(current.dropped);
    if (green) green.textContent = fmtPct(current.greenShare);
    if (conv) conv.textContent = current.convergence == null
      ? '—' : `${(current.convergence * 100).toFixed(1)} пп`;
  }

  let lastFrame = typeof performance !== 'undefined' ? performance.now() : 0;
  function frame(now) {
    const dt = Math.min(55, Math.max(0, now - lastFrame));
    lastFrame = now;
    if (state.active && state.model) {
      state.lastSpawn += dt;
      if (state.lastSpawn >= state.nextSpawnIn) {
        state.lastSpawn = 0;
        state.nextSpawnIn = 285 + (state.sequence % 7) * 24;
        spawnBall();
      }
      stepBalls(dt);
    }
    draw();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  if (typeof window !== 'undefined') {
    setInterval(updateDom, 500);
    window.addEventListener('beforeunload', () => saveStored(state.tradeId, state, storage));
  }

  return {
    setData,
    reset() { reset({ clearStorage: true }); updateDom(); },
    get stats() { return stats(); },
  };
}
