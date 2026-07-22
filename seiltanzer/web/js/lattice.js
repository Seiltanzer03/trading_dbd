// Probability Lattice — живая доска Гальтона.
//
// Честность доски: корзина каждого шарика СЭМПЛИРУЕТСЯ ЗАРАНЕЕ из
// МК-распределения терминального R (mc.hist.probs), а траектория подгоняется
// под неё (случайный порядок влево/вправо). Наклон рядов штырьков — текущая
// P модели. Доска красива И статистически честна: доля зелёных сходится к
// модельной вероятности (см. метрику «сходимость»).

import { COLORS, setupCanvas } from './util.js';

const ROWS = 8;
const BINS = 9;
const H = 380;

export function initLattice(canvas) {
  const state = {
    active: false,
    p: null,          // текущая P модели (наклон)
    T: 2.5,
    probs: null,      // 9 вероятностей корзин из МК
    edges: null,
    counts: new Array(BINS).fill(0),
    balls: [],        // летящие шарики
    dropped: 0,
    green: 0,
    lastSpawn: 0,
    nextSpawnIn: 500,
    tradeId: null,
  };

  function reset() {
    state.counts.fill(0);
    state.balls = [];
    state.dropped = 0;
    state.green = 0;
  }

  function setData({ active, p, T, hist, tradeId }) {
    if (tradeId !== state.tradeId) {
      state.tradeId = tradeId;
      reset();
    }
    state.active = active;
    if (active) {
      state.p = p;
      state.T = T;
      state.probs = hist ? hist.probs : null;
      state.edges = hist ? hist.edges : null;
    }
  }

  // --------------------------------------------------------------- geometry

  function geom(w) {
    const padX = 26, padTop = 30;
    const histH = 120, axisH = 24;
    const boardH = H - padTop - histH - axisH - 8;
    const binW = (w - 2 * padX) / BINS;
    const rowH = boardH / (ROWS + 1);
    return { padX, padTop, histH, axisH, boardH, binW, rowH, w };
  }

  // сдвиг ряда j (0..ROWS) от наклона доски: пропорционален (p − 0.5)
  function rowShear(g, j) {
    if (state.p == null) return 0;
    return (state.p - 0.5) * g.binW * 1.6 * (j / ROWS);
  }

  function binCenterX(g, b) {
    return g.padX + (b + 0.5) * g.binW;
  }

  // x позиции шарика после j решений, i — число «вправо»
  function pegX(g, j, i) {
    const start = g.padX + (BINS / 2) * g.binW;
    return start + (2 * i - j) * g.binW / 2 + rowShear(g, j);
  }

  function pegY(g, j) {
    return g.padTop + j * g.rowH;
  }

  // ------------------------------------------------------------------ balls

  function sampleBin() {
    if (!state.probs) return null;
    const u = Math.random();
    let acc = 0;
    for (let b = 0; b < BINS; b++) {
      acc += state.probs[b];
      if (u <= acc) return b;
    }
    return BINS - 1;
  }

  function spawnBall() {
    const bin = sampleBin();
    if (bin == null) return;
    // порядок из `bin` шагов вправо среди ROWS решений — случайная перестановка
    const dirs = [];
    for (let i = 0; i < ROWS; i++) dirs.push(i < bin);
    for (let i = dirs.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [dirs[i], dirs[j]] = [dirs[j], dirs[i]];
    }
    state.balls.push({
      bin, dirs,
      seg: 0,               // текущий сегмент (0..ROWS): между рядами
      t: 0,                 // прогресс сегмента 0..1
      rights: 0,
      wobble: 4 + Math.random() * 3,
      speed: 0.9 + Math.random() * 0.35,
      settled: false,
    });
  }

  function ballPos(g, ball) {
    const j = ball.seg;
    const t = ball.t;
    if (j < ROWS) {
      const x0 = pegX(g, j, ball.rights);
      const nextRights = ball.rights + (ball.dirs[j] ? 1 : 0);
      const x1 = pegX(g, j + 1, nextRights);
      const y0 = pegY(g, j), y1 = pegY(g, j + 1);
      // отскок: горизонталь запаздывает и слегка перелетает
      const te = t * t * (3 - 2 * t);
      const overshoot = Math.sin(t * Math.PI) * ball.wobble * (ball.dirs[j] ? 1 : -1);
      return { x: x0 + (x1 - x0) * te + overshoot, y: y0 + (y1 - y0) * t };
    }
    // финальный сегмент: от нижнего ряда в корзину (шир сходит к нулю)
    const x0 = pegX(g, ROWS, ball.rights);
    const x1 = binCenterX(g, ball.bin);
    const y0 = pegY(g, ROWS);
    const stackH = binStackH(g, ball.bin);
    const y1 = H - g.axisH - 4 - stackH;
    const te = t * t;
    return { x: x0 + (x1 - x0) * Math.min(1, t * 1.4), y: y0 + (y1 - y0) * te };
  }

  function stepBalls(dtMs) {
    for (const b of state.balls) {
      const segTime = b.seg < ROWS ? 95 : 240;  // мс на сегмент
      b.t += (dtMs / segTime) * b.speed;
      while (b.t >= 1) {
        b.t -= 1;
        if (b.seg < ROWS) {
          if (b.dirs[b.seg]) b.rights += 1;
          b.seg += 1;
        } else {
          b.settled = true;
          b.t = 1;
          break;
        }
      }
    }
    const landed = state.balls.filter((b) => b.settled);
    for (const b of landed) {
      state.counts[b.bin] += 1;
      state.dropped += 1;
      if (binIsGreen(b.bin)) state.green += 1;
    }
    state.balls = state.balls.filter((b) => !b.settled);
  }

  function binMidR(b) {
    if (!state.edges) return null;
    return (state.edges[b] + state.edges[b + 1]) / 2;
  }

  function binIsGreen(b) {
    const m = binMidR(b);
    return m != null && m > 0;
  }

  function binStackH(g, b) {
    const maxCount = Math.max(1, ...state.counts);
    const unit = Math.min(9, (g.histH - 8) / maxCount);
    return state.counts[b] * unit;
  }

  // ------------------------------------------------------------------ draw

  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    const g = geom(w);
    ctx.clearRect(0, 0, w, H);
    if (!state.active) return;

    // фон histogram-зоны
    ctx.fillStyle = '#FBFAF6';
    ctx.fillRect(g.padX - 8, H - g.axisH - g.histH - 6, w - 2 * g.padX + 16, g.histH + 6);

    // нулевая линия R (граница красное/зелёное) по шкале [-1, T]
    const T = state.T;
    const xZero = g.padX + ((0 - (-1)) / (T + 1)) * (w - 2 * g.padX);
    ctx.strokeStyle = COLORS.rule;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(xZero, g.padTop - 12);
    ctx.lineTo(xZero, H - g.axisH);
    ctx.stroke();
    ctx.setLineDash([]);

    // штырьки (со сдвигом рядов = наклон от P)
    ctx.fillStyle = COLORS.dim;
    for (let j = 1; j <= ROWS; j++) {
      for (let i = 0; i <= j; i++) {
        const x = pegX(g, j, i), y = pegY(g, j);
        ctx.beginPath();
        ctx.arc(x, y, 1.7, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // корзины + накопленная гистограмма
    for (let b = 0; b < BINS; b++) {
      const x = g.padX + b * g.binW;
      const green = binIsGreen(b);
      const hgt = binStackH(g, b);
      ctx.fillStyle = green ? COLORS.green : COLORS.red;
      ctx.globalAlpha = green ? 0.85 : 0.7;
      ctx.fillRect(x + 2, H - g.axisH - 4 - hgt, g.binW - 4, hgt);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = COLORS.rule;
      ctx.strokeRect(x + 2, H - g.axisH - g.histH - 4, g.binW - 4, g.histH);
      // ожидаемая доля из МК — маленькая метка на корзине
      if (state.probs) {
        ctx.fillStyle = COLORS.dim;
        ctx.font = '9px "IBM Plex Mono", monospace';
        ctx.textAlign = 'center';
        ctx.fillText((state.probs[b] * 100).toFixed(0), x + g.binW / 2, H - g.axisH - g.histH - 8);
      }
    }

    // ось: -1R, 0, T
    ctx.fillStyle = COLORS.ink;
    ctx.font = '10px "IBM Plex Mono", monospace';
    ctx.textAlign = 'left';
    ctx.fillText('-1R (СТОП)', g.padX, H - 8);
    ctx.textAlign = 'center';
    ctx.fillText('0', xZero, H - 8);
    ctx.textAlign = 'right';
    ctx.fillText(`+${T.toFixed(2)}R (ТЕЙК)`, w - g.padX, H - 8);

    // подпись наклона
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.dim;
    ctx.font = '9px "IBM Plex Mono", monospace';
    if (state.p != null) {
      ctx.fillText(`НАКЛОН ДОСКИ = P МОДЕЛИ = ${(state.p * 100).toFixed(1)}%`, w / 2, 12);
    }

    // шарики (лёгкая подложка-тень разрешена ТЗ)
    for (const b of state.balls) {
      const pos = ballPos(g, b);
      ctx.beginPath();
      ctx.arc(pos.x + 1, pos.y + 2, 3.4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(20,20,15,0.12)';
      ctx.fill();
      ctx.beginPath();
      ctx.arc(pos.x, pos.y, 3.4, 0, Math.PI * 2);
      ctx.fillStyle = b.seg >= ROWS
        ? (binIsGreen(b.bin) ? COLORS.green : COLORS.red)
        : COLORS.ink;
      ctx.fill();
    }
  }

  // ------------------------------------------------------------------ loop

  let lastT = performance.now();
  let fpsGuard = 0;
  function frame(now) {
    const dt = Math.min(now - lastT, 100);
    lastT = now;
    // деградация до 30fps: пропускаем кадр, если предыдущий был тяжёлым
    fpsGuard = dt > 40 ? fpsGuard + 1 : 0;
    if (state.active && state.probs) {
      state.lastSpawn += dt;
      if (state.lastSpawn >= state.nextSpawnIn) {
        state.lastSpawn = 0;
        state.nextSpawnIn = 400 + Math.random() * 300; // ТЗ: каждые 400–700 мс
        spawnBall();
      }
      stepBalls(dt);
    }
    if (fpsGuard % 2 === 0) draw();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    setData,
    reset,
    get stats() {
      const greenShare = state.dropped ? state.green / state.dropped : null;
      let pGreenModel = null;
      if (state.probs && state.edges) {
        pGreenModel = state.probs.reduce(
          (acc, p, b) => acc + (binIsGreen(b) ? p : 0), 0);
      }
      return {
        dropped: state.dropped,
        greenShare,
        pGreenModel,
        convergence: (greenShare != null && pGreenModel != null)
          ? Math.abs(greenShare - pGreenModel) : null,
      };
    },
  };
}
