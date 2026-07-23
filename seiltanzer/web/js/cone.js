// Probability Cone — 3D-конус вероятности (first-passage во времени).
//
// Идея: где окажется сделка, если дать ей развернуться. Ось R (стоп −1 … 0 … тейк
// +T) идёт вправо, ось ВРЕМЕНИ уходит вглубь (near = «сейчас», far = «развязка»),
// высота = плотность живых (ещё не поглощённых) путей. Узкий гребень у «сейчас»
// расплывается в плато и СЛИВАЕТСЯ к двум стенам-барьерам:
//   • левая КРАСНАЯ стена (СТОП): кривая ползёт вверх = накопленная P дойти до стопа;
//   • правая ЗЕЛЁНАЯ стена (ТЕЙК): кривая ползёт вверх = накопленная P дойти до тейка;
//   • их высота у дальней грани = P(стоп) / P(тейк) — это и есть шапка-P.
// Оранжевый луч = текущая цена (r). Тёмный пунктир на дальней стене = терминальная
// плотность РЫНКА (risk-neutral) — где рынок ждёт цену на экспирации.

import { COLORS, setupCanvas } from './util.js';
import { approach, approachArr, pulse } from './anim.js';

const H = 344;

export function initCone(canvas) {
  let data = null;
  const live = { r: null, direction: 'long', headlineP: null };
  // сглаживаемое состояние
  let curDens = null, curTake = null, curStop = null, curR = null, curMkt = null;

  function setData(cone, extra) {
    data = cone && cone.available ? cone : null;
    if (extra) Object.assign(live, extra);
  }
  function updateLive(p) { Object.assign(live, p); }

  function draw(now) {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data) return;

    const T = data.T;
    const nS = curDens ? curDens.length : data.density.length;
    const nB = data.edges.length - 1;
    const edges = data.edges;
    const binMid = (b) => (edges[b] + edges[b + 1]) / 2;

    // --- геометрия изометрии
    const padL = 46, padR = 18, padT = 22, padB = 54;
    const depthDX = w * 0.17;              // горизонтальный увод в глубину
    const depthDY = (H - padT - padB) * 0.42;  // вертикальный увод
    const floorY = H - padB;
    const originX = padL;
    const plotW = w - padL - padR - depthDX;
    const surfAmp = (floorY - padT - depthDY) * 0.92;  // высота гребней плотности
    const wallAmp = (floorY - padT - depthDY) * 0.96;  // высота стен (для P 0..1)

    const rxOf = (R) => (R - (-1)) / (T + 1);
    const depthOf = (j) => (nS > 1 ? j / (nS - 1) : 0);   // 0=near .. 1=far
    const proj = (R, d, z) => [
      originX + rxOf(R) * plotW + d * depthDX,
      floorY - d * depthDY - z,
    ];

    // глобальный максимум плотности — для нормировки высоты гребней
    let gmax = 1e-9;
    for (const row of curDens) for (const v of row) if (v > gmax) gmax = v;

    // ---------- пол (плоскость R × время) + направляющие барьеров
    ctx.fillStyle = '#FBFAF6';
    ctx.beginPath();
    let p = proj(-1, 0, 0); ctx.moveTo(p[0], p[1]);
    p = proj(T, 0, 0); ctx.lineTo(p[0], p[1]);
    p = proj(T, 1, 0); ctx.lineTo(p[0], p[1]);
    p = proj(-1, 1, 0); ctx.lineTo(p[0], p[1]);
    ctx.closePath(); ctx.fill();

    // линии-грани по ключевым R (стоп/0/тейк) вглубь
    const guides = [{ R: -1, c: COLORS.red }, { R: 0, c: COLORS.rule }, { R: T, c: COLORS.green }];
    for (const gd of guides) {
      const a = proj(gd.R, 0, 0), b = proj(gd.R, 1, 0);
      ctx.strokeStyle = gd.c; ctx.globalAlpha = 0.4; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // ---------- СТЕНЫ барьеров: заполнение восходящей кривой P(дошло к времени t)
    function wall(R, series, color) {
      // ribbon от пола до кривой P по глубине
      ctx.beginPath();
      let started = false;
      for (let j = 0; j < nS; j++) {
        const q = proj(R, depthOf(j), series[j] * wallAmp);
        started ? ctx.lineTo(q[0], q[1]) : (ctx.moveTo(q[0], q[1]), started = true);
      }
      for (let j = nS - 1; j >= 0; j--) { const q = proj(R, depthOf(j), 0); ctx.lineTo(q[0], q[1]); }
      ctx.closePath();
      ctx.fillStyle = color.fill; ctx.fill();
      // жирная кромка кривой
      ctx.beginPath(); started = false;
      for (let j = 0; j < nS; j++) {
        const q = proj(R, depthOf(j), series[j] * wallAmp);
        started ? ctx.lineTo(q[0], q[1]) : (ctx.moveTo(q[0], q[1]), started = true);
      }
      ctx.strokeStyle = color.line; ctx.lineWidth = 2; ctx.stroke();
    }
    wall(-1, curStop, { fill: 'rgba(198,55,60,0.14)', line: COLORS.red });
    wall(T, curTake, { fill: 'rgba(46,125,79,0.16)', line: COLORS.green });

    // ---------- поверхность плотности: гряды от дальней к ближней (painter)
    for (let j = nS - 1; j >= 0; j--) {
      const d = depthOf(j);
      const row = curDens[j];
      // площадь под гребнем
      ctx.beginPath();
      let q = proj(edges[0], d, 0); ctx.moveTo(q[0], q[1]);
      for (let b = 0; b < nB; b++) { q = proj(binMid(b), d, (row[b] / gmax) * surfAmp); ctx.lineTo(q[0], q[1]); }
      q = proj(edges[nB], d, 0); ctx.lineTo(q[0], q[1]);
      ctx.closePath();
      const near = 1 - d;                      // ближние ярче
      ctx.fillStyle = `rgba(232,98,42,${0.10 + 0.34 * near})`;
      ctx.fill();
      ctx.strokeStyle = j === 0 ? '#E8622A' : `rgba(232,98,42,${0.25 + 0.4 * near})`;
      ctx.lineWidth = j === 0 ? 1.8 : 0.8; ctx.stroke();
    }

    // ---------- терминальная плотность РЫНКА на дальней грани (пунктир)
    if (curMkt) {
      let mmax = 1e-9; for (const v of curMkt) if (v > mmax) mmax = v;
      const medges = data.market_edges || edges;
      const mMid = (b) => (medges[b] + medges[b + 1]) / 2;
      ctx.beginPath(); let started = false;
      for (let b = 0; b < curMkt.length; b++) {
        const q = proj(mMid(b), 1, (curMkt[b] / mmax) * surfAmp * 0.85);
        started ? ctx.lineTo(q[0], q[1]) : (ctx.moveTo(q[0], q[1]), started = true);
      }
      ctx.strokeStyle = COLORS.ink; ctx.lineWidth = 1.4; ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]);
      const lp = proj(mMid(curMkt.length - 1), 1, 0);
      ctx.fillStyle = COLORS.dim; ctx.font = '8px "IBM Plex Mono", monospace'; ctx.textAlign = 'right';
      ctx.fillText('РЫНОК·ЭКСПИР.' + (data.market_demo ? ' ◆' : ''), Math.min(lp[0], w - padR), lp[1] + 10);
    }

    // ---------- ЖИВОЙ ЛУЧ ЦЕНЫ (r) на ближней грани, пульсирует
    const rNow = Math.max(-1, Math.min(T, curR != null ? curR : data.r0));
    const beamBase = proj(rNow, 0, 0);
    const beamTop = proj(rNow, 0, surfAmp * 1.02);
    const pw = 0.5 + 0.5 * pulse(now, 1500);
    ctx.strokeStyle = `rgba(232,98,42,${0.2 * pw})`; ctx.lineWidth = 6;
    ctx.beginPath(); ctx.moveTo(beamBase[0], beamBase[1]); ctx.lineTo(beamTop[0], beamTop[1]); ctx.stroke();
    ctx.strokeStyle = '#E8622A'; ctx.lineWidth = 2.2;
    ctx.beginPath(); ctx.moveTo(beamBase[0], beamBase[1]); ctx.lineTo(beamTop[0], beamTop[1]); ctx.stroke();
    ctx.fillStyle = '#E8622A';
    ctx.beginPath(); ctx.moveTo(beamTop[0] - 4, beamTop[1]); ctx.lineTo(beamTop[0] + 4, beamTop[1]); ctx.lineTo(beamTop[0], beamTop[1] - 7); ctx.closePath(); ctx.fill();
    ctx.font = '700 10px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
    ctx.fillText(`r=${rNow >= 0 ? '+' : ''}${rNow.toFixed(2)}`, beamTop[0], beamTop[1] - 10);

    // ---------- подписи осей
    ctx.font = '9px "IBM Plex Mono", monospace';
    // R-ось по ближней (front) грани
    const axStop = proj(-1, 0, 0), ax0 = proj(0, 0, 0), axTake = proj(T, 0, 0);
    ctx.textAlign = 'center';
    ctx.fillStyle = COLORS.red; ctx.fillText('СТОП −1R', axStop[0] + 8, floorY + 15);
    ctx.fillStyle = COLORS.dim; ctx.fillText('0', ax0[0], floorY + 15);
    ctx.fillStyle = COLORS.green; ctx.fillText(`ТЕЙК +${T.toFixed(1)}R`, axTake[0] - 10, floorY + 15);
    // ось времени — одна однозначная подпись снизу по центру (глубина = время)
    ctx.fillStyle = COLORS.dim; ctx.textAlign = 'center';
    ctx.fillText('ОСЬ ВРЕМЕНИ ⟶ вглубь:  СЕЙЧАС (спереди)  →  РАЗВЯЗКА (у стен)',
                 (padL + w - padR) / 2, floorY + 31);

    // ---------- ридаут: куда клонит конус
    const pt = curTake[nS - 1], ps = curStop[nS - 1];
    const lean = pt > ps + 0.03 ? { t: 'КЛОНИТ К ТЕЙКУ', c: COLORS.green }
              : ps > pt + 0.03 ? { t: 'КЛОНИТ К СТОПУ', c: COLORS.red }
              : { t: '≈ 50/50', c: COLORS.dim };
    ctx.font = '700 12px "IBM Plex Mono", monospace'; ctx.textAlign = 'left';
    ctx.fillStyle = lean.c; ctx.fillText(lean.t, padL, padT - 4);
    ctx.font = '10px "IBM Plex Mono", monospace'; ctx.fillStyle = COLORS.dim; ctx.textAlign = 'right';
    ctx.fillText(`ТЕЙК ${(pt * 100).toFixed(0)}% · СТОП ${(ps * 100).toFixed(0)}%`, w - padR, padT - 4);
  }

  // непрерывный рендер + сглаживание к целевому конусу
  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (data) {
      const tgt = data.density;
      if (!curDens || curDens.length !== tgt.length) curDens = tgt.map((r) => r.slice());
      else for (let j = 0; j < tgt.length; j++) curDens[j] = approachArr(curDens[j], tgt[j], dt, 4);
      curTake = approachArr(curTake, data.p_take_by_t, dt, 4);
      curStop = approachArr(curStop, data.p_stop_by_t, dt, 4);
      curMkt = data.market_terminal ? approachArr(curMkt, data.market_terminal, dt, 4) : null;
      const targetR = live.r != null ? live.r : data.r0;
      curR = approach(curR, targetR, dt, 6);
    }
    draw(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setData, updateLive };
}
