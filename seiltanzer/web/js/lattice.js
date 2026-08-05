// Probability Lattice — option-implied distribution in an actionable R window.
// The visible histogram is conditional on the working window; distant tails are
// reported separately and are never folded into the first/last ordinary bins.

import { COLORS, setupCanvas } from './util.js';
import { approach, approachArr, pulse } from './anim.js';

const ROWS = 8;
const BINS = 11;
const MAX_SAMPLES = 1600;
const DOMAIN_STEP_R = 0.25;
const WINDOW_MARGIN_R = 1.0;

function fmtProb(p) {
  if (p == null || !Number.isFinite(p)) return '—';
  const pct = p * 100;
  if (pct < 0.1) return '<0.1%';
  return `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`;
}

function finite(value, fallback) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}

function clamp(value, lo, hi) {
  return Math.max(lo, Math.min(hi, value));
}

function floorDomain(value, step = DOMAIN_STEP_R) {
  return Math.floor((value + 1e-9) / step) * step;
}

function ceilDomain(value, step = DOMAIN_STEP_R) {
  return Math.ceil((value - 1e-9) / step) * step;
}

/**
 * Actionable lattice window: one R beyond the stop and one R beyond the take.
 * The live price can expand the window if it has already moved outside it.
 * Quantisation avoids scale jitter from micro-ticks.
 */
export function computeFocusDomain({ edges, T = 2.5, r = 0 }) {
  const rawLo = Array.isArray(edges) && Number.isFinite(Number(edges[0]))
    ? Number(edges[0]) : -2;
  const rawHi = Array.isArray(edges) && Number.isFinite(Number(edges.at(-1)))
    ? Number(edges.at(-1)) : finite(T, 2.5) + WINDOW_MARGIN_R;
  const take = Math.max(0.25, finite(T, 2.5));
  const current = finite(r, 0);

  let lo = Math.min(-1 - WINDOW_MARGIN_R, current - WINDOW_MARGIN_R);
  let hi = Math.max(take + WINDOW_MARGIN_R, current + WINDOW_MARGIN_R);
  lo = Math.max(rawLo, floorDomain(lo));
  hi = Math.min(rawHi, ceilDomain(hi));

  if (hi - lo < 2.5) {
    const mid = (lo + hi) / 2;
    lo = Math.max(rawLo, floorDomain(mid - 1.25));
    hi = Math.min(rawHi, ceilDomain(mid + 1.25));
  }
  if (!(hi > lo)) {
    return { lo: rawLo, hi: rawHi, rawLo, rawHi, compressed: false };
  }
  return {
    lo,
    hi,
    rawLo,
    rawHi,
    compressed: lo > rawLo + 1e-9 || hi < rawHi - 1e-9,
  };
}

/**
 * Rebin only the probability that genuinely lies inside [lo, hi].
 * Outside mass is returned as leftTail/rightTail and never added to edge bins.
 * `probs` is conditional on being inside the visible window, which preserves
 * the readable distribution shape even for very broad ETF-proxy support.
 */
