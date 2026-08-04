// Probability Lattice — option-implied распределение сделки в практическом R-масштабе.
// Дальние хвосты не растягивают рабочую область: их масса агрегируется в крайние
// корзины видимого диапазона, а стоп, тейк и текущий r остаются читаемыми.

import { COLORS, setupCanvas } from './util.js';
import { approach, approachArr, pulse } from './anim.js';

const ROWS = 8;
const BINS = 11;

function fmtProb(p) {
  if (p == null || !Number.isFinite(p)) return '—';
  const pct = p * 100;
  if (pct < 0.1) return '<0.1%';
  return `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`;
}

function finite(value, fallback) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}

export function computeFocusDomain({ edges, T = 2.5, r = 0, q10 = null, q90 = null }) {
  const rawLo = Array.isArray(edges) && Number.isFinite(edges[0]) ? Number(edges[0]) : -2;
  const rawHi = Array.isArray(edges) && Number.isFinite(edges.at(-1)) ? Number(edges.at(-1)) : T + 2;
  const take = Math.max(0.25, finite(T, 2.5));
  const current = finite(r, 0);

  // Ядро обязано показывать оба барьера и текущую позицию с воздухом по краям.
  const coreLo = Math.min(-1.45, current - 0.75);
  const coreHi = Math.max(take + 0.75, current + 0.75);
  let lo = Math.min(coreLo, Number.isFinite(Number(q10)) ? Number(q10) - 0.25 : coreLo);
  let hi = Math.max(coreHi, Number.isFinite(Number(q90)) ? Number(q90) + 0.25 : coreHi);

  // Полный RND иногда даёт огромные хвосты. Ограничиваем только визуальный span;
  // вероятность за пределами не теряется — rebinDistribution сложит её по краям.
  const maxSpan = Math.max(5.5, take + 3.5);
  if (hi - lo > maxSpan) {
    const coreSpan = coreHi - coreLo;
    const extra = Math.max(0, maxSpan - coreSpan);
    lo = coreLo - extra * 0.42;
    hi = coreHi + extra * 0.58;
  }

  lo = Math.max(rawLo, lo);
  hi = Math.min(rawHi, hi);
  if (hi - lo < 2.5) {
    const mid = (lo + hi) / 2;
    lo = Math.max(rawLo, mid - 1.25);
    hi = Math.min(rawHi, mid + 1.25);
  }
  if (!(hi > lo)) return { lo: rawLo, hi: rawHi, rawLo, rawHi, compressed: false };
  return {
    lo,
    hi,
    rawLo,
    rawHi,
    compressed: lo > rawLo + 1e-9 || hi < rawHi - 1e-9,
  };
}

