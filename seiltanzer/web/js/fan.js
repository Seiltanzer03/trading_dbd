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
    const skew = data.skew || 0;            // >0 → сторона −R (страх) шире
    const ratio = data.rv_iv_ratio;         // реализ./implied вола (наценка ММ)
    const termSlope = data.term_slope || 0; // >0 контанго (вола дышит позже)
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

    // веер перцентилей — АСИММЕТРИЧНЫЙ по скью (сторона −R шире при skew>0):
    // это уже не симметричный Блэк-Шоулз, а реальная улыбка волы (толще хвост страха)
    const N = 64;
    // TERM-STRUCTURE: разброс растёт НЕ как √t линейно, а по форвардной воле.
    // varFrac(τ) = ∫₀^τ g² / ∫₀¹ g², g(s)=1+slope·(2s−1) — доля дисперсии к моменту
    // τ (та же нормировка, что в конусе: полная дисперсия сохранена). Контанго →
    // узко рано, шире к развязке; бэквордация → раздувается сразу.
    const aT = 1 - termSlope, bT = 2 * termSlope;
    const gInt1 = aT * aT + aT * bT + bT * bT / 3;              // ∫₀¹ g²
    const varFrac = (tau) => {
      const v = aT * aT * tau + aT * bT * tau * tau + bT * bT * tau * tau * tau / 3;
      return gInt1 > 1e-9 ? Math.max(0, v / gInt1) : tau;
    };
    const stdUp = (tau) => sig * (1 - skew) * Math.sqrt(varFrac(tau));   // вверх (+R)
    const stdDn = (tau) => sig * (1 + skew) * Math.sqrt(varFrac(tau));   // вниз (−R)
    const curve = (z, sign, upFn, dnFn) => {
      const pts = [];
      for (let i = 0; i <= N; i++) {
        const tau = i / N, m = r0 + drift * tau;
        pts.push([X(tau), Y(m + (sign > 0 ? z * upFn(tau) : -z * dnFn(tau)))]);
      }
      return pts;
    };
    const fill = (up, lo, color) => {
      ctx.beginPath();
      up.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      for (let i = lo.length - 1; i >= 0; i--) ctx.lineTo(lo[i][0], lo[i][1]);
      ctx.closePath(); ctx.fillStyle = color; ctx.fill();
    };
    const stroke = (pts, color, dash) => {
      ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]);
    };
    ctx.save();
    ctx.beginPath(); ctx.rect(padL, padT, plotW, plotH); ctx.clip();
    fill(curve(Z95, 1, stdUp, stdDn), curve(Z95, -1, stdUp, stdDn), 'rgba(232,98,42,0.10)');  // 5–95% implied
    fill(curve(Z75, 1, stdUp, stdDn), curve(Z75, -1, stdUp, stdDn), 'rgba(232,98,42,0.20)');  // 25–75%
    // веер РЕАЛИЗОВАННОЙ волы (пунктир) — сравнение с рынком опционов
    if (ratio) {
      const upR = (tau) => sig * ratio * (1 - skew) * Math.sqrt(varFrac(tau));
      const dnR = (tau) => sig * ratio * (1 + skew) * Math.sqrt(varFrac(tau));
      stroke(curve(Z95, 1, upR, dnR), COLORS.dim, [4, 3]);
      stroke(curve(Z95, -1, upR, dnR), COLORS.dim, [4, 3]);
    }
    // медиана
    stroke(curve(0, 1, stdUp, stdDn), '#E8622A', []);
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
      const termTag = termSlope > 0.03 ? ' · контанго' : termSlope < -0.03 ? ' · бэквордация' : '';
      ctx.fillText(`медиана ≈ ${fmtTime(data.median_years)}${termTag}`, X(tau), padT - 4);
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

    // ридаут строка 1: куда клонит + вероятности
    const lean = data.p_take > data.p_stop + 0.03 ? { t: 'КЛОНИТ К ТЕЙКУ', c: COLORS.green }
      : data.p_stop > data.p_take + 0.03 ? { t: 'КЛОНИТ К СТОПУ', c: COLORS.red }
      : { t: '≈ 50/50', c: COLORS.dim };
    ctx.textAlign = 'left'; ctx.font = '700 12px "IBM Plex Mono", monospace'; ctx.fillStyle = lean.c;
    ctx.fillText(lean.t, padL, 14);
    ctx.textAlign = 'right'; ctx.font = '10px "IBM Plex Mono", monospace'; ctx.fillStyle = COLORS.dim;
    ctx.fillText(`P дойти: ТЕЙК ${(data.p_take * 100).toFixed(0)}% · СТОП ${(data.p_stop * 100).toFixed(0)}%`, w - padR, 14);
    // строка 2: наценка ММ (IV vs RV, пунктирный веер) + перекос волы (скью)
    ctx.font = '9px "IBM Plex Mono", monospace';
    if (ratio != null) {
      const mm = ratio < 0.88 ? { t: `опционы ДОРОЖЕ факта ×${(1 / ratio).toFixed(2)} (RR обманчив)`, c: COLORS.red }
        : ratio > 1.14 ? { t: `опционы дешевле факта ×${ratio.toFixed(2)}`, c: COLORS.green }
        : { t: 'опционы ≈ реальной воле', c: COLORS.dim };
      ctx.textAlign = 'left'; ctx.fillStyle = mm.c; ctx.fillText('◌ пунктир = реализ. вола · ' + mm.t, padL, 30);
    }
    if (Math.abs(skew) > 0.03) {
      ctx.textAlign = 'right'; ctx.fillStyle = COLORS.dim;
      ctx.fillText(`скью: хвост страха ${skew > 0 ? 'к стопу' : 'к тейку'} шире`, w - padR, 30);
    }
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
