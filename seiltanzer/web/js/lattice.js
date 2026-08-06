// Probability Lattice v30 — absorbing-column live Galton board.
//
// app.js is the only market-data source. CURRENT RND changes on every tick.
// Each spawned ball freezes that tick's probability snapshot and completes its
// physical path. On landing the ball is absorbed into its bin: the bin column
// grows and becomes the empirical distribution. No settled decorative dots.
import { COLORS, setupCanvas } from './util.js';

const ROWS = 10;
const BINS = ROWS + 1;
const GOLDEN = 0.6180339887498949;
const DOMAIN_STEP_R = 0.25;
const STORAGE_VERSION = 5;
const STORAGE_PREFIX = 'seiltanzer:lattice:v5:';
const LEGACY_STORAGE_PREFIX = 'seiltanzer:lattice:v4:';
const IMPACT_HOLD_MS = 150;
const WARMUP_BALLS = 30;

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
function validDomain(domain) {
  return !!domain && Number.isFinite(Number(domain.lo))
    && Number.isFinite(Number(domain.hi)) && Number(domain.hi) > Number(domain.lo);
}
function normalise(values, expectedLength = null) {
  if (!Array.isArray(values) || (expectedLength != null && values.length !== expectedLength)) return null;
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

// The R axis belongs to the trade and is fixed until the trade is closed.
export function computeFocusDomain({ edges, T = 2.5 }) {
  const rawLo = Array.isArray(edges) && Number.isFinite(Number(edges[0]))
    ? Number(edges[0]) : -2;
  const rawHi = Array.isArray(edges) && Number.isFinite(Number(edges.at(-1)))
    ? Number(edges.at(-1)) : finite(T, 2.5) + 1;
  const take = Math.max(0.25, finite(T, 2.5));
  const lo = floorDomain(-2);
  const hi = ceilDomain(take + 1);
  return {
    lo, hi, rawLo, rawHi,
    compressed: lo > rawLo + 1e-9 || hi < rawHi - 1e-9,
  };
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
  return {
    probs: visibleMass > 0 ? out.map((value) => value / visibleMass) : out,
    edges: outEdges,
    leftTail,
    rightTail,
    visibleMass,
  };
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
  bins = BINS, domainOverride = null,
}) {
  const domain = validDomain(domainOverride)
    ? { lo: Number(domainOverride.lo), hi: Number(domainOverride.hi) }
    : computeFocusDomain({ edges, T, r });
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
  sigma = clamp(sigma, Math.max(0.30, span * 0.085), span * 0.275);
  center = clamp(center, lo + sigma * 0.55, hi - sigma * 0.55);
  const skew = clamp(finite(raw.skew, 0), -1.5, 1.5);
  const outEdges = Array.from({ length: bins + 1 }, (_, i) => lo + span * i / bins);
  const weights = outEdges.slice(0, -1).map((a, i) => {
    const mid = (a + outEdges[i + 1]) / 2;
    const z = (mid - center) / sigma;
    return Math.exp(-0.5 * z * z + skew * 0.08 * z);
  });
  return {
    probs: normalise(weights) || new Array(bins).fill(1 / bins),
    edges: outEdges, lo, hi, center, sigma, skew,
    rawMean: raw.mean, rawSigma: raw.sigma, domain,
  };
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

export function accumulateSnapshotMass(totalMass, snapshotProbs) {
  const out = Array.isArray(totalMass) && totalMass.length === BINS
    ? totalMass.slice() : new Array(BINS).fill(0);
  const snapshot = normalise(snapshotProbs, BINS);
  if (!snapshot) return out;
  for (let i = 0; i < BINS; i++) out[i] += snapshot[i];
  return out;
}

// Early-board semantics: counts become columns. During warm-up the denominator
// is fixed, so every absorbed ball visibly grows exactly one column. Afterwards
// the columns are the empirical probability distribution.
export function columnSharesFromCounts(counts, warmupBalls = WARMUP_BALLS) {
  const safe = Array.isArray(counts)
    ? counts.map((value) => Math.max(0, finite(value, 0))) : new Array(BINS).fill(0);
  const total = safe.reduce((sum, value) => sum + value, 0);
  const denominator = Math.max(1, warmupBalls, total);
  return safe.map((value) => value / denominator);
}
export function columnScaleMax(counts, currentProbs = null, expectedProbs = null) {
  const empirical = columnSharesFromCounts(counts);
  const values = [0.22, ...empirical];
  if (Array.isArray(currentProbs)) values.push(...currentProbs);
  if (Array.isArray(expectedProbs)) values.push(...expectedProbs);
  return Math.min(0.60, Math.max(...values) * 1.14);
}

function safeStorage() {
  try { return typeof localStorage === 'undefined' ? null : localStorage; } catch (_) { return null; }
}
function storageKey(tradeId) { return `${STORAGE_PREFIX}${encodeURIComponent(String(tradeId))}`; }
function legacyStorageKey(tradeId) { return `${LEGACY_STORAGE_PREFIX}${encodeURIComponent(String(tradeId))}`; }
function sanitizeCounts(values) {
  if (!Array.isArray(values)) return null;
  const counts = values.slice(0, BINS).map((n) => Math.max(0, Math.floor(finite(n, 0))));
  return counts.length === BINS ? counts : null;
}
function sanitizeMass(values) {
  if (!Array.isArray(values)) return new Array(BINS).fill(0);
  const mass = values.slice(0, BINS).map((n) => Math.max(0, finite(n, 0)));
  return mass.length === BINS ? mass : new Array(BINS).fill(0);
}
function loadStored(tradeId, storage) {
  if (!storage || tradeId == null) return null;
  try {
    let value = JSON.parse(storage.getItem(storageKey(tradeId)) || 'null');
    if (!value) {
      const legacy = JSON.parse(storage.getItem(legacyStorageKey(tradeId)) || 'null');
      if (legacy && String(legacy.tradeId) === String(tradeId)) {
        value = {
          tradeId: String(tradeId), counts: legacy.counts, green: legacy.green,
          sequence: legacy.sequence, expectedMass: legacy.counts, domain: null,
        };
      }
    }
    if (!value || String(value.tradeId) !== String(tradeId)) return null;
    const counts = sanitizeCounts(value.counts);
    if (!counts) return null;
    return {
      counts,
      displayCounts: counts.map(Number),
      dropped: counts.reduce((sum, n) => sum + n, 0),
      green: Math.max(0, Math.floor(finite(value.green, 0))),
      sequence: Math.max(0, Math.floor(finite(value.sequence, 0))),
      expectedMass: sanitizeMass(value.expectedMass),
      domain: validDomain(value.domain) ? { lo: Number(value.domain.lo), hi: Number(value.domain.hi) } : null,
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
      expectedMass: state.expectedMass,
      domain: state.domain,
    }));
  } catch (_) { /* optional persistence */ }
}
function clearStored(tradeId, storage) {
  if (!storage || tradeId == null) return;
  try {
    storage.removeItem(storageKey(tradeId));
    storage.removeItem(legacyStorageKey(tradeId));
  } catch (_) { /* optional persistence */ }
}