export function rebinDistribution(probs, edges, lo, hi, bins = BINS) {
  const out = new Array(bins).fill(0);
  const outEdges = Array.from({ length: bins + 1 }, (_, i) => lo + (hi - lo) * i / bins);
  if (!Array.isArray(probs) || !Array.isArray(edges)
      || edges.length !== probs.length + 1 || !(hi > lo)) {
    return {
      probs: out,
      absoluteProbs: out.slice(),
      edges: outEdges,
      leftTail: 0,
      rightTail: 0,
      visibleMass: 0,
      totalMass: 0,
    };
  }

  let leftTail = 0;
  let rightTail = 0;
  let totalMass = 0;
  for (let i = 0; i < probs.length; i++) {
    const p = Math.max(0, finite(probs[i], 0));
    const a = finite(edges[i], NaN);
    const b = finite(edges[i + 1], NaN);
    if (!p || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) continue;
    totalMass += p;
    const width = b - a;

    if (b <= lo) {
      leftTail += p;
      continue;
    }
    if (a >= hi) {
      rightTail += p;
      continue;
    }

    if (a < lo) leftTail += p * (Math.min(b, lo) - a) / width;
    if (b > hi) rightTail += p * (b - Math.max(a, hi)) / width;

    const clippedA = Math.max(a, lo);
    const clippedB = Math.min(b, hi);
    if (clippedB <= clippedA) continue;
    for (let j = 0; j < bins; j++) {
      const overlap = Math.max(
        0,
        Math.min(clippedB, outEdges[j + 1]) - Math.max(clippedA, outEdges[j]),
      );
      if (overlap > 0) out[j] += p * overlap / width;
    }
  }

  if (totalMass > 0 && Math.abs(totalMass - 1) > 1e-12) {
    leftTail /= totalMass;
    rightTail /= totalMass;
    for (let i = 0; i < out.length; i++) out[i] /= totalMass;
    totalMass = 1;
  }
  const absoluteProbs = out.slice();
  const visibleMass = absoluteProbs.reduce((sum, p) => sum + p, 0);
  if (visibleMass > 0) {
    for (let i = 0; i < out.length; i++) out[i] /= visibleMass;
  }
  return {
    probs: out,
    absoluteProbs,
    edges: outEdges,
    leftTail,
    rightTail,
    visibleMass,
    totalMass,
  };
}

export function binIndexForR(value, edges) {
  if (!Array.isArray(edges) || edges.length < 2) return -1;
  const bins = edges.length - 1;
  if (value < edges[0] || value > edges.at(-1)) return -1;
  if (value === edges.at(-1)) return bins - 1;
  for (let i = 0; i < bins; i++) {
    if (value < edges[i + 1]) return i;
  }
  return -1;
}

/** Recalculate landed balls for the current visual window without tail folding. */
export function empiricalCounts(samples, edges) {
  const bins = Math.max(0, (edges?.length || 1) - 1);
  const counts = new Array(bins).fill(0);
  for (const value of samples || []) {
    const index = binIndexForR(value, edges);
    if (index >= 0) counts[index]++;
  }
  return counts;
}

