// Strike Landscape — живая гряда risk-neutral плотностей.
//
//  • серые гряды «дышат» (лёгкая анимация), нижняя жирная с заливкой — актуальная
//    рыночная плотность, плавно перетекающая при обновлении цепочки;
//  • оранжевые тейл-зоны — области за тейком/стопом, «куда платит рынок»;
//  • красная кривая — проекция вашей модели (обновляется каждый тик);
//  • профиль open interest (объём) снизу; курсор цены скользит в реальном времени.

import { COLORS, setupCanvas, fmtPrice } from './util.js';
import { approach, approachArr, pulse } from './anim.js';

const H = 360;

export function initRidge(canvas) {
  let data = null;
  const live = { price: null, modelHist: null, trade: null, modelProb: null };
  let curPrice = null, curModel = null, curLatest = null;

  function setData(payload, modelProb) { data = payload; live.modelProb = modelProb; }
  function updateLive(p) { Object.assign(live, p); }

  const priceToR = (price, tr) => {
    const risk = Math.abs(tr.entry - tr.stop) || 1;
    return tr.direction === 'long' ? (price - tr.entry) / risk : (tr.entry - price) / risk;
  };
  const rToPrice = (R, tr) => {
    const risk = Math.abs(tr.entry - tr.stop) || 1;
    return tr.direction === 'long' ? tr.entry + R * risk : tr.entry - R * risk;
  };

  function draw(now) {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data || !data.available || !data.snapshots?.length) return;

    const scale = data.scale || 1.0;
    const snaps = data.snapshots.slice(-9);
    const latest = snaps[snaps.length - 1];
    const trade = live.trade || data.trade;
    const price = curPrice ?? live.price ?? data.price;

    // домен: зум в коридор сделки
    const ks = latest.density.strikes.map((k) => k * scale);
    const qs = curLatest || latest.density.q;
    const qmax = Math.max(...qs);
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < ks.length; i++) if (qs[i] > qmax * 0.002) { lo = Math.min(lo, ks[i]); hi = Math.max(hi, ks[i]); }
    if (trade) {
      const pts = [trade.stop, trade.take, trade.entry, price].filter((x) => x != null);
      const tLo = Math.min(...pts), tHi = Math.max(...pts), span = (tHi - tLo) || 1;
      lo = Math.max(lo, tLo - span * 1.3); hi = Math.min(hi, tHi + span * 1.3);
      if (lo >= hi) { lo = tLo - span * 1.3; hi = tHi + span * 1.3; }
    }
    const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;

    const padL = 64, padR = 18, padT = 28, oiH = 44, padB = 24;
    const plotW = w - padL - padR;
    const ridgeBottom = H - padB - oiH;
    const X = (p) => padL + ((p - lo) / (hi - lo)) * plotW;

    // оранжевые тейл-зоны
    if (trade) {
      const glow = 0.05 + 0.05 * pulse(now, 2000);
      const tX = X(trade.take), sX = X(trade.stop);
      ctx.fillStyle = `rgba(232,98,42,${glow})`;
      if (trade.direction === 'long') ctx.fillRect(tX, padT - 4, (w - padR) - tX, ridgeBottom - padT + 4);
      else ctx.fillRect(padL, padT - 4, tX - padL, ridgeBottom - padT + 4);
      ctx.fillStyle = `rgba(198,55,60,${glow * 0.7})`;
      if (trade.direction === 'long') ctx.fillRect(padL, padT - 4, sX - padL, ridgeBottom - padT + 4);
      else ctx.fillRect(sX, padT - 4, (w - padR) - sX, ridgeBottom - padT + 4);
    }

    // профиль ЧИСТОЙ ГАММЫ (Net GEX) — «стены» дилерского хеджа (как на квант-деске):
    //   зелёное ВВЕРХ = + гамма (дилеры гасят движение -> пиннинг, реальная
    //     поддержка/сопротивление у крупных страйков);
    //   красное ВНИЗ = − гамма (дилеры разгоняют -> зоны пробоя, движения резче).
    // Заменяет сырой OI (сырой OI как «стены ликвидности» вынесен в колонку слева).
    const gx = latest.gex;
    if (gx && gx.strikes && gx.net) {
      const inWin = gx.strikes.map((k, i) => ({ x: k * scale, g: gx.net[i] }))
        .filter((o) => o.x >= lo && o.x <= hi);
      const gAbsMax = Math.max(1e-9, ...inWin.map((o) => Math.abs(o.g)));
      const bw = Math.max(2, plotW / Math.max(inWin.length, 1) - 1);
      const midY = ridgeBottom + oiH / 2;   // нулевая линия гаммы (центр полосы)
      const half = oiH / 2 - 3;
      ctx.strokeStyle = COLORS.rule; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, midY); ctx.lineTo(w - padR, midY); ctx.stroke();
      inWin.forEach((o) => {
        const hgt = (Math.abs(o.g) / gAbsMax) * half;
        if (o.g >= 0) { ctx.fillStyle = 'rgba(46,125,79,0.6)'; ctx.fillRect(X(o.x) - bw / 2, midY - hgt, bw, hgt); }
        else { ctx.fillStyle = 'rgba(198,55,60,0.55)'; ctx.fillRect(X(o.x) - bw / 2, midY, bw, hgt); }
      });
      // zero-gamma flip — граница режимов (пунктир)
      if (gx.zero_flip) {
        const fx = X(gx.zero_flip * scale);
        if (fx >= padL && fx <= w - padR) {
          ctx.strokeStyle = '#A87A18'; ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(fx, ridgeBottom); ctx.lineTo(fx, H - padB); ctx.stroke(); ctx.setLineDash([]);
        }
      }
      // подпись с белой подложкой (читаемо поверх столбиков)
      ctx.font = '8px "IBM Plex Mono", monospace'; ctx.textAlign = 'left';
      const cap = 'NET GEX · +гамма зел(гасит/пиннинг) / −гамма крас(разгоняет)';
      const cw = ctx.measureText(cap).width;
      ctx.fillStyle = 'rgba(255,255,255,0.82)'; ctx.fillRect(padL - 1, ridgeBottom + 2, cw + 4, 10);
      ctx.fillStyle = COLORS.dim; ctx.fillText(cap, padL + 1, ridgeBottom + 10);
    }

    // гряды с дыханием + изометрическая перспектива (3D-объём): старые ряды
    // уходят вглубь-вправо, текущая (нижняя) выровнена с маркерами сделки
    const rowGap = (ridgeBottom - padT) / snaps.length, amp = rowGap * 2.2;
    const DEPTH = plotW * 0.024;
    snaps.forEach((snap, i) => {
      const isLast = i === snaps.length - 1;
      const baseY = padT + rowGap * (i + 1);
      const depthX = (snaps.length - 1 - i) * DEPTH;   // 0 для текущей, растёт для старых
      const XD = (p) => X(p) + depthX;
      const kk = snap.density.strikes.map((k) => k * scale);
      const qq = isLast && curLatest ? curLatest : snap.density.q;
      const qm = Math.max(...qq) || 1;
      const breath = 1 + 0.05 * Math.sin(now / 900 + i * 0.7);  // лёгкое дыхание
      ctx.beginPath(); ctx.moveTo(XD(Math.max(kk[0], lo)), baseY);
      for (let j = 0; j < kk.length; j++) { if (kk[j] < lo || kk[j] > hi) continue; ctx.lineTo(XD(kk[j]), baseY - (qq[j] / qm) * amp * (isLast ? 1 : breath)); }
      ctx.lineTo(XD(Math.min(kk[kk.length - 1], hi)), baseY); ctx.closePath();
      ctx.fillStyle = isLast ? 'rgba(20,20,15,0.06)' : 'rgba(255,255,255,0.9)'; ctx.fill();
      ctx.strokeStyle = isLast ? COLORS.ink : COLORS.dim; ctx.lineWidth = isLast ? 2 : 0.8;
      ctx.globalAlpha = isLast ? 1 : 0.4 + 0.5 * (i / snaps.length); ctx.stroke(); ctx.globalAlpha = 1; ctx.lineWidth = 1;
      const d = new Date(snap.ts * 1000);
      ctx.fillStyle = isLast ? COLORS.ink : COLORS.dim; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'right';
      ctx.fillText(d.toISOString().slice(11, 16), padL - 6 + depthX, baseY - 2);
    });

    // проекция модели (красная, живая)
    if (curModel && trade) {
      const edges = (live.modelHist || {}).edges;
      if (edges) {
        const pm = Math.max(...curModel) || 1; let started = false;
        ctx.beginPath();
        for (let b = 0; b < curModel.length; b++) {
          const rMid = (edges[b] + edges[b + 1]) / 2, px = rToPrice(rMid, trade);
          if (px < lo || px > hi) continue;
          const x = X(px), y = ridgeBottom - (curModel[b] / pm) * amp * 1.1;
          started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
        }
        ctx.strokeStyle = COLORS.red; ctx.lineWidth = 2; ctx.stroke();
      }
    }

    // маркеры
    function vline(p, color, dash, label, top) {
      if (p == null || p < lo || p > hi) return;
      const x = X(p); ctx.strokeStyle = color; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(x, padT - 4); ctx.lineTo(x, ridgeBottom); ctx.stroke(); ctx.setLineDash([]);
      if (label) { ctx.fillStyle = color; ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'center'; ctx.fillText(label, x, top ? padT - 6 : ridgeBottom + 11); }
    }
    const gex = latest.gex || {};
    (gex.top || []).forEach((t) => vline(t.strike * scale, COLORS.dim, [4, 4], 'GEX', true));
    if (gex.zero_flip) vline(gex.zero_flip * scale, '#A87A18', [2, 3], 'FLIP', true);
    if (trade) { vline(trade.entry, COLORS.ink, [], 'ВХОД', false); vline(trade.stop, COLORS.red, [], 'СТОП', false); vline(trade.take, '#E8622A', [], 'ТЕЙК', false); }
    // ЖИВОЙ ЛУЧ ЦЕНЫ — жирная вертикаль через весь 3D-стек, скользит с котировкой
    if (price != null && price >= lo && price <= hi) {
      const x = X(price);
      const pw = 0.5 + 0.5 * pulse(now, 1400);
      ctx.strokeStyle = 'rgba(232,98,42,0.85)'; ctx.lineWidth = 2.2;
      ctx.beginPath(); ctx.moveTo(x, padT - 14); ctx.lineTo(x, ridgeBottom); ctx.stroke();
      ctx.strokeStyle = `rgba(232,98,42,${0.18 * pw})`; ctx.lineWidth = 6;
      ctx.beginPath(); ctx.moveTo(x, padT - 14); ctx.lineTo(x, ridgeBottom); ctx.stroke();
      ctx.lineWidth = 1; ctx.fillStyle = '#E8622A';
      ctx.beginPath(); ctx.moveTo(x - 5, padT - 14); ctx.lineTo(x + 5, padT - 14); ctx.lineTo(x, padT - 6); ctx.closePath(); ctx.fill();
      ctx.font = '9px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
      ctx.fillText('ЦЕНА ' + fmtPrice(price), x, padT - 18);
    }

    // выноска рынок vs модель
    if (data.rn_probs && trade) {
      const pt = data.rn_probs.p_beyond_take;
      const mult = pt > 0 ? (1 / pt) : null;
      const box = [
        `P(ЗА ТЕЙК) РЫНОК ${(pt * 100).toFixed(1)}%`,
        mult ? `×${mult.toFixed(mult >= 10 ? 0 : 1)} ЕСЛИ ПРОБЬЁТ ТЕЙК` : null,
        `P(ЗА СТОП) РЫНОК ${(data.rn_probs.p_beyond_stop * 100).toFixed(1)}%`,
        live.modelProb != null ? `P МОДЕЛИ ${(live.modelProb * 100).toFixed(1)}%` : null,
      ].filter(Boolean);
      ctx.font = '9px "IBM Plex Mono", monospace';
      const bw = Math.max(...box.map((s) => ctx.measureText(s).width)) + 14;
      const bx = Math.min(X(trade.take) + 8, w - padR - bw), by = padT + 4;
      ctx.fillStyle = 'rgba(255,255,255,0.95)'; ctx.strokeStyle = COLORS.rule;
      ctx.fillRect(bx, by, bw, box.length * 13 + 8); ctx.strokeRect(bx, by, bw, box.length * 13 + 8);
      ctx.textAlign = 'left';
      box.forEach((str, i) => {
        ctx.fillStyle = str.includes('ТЕЙК') ? '#E8622A' : str.includes('СТОП') ? COLORS.red : COLORS.ink;
        ctx.fillText(str, bx + 7, by + 14 + i * 13);
      });
    }

    // ось
    ctx.strokeStyle = COLORS.rule; ctx.beginPath(); ctx.moveTo(padL, ridgeBottom); ctx.lineTo(w - padR, ridgeBottom); ctx.stroke();
    ctx.font = '9px "IBM Plex Mono", monospace';
    for (let i = 0; i <= 6; i++) {
      const p = lo + ((hi - lo) * i) / 6, x = X(p);
      ctx.textAlign = 'center'; ctx.fillStyle = COLORS.dim;
      ctx.fillText(fmtPrice(p), x, H - 5);
      if (trade) { const r = priceToR(p, trade); ctx.fillText(`${r >= 0 ? '+' : ''}${r.toFixed(1)}R`, x, padT - 16); }
    }
  }

  // непрерывный рендер + сглаживание
  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (live.price != null) curPrice = approach(curPrice, live.price, dt, 6);
    if (live.modelHist?.probs) curModel = approachArr(curModel, live.modelHist.probs, dt, 6);
    const latest = data?.snapshots?.[data.snapshots.length - 1];
    if (latest) curLatest = approachArr(curLatest, latest.density.q, dt, 3);
    draw(now);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setData, updateLive, redraw: () => {} };
}
