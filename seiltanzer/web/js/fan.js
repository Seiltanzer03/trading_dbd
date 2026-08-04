// Probability Fan — 2D «вероятностный веер» (стандарт квант-деска).
//
// Оранжевая центральная линия — условная медиана ещё не поглощённых сценариев
// опционного конуса. К началу траектории добавляется короткий live-импульс цены,
// который быстро затухает и не экстраполируется на весь option-horizon.

import { COLORS, setupCanvas } from './util.js';
import { approach, pulse } from './anim.js';

const H = 360;
const Z95 = 1.6449, Z75 = 0.6745;
const LIVE_DECAY_TAU = 0.18;

function fmtProb(p) {
  if (p == null || !Number.isFinite(p)) return '—';
  const pct = p * 100;
  if (pct < 0.1) return '<0.1%';
  return `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`;
}

function fmtTime(years) {
  if (years == null || !isFinite(years)) return '—';
  const min = years * 365 * 24 * 60;
  if (min < 1) return '<1 мин';
  if (min < 90) return `${Math.round(min)} мин`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)} ч`;
  return `${(h / 24).toFixed(1)} дн`;
}

function clamp(value, limit) {
  const v = Number(value) || 0;
  return Math.max(-limit, Math.min(limit, v));
}

/** Квантиль гистограммы; веса могут быть ненормированными. */
export function histogramQuantile(weights, edges, q = 0.5) {
  if (!Array.isArray(weights) || !Array.isArray(edges)
      || edges.length !== weights.length + 1 || !weights.length) return null;
  const clean = weights.map((value) => Math.max(0, Number(value) || 0));
  const total = clean.reduce((sum, value) => sum + value, 0);
  if (!(total > 0)) return null;
  const target = Math.max(0, Math.min(1, q)) * total;
  let cumulative = 0;
  for (let i = 0; i < clean.length; i++) {
    const next = cumulative + clean[i];
    if (target <= next || i === clean.length - 1) {
      const width = Number(edges[i + 1]) - Number(edges[i]);
      const frac = clean[i] > 0 ? (target - cumulative) / clean[i] : 0.5;
      return Number(edges[i]) + Math.max(0, Math.min(1, frac)) * width;
    }
    cumulative = next;
  }
  return Number(edges.at(-1));
}

/**
 * Медианная траектория живых путей из уже рассчитанной cone.density.
 * Значения храним как смещение относительно r0, чтобы текущий live-r мог
 * подвинуть всю кривую без пересчёта Монте-Карло на каждом тике.
 */
export function buildConditionalMedianPath(cone) {
  const density = cone?.density;
  const times = cone?.times_frac;
  const edges = cone?.edges;
  const r0 = Number(cone?.r0);
  if (!Array.isArray(density) || !Array.isArray(times) || !Array.isArray(edges)
      || density.length !== times.length || !Number.isFinite(r0)) return [];

  const path = [{ tau: 0, offset: 0, alive: 1 }];
  let lastOffset = 0;
  for (let i = 0; i < density.length; i++) {
    const row = density[i];
    const tau = Math.max(0, Math.min(1, Number(times[i]) || 0));
    const alive = Array.isArray(row)
      ? row.reduce((sum, value) => sum + Math.max(0, Number(value) || 0), 0)
      : 0;
    const median = alive >= 0.015 ? histogramQuantile(row, edges, 0.5) : null;
    if (Number.isFinite(median)) lastOffset = median - r0;
    path.push({ tau, offset: lastOffset, alive });
  }

  path.sort((a, b) => a.tau - b.tau);
  const compact = [];
  for (const point of path) {
    if (compact.length && Math.abs(point.tau - compact.at(-1).tau) < 1e-9) {
      compact[compact.length - 1] = point;
    } else {
      compact.push(point);
    }
  }
  return compact;
}

export function interpolateMedianOffset(path, tau) {
  if (!Array.isArray(path) || !path.length) return 0;
  const t = Math.max(0, Math.min(1, Number(tau) || 0));
  if (t <= path[0].tau) return path[0].offset;
  for (let i = 1; i < path.length; i++) {
    if (t <= path[i].tau) {
      const a = path[i - 1], b = path[i];
      const span = Math.max(b.tau - a.tau, 1e-9);
      const u = (t - a.tau) / span;
      return a.offset + (b.offset - a.offset) * u;
    }
  }
  return path.at(-1).offset;
}

/** Нормированная ранняя пульсация: максимум = 1 около decayTau, затем → 0. */
export function liveImpulseShape(tau, decayTau = LIVE_DECAY_TAU) {
  const t = Math.max(0, Number(tau) || 0);
  const d = Math.max(0.03, Number(decayTau) || LIVE_DECAY_TAU);
  const x = t / d;
  return x * Math.exp(1 - x);
}

export function initFan(canvas) {
  let data = null;
  let medianPath = [];
  const live = { r: null };
  let curR = null;
  let rTrail = [];
  let liveImpulse = 0;

  function setData(cone) {
    data = cone && cone.available ? cone : null;
    medianPath = data ? buildConditionalMedianPath(data) : [];
    if (!data) {
      rTrail = [];
      liveImpulse = 0;
    }
  }

  function updateLive(p) {
    if (!p) return;
    Object.assign(live, p);
    const r = Number(p.r), ts = performance.now();
    if (Number.isFinite(r) && (!rTrail.length || Math.abs(r - rTrail.at(-1).r) > 1e-7)) {
      rTrail.push({ r, ts });
    }
    rTrail = rTrail.filter((pt) => ts - pt.ts <= 120000);
  }

  function projectedMove(windowMs, projectionSec) {
    if (rTrail.length < 2) return 0;
    const end = rTrail.at(-1);
    const pts = rTrail.filter((pt) => end.ts - pt.ts <= windowMs);
    if (pts.length < 2) return 0;
    const first = pts[0], elapsed = Math.max((end.ts - first.ts) / 1000, 1);
    return (end.r - first.r) / elapsed * projectionSec;
  }

  function targetLiveImpulse() {
    const fast = clamp(projectedMove(18000, 30), 0.38);
    const slow = clamp(projectedMove(90000, 80), 0.30);
    return clamp(fast * 0.68 + slow * 0.32, 0.45);
  }

  function draw(now) {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data) return;

    const T = data.T, r0 = data.r0, sig = data.sigma_R, drift = data.drift_R || 0;
    const hy = data.horizon_years;
    const skew = data.skew || 0;
    const ratio = data.rv_iv_ratio;
    const termSlope = data.term_slope || 0;
    const anchored = !!data.option_anchored;
    const touchTake = data.p_take;
    const touchStop = data.p_stop;
    const noTouch = data.unresolved;
    const rNow = curR != null ? curR : r0;

    const optionCenter = (tau) => {
      if (medianPath.length) return rNow + interpolateMedianOffset(medianPath, tau);
      return rNow + drift * tau;
    };
    const centerAt = (tau) => optionCenter(tau)
      + liveImpulse * liveImpulseShape(tau, LIVE_DECAY_TAU);

    const padL = 58, padR = 16, padT = 40, padB = 34;
    const plotW = w - padL - padR, plotH = H - padT - padB;
    const centerSamples = Array.from({ length: 17 }, (_, i) => centerAt(i / 16));
    const centerLo = Math.min(...centerSamples);
    const centerHi = Math.max(...centerSamples);
    const yLo = Math.min(-1.35, rNow - 0.4, centerLo - 0.25);
    const yHi = Math.max(T + 0.45, rNow + 0.4, centerHi + 0.25);
    const X = (tau) => padL + tau * plotW;
    const Y = (R) => padT + (yHi - R) / (yHi - yLo) * plotH;

    ctx.font = '8px "IBM Plex Mono", monospace';
    [0, 0.25, 0.5, 0.75, 1].forEach((tau) => {
      ctx.strokeStyle = tau === 1 ? 'rgba(20,20,15,.38)' : 'rgba(20,20,15,.10)';
      ctx.lineWidth = 1; ctx.setLineDash(tau === 1 ? [3, 3] : [2, 4]);
      ctx.beginPath(); ctx.moveTo(X(tau), padT); ctx.lineTo(X(tau), padT + plotH); ctx.stroke();
      ctx.setLineDash([]);
    });

    ctx.fillStyle = 'rgba(46,125,79,0.06)'; ctx.fillRect(padL, Y(yHi), plotW, Y(T) - Y(yHi));
    ctx.fillStyle = 'rgba(198,55,60,0.06)'; ctx.fillRect(padL, Y(-1), plotW, Y(yLo) - Y(-1));

    const N = 64;
    const aT = 1 - termSlope, bT = 2 * termSlope;
    const gInt1 = aT * aT + aT * bT + bT * bT / 3;
    const varFrac = (tau) => {
      const v = aT * aT * tau + aT * bT * tau * tau + bT * bT * tau * tau * tau / 3;
      return gInt1 > 1e-9 ? Math.max(0, v / gInt1) : tau;
    };
    const stdUp = (tau) => sig * (1 - skew) * Math.sqrt(varFrac(tau));
    const stdDn = (tau) => sig * (1 + skew) * Math.sqrt(varFrac(tau));
    const curve = (z, sign, upFn, dnFn) => {
      const pts = [];
      for (let i = 0; i <= N; i++) {
        const tau = i / N, m = centerAt(tau);
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
    const stroke = (pts, color, dash, width = 1) => {
      ctx.beginPath(); pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
      ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash); ctx.stroke(); ctx.setLineDash([]);
    };
    ctx.save();
    ctx.beginPath(); ctx.rect(padL, padT, plotW, plotH); ctx.clip();
    fill(curve(Z95, 1, stdUp, stdDn), curve(Z95, -1, stdUp, stdDn), 'rgba(232,98,42,0.10)');
    fill(curve(Z75, 1, stdUp, stdDn), curve(Z75, -1, stdUp, stdDn), 'rgba(232,98,42,0.20)');
    if (ratio) {
      const upR = (tau) => sig * ratio * (1 - skew) * Math.sqrt(varFrac(tau));
      const dnR = (tau) => sig * ratio * (1 + skew) * Math.sqrt(varFrac(tau));
      stroke(curve(Z95, 1, upR, dnR), COLORS.dim, [4, 3]);
      stroke(curve(Z95, -1, upR, dnR), COLORS.dim, [4, 3]);
    }
    stroke(curve(0, 1, stdUp, stdDn), '#E8622A', [], 2);
    ctx.restore();

    const hline = (R, color, dash, lbl, lblColor) => {
      ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(padL, Y(R)); ctx.lineTo(w - padR, Y(R)); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = lblColor || color; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'left';
      ctx.fillText(lbl, padL + 2, Y(R) - 3);
    };
    hline(T, COLORS.green, [], `ТЕЙК +${T.toFixed(2)}R · TOUCH≤H ${fmtProb(touchTake)}`, COLORS.green);
    hline(0, COLORS.dim, [3, 3], 'ВХОД (0)', COLORS.dim);
    hline(-1, COLORS.red, [], `СТОП −1R · TOUCH≤H ${fmtProb(touchStop)}`, COLORS.red);

    if (hy) {
      const hasMedian = data.median_years != null;
      const tau = hasMedian ? Math.max(0, Math.min(1, data.median_years / hy)) : 1;
      ctx.strokeStyle = COLORS.dim; ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(tau), padT); ctx.lineTo(X(tau), padT + plotH); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = COLORS.dim; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
      const termTag = termSlope > 0.03 ? ' · контанго' : termSlope < -0.03 ? ' · бэквордация' : '';
      const medianLabel = hasMedian
        ? `медиана касания ≈ ${fmtTime(data.median_years)}`
        : `медиана касания > ${fmtTime(hy)}`;
      ctx.textAlign = hasMedian ? 'center' : 'right';
      ctx.fillText(`${medianLabel}${termTag}`, X(tau) - (hasMedian ? 0 : 3), padT - 4);
    }

    const yr = Y(Math.max(yLo, Math.min(yHi, rNow)));
    const pw = 0.5 + 0.5 * pulse(now, 1500);
    ctx.fillStyle = `rgba(232,98,42,${0.25 * pw})`; ctx.beginPath(); ctx.arc(X(0), yr, 9, 0, 7); ctx.fill();
    ctx.fillStyle = '#E8622A'; ctx.beginPath(); ctx.arc(X(0), yr, 4.5, 0, 7); ctx.fill();
    ctx.font = '700 10px "IBM Plex Mono", monospace'; ctx.textAlign = 'left';
    ctx.fillText(`r=${rNow >= 0 ? '+' : ''}${rNow.toFixed(2)}`, X(0) + 8, yr - 7);

    ctx.fillStyle = COLORS.dim; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
    const tlabel = (tau) => hy ? fmtTime(hy * tau) : `${(tau * 100).toFixed(0)}%`;
    [0, 0.25, 0.5, 0.75, 1].forEach((tau) => {
      ctx.textAlign = tau === 0 ? 'left' : tau === 1 ? 'right' : 'center';
      const label = tau === 0 ? 'сейчас' : tau === 1 ? `H ${tlabel(1)}` : tlabel(tau);
      ctx.fillText(label, X(tau), H - 12);
      ctx.fillRect(X(tau) - 0.5, H - 28, 1, 4);
    });

    ctx.textAlign = 'right'; ctx.fillStyle = COLORS.dim;
    [T, 0, -1].forEach((R) => ctx.fillText(`${R >= 0 ? '+' : ''}${R.toFixed(2)}R`, padL - 4, Y(R) + 3));

    const nearTrend = centerAt(0.28) - rNow;
    const lean = nearTrend > 0.04 ? { t: 'ЖИВОЙ УКЛОН К ТЕЙКУ', c: COLORS.green }
      : nearTrend < -0.04 ? { t: 'ЖИВОЙ УКЛОН К СТОПУ', c: COLORS.red }
      : { t: 'ЖИВОЙ УКЛОН НЕЙТРАЛЕН', c: COLORS.dim };
    ctx.textAlign = 'left'; ctx.font = '700 12px "IBM Plex Mono", monospace'; ctx.fillStyle = lean.c;
    ctx.fillText(anchored ? lean.t : 'СЦЕНАРНЫЙ ВЕЕР · БЕЗ P / EDGE', padL, 14);
    ctx.textAlign = 'right'; ctx.font = '10px "IBM Plex Mono", monospace'; ctx.fillStyle = COLORS.dim;
    ctx.fillText(`${anchored ? 'FIRST-TOUCH≤H' : 'сценарии'}: ТЕЙК ${fmtProb(touchTake)} · СТОП ${fmtProb(touchStop)} · NO-TOUCH ${fmtProb(noTouch)}`, w - padR, 14);
    ctx.font = '9px "IBM Plex Mono", monospace';
    if (ratio != null) {
      const mm = ratio < 0.88 ? { t: `опционы ДОРОЖЕ факта ×${(1 / ratio).toFixed(2)} (RR обманчив)`, c: COLORS.red }
        : ratio > 1.14 ? { t: `опционы дешевле факта ×${ratio.toFixed(2)}`, c: COLORS.green }
        : { t: 'опционы ≈ реальной воле', c: COLORS.dim };
      ctx.textAlign = 'left'; ctx.fillStyle = mm.c; ctx.fillText('◌ пунктир = реализ. вола · ' + mm.t, padL, 30);
    } else {
      ctx.textAlign = 'left'; ctx.fillStyle = COLORS.dim;
      ctx.fillText('оранжевый = медиана живых option-paths + затухающий live-импульс', padL, 30);
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
    liveImpulse = approach(liveImpulse, targetLiveImpulse(), dt, 4);
    draw(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setData, updateLive };
}
