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

export function initLattice(canvas) {
  const s = {
    active: false, marketAvail: false,
    T: 2.5, tradeId: null, regime: null,
    tgt: { probs: null, model: null, r: 0, tilt: 0.5, edge: null, hit: null },
    cur: { probs: null, model: null, r: 0, tilt: 0.5 },
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
    s.marketAvail = !!d.marketProbs;
    const primary = d.marketProbs || d.modelProbs;
    s.tgt.probs = primary;
    s.tgt.model = d.modelProbs;
    s.tgt.r = d.r ?? 0;
    s.tgt.tilt = d.hit != null ? d.hit : (d.p ?? 0.5);
    s.tgt.edge = d.edge;
    s.tgt.hit = d.hit;
    s.edges = d.edges;
    if (!s.cur.probs) { s.cur.probs = primary.slice(); s.cur.model = (d.modelProbs || primary).slice(); s.cur.r = s.tgt.r; s.cur.tilt = s.tgt.tilt; }
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
  const xOfR = (g, R) => g.padX + ((R + 1) / (s.T + 1)) * (g.w - 2 * g.padX);
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
    const mdMax = Math.max(...(s.cur.model || [0.001]), 0.001);

    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 8, baseY - g.distH, w - 2 * g.padX + 16, g.distH);

    // тейл-зона (справа от тейка) — оранжевое свечение «куда платит рынок»
    const xt = xOfR(g, s.T);
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
    // проекция модели — тёмная линия
    if (s.cur.model) {
      ctx.beginPath();
      for (let b = 0; b < BINS; b++) { const cx = g.padX + (b + 0.5) * g.binW, cy = baseY - (s.cur.model[b] / mdMax) * (g.distH - 10); b ? ctx.lineTo(cx, cy) : ctx.moveTo(cx, cy); }
      ctx.strokeStyle = COLORS.ink; ctx.lineWidth = 1.2; ctx.setLineDash([4, 2]); ctx.stroke(); ctx.setLineDash([]);
    }

    // линия 0 и барьеры
    ctx.strokeStyle = COLORS.rule; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x0, g.padTop - 10); ctx.lineTo(x0, baseY); ctx.stroke(); ctx.setLineDash([]);

    // маркер текущего r (скользит) — оранжевый
    const xr = Math.max(g.padX, Math.min(w - g.padX, xOfR(g, s.cur.r)));
    ctx.strokeStyle = '#E8622A'; ctx.lineWidth = 1.5; ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(xr, g.padTop - 10); ctx.lineTo(xr, baseY + 4); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = '#E8622A';
    ctx.beginPath(); ctx.moveTo(xr - 5, baseY + 4); ctx.lineTo(xr + 5, baseY + 4); ctx.lineTo(xr, baseY - 3); ctx.closePath(); ctx.fill();

    // ось
    ctx.fillStyle = COLORS.ink; ctx.font = '10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left'; ctx.fillText('-1R (СТОП)', g.padX, H - 8);
    ctx.textAlign = 'center'; ctx.fillText('0', x0, H - 8);
    ctx.fillText(`r=${s.cur.r >= 0 ? '+' : ''}${s.cur.r.toFixed(2)}`, xr, baseY + 20);
    ctx.textAlign = 'right'; ctx.fillText(`+${s.T.toFixed(2)}R (ТЕЙК)`, w - g.padX, H - 8);

    // штырьки (наклон = рыночный tilt)
    ctx.fillStyle = COLORS.dim;
    for (let j = 1; j <= ROWS; j++) for (let i = 0; i <= j; i++) { ctx.beginPath(); ctx.arc(pegX(g, j, i), pegY(g, j), 1.6, 0, Math.PI * 2); ctx.fill(); }

    // заголовок + edge
    ctx.textAlign = 'center'; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.fillStyle = COLORS.dim;
    const src = s.marketAvail ? 'РЫНОК (risk-neutral)' : 'МОДЕЛЬ (нет опционов)';
    const reg = s.regime ? ` · ВОЛА ${s.regime}` : '';
    ctx.fillText(`РАСПРЕДЕЛЕНИЕ: ${src}${reg}`, w / 2, 12);
    if (s.tgt.edge != null) {
      const ed = s.tgt.edge;
      ctx.fillStyle = ed >= 0 ? COLORS.green : COLORS.red;
      ctx.font = '10px "IBM Plex Mono", monospace'; ctx.textAlign = 'right';
      ctx.fillText(`КРАЙ vs РЫНОК ${ed >= 0 ? '+' : ''}${(ed * 100).toFixed(1)}%`, w - g.padX, 12);
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
      s.cur.model = approachArr(s.cur.model, s.tgt.model, dt, 7);
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
