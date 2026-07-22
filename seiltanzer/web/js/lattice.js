// Probability Lattice — живая доска Гальтона + распределение исхода.
//
// Что показано:
//  • модельное распределение проекции сделки в R (11 корзин, из forward-МК):
//    красное слева от 0, зелёное справа; ширина колокола = режим опционной волы,
//    центр сдвигается с текущим r (движением цены);
//  • шарики Монте-Карло падают через штырьки и сходятся к этому распределению
//    (эмпирические столбики растут к модельным — «доска не врёт»);
//  • маркер текущего r на оси движется в реальном времени.

import { COLORS, setupCanvas } from './util.js';

const ROWS = 8;
const BINS = 11;
const H = 380;

export function initLattice(canvas) {
  const state = {
    active: false, p: null, T: 2.5, r: 0,
    probs: null, edges: null,
    counts: new Array(BINS).fill(0),
    balls: [], dropped: 0, green: 0,
    lastSpawn: 0, nextSpawnIn: 500, tradeId: null,
    regime: null,
  };

  function reset() {
    state.counts.fill(0); state.balls = []; state.dropped = 0; state.green = 0;
  }

  function setData({ active, p, T, r, hist, tradeId, regime }) {
    if (tradeId !== state.tradeId) { state.tradeId = tradeId; reset(); }
    state.active = active;
    if (active) {
      state.p = p; state.T = T; state.r = r ?? 0; state.regime = regime;
      state.probs = hist ? hist.probs : null;
      state.edges = hist ? hist.edges : null;
    }
  }

  // ------------------------------------------------------------- geometry
  function geom(w) {
    const padX = 30, padTop = 26;
    const distH = 150, axisH = 26;
    const boardH = H - padTop - distH - axisH - 6;
    return { padX, padTop, distH, axisH, boardH,
             binW: (w - 2 * padX) / BINS, rowH: boardH / (ROWS + 1), w };
  }
  const binMidR = (b) => state.edges ? (state.edges[b] + state.edges[b + 1]) / 2 : 0;
  const binIsGreen = (b) => binMidR(b) > 0;

  // x-координата значения R на оси [-1, T]
  function xOfR(g, R) {
    const T = state.T;
    return g.padX + ((R - (-1)) / (T + 1)) * (g.w - 2 * g.padX);
  }
  function rowShear(g, j) {
    if (state.p == null) return 0;
    return (state.p - 0.5) * g.binW * 1.4 * (j / ROWS);
  }
  const pegX = (g, j, i) =>
    g.padX + (BINS / 2) * g.binW + (2 * i - j) * g.binW / 2 + rowShear(g, j);
  const pegY = (g, j) => g.padTop + j * g.rowH;

  // ------------------------------------------------------------- balls
  function sampleBin() {
    if (!state.probs) return null;
    const u = Math.random();
    let acc = 0;
    for (let b = 0; b < BINS; b++) { acc += state.probs[b]; if (u <= acc) return b; }
    return BINS - 1;
  }
  function spawnBall() {
    const bin = sampleBin();
    if (bin == null) return;
    const target = bin / (BINS - 1);          // 0..1 позиция корзины
    const rights = Math.round(target * ROWS);
    const dirs = [];
    for (let i = 0; i < ROWS; i++) dirs.push(i < rights);
    for (let i = dirs.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [dirs[i], dirs[j]] = [dirs[j], dirs[i]];
    }
    state.balls.push({ bin, dirs, seg: 0, t: 0, rights: 0,
                       wobble: 3 + Math.random() * 3, speed: 0.9 + Math.random() * 0.3,
                       settled: false });
  }
  function ballPos(g, ball) {
    const j = ball.seg, t = ball.t;
    if (j < ROWS) {
      const x0 = pegX(g, j, ball.rights);
      const x1 = pegX(g, j + 1, ball.rights + (ball.dirs[j] ? 1 : 0));
      const y0 = pegY(g, j), y1 = pegY(g, j + 1);
      const te = t * t * (3 - 2 * t);
      const over = Math.sin(t * Math.PI) * ball.wobble * (ball.dirs[j] ? 1 : -1);
      return { x: x0 + (x1 - x0) * te + over, y: y0 + (y1 - y0) * t };
    }
    const x0 = pegX(g, ROWS, ball.rights);
    const x1 = xOfR(g, binMidR(ball.bin));
    const y0 = pegY(g, ROWS);
    const y1 = H - g.axisH - 6 - binStackH(g, ball.bin);
    return { x: x0 + (x1 - x0) * Math.min(1, t * 1.4), y: y0 + (y1 - y0) * (t * t) };
  }
  function stepBalls(dtMs) {
    for (const b of state.balls) {
      const segTime = b.seg < ROWS ? 90 : 220;
      b.t += (dtMs / segTime) * b.speed;
      while (b.t >= 1) {
        b.t -= 1;
        if (b.seg < ROWS) { if (b.dirs[b.seg]) b.rights += 1; b.seg += 1; }
        else { b.settled = true; b.t = 1; break; }
      }
    }
    for (const b of state.balls.filter((x) => x.settled)) {
      state.counts[b.bin] += 1; state.dropped += 1;
      if (binIsGreen(b.bin)) state.green += 1;
    }
    state.balls = state.balls.filter((b) => !b.settled);
  }
  function binStackH(g, b) {
    const total = Math.max(1, state.dropped);
    const share = state.counts[b] / total;
    return Math.min(g.distH - 6, share * g.distH * 2.4);
  }

  // ------------------------------------------------------------- draw
  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    const g = geom(w);
    ctx.clearRect(0, 0, w, H);
    if (!state.active || !state.probs) return;

    const T = state.T;
    const baseY = H - g.axisH - 6;
    const modelMax = Math.max(...state.probs, 0.001);

    // подложка зоны распределения
    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 8, baseY - g.distH, w - 2 * g.padX + 16, g.distH);

    // модельное распределение — 11 столбиков-корзин, колокол
    for (let b = 0; b < BINS; b++) {
      const x = g.padX + b * g.binW;
      const green = binIsGreen(b);
      const hM = (state.probs[b] / modelMax) * (g.distH - 10);
      ctx.fillStyle = green ? COLORS.greenSoft : COLORS.redSoft;
      ctx.fillRect(x + 1.5, baseY - hM, g.binW - 3, hM);
      // эмпирические шарики поверх — тёмный контур, растёт к модели
      const hE = binStackH(g, b);
      ctx.strokeStyle = green ? COLORS.green : COLORS.red;
      ctx.lineWidth = 1.2;
      ctx.strokeRect(x + 1.5, baseY - hE, g.binW - 3, hE);
    }

    // плавная линия модели поверх столбиков
    ctx.beginPath();
    for (let b = 0; b < BINS; b++) {
      const cx = g.padX + (b + 0.5) * g.binW;
      const cy = baseY - (state.probs[b] / modelMax) * (g.distH - 10);
      b === 0 ? ctx.moveTo(cx, cy) : ctx.lineTo(cx, cy);
    }
    ctx.strokeStyle = COLORS.ink;
    ctx.lineWidth = 1.2;
    ctx.stroke();

    // линии -1 / 0 / T
    const x0 = xOfR(g, 0);
    ctx.strokeStyle = COLORS.rule;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x0, g.padTop - 10); ctx.lineTo(x0, baseY); ctx.stroke();
    ctx.setLineDash([]);

    // маркер текущего r (движется с ценой)
    const xr = Math.max(g.padX, Math.min(w - g.padX, xOfR(g, state.r)));
    ctx.strokeStyle = COLORS.ink;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(xr, g.padTop - 10); ctx.lineTo(xr, baseY + 4); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = COLORS.ink;
    ctx.beginPath();
    ctx.moveTo(xr - 5, baseY + 4); ctx.lineTo(xr + 5, baseY + 4);
    ctx.lineTo(xr, baseY - 3); ctx.closePath(); ctx.fill();

    // ось
    ctx.fillStyle = COLORS.ink;
    ctx.font = '10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillText('-1R (СТОП)', g.padX, H - 8);
    ctx.textAlign = 'center';
    ctx.fillText('0', x0, H - 8);
    ctx.fillText(`r=${state.r >= 0 ? '+' : ''}${state.r.toFixed(2)}`, xr, baseY + 20);
    ctx.textAlign = 'right';
    ctx.fillText(`+${T.toFixed(2)}R (ТЕЙК)`, w - g.padX, H - 8);

    // штырьки (наклон = P модели)
    ctx.fillStyle = COLORS.dim;
    for (let j = 1; j <= ROWS; j++)
      for (let i = 0; i <= j; i++) {
        ctx.beginPath();
        ctx.arc(pegX(g, j, i), pegY(g, j), 1.6, 0, Math.PI * 2); ctx.fill();
      }

    // подпись режима волы
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.dim;
    ctx.font = '9px "IBM Plex Mono", monospace';
    const reg = state.regime ? ` · ВОЛА: ${state.regime.toUpperCase()}` : '';
    if (state.p != null)
      ctx.fillText(`НАКЛОН = P МОДЕЛИ ${(state.p * 100).toFixed(1)}%${reg}`, w / 2, 12);

    // шарики
    for (const b of state.balls) {
      const pos = ballPos(g, b);
      ctx.beginPath();
      ctx.arc(pos.x + 1, pos.y + 2, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(20,20,15,0.12)'; ctx.fill();
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, 3.2, 0, Math.PI * 2);
      ctx.fillStyle = b.seg >= ROWS
        ? (binIsGreen(b.bin) ? COLORS.green : COLORS.red) : COLORS.ink;
      ctx.fill();
    }
  }

  // ------------------------------------------------------------- loop
  let lastT = performance.now(), fpsGuard = 0;
  function frame(now) {
    const dt = Math.min(now - lastT, 100); lastT = now;
    fpsGuard = dt > 40 ? fpsGuard + 1 : 0;
    if (state.active && state.probs) {
      state.lastSpawn += dt;
      if (state.lastSpawn >= state.nextSpawnIn) {
        state.lastSpawn = 0;
        state.nextSpawnIn = 380 + Math.random() * 260;
        spawnBall();
      }
      stepBalls(dt);
    }
    if (fpsGuard % 2 === 0) draw();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    setData, reset,
    get stats() {
      const greenShare = state.dropped ? state.green / state.dropped : null;
      let pGreenModel = null;
      if (state.probs) pGreenModel = state.probs.reduce(
        (a, p, b) => a + (binIsGreen(b) ? p : 0), 0);
      return {
        dropped: state.dropped, greenShare, pGreenModel,
        convergence: (greenShare != null && pGreenModel != null)
          ? Math.abs(greenShare - pGreenModel) : null,
      };
    },
  };
}
