// Probability Lattice — распределение исхода сделки из ОПЦИОННОГО РЫНКА.
//
//  • заполненные корзины — risk-neutral плотность рынка q(S) в R-координатах
//    сделки (красное слева от 0 / зелёное справа): это честные шансы, которые
//    закладывает опционный рынок;
//  • оранжевая линия — та же рыночная плотность (огибающая), тёмная линия —
//    проекция ВАШЕЙ модели (винрейт+вола); их расхождение = КРАЙ;
//  • шарики Монте-Карло сэмплируются из рыночного распределения и сходятся к нему;
//  • маркер r и наклон доски скользят в реальном времени (сглаживание 60fps);
//  • правая тейл-зона подсвечивается оранжевым — «куда платит рынок».

import { COLORS, setupCanvas } from './util.js';
import { approach, approachArr, pulse } from './anim.js';

const ROWS = 8;
const BINS = 11;
const H = 380;

function fmtProb(p) {
  if (p == null || !Number.isFinite(p)) return '—';
  const pct = p * 100;
  if (pct < 0.1) return '<0.1%';
  return `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`;
}

export function initLattice(canvas) {
  const s = {
    active: false, marketAvail: false,
    T: 2.5, tradeId: null, regime: null,
    tgt: { probs: null, r: 0, tilt: 0.5, edge: null, hit: null,
           pStop: null, pTake: null, unresolved: null,
           q10: null, q50: null, q90: null, mode: null },
    cur: { probs: null, r: 0, tilt: 0.5 },
    edges: null,
    counts: new Array(BINS).fill(0), balls: [], dropped: 0, green: 0,
    lastSpawn: 0, nextSpawnIn: 480,
  };

  function reset() { s.counts.fill(0); s.balls = []; s.dropped = 0; s.green = 0; }

  function setData(d) {
    if (d.tradeId !== s.tradeId) { s.tradeId = d.tradeId; reset(); }
    s.active = d.active; s.regime = d.regime;
    if (!d.active) return;
    s.T = d.T ?? 2.5;
    s.marketAvail = !!d.optionAnchored;
    const primary = d.distributionProbs;
    if (!primary || !primary.length) return;
    s.tgt.probs = primary;
    s.tgt.r = d.r ?? 0;
    s.tgt.tilt = d.hit != null ? d.hit
      : primary.reduce((a, v, b) => a + ((d.edges?.[b] ?? -1) >= 0 ? v : 0), 0);
    s.tgt.edge = d.edge;
    s.tgt.hit = d.hit;
    s.tgt.pStop = d.pStop;
    s.tgt.pTake = d.pTake;
    s.tgt.unresolved = d.unresolved;
    s.tgt.q10 = d.q10; s.tgt.q50 = d.q50; s.tgt.q90 = d.q90; s.tgt.mode = d.mode;
    s.edges = d.edges;
    if (!s.cur.probs) {
      s.cur.probs = primary.slice(); s.cur.r = s.tgt.r; s.cur.tilt = s.tgt.tilt;
    }
  }

  const binMid = (b) => s.edges ? (s.edges[b] + s.edges[b + 1]) / 2 : 0;
  const isGreen = (b) => binMid(b) > 0;
  const isTail = (b) => binMid(b) >= s.T - 1e-9;

  function geom(w) {
    const padX = 30, padTop = 26, distH = 152, axisH = 26;
    const boardH = H - padTop - distH - axisH - 6;
    return { padX, padTop, distH, axisH, boardH,
             binW: (w - 2 * padX) / BINS, rowH: boardH / (ROWS + 1), w,
             baseY: H - axisH - 6 };
  }
  const domain = () => ({ lo: s.edges?.[0] ?? -1, hi: s.edges?.at(-1) ?? s.T });
  const xOfR = (g, R) => {
    const { lo, hi } = domain();
    return g.padX + ((R - lo) / Math.max(hi - lo, 1e-9)) * (g.w - 2 * g.padX);
  };
  const rowShear = (g, j) => (s.cur.tilt - 0.5) * g.binW * 1.4 * (j / ROWS);
  const pegX = (g, j, i) => g.padX + (BINS / 2) * g.binW + (2 * i - j) * g.binW / 2 + rowShear(g, j);
  const pegY = (g, j) => g.padTop + j * g.rowH;

  // ------- шарики (сэмпл из целевого рыночного распределения)
  function sampleBin() {
    const p = s.tgt.probs; if (!p) return null;
    const u = Math.random(); let a = 0;
    for (let b = 0; b < BINS; b++) { a += p[b]; if (u <= a) return b; }
    return BINS - 1;
  }
  function spawnBall() {
    const bin = sampleBin(); if (bin == null) return;
    const rights = Math.round((bin / (BINS - 1)) * ROWS);
    const dirs = [];
    for (let i = 0; i < ROWS; i++) dirs.push(i < rights);
    for (let i = dirs.length - 1; i > 0; i--) { const j = (Math.random() * (i + 1)) | 0; [dirs[i], dirs[j]] = [dirs[j], dirs[i]]; }
    s.balls.push({ bin, dirs, seg: 0, t: 0, rights: 0, wob: 3 + Math.random() * 3, sp: 0.9 + Math.random() * 0.3, settled: false });
  }
  function binH(g, b) {
    const tot = Math.max(1, s.dropped);
    return Math.min(g.distH - 6, (s.counts[b] / tot) * g.distH * 2.4);
  }
  function ballPos(g, ball) {
    const j = ball.seg, t = ball.t;
    if (j < ROWS) {
      const x0 = pegX(g, j, ball.rights), x1 = pegX(g, j + 1, ball.rights + (ball.dirs[j] ? 1 : 0));
      const y0 = pegY(g, j), y1 = pegY(g, j + 1);
      const te = t * t * (3 - 2 * t), ov = Math.sin(t * Math.PI) * ball.wob * (ball.dirs[j] ? 1 : -1);
      return { x: x0 + (x1 - x0) * te + ov, y: y0 + (y1 - y0) * t };
    }
    const x0 = pegX(g, ROWS, ball.rights), x1 = xOfR(g, binMid(ball.bin));
    return { x: x0 + (x1 - x0) * Math.min(1, t * 1.4), y: pegY(g, ROWS) + (g.baseY - binH(g, ball.bin) - pegY(g, ROWS)) * (t * t) };
  }
  function stepBalls(dt) {
    for (const b of s.balls) {
      const seg = b.seg < ROWS ? 90 : 220;
      b.t += (dt / seg) * b.sp;
      while (b.t >= 1) { b.t -= 1; if (b.seg < ROWS) { if (b.dirs[b.seg]) b.rights++; b.seg++; } else { b.settled = true; b.t = 1; break; } }
    }
    for (const b of s.balls.filter((x) => x.settled)) { s.counts[b.bin]++; s.dropped++; if (isGreen(b.bin)) s.green++; }
    s.balls = s.balls.filter((b) => !b.settled);
  }

  // ------------------------------------------------------------- draw
  function draw(now) {
    const { ctx, w } = setupCanvas(canvas, H);
    const g = geom(w);
    ctx.clearRect(0, 0, w, H);
    if (!s.active || !s.cur.probs) return;
    const baseY = g.baseY, x0 = xOfR(g, 0);
    const mMax = Math.max(...s.cur.probs, 0.001);

    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 8, baseY - g.distH, w - 2 * g.padX + 16, g.distH);

    // Положительная зона результата; полная RND продолжается за обоими
    // барьерами, поэтому график больше не обрезается у stop/take.
    const xt = xOfR(g, 0);
    const glow = 0.06 + 0.05 * pulse(now, 1800);
    ctx.fillStyle = `rgba(232,98,42,${glow})`;
    ctx.fillRect(xt, baseY - g.distH, (w - g.padX) - xt, g.distH);

    // рыночное распределение — заполненные корзины
    for (let b = 0; b < BINS; b++) {
      const x = g.padX + b * g.binW;
      const h = (s.cur.probs[b] / mMax) * (g.distH - 10);
      ctx.fillStyle = isTail(b) ? 'rgba(232,98,42,0.5)' : isGreen(b) ? COLORS.greenSoft : COLORS.redSoft;
      ctx.fillRect(x + 1.5, baseY - h, g.binW - 3, h);
      // эмпирические шарики (контур) — сходятся к рынку
      const he = binH(g, b);
      ctx.strokeStyle = isGreen(b) ? COLORS.green : COLORS.red;
      ctx.lineWidth = 1.1;
      ctx.strokeRect(x + 1.5, baseY - he, g.binW - 3, he);
    }
    // огибающая рынка — оранжевая
    ctx.beginPath();
    for (let b = 0; b < BINS; b++) { const cx = g.padX + (b + 0.5) * g.binW, cy = baseY - (s.cur.probs[b] / mMax) * (g.distH - 10); b ? ctx.lineTo(cx, cy) : ctx.moveTo(cx, cy); }
    ctx.strokeStyle = '#E8622A'; ctx.lineWidth = 2; ctx.stroke();
    // Опционные/сценарные квантили — практический диапазон живой массы.
    const qs = [
      [s.tgt.q10, 'P10', COLORS.red],
      [s.tgt.q50, 'P50', COLORS.ink],
      [s.tgt.q90, 'P90', COLORS.green],
    ];
    for (const [q, label, color] of qs) {
      const { lo, hi } = domain();
      if (q == null || q < lo || q > hi) continue;
      const x = xOfR(g, q);
      ctx.strokeStyle = color; ctx.lineWidth = label === 'P50' ? 1.5 : 1;
      ctx.setLineDash(label === 'P50' ? [4, 2] : [2, 3]);
      ctx.beginPath(); ctx.moveTo(x, baseY - g.distH); ctx.lineTo(x, baseY); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color; ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center'; ctx.fillText(label, x, baseY - g.distH + 10);
    }
    const dom = domain();
    if (s.tgt.mode != null && s.tgt.mode >= dom.lo && s.tgt.mode <= dom.hi) {
      const xm = xOfR(g, s.tgt.mode);
      ctx.fillStyle = '#E8622A';
      ctx.beginPath(); ctx.moveTo(xm - 4, baseY - 4); ctx.lineTo(xm + 4, baseY - 4);
      ctx.lineTo(xm, baseY - 11); ctx.closePath(); ctx.fill();
    }

    // вход и реальные барьеры внутри расширенной оси RND
    ctx.strokeStyle = COLORS.rule; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x0, g.padTop - 10); ctx.lineTo(x0, baseY); ctx.stroke(); ctx.setLineDash([]);
    for (const [R, color, label] of [[-1, COLORS.red, 'СТОП −1R'], [s.T, COLORS.green, `ТЕЙК +${s.T.toFixed(2)}R`]]) {
      const xb = xOfR(g, R);
      ctx.strokeStyle = color; ctx.lineWidth = 1.3; ctx.setLineDash([5, 3]);
      ctx.beginPath(); ctx.moveTo(xb, baseY - g.distH); ctx.lineTo(xb, baseY); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = color; ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center'; ctx.fillText(label, xb, baseY - g.distH + 20);
    }

    // маркер текущего r (скользит) — оранжевый
    const xr = Math.max(g.padX, Math.min(w - g.padX, xOfR(g, s.cur.r)));
    ctx.strokeStyle = '#E8622A'; ctx.lineWidth = 1.5; ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(xr, g.padTop - 10); ctx.lineTo(xr, baseY + 4); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#E8622A';
    ctx.beginPath(); ctx.moveTo(xr - 5, baseY + 4); ctx.lineTo(xr + 5, baseY + 4); ctx.lineTo(xr, baseY - 3); ctx.closePath(); ctx.fill();

    // ось
    ctx.fillStyle = COLORS.ink; ctx.font = '10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left'; ctx.fillText(`${dom.lo >= 0 ? '+' : ''}${dom.lo.toFixed(2)}R`, g.padX, H - 8);
    ctx.textAlign = 'center'; ctx.fillText('0', x0, H - 8);
    ctx.fillText(`r=${s.cur.r >= 0 ? '+' : ''}${s.cur.r.toFixed(2)}`, xr, baseY + 20);
    ctx.textAlign = 'right'; ctx.fillText(`${dom.hi >= 0 ? '+' : ''}${dom.hi.toFixed(2)}R`, w - g.padX, H - 8);

    // штырьки (наклон = рыночный tilt)
    ctx.fillStyle = COLORS.dim;
    for (let j = 1; j <= ROWS; j++) for (let i = 0; i <= j; i++) { ctx.beginPath(); ctx.arc(pegX(g, j, i), pegY(g, j), 1.6, 0, Math.PI * 2); ctx.fill(); }

    // заголовок + edge
    ctx.textAlign = 'center'; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.fillStyle = COLORS.dim;
    const src = s.marketAvail
      ? 'OPTION-ANCHORED · FULL-HORIZON RND · БЕЗ ОБРЕЗКИ БАРЬЕРАМИ'
      : 'СЦЕНАРНАЯ ПЛОТНОСТЬ · БЕЗ P / EDGE';
    const reg = s.regime ? ` · ВОЛА ${s.regime}` : '';
    ctx.fillText(`РАСПРЕДЕЛЕНИЕ: ${src}${reg}`, w / 2, 12);
    if (s.tgt.edge != null) {
      const ed = s.tgt.edge;
      ctx.fillStyle = ed >= 0 ? COLORS.green : COLORS.red;
      ctx.font = '10px "IBM Plex Mono", monospace'; ctx.textAlign = 'right';
      ctx.fillText(`OPTION EDGE vs EV=0 ${ed >= 0 ? '+' : ''}${(ed * 100).toFixed(1)}%`, w - g.padX, 12);
    }
    ctx.font = '8px "IBM Plex Mono", monospace';
    if (s.tgt.pStop != null) {
      ctx.fillStyle = COLORS.red; ctx.textAlign = 'left';
      ctx.fillText(`СТОП FIRST-TOUCH≤H ${fmtProb(s.tgt.pStop)}`, g.padX, 24);
    }
    if (s.tgt.pTake != null) {
      ctx.fillStyle = COLORS.green; ctx.textAlign = 'right';
      ctx.fillText(`ТЕЙК FIRST-TOUCH≤H ${fmtProb(s.tgt.pTake)} · NO-TOUCH ${fmtProb(s.tgt.unresolved)}`, w - g.padX, 24);
    }

    // шарики
    for (const b of s.balls) {
      const p = ballPos(g, b);
      ctx.beginPath(); ctx.arc(p.x + 1, p.y + 2, 3.2, 0, Math.PI * 2); ctx.fillStyle = 'rgba(20,20,15,0.12)'; ctx.fill();
      ctx.beginPath(); ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = b.seg >= ROWS ? (isTail(b.bin) ? '#E8622A' : isGreen(b.bin) ? COLORS.green : COLORS.red) : COLORS.ink;
      ctx.fill();
    }
  }

  // ------------------------------------------------------------- loop
  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (s.active && s.tgt.probs) {
      s.cur.probs = approachArr(s.cur.probs, s.tgt.probs, dt, 7);
      s.cur.r = approach(s.cur.r, s.tgt.r, dt, 8);
      s.cur.tilt = approach(s.cur.tilt, s.tgt.tilt, dt, 6);
      s.lastSpawn += dt * 1000;
      if (s.lastSpawn >= s.nextSpawnIn) { s.lastSpawn = 0; s.nextSpawnIn = 360 + Math.random() * 240; spawnBall(); }
      stepBalls(dt * 1000);
    }
    draw(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    setData, reset,
    get stats() {
      const gs = s.dropped ? s.green / s.dropped : null;
      let pg = null;
      if (s.tgt.probs) pg = s.tgt.probs.reduce((a, p, b) => a + (isGreen(b) ? p : 0), 0);
      return { dropped: s.dropped, greenShare: gs, pGreenModel: pg,
               convergence: (gs != null && pg != null) ? Math.abs(gs - pg) : null };
    },
  };
}