export function initLattice(canvas) {
  const storage = safeStorage();
  const state = {
    active: false,
    tradeId: null,
    T: 2.5,
    r: 0,
    domain: null,
    model: null,
    displayProbs: null,
    rawSlice: null,
    counts: new Array(BINS).fill(0),
    displayCounts: new Array(BINS).fill(0),
    expectedMass: new Array(BINS).fill(0),
    balls: [],
    dropped: 0,
    green: 0,
    sequence: 0,
    modelRevision: 0,
    lastSpawn: 0,
    nextSpawnIn: 300,
    sourceLabel: null,
  };

  function clearRuntime({ keepTradeId = true } = {}) {
    const tradeId = state.tradeId;
    state.active = false;
    state.T = 2.5;
    state.r = 0;
    state.domain = null;
    state.model = null;
    state.displayProbs = null;
    state.rawSlice = null;
    state.counts = new Array(BINS).fill(0);
    state.displayCounts = new Array(BINS).fill(0);
    state.expectedMass = new Array(BINS).fill(0);
    state.balls = [];
    state.dropped = 0;
    state.green = 0;
    state.sequence = 0;
    state.modelRevision = 0;
    state.lastSpawn = 0;
    state.nextSpawnIn = 300;
    state.sourceLabel = null;
    state.tradeId = keepTradeId ? tradeId : null;
  }
  function switchTrade(nextTradeId) {
    if (nextTradeId === state.tradeId) return;
    const previousTradeId = state.tradeId;
    if (previousTradeId != null) {
      if (nextTradeId == null) clearStored(previousTradeId, storage);
      else saveStored(previousTradeId, state, storage);
    }
    clearRuntime({ keepTradeId: false });
    state.tradeId = nextTradeId;
    if (nextTradeId != null) {
      const stored = loadStored(nextTradeId, storage);
      if (stored) Object.assign(state, stored);
    }
  }

  function setData(d) {
    switchTrade(d.tradeId ?? null);
    state.active = !!d.active;
    state.T = Math.max(0.25, finite(d.T, state.T));
    state.r = finite(d.r, state.r);
    state.sourceLabel = d.optionAnchored ? 'OPTIONS RND → LIVE COLUMNS' : 'SCENARIO → LIVE COLUMNS';
    if (!state.active || !Array.isArray(d.distributionProbs) || !Array.isArray(d.edges)) return;
    if (!validDomain(state.domain)) state.domain = computeFocusDomain({ edges: d.edges, T: state.T });
    const nextModel = buildGaltonDistribution({
      probs: d.distributionProbs,
      edges: d.edges,
      T: state.T,
      r: state.r,
      q10: d.q10,
      q50: d.q50,
      q90: d.q90,
      bins: BINS,
      domainOverride: state.domain,
    });
    state.model = nextModel;
    state.displayProbs ||= nextModel.probs.slice();
    state.rawSlice = rebinDistribution(
      d.distributionProbs, d.edges, state.domain.lo, state.domain.hi, BINS,
    );
    state.modelRevision++;
  }

  function canvasHeight() {
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 760;
    return Math.round(Math.max(440, Math.min(560, width * 0.66)));
  }
  function geometry(w, h) {
    const padX = Math.max(36, Math.min(56, w * 0.057));
    const top = 64;
    const axisH = 32;
    const histH = Math.max(150, Math.min(190, h * 0.36));
    const baseY = h - axisH - 8;
    const histTop = baseY - histH;
    const boardBottom = histTop - 8;
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
    const lo = state.domain?.lo ?? -2;
    const hi = state.domain?.hi ?? state.T + 1;
    return g.padX + (value - lo) / Math.max(1e-9, hi - lo) * (g.w - 2 * g.padX);
  }
  function binCenterX(g, bin) { return g.padX + (bin + 0.5) * g.binW; }
  function pegX(g, row, rights) {
    const center = g.padX + BINS * g.binW / 2;
    return center + (2 * rights - row) * g.binW / 2;
  }
  function pegY(g, row) { return g.top + row * g.rowH; }

  function averageExpected() {
    if (!state.dropped) return state.model?.probs?.slice() || new Array(BINS).fill(0);
    return state.expectedMass.map((value) => value / state.dropped);
  }
  function empiricalShares(useDisplay = false) {
    return columnSharesFromCounts(useDisplay ? state.displayCounts : state.counts);
  }
  function scaleMax() {
    return columnScaleMax(state.displayCounts, state.displayProbs, averageExpected());
  }
  function columnHeight(g, bin, countOverride = null) {
    const counts = state.displayCounts.slice();
    if (countOverride != null) counts[bin] = countOverride;
    const shares = columnSharesFromCounts(counts);
    return Math.min(g.histH - 10, shares[bin] / Math.max(1e-9, scaleMax()) * (g.histH - 12));
  }
  function columnTop(g, bin, countOverride = null) {
    return g.baseY - columnHeight(g, bin, countOverride);
  }

  function spawnBall() {
    if (!state.model) return;
    const snapshotProbs = state.model.probs.slice();
    const bin = deterministicBin(snapshotProbs, state.sequence++);
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
      snapshotProbs,
      snapshotRevision: state.modelRevision,
      landingCount: state.counts[bin] + queued,
      dirs,
      seg: 0,
      t: 0,
      rights: 0,
      speed: 0.92 + (seed % 18) / 100,
      wobble: 2.8 + (seed % 17) / 10,
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
        state.expectedMass = accumulateSnapshotMass(state.expectedMass, ball.snapshotProbs);
        saveStored(state.tradeId, state, storage);
      }
      if (result.expired) ball.expired = true;
    }
    state.balls = state.balls.filter((ball) => !ball.expired);
  }
  function smoothVisuals(dtMs) {
    const k = 1 - Math.exp(-dtMs / 130);
    for (let i = 0; i < BINS; i++) {
      state.displayCounts[i] += (state.counts[i] - state.displayCounts[i]) * k;
      if (state.displayProbs && state.model) {
        state.displayProbs[i] += (state.model.probs[i] - state.displayProbs[i]) * (1 - Math.exp(-dtMs / 260));
      }
    }
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
        radius: 3.3,
        alpha: 1,
      };
    }
    const x0 = pegX(g, ROWS, ball.rights);
    const y0 = pegY(g, ROWS);
    const x1 = binCenterX(g, ball.bin);
    const y1 = columnTop(g, ball.bin, ball.landingCount);
    if (ball.impacted) {
      const phase = clamp(ball.impactMs / IMPACT_HOLD_MS, 0, 1);
      return {
        x: x1,
        y: y1 + Math.min(8, columnHeight(g, ball.bin) * phase),
        radius: 3.3 * (1 - phase),
        alpha: 1 - phase,
      };
    }
    const eased = ball.t * ball.t;
    return {
      x: x0 + (x1 - x0) * Math.min(1, ball.t * 1.25),
      y: y0 + (y1 - y0) * eased,
      radius: 3.3,
      alpha: 1,
    };
  }

  function drawLine(ctx, g, probs, scale, style) {
    if (!Array.isArray(probs)) return;
    ctx.save();
    ctx.strokeStyle = style.color;
    ctx.lineWidth = style.width;
    ctx.setLineDash(style.dash || []);
    ctx.globalAlpha = style.alpha ?? 1;
    ctx.beginPath();
    probs.forEach((value, bin) => {
      const x = binCenterX(g, bin);
      const y = g.baseY - value / Math.max(1e-9, scale) * (g.histH - 12);
      if (bin) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.stroke();
    ctx.restore();
  }
  function drawMarker(ctx, g, value, label, color, dash = [4, 3]) {
    if (!state.domain || value < state.domain.lo || value > state.domain.hi) return;
    const x = xOfR(g, value);
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x, g.top - 9);
    ctx.lineTo(x, g.baseY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = 'center';
    ctx.fillText(label, x, g.top - 13);
    ctx.restore();
  }

  function stats() {
    const empirical = state.dropped
      ? state.counts.map((value) => value / state.dropped)
      : new Array(BINS).fill(0);
    const expected = averageExpected();
    const current = state.model?.probs || new Array(BINS).fill(0);
    const greenShare = state.dropped ? state.green / state.dropped : null;
    const pGreenModel = current.reduce((sum, p, bin) => sum + (binIsGreen(bin) ? p : 0), 0);
    const pGreenHistory = expected.reduce((sum, p, bin) => sum + (binIsGreen(bin) ? p : 0), 0);
    const shapeError = state.dropped
      ? 0.5 * empirical.reduce((sum, p, bin) => sum + Math.abs(p - expected[bin]), 0)
      : null;
    const currentShift = state.dropped
      ? 0.5 * current.reduce((sum, p, bin) => sum + Math.abs(p - expected[bin]), 0)
      : null;
    return {
      dropped: state.dropped,
      greenShare,
      pGreenModel,
      pGreenHistory,
      convergence: shapeError,
      shapeError,
      currentShift,
      modelMean: state.model?.center ?? null,
      modelSigma: state.model?.sigma ?? null,
      absorbedColumns: state.counts.slice(),
    };
  }

  function draw() {
    const { ctx, w, h } = setupCanvas(canvas, canvasHeight());
    const g = geometry(w, h);
    ctx.clearRect(0, 0, w, h);
    if (!state.active || !state.model) return;

    const zeroX = clamp(xOfR(g, 0), g.padX, w - g.padX);
    const takeX = clamp(xOfR(g, state.T), g.padX, w - g.padX);
    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 6, g.top - 25, w - 2 * g.padX + 12, g.baseY - g.top + 30);
    ctx.fillStyle = 'rgba(198,55,60,0.035)';
    ctx.fillRect(g.padX, g.top, Math.max(0, zeroX - g.padX), g.baseY - g.top);
    ctx.fillStyle = 'rgba(46,125,79,0.035)';
    ctx.fillRect(zeroX, g.top, Math.max(0, takeX - zeroX), g.baseY - g.top);
    ctx.fillStyle = 'rgba(232,98,42,0.05)';
    ctx.fillRect(takeX, g.top, Math.max(0, w - g.padX - takeX), g.baseY - g.top);

    ctx.fillStyle = COLORS.dim;
    for (let row = 1; row <= ROWS; row++) {
      for (let rights = 0; rights <= row; rights++) {
        ctx.beginPath();
        ctx.arc(pegX(g, row, rights), pegY(g, row), 1.75, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Falling balls are drawn before the histogram. As a column grows after
    // impact, it literally covers the shrinking ball: the column absorbs it.
    for (const ball of state.balls) {
      const point = ballPosition(g, ball);
      if (point.radius <= 0 || point.alpha <= 0) continue;
      ctx.save();
      ctx.globalAlpha = point.alpha;
      ctx.beginPath();
      ctx.arc(point.x + 1, point.y + 1.5, point.radius, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(20,20,15,0.12)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
      ctx.fillStyle = ball.seg < ROWS ? COLORS.ink
        : binIsTake(ball.bin) ? '#E8622A' : binIsGreen(ball.bin) ? COLORS.green : COLORS.red;
      ctx.fill();
      ctx.restore();
    }

    const scale = scaleMax();
    const empirical = empiricalShares(true);
    const expected = averageExpected();
    const current = state.displayProbs || state.model.probs;

    // Empty basket outlines and the actual absorbing columns.
    for (let bin = 0; bin < BINS; bin++) {
      const x = g.padX + bin * g.binW;
      const hgt = empirical[bin] / Math.max(1e-9, scale) * (g.histH - 12);
      ctx.strokeStyle = 'rgba(20,20,15,0.20)';
      ctx.strokeRect(x + 2, g.histTop, g.binW - 4, g.histH);
      ctx.fillStyle = binIsTake(bin)
        ? 'rgba(232,98,42,0.88)'
        : binIsGreen(bin) ? 'rgba(46,125,79,0.84)' : 'rgba(198,55,60,0.76)';
      ctx.fillRect(x + 2, g.baseY - hgt, g.binW - 4, hgt);

      // Grey notch = time-average of launch snapshots; black notch = live tick.
      const expectedY = g.baseY - expected[bin] / scale * (g.histH - 12);
      const currentY = g.baseY - current[bin] / scale * (g.histH - 12);
      ctx.strokeStyle = '#898178';
      ctx.lineWidth = 1.4;
      ctx.setLineDash([3, 2]);
      ctx.beginPath();
      ctx.moveTo(x + 5, expectedY);
      ctx.lineTo(x + g.binW - 5, expectedY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = COLORS.ink;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(x + 8, currentY);
      ctx.lineTo(x + g.binW - 8, currentY);
      ctx.stroke();

      ctx.font = '7px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = COLORS.dim;
      if (state.dropped > 0) {
        const actualShare = state.counts[bin] / state.dropped;
        ctx.fillText(`${(actualShare * 100).toFixed(0)}%`, binCenterX(g, bin), g.baseY - hgt - 5);
      }
    }

    // Contours are secondary references; columns remain the main distribution.
    drawLine(ctx, g, expected, scale,
      { color: '#898178', width: 1.1, dash: [4, 3], alpha: 0.75 });
    drawLine(ctx, g, current, scale,
      { color: COLORS.ink, width: 1.55, alpha: 0.80 });

    drawMarker(ctx, g, -1, 'СТОП −1R', COLORS.red);
    drawMarker(ctx, g, state.T, `ТЕЙК +${state.T.toFixed(2)}R`, COLORS.green);
    drawMarker(ctx, g, state.model.center, 'ЦЕНТР LIVE', '#6F685F', [2, 3]);
    const currentX = clamp(xOfR(g, state.r), g.padX, w - g.padX);
    ctx.strokeStyle = '#E8622A';
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(currentX, g.top - 9);
    ctx.lineTo(currentX, g.baseY + 3);
    ctx.stroke();
    ctx.setLineDash([]);

    const st = stats();
    ctx.font = '700 10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillStyle = COLORS.green;
    ctx.fillText('LIVE GALTON · ШАРИК → ВКЛАД → РОСТ КОЛОНКИ', g.padX, 17);
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.dim;
    ctx.fillText('ЦВЕТНЫЕ КОЛОНКИ — ФАКТИЧЕСКО УПАВШИЕ ШАРИКИ · ЧЁРНЫЕ РИСКИ — CURRENT RND', g.padX, 31);
    ctx.fillText('СЕРЫЕ РИСКИ — СРЕДНЕЕ СНИМКОВ · ОСЕВШИХ ТОЧЕК НЕТ: ИХ МАССА ВНУТРИ КОЛОНОК', g.padX, 43);
    ctx.textAlign = 'right';
    ctx.fillText(`${state.sourceLabel || 'LIVE COLUMNS'} · μ ${fmtR(state.model.center)} · σ ${fmtR(state.model.sigma)}`,
      w - g.padX, 17);
    ctx.fillText(`ВПИТАНО ${state.dropped} · P(+R) ${fmtPct(st.greenShare)} · ОШИБКА ФОРМЫ ${fmtPct(st.shapeError)}`,
      w - g.padX, 31);

    ctx.font = '9px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.ink;
    ctx.textAlign = 'left';
    ctx.fillText(`${state.domain.lo.toFixed(2)}R`, g.padX, h - 9);
    ctx.textAlign = 'center';
    ctx.fillText('0', zeroX, h - 9);
    ctx.fillText(`r=${fmtR(state.r)}`, currentX, g.baseY + 21);
    ctx.textAlign = 'right';
    ctx.fillText(`${state.domain.hi >= 0 ? '+' : ''}${state.domain.hi.toFixed(2)}R`, w - g.padX, h - 9);
  }

  function updateDom() {
    if (typeof document === 'undefined') return;
    const st = stats();
    const title = document.querySelector('#panel-lattice h2');
    if (title) title.textContent = 'PROBABILITY LATTICE · ЖИВАЯ ДОСКА ГАЛЬТОНА';
    const button = document.getElementById('btn-lattice-reset');
    if (button) {
      button.textContent = state.active ? 'ИСТОРИЯ ДО ЗАКРЫТИЯ' : 'СБРОС КОЛОНОК';
      button.disabled = !!state.active;
      button.dataset.tip = state.active
        ? 'Колонки содержат историю текущей сделки и удаляются только после её закрытия.'
        : 'Очистить накопленные колонки, когда активной сделки нет.';
    }
    const labels = {
      'lat-balls': 'ШАРИКОВ ВПИТАНО',
      'lat-green': 'МАССА КОЛОНОК В +R',
      'lat-conv': 'ОШИБКА ФОРМЫ',
      'lat-calib': 'LIVE / ИСТОРИЯ',
    };
    for (const [id, text] of Object.entries(labels)) {
      const value = document.getElementById(id);
      const label = value?.parentElement?.querySelector('.lbl');
      if (label) label.textContent = text;
    }
    const set = (id, text, tone = '') => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = text;
      el.className = `val${tone ? ` ${tone}` : ''}`;
    };
    set('lat-balls', String(st.dropped));
    set('lat-green', fmtPct(st.greenShare),
      st.greenShare > 0.55 ? 'green' : st.greenShare < 0.45 ? 'red' : '');
    set('lat-conv', fmtPct(st.shapeError),
      st.shapeError == null ? '' : st.shapeError < 0.10 ? 'green' : st.shapeError > 0.24 ? 'red' : '');
    set('lat-calib', `${fmtPct(st.pGreenModel)} / ${fmtPct(st.pGreenHistory)}`);
    const read = document.getElementById('lat-read');
    if (read) {
      read.textContent = state.dropped
        ? `КОЛОНКИ = ${state.dropped} впитанных шариков · P(+R) ${fmtPct(st.greenShare)} · средняя ожидаемая ${fmtPct(st.pGreenHistory)} · live ${fmtPct(st.pGreenModel)} · сдвиг live от истории ${fmtPct(st.currentShift)}`
        : 'Колонки пока пусты: первый шарик после приземления увеличит одну из 11 корзин.';
      read.className = 'lat-read';
    }
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
      smoothVisuals(dt);
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
    reset() {
      if (state.active && state.tradeId != null) return false;
      clearStored(state.tradeId, storage);
      clearRuntime({ keepTradeId: true });
      updateDom();
      return true;
    },
    get stats() { return stats(); },
  };
}