export function rebinDistribution(probs, edges, lo, hi, bins = BINS) {
  const out = new Array(bins).fill(0);
  const outEdges = Array.from({ length: bins + 1 }, (_, i) => lo + (hi - lo) * i / bins);
  if (!Array.isArray(probs) || !Array.isArray(edges) || edges.length !== probs.length + 1 || !(hi > lo)) {
    return { probs: out, edges: outEdges };
  }

  for (let i = 0; i < probs.length; i++) {
    const p = Math.max(0, finite(probs[i], 0));
    const a = finite(edges[i], NaN);
    const b = finite(edges[i + 1], NaN);
    if (!p || !Number.isFinite(a) || !Number.isFinite(b) || b <= a) continue;
    const width = b - a;

    if (b <= lo) {
      out[0] += p;
      continue;
    }
    if (a >= hi) {
      out[bins - 1] += p;
      continue;
    }

    if (a < lo) out[0] += p * (Math.min(b, lo) - a) / width;
    if (b > hi) out[bins - 1] += p * (b - Math.max(a, hi)) / width;

    const clippedA = Math.max(a, lo);
    const clippedB = Math.min(b, hi);
    if (clippedB <= clippedA) continue;
    for (let j = 0; j < bins; j++) {
      const overlap = Math.max(0, Math.min(clippedB, outEdges[j + 1]) - Math.max(clippedA, outEdges[j]));
      if (overlap > 0) out[j] += p * overlap / width;
    }
  }

  const total = out.reduce((sum, p) => sum + p, 0);
  if (total > 0) for (let i = 0; i < out.length; i++) out[i] /= total;
  return { probs: out, edges: outEdges };
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
    },
    cur: { probs: null, r: 0, tilt: 0.5 },
    edges: null,
    rawDomain: null,
    domainKey: null,
    counts: new Array(BINS).fill(0),
    balls: [],
    dropped: 0,
    green: 0,
    lastSpawn: 0,
    nextSpawnIn: 480,
  };

  function reset() {
    s.counts.fill(0);
    s.balls = [];
    s.dropped = 0;
    s.green = 0;
  }

  function setData(d) {
    if (d.tradeId !== s.tradeId) {
      s.tradeId = d.tradeId;
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

    const focused = computeFocusDomain({
      edges: rawEdges,
      T: s.T,
      r: d.r,
      q10: d.q10,
      q90: d.q90,
    });
    const rebinned = rebinDistribution(primary, rawEdges, focused.lo, focused.hi, BINS);
    const nextKey = `${focused.lo.toFixed(5)}:${focused.hi.toFixed(5)}:${rebinned.probs.length}`;
    const domainChanged = !!s.domainKey && s.domainKey !== nextKey;
    if (domainChanged) reset();
    s.domainKey = nextKey;
    s.rawDomain = focused;
    s.edges = rebinned.edges;
    s.tgt.probs = rebinned.probs;
    s.tgt.r = finite(d.r, 0);
    s.tgt.tilt = d.hit != null
      ? finite(d.hit, 0.5)
      : rebinned.probs.reduce((sum, p, b) => sum + (binMid(b) >= 0 ? p : 0), 0);
    s.tgt.edge = d.edge;
    s.tgt.hit = d.hit;
    s.tgt.pStop = d.pStop;
    s.tgt.pTake = d.pTake;
    s.tgt.unresolved = d.unresolved;
    s.tgt.q10 = d.q10;
    s.tgt.q50 = d.q50;
    s.tgt.q90 = d.q90;
    s.tgt.mode = d.mode;

    if (!s.cur.probs || s.cur.probs.length !== rebinned.probs.length || domainChanged) {
      s.cur.probs = rebinned.probs.slice();
      s.cur.r = s.tgt.r;
      s.cur.tilt = s.tgt.tilt;
    }
  }

  const binMid = (b) => s.edges ? (s.edges[b] + s.edges[b + 1]) / 2 : 0;
  const isGreen = (b) => binMid(b) > 0;
  const isTail = (b) => binMid(b) >= s.T - 1e-9;
  const domain = () => ({ lo: s.edges?.[0] ?? -1.5, hi: s.edges?.at(-1) ?? s.T + 1 });

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

  const xOfR = (g, R) => {
    const { lo, hi } = domain();
    return g.padX + ((R - lo) / Math.max(hi - lo, 1e-9)) * (g.w - 2 * g.padX);
  };
  const rowShear = (g, j) => (s.cur.tilt - 0.5) * g.binW * 1.25 * (j / ROWS);
  const pegX = (g, j, i) => g.padX + (BINS / 2) * g.binW + (2 * i - j) * g.binW / 2 + rowShear(g, j);
  const pegY = (g, j) => g.padTop + j * g.rowH;

  function sampleBin() {
    const p = s.tgt.probs;
    if (!p) return null;
    const u = Math.random();
    let sum = 0;
    for (let b = 0; b < BINS; b++) {
      sum += p[b];
      if (u <= sum) return b;
    }
    return BINS - 1;
  }

  function spawnBall() {
    const bin = sampleBin();
    if (bin == null) return;
    const rights = Math.round((bin / (BINS - 1)) * ROWS);
    const dirs = [];
    for (let i = 0; i < ROWS; i++) dirs.push(i < rights);
    for (let i = dirs.length - 1; i > 0; i--) {
      const j = (Math.random() * (i + 1)) | 0;
      [dirs[i], dirs[j]] = [dirs[j], dirs[i]];
    }
    s.balls.push({
      bin, dirs, seg: 0, t: 0, rights: 0,
      wob: 3 + Math.random() * 3,
      sp: 0.9 + Math.random() * 0.3,
      settled: false,
    });
  }

  function binH(g, b) {
    const total = Math.max(1, s.dropped);
    return Math.min(g.distH - 6, (s.counts[b] / total) * g.distH * 2.4);
  }

  function ballPos(g, ball) {
    const j = ball.seg;
    const t = ball.t;
    if (j < ROWS) {
      const x0 = pegX(g, j, ball.rights);
      const x1 = pegX(g, j + 1, ball.rights + (ball.dirs[j] ? 1 : 0));
      const y0 = pegY(g, j);
      const y1 = pegY(g, j + 1);
      const te = t * t * (3 - 2 * t);
      const ov = Math.sin(t * Math.PI) * ball.wob * (ball.dirs[j] ? 1 : -1);
      return { x: x0 + (x1 - x0) * te + ov, y: y0 + (y1 - y0) * t };
    }
    const x0 = pegX(g, ROWS, ball.rights);
    const x1 = xOfR(g, binMid(ball.bin));
    return {
      x: x0 + (x1 - x0) * Math.min(1, t * 1.4),
      y: pegY(g, ROWS) + (g.baseY - binH(g, ball.bin) - pegY(g, ROWS)) * (t * t),
    };
  }

  function stepBalls(dt) {
    for (const ball of s.balls) {
      const segmentMs = ball.seg < ROWS ? 90 : 220;
      ball.t += (dt / segmentMs) * ball.sp;
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
    for (const ball of s.balls.filter((item) => item.settled)) {
      s.counts[ball.bin]++;
      s.dropped++;
      if (isGreen(ball.bin)) s.green++;
    }
    s.balls = s.balls.filter((ball) => !ball.settled);
  }

  function draw(now) {
    const height = canvasHeight();
    const { ctx, w, h } = setupCanvas(canvas, height);
    const g = geom(w, h);
    ctx.clearRect(0, 0, w, h);
    if (!s.active || !s.cur.probs) return;

    const baseY = g.baseY;
    const x0 = xOfR(g, 0);
    const maxProb = Math.max(...s.cur.probs, 0.001);
    const dom = domain();

    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 8, baseY - g.distH, w - 2 * g.padX + 16, g.distH);

    const positiveX = Math.max(g.padX, Math.min(w - g.padX, xOfR(g, 0)));
    const glow = 0.06 + 0.05 * pulse(now, 1800);
    ctx.fillStyle = `rgba(232,98,42,${glow})`;
    ctx.fillRect(positiveX, baseY - g.distH, (w - g.padX) - positiveX, g.distH);

    for (let b = 0; b < BINS; b++) {
      const x = g.padX + b * g.binW;
      const barH = (s.cur.probs[b] / maxProb) * (g.distH - 12);
      ctx.fillStyle = isTail(b)
        ? 'rgba(232,98,42,0.5)'
        : isGreen(b) ? COLORS.greenSoft : COLORS.redSoft;
      ctx.fillRect(x + 1.5, baseY - barH, g.binW - 3, barH);
      const empiricalH = binH(g, b);
      ctx.strokeStyle = isGreen(b) ? COLORS.green : COLORS.red;
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

    const quantiles = [
      [s.tgt.q10, 'P10', COLORS.red],
      [s.tgt.q50, 'P50', COLORS.ink],
      [s.tgt.q90, 'P90', COLORS.green],
    ];
    for (const [q, label, color] of quantiles) {
      if (!Number.isFinite(Number(q)) || q < dom.lo || q > dom.hi) continue;
      const x = xOfR(g, Number(q));
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

    if (Number.isFinite(Number(s.tgt.mode)) && s.tgt.mode >= dom.lo && s.tgt.mode <= dom.hi) {
      const modeX = xOfR(g, Number(s.tgt.mode));
      ctx.fillStyle = '#E8622A';
      ctx.beginPath();
      ctx.moveTo(modeX - 4, baseY - 4);
      ctx.lineTo(modeX + 4, baseY - 4);
      ctx.lineTo(modeX, baseY - 11);
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

    for (const [R, color, label] of [
      [-1, COLORS.red, 'СТОП −1R'],
      [s.T, COLORS.green, `ТЕЙК +${s.T.toFixed(2)}R`],
    ]) {
      if (R < dom.lo || R > dom.hi) continue;
      const x = xOfR(g, R);
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

    const currentX = Math.max(g.padX, Math.min(w - g.padX, xOfR(g, s.cur.r)));
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
      ? 'OPTION-ANCHORED · РАБОЧЕЕ ОКНО RND'
      : 'СЦЕНАРНАЯ ПЛОТНОСТЬ · БЕЗ P / EDGE';
    const regime = s.regime ? ` · ВОЛА ${s.regime}` : '';
    ctx.fillText(`РАСПРЕДЕЛЕНИЕ: ${source}${regime}`, w / 2, 12);
    if (s.rawDomain?.compressed) {
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.fillText(
        `ХВОСТЫ ${s.rawDomain.rawLo.toFixed(1)}…${s.rawDomain.rawHi.toFixed(1)}R СЖАТЫ В КРАЙНИЕ КОРЗИНЫ`,
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
      ctx.fillText(`СТОП FIRST-TOUCH≤H ${fmtProb(s.tgt.pStop)}`, g.padX, s.rawDomain?.compressed ? 34 : 24);
    }
    if (s.tgt.pTake != null) {
      ctx.fillStyle = COLORS.green;
      ctx.textAlign = 'right';
      ctx.fillText(
        `ТЕЙК FIRST-TOUCH≤H ${fmtProb(s.tgt.pTake)} · NO-TOUCH ${fmtProb(s.tgt.unresolved)}`,
        w - g.padX,
        s.rawDomain?.compressed ? 34 : 24,
      );
    }

    for (const ball of s.balls) {
      const p = ballPos(g, ball);
      ctx.beginPath();
      ctx.arc(p.x + 1, p.y + 2, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(20,20,15,0.12)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = ball.seg >= ROWS
        ? (isTail(ball.bin) ? '#E8622A' : isGreen(ball.bin) ? COLORS.green : COLORS.red)
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
      if (s.tgt.probs) {
        pGreenModel = s.tgt.probs.reduce((sum, p, b) => sum + (isGreen(b) ? p : 0), 0);
      }
      return {
        dropped: s.dropped,
        greenShare,
        pGreenModel,
        convergence: greenShare != null && pGreenModel != null
          ? Math.abs(greenShare - pGreenModel)
          : null,
      };
    },
  };
}