export function initLattice(canvas) {
  const s = {
    active: false,
    marketAvail: false,
    T: 2.5,
    tradeId: null,
    regime: null,
    tgt: {
      probs: null, r: 0, tilt: 0.5, edge: null, hit: null,
      pStop: null, pTake: null, unresolved: null,
      q10: null, q50: null, q90: null, mode: null,
      leftTail: 0, rightTail: 0, visibleMass: 1,
    },
    cur: { probs: null, r: 0, tilt: 0.5 },
    edges: null,
    rawDomain: null,
    samples: [],
    balls: [],
    dropped: 0,
    green: 0,
    lastSpawn: 0,
    nextSpawnIn: 480,
  };

  function reset() {
    s.samples = [];
    s.balls = [];
    s.dropped = 0;
    s.green = 0;
    s.lastSpawn = 0;
  }

  function setData(d) {
    if (d.tradeId !== s.tradeId) {
      s.tradeId = d.tradeId;
      s.cur.probs = null;
      s.edges = null;
      s.rawDomain = null;
      reset();
    }
    s.active = !!d.active;
    s.regime = d.regime;
    if (!s.active) return;

    s.T = finite(d.T, 2.5);
    s.marketAvail = !!d.optionAnchored;
    const primary = d.distributionProbs;
    const rawEdges = d.edges;
    if (!Array.isArray(primary) || !primary.length || !Array.isArray(rawEdges)) return;

    const focused = computeFocusDomain({ edges: rawEdges, T: s.T, r: d.r });
    const rebinned = rebinDistribution(primary, rawEdges, focused.lo, focused.hi, BINS);
    if (!(rebinned.visibleMass > 0)) return;

    s.rawDomain = focused;
    s.edges = rebinned.edges;
    s.tgt.probs = rebinned.probs;
    s.tgt.leftTail = rebinned.leftTail;
    s.tgt.rightTail = rebinned.rightTail;
    s.tgt.visibleMass = rebinned.visibleMass;
    s.tgt.r = finite(d.r, 0);
    s.tgt.tilt = d.hit != null
      ? finite(d.hit, 0.5)
      : rebinned.probs.reduce((sum, p, b) => {
        const mid = (rebinned.edges[b] + rebinned.edges[b + 1]) / 2;
        return sum + (mid >= 0 ? p : 0);
      }, 0);
    s.tgt.edge = d.edge;
    s.tgt.hit = d.hit;
    s.tgt.pStop = d.pStop;
    s.tgt.pTake = d.pTake;
    s.tgt.unresolved = d.unresolved;
    s.tgt.q10 = d.q10;
    s.tgt.q50 = d.q50;
    s.tgt.q90 = d.q90;
    s.tgt.mode = d.mode;

    if (!s.cur.probs || s.cur.probs.length !== rebinned.probs.length) {
      s.cur.probs = rebinned.probs.slice();
      s.cur.r = s.tgt.r;
      s.cur.tilt = s.tgt.tilt;
    }
  }

  const domain = () => ({
    lo: s.edges?.[0] ?? -2,
    hi: s.edges?.at(-1) ?? s.T + 1,
  });
  const binMid = (b) => s.edges ? (s.edges[b] + s.edges[b + 1]) / 2 : 0;
  const isGreenBin = (b) => binMid(b) > 0;
  const isTailBin = (b) => binMid(b) >= s.T - 1e-9;

  function canvasHeight() {
    const width = canvas.clientWidth || canvas.parentElement?.clientWidth || 700;
    return Math.round(Math.max(380, Math.min(500, width * 0.58)));
  }

  function geom(w, h) {
    const padX = Math.max(30, Math.min(44, w * 0.045));
    const padTop = 30;
    const axisH = 30;
    const distH = Math.max(145, Math.min(190, h * 0.38));
    const boardH = h - padTop - distH - axisH - 8;
    return {
      padX, padTop, distH, axisH, boardH,
      binW: (w - 2 * padX) / BINS,
      rowH: boardH / (ROWS + 1),
      w, h,
      baseY: h - axisH - 8,
    };
  }

  const xOfR = (g, value) => {
    const { lo, hi } = domain();
    return g.padX + ((value - lo) / Math.max(hi - lo, 1e-9)) * (g.w - 2 * g.padX);
  };
  const rowShear = (g, row) => (s.cur.tilt - 0.5) * g.binW * 1.25 * (row / ROWS);
  const pegX = (g, row, i) => (
    g.padX + (BINS / 2) * g.binW + (2 * i - row) * g.binW / 2 + rowShear(g, row)
  );
  const pegY = (g, row) => g.padTop + row * g.rowH;

  function sampleTargetR() {
    const probs = s.tgt.probs;
    if (!probs || !s.edges) return null;
    const u = Math.random();
    let total = 0;
    let bin = BINS - 1;
    for (let i = 0; i < BINS; i++) {
      total += probs[i];
      if (u <= total) { bin = i; break; }
    }
    const a = s.edges[bin];
    const b = s.edges[bin + 1];
    return a + Math.random() * Math.max(b - a, 1e-9);
  }

  function spawnBall() {
    const targetR = sampleTargetR();
    if (targetR == null) return;
    const dom = domain();
    const normalized = clamp((targetR - dom.lo) / Math.max(dom.hi - dom.lo, 1e-9), 0, 1);
    const rights = Math.round(normalized * ROWS);
    const dirs = [];
    for (let i = 0; i < ROWS; i++) dirs.push(i < rights);
    for (let i = dirs.length - 1; i > 0; i--) {
      const j = (Math.random() * (i + 1)) | 0;
      [dirs[i], dirs[j]] = [dirs[j], dirs[i]];
    }
    s.balls.push({
      targetR, dirs, seg: 0, t: 0, rights: 0,
      wob: 3 + Math.random() * 3,
      sp: 0.9 + Math.random() * 0.3,
      settled: false,
    });
  }

  function binHeight(g, counts, bin) {
    const total = Math.max(1, s.samples.length);
    return Math.min(g.distH - 6, (counts[bin] / total) * g.distH * 2.4);
  }

  function ballPos(g, ball, counts) {
    const row = ball.seg;
    const t = ball.t;
    if (row < ROWS) {
      const x0 = pegX(g, row, ball.rights);
      const x1 = pegX(g, row + 1, ball.rights + (ball.dirs[row] ? 1 : 0));
      const y0 = pegY(g, row);
      const y1 = pegY(g, row + 1);
      const eased = t * t * (3 - 2 * t);
      const wobble = Math.sin(t * Math.PI) * ball.wob * (ball.dirs[row] ? 1 : -1);
      return { x: x0 + (x1 - x0) * eased + wobble, y: y0 + (y1 - y0) * t };
    }
    const bin = binIndexForR(ball.targetR, s.edges);
    const x0 = pegX(g, ROWS, ball.rights);
    const x1 = clamp(xOfR(g, ball.targetR), g.padX, g.w - g.padX);
    return {
      x: x0 + (x1 - x0) * Math.min(1, t * 1.4),
      y: pegY(g, ROWS) + (g.baseY - binHeight(g, counts, Math.max(0, bin)) - pegY(g, ROWS)) * (t * t),
    };
  }

  function stepBalls(dtMs) {
    for (const ball of s.balls) {
      const segmentMs = ball.seg < ROWS ? 90 : 220;
      ball.t += (dtMs / segmentMs) * ball.sp;
      while (ball.t >= 1) {
        ball.t -= 1;
        if (ball.seg < ROWS) {
          if (ball.dirs[ball.seg]) ball.rights++;
          ball.seg++;
        } else {
          ball.settled = true;
          ball.t = 1;
          break;
        }
      }
    }
    const settled = s.balls.filter((ball) => ball.settled);
    for (const ball of settled) {
      s.samples.push(ball.targetR);
      if (s.samples.length > MAX_SAMPLES) s.samples.shift();
      s.dropped++;
      if (ball.targetR > 0) s.green++;
    }
    s.balls = s.balls.filter((ball) => !ball.settled);
  }

  function drawTailBadge(ctx, g, side, mass) {
    if (!(mass > 0.0005)) return;
    const left = side === 'left';
    const x = left ? g.padX : g.w - g.padX;
    const y0 = g.baseY - g.distH;
    ctx.fillStyle = left ? 'rgba(198,55,60,0.12)' : 'rgba(46,125,79,0.12)';
    ctx.fillRect(left ? x - 7 : x + 1, y0, 6, g.distH);
    ctx.fillStyle = left ? COLORS.red : COLORS.green;
    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textAlign = left ? 'left' : 'right';
    ctx.fillText(`${left ? '←' : '→'} ${(mass * 100).toFixed(1)}% ЗА ОКНОМ`, x, y0 - 4);
  }

  function draw(now) {
    const height = canvasHeight();
    const { ctx, w, h } = setupCanvas(canvas, height);
    const g = geom(w, h);
    ctx.clearRect(0, 0, w, h);
    if (!s.active || !s.cur.probs || !s.edges) return;

    const counts = empiricalCounts(s.samples, s.edges);
    const baseY = g.baseY;
    const x0 = xOfR(g, 0);
    const maxProb = Math.max(...s.cur.probs, 0.001);
    const dom = domain();

    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 8, baseY - g.distH, w - 2 * g.padX + 16, g.distH);
    const positiveX = clamp(xOfR(g, 0), g.padX, w - g.padX);
    const glow = 0.06 + 0.05 * pulse(now, 1800);
    ctx.fillStyle = `rgba(232,98,42,${glow})`;
    ctx.fillRect(positiveX, baseY - g.distH, (w - g.padX) - positiveX, g.distH);

    for (let b = 0; b < BINS; b++) {
      const x = g.padX + b * g.binW;
      const marketH = (s.cur.probs[b] / maxProb) * (g.distH - 12);
      ctx.fillStyle = isTailBin(b)
        ? 'rgba(232,98,42,0.5)'
        : isGreenBin(b) ? COLORS.greenSoft : COLORS.redSoft;
      ctx.fillRect(x + 1.5, baseY - marketH, g.binW - 3, marketH);
      const empiricalH = binHeight(g, counts, b);
      ctx.strokeStyle = isGreenBin(b) ? COLORS.green : COLORS.red;
      ctx.lineWidth = 1.1;
      ctx.strokeRect(x + 1.5, baseY - empiricalH, g.binW - 3, empiricalH);
    }

    ctx.beginPath();
    for (let b = 0; b < BINS; b++) {
      const cx = g.padX + (b + 0.5) * g.binW;
      const cy = baseY - (s.cur.probs[b] / maxProb) * (g.distH - 12);
      if (b) ctx.lineTo(cx, cy); else ctx.moveTo(cx, cy);
    }
    ctx.strokeStyle = '#E8622A';
    ctx.lineWidth = 2;
    ctx.stroke();

    drawTailBadge(ctx, g, 'left', s.tgt.leftTail);
    drawTailBadge(ctx, g, 'right', s.tgt.rightTail);

    const quantiles = [
      [s.tgt.q10, 'P10', COLORS.red],
      [s.tgt.q50, 'P50', COLORS.ink],
      [s.tgt.q90, 'P90', COLORS.green],
    ];
    for (const [value, label, color] of quantiles) {
      const q = Number(value);
      if (!Number.isFinite(q) || q < dom.lo || q > dom.hi) continue;
      const x = xOfR(g, q);
      ctx.strokeStyle = color;
      ctx.lineWidth = label === 'P50' ? 1.5 : 1;
      ctx.setLineDash(label === 'P50' ? [4, 2] : [2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, baseY - g.distH);
      ctx.lineTo(x, baseY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(label, x, baseY - g.distH + 10);
    }

    const mode = Number(s.tgt.mode);
    if (Number.isFinite(mode) && mode >= dom.lo && mode <= dom.hi) {
      const x = xOfR(g, mode);
      ctx.fillStyle = '#E8622A';
      ctx.beginPath();
      ctx.moveTo(x - 4, baseY - 4);
      ctx.lineTo(x + 4, baseY - 4);
      ctx.lineTo(x, baseY - 11);
      ctx.closePath();
      ctx.fill();
    }

    ctx.strokeStyle = COLORS.rule;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x0, g.padTop - 10);
    ctx.lineTo(x0, baseY);
    ctx.stroke();
    ctx.setLineDash([]);

    for (const [value, color, label] of [
      [-1, COLORS.red, 'СТОП −1R'],
      [s.T, COLORS.green, `ТЕЙК +${s.T.toFixed(2)}R`],
    ]) {
      if (value < dom.lo || value > dom.hi) continue;
      const x = xOfR(g, value);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.3;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      ctx.moveTo(x, baseY - g.distH);
      ctx.lineTo(x, baseY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(label, x, baseY - g.distH + 20);
    }

    const currentX = clamp(xOfR(g, s.cur.r), g.padX, w - g.padX);
    ctx.strokeStyle = '#E8622A';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([2, 2]);
    ctx.beginPath();
    ctx.moveTo(currentX, g.padTop - 10);
    ctx.lineTo(currentX, baseY + 4);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#E8622A';
    ctx.beginPath();
    ctx.moveTo(currentX - 5, baseY + 4);
    ctx.lineTo(currentX + 5, baseY + 4);
    ctx.lineTo(currentX, baseY - 3);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = COLORS.ink;
    ctx.font = '10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`${dom.lo >= 0 ? '+' : ''}${dom.lo.toFixed(2)}R`, g.padX, h - 8);
    ctx.textAlign = 'center';
    ctx.fillText('0', x0, h - 8);
    ctx.fillText(`r=${s.cur.r >= 0 ? '+' : ''}${s.cur.r.toFixed(2)}`, currentX, baseY + 20);
    ctx.textAlign = 'right';
    ctx.fillText(`${dom.hi >= 0 ? '+' : ''}${dom.hi.toFixed(2)}R`, w - g.padX, h - 8);

    ctx.fillStyle = COLORS.dim;
    for (let row = 1; row <= ROWS; row++) {
      for (let i = 0; i <= row; i++) {
        ctx.beginPath();
        ctx.arc(pegX(g, row, i), pegY(g, row), 1.6, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    ctx.textAlign = 'center';
    ctx.font = '9px "IBM Plex Mono", monospace';
    ctx.fillStyle = COLORS.dim;
    const source = s.marketAvail
      ? 'OPTION-ANCHORED · АДАПТИВНОЕ ОКНО СДЕЛКИ'
      : 'СЦЕНАРНАЯ ПЛОТНОСТЬ · БЕЗ P / EDGE';
    const regime = s.regime ? ` · ВОЛА ${s.regime}` : '';
    ctx.fillText(`РАСПРЕДЕЛЕНИЕ: ${source}${regime}`, w / 2, 12);
    const hasOutside = s.tgt.leftTail > 0.0005 || s.tgt.rightTail > 0.0005;
    if (hasOutside) {
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.fillText(
        `ОКНО ${dom.lo.toFixed(2)}…${dom.hi.toFixed(2)}R · ВНУТРИ ${(s.tgt.visibleMass * 100).toFixed(1)}% · ХВОСТЫ ПОКАЗАНЫ ОТДЕЛЬНО`,
        w / 2,
        23,
      );
    }

    if (s.tgt.edge != null) {
      const edge = Number(s.tgt.edge);
      ctx.fillStyle = edge >= 0 ? COLORS.green : COLORS.red;
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(`OPTION EDGE vs EV=0 ${edge >= 0 ? '+' : ''}${(edge * 100).toFixed(1)}%`, w - g.padX, 12);
    }
    ctx.font = '8px "IBM Plex Mono", monospace';
    if (s.tgt.pStop != null) {
      ctx.fillStyle = COLORS.red;
      ctx.textAlign = 'left';
      ctx.fillText(`СТОП FIRST-TOUCH≤H ${fmtProb(s.tgt.pStop)}`, g.padX, hasOutside ? 34 : 24);
    }
    if (s.tgt.pTake != null) {
      ctx.fillStyle = COLORS.green;
      ctx.textAlign = 'right';
      ctx.fillText(
        `ТЕЙК FIRST-TOUCH≤H ${fmtProb(s.tgt.pTake)} · NO-TOUCH ${fmtProb(s.tgt.unresolved)}`,
        w - g.padX,
        hasOutside ? 34 : 24,
      );
    }

    for (const ball of s.balls) {
      const p = ballPos(g, ball, counts);
      ctx.beginPath();
      ctx.arc(p.x + 1, p.y + 2, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(20,20,15,0.12)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
      const bin = binIndexForR(ball.targetR, s.edges);
      ctx.fillStyle = ball.seg >= ROWS
        ? (isTailBin(Math.max(0, bin)) ? '#E8622A' : ball.targetR > 0 ? COLORS.green : COLORS.red)
        : COLORS.ink;
      ctx.fill();
    }
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    if (s.active && s.tgt.probs) {
      s.cur.probs = approachArr(s.cur.probs, s.tgt.probs, dt, 7);
      s.cur.r = approach(s.cur.r, s.tgt.r, dt, 8);
      s.cur.tilt = approach(s.cur.tilt, s.tgt.tilt, dt, 6);
      s.lastSpawn += dt * 1000;
      if (s.lastSpawn >= s.nextSpawnIn) {
        s.lastSpawn = 0;
        s.nextSpawnIn = 360 + Math.random() * 240;
        spawnBall();
      }
      stepBalls(dt * 1000);
    }
    draw(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    setData,
    reset,
    get stats() {
      const greenShare = s.dropped ? s.green / s.dropped : null;
      let pGreenModel = null;
      if (s.tgt.probs && s.edges) {
        pGreenModel = s.tgt.probs.reduce((sum, p, b) => sum + (binMid(b) > 0 ? p : 0), 0);
      }
      return {
        dropped: s.dropped,
        greenShare,
        pGreenModel,
        convergence: greenShare != null && pGreenModel != null
          ? Math.abs(greenShare - pGreenModel)
          : null,
        visibleMass: s.tgt.visibleMass,
        leftTail: s.tgt.leftTail,
        rightTail: s.tgt.rightTail,
      };
    },
  };
}
