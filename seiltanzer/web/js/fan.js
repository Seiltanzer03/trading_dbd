// Probability Fan — 2D «вероятностный веер» (стандарт квант-деска).
//
// Читается за секунду: ГДЕ цена сейчас (оранжевая точка), КУДА и с каким разбросом
// она пойдёт (веер перцентилей риск-нейтральной диффузии под опционную волу),
// КОГДА может дойти до тейка/стопа (пересечение веера с линиями барьеров + метка
// медианы развязки) и С КАКОЙ вероятностью (P тейк/стоп). Ось X — реальное
// адаптивное время (мин/часы/дни). Веер выходит за барьеры в зоны прибыли/убытка —
// это не «обрезание», а видимая вероятность оказаться там.

import { COLORS, setupCanvas } from './util.js';
import { approach, pulse } from './anim.js';

const H = 360;
const Z95 = 1.6449, Z75 = 0.6745;

function fmtTime(years) {
  if (years == null || !isFinite(years)) return '—';
  const min = years * 365 * 24 * 60;
  if (min < 1) return '<1 мин';
  if (min < 90) return `${Math.round(min)} мин`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)} ч`;
  return `${(h / 24).toFixed(1)} дн`;
}

export function initFan(canvas) {
  let data = null;
  const live = { r: null };
  let curR = null;

  function setData(cone) { data = cone && cone.available ? cone : null; }
  function updateLive(p) { if (p) Object.assign(live, p); }

  function draw(now) {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data) return;

    const T = data.T, r0 = data.r0, sig = data.sigma_R, drift = data.drift_R || 0;
    const hy = data.horizon_years;
    const rNow = curR != null ? curR : r0;

    const padL = 58, padR = 16, padT = 40, padB = 34;
    const plotW = w - padL - padR, plotH = H - padT - padB;
    // домен R: с запасом за барьерами, чтобы веер уходил в зоны П/У (без «обрезки»)
    const yLo = Math.min(-1.35, rNow - 0.4);
    const yHi = Math.max(T + 0.45, rNow + 0.4);
    const X = (tau) => padL + tau * plotW;
    const Y = (R) => padT + (yHi - R) / (yHi - yLo) * plotH;

    // зоны прибыли/убытка
    ctx.fillStyle = 'rgba(46,125,79,0.06)'; ctx.fillRect(padL, Y(yHi), plotW, Y(T) - Y(yHi));
    ctx.fillStyle = 'rgba(198,55,60,0.06)'; ctx.fillRect(padL, Y(-1), plotW, Y(yLo) - Y(-1));

    // веер: перцентили аналитической диффузии (гладко, без шума МК)
    const N = 64;
    const band = (z, sign) => {
      const pts = [];
      for (let i = 0; i <= N; i++) {
        const tau = i / N;
        const m = r0 + drift * tau, s = sig * Math.sqrt(tau);
        pts.push([X(tau), Y(m + sign * z * s)]);
      }
      return pts;
    };
    const fill = (up, lo, color) => {
      ctx.beginPath();
      up.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      for (let i = lo.length - 1; i >= 0; i--) ctx.lineTo(lo[i][0], lo[i][1]);
      ctx.closePath(); ctx.fillStyle = color; ctx.fill();
    };
    ctx.save();
    ctx.beginPath(); ctx.rect(padL, padT, plotW, plotH); ctx.clip();
    fill(band(Z95, 1), band(Z95, -1), 'rgba(232,98,42,0.10)');   // 5–95%
    fill(band(Z75, 1), band(Z75, -1), 'rgba(232,98,42,0.20)');   // 25–75%
    // медиана
    ctx.beginPath();
    for (let i = 0; i <= N; i++) { const tau = i / N; const p = [X(tau), Y(r0 + drift * tau)]; i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); }
    ctx.strokeStyle = '#E8622A'; ctx.lineWidth = 2; ctx.stroke();
    ctx.restore();

    // барьеры + вход
    const hline = (R, color, dash, lbl, lblColor) => {
      ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(padL, Y(R)); ctx.lineTo(w - padR, Y(R)); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = lblColor || color; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'left';
      ctx.fillText(lbl, padL + 2, Y(R) - 3);
    };
    hline(T, COLORS.green, [], `ТЕЙК +${T.toFixed(2)}R · дойти ${(data.p_take * 100).toFixed(0)}%`, COLORS.green);
    hline(0, COLORS.dim, [3, 3], 'ВХОД (0)', COLORS.dim);
    hline(-1, COLORS.red, [], `СТОП −1R · дойти ${(data.p_stop * 100).toFixed(0)}%`, COLORS.red);

    // медиана развязки — вертикаль
    if (hy && data.median_years != null) {
      const tau = Math.max(0, Math.min(1, data.median_years / hy));
      ctx.strokeStyle = COLORS.dim; ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(tau), padT); ctx.lineTo(X(tau), padT + plotH); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = COLORS.dim; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
      ctx.fillText(`медиана ≈ ${fmtTime(data.median_years)}`, X(tau), padT - 4);
    }

    // текущая цена — пульсирующая точка на левом краю
    const yr = Y(Math.max(yLo, Math.min(yHi, rNow)));
    const pw = 0.5 + 0.5 * pulse(now, 1500);
    ctx.fillStyle = `rgba(232,98,42,${0.25 * pw})`; ctx.beginPath(); ctx.arc(X(0), yr, 9, 0, 7); ctx.fill();
    ctx.fillStyle = '#E8622A'; ctx.beginPath(); ctx.arc(X(0), yr, 4.5, 0, 7); ctx.fill();
    ctx.font = '700 10px "IBM Plex Mono", monospace'; ctx.textAlign = 'left';
    ctx.fillText(`r=${rNow >= 0 ? '+' : ''}${rNow.toFixed(2)}`, X(0) + 8, yr - 7);

    // ось времени
    ctx.fillStyle = COLORS.dim; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
    const tlabel = (tau) => hy ? fmtTime(hy * tau) : `${(tau * 100).toFixed(0)}%`;
    ctx.textAlign = 'left'; ctx.fillText('сейчас', padL, H - 12);
    ctx.textAlign = 'center'; ctx.fillText(tlabel(0.5), padL + plotW / 2, H - 12);
    ctx.textAlign = 'right'; ctx.fillText('развязка ' + tlabel(1), w - padR, H - 12);

    // R-подписи слева
    ctx.textAlign = 'right'; ctx.fillStyle = COLORS.dim;
    [T, 0, -1].forEach((R) => ctx.fillText(`${R >= 0 ? '+' : ''}${R}R`, padL - 4, Y(R) + 3));

    // ридаут: куда клонит + вероятности
    const lean = data.p_take > data.p_stop + 0.03 ? { t: 'КЛОНИТ К ТЕЙКУ', c: COLORS.green }
      : data.p_stop > data.p_take + 0.03 ? { t: 'КЛОНИТ К СТОПУ', c: COLORS.red }
      : { t: '≈ 50/50', c: COLORS.dim };
    ctx.textAlign = 'left'; ctx.font = '700 13px "IBM Plex Mono", monospace'; ctx.fillStyle = lean.c;
    ctx.fillText(lean.t, padL, 20);
    ctx.textAlign = 'right'; ctx.font = '10px "IBM Plex Mono", monospace'; ctx.fillStyle = COLORS.dim;
    ctx.fillText(`P дойти к развязке: ТЕЙК ${(data.p_take * 100).toFixed(0)}% · СТОП ${(data.p_stop * 100).toFixed(0)}%`, w - padR, 20);
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    const target = live.r != null ? live.r : (data ? data.r0 : null);
    if (target != null) curR = approach(curR, target, dt, 6);
    draw(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setData, updateLive };
}
