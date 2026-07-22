// Strike Landscape — гряда risk-neutral плотностей + профиль объёма (OI) + живая
// проекция модели.
//
//  • серые гряды — risk-neutral плотности из снапшотов цепочки (история интрадей);
//    нижняя жирная с заливкой — актуальная плотность рынка;
//  • столбики снизу — профиль open interest по страйкам (объём/ликвидность);
//  • цветная кривая спереди — проекция ВАШЕЙ модели (обновляется каждый тик,
//    сдвигается с ценой) → видно расхождение «рынок vs ваша статистика»;
//  • курсор цены и маркеры вход/стоп/тейк/GEX двигаются в реальном времени.

import { COLORS, setupCanvas, fmtPrice } from './util.js';

const H = 360;

export function initRidge(canvas) {
  let data = null;        // ridge payload
  let live = { price: null, modelHist: null, trade: null };

  function setData(ridgePayload, modelProb) {
    data = ridgePayload;
    live.modelProb = modelProb;
    draw();
  }
  // вызывается каждый тик — двигает курсор и проекцию модели без ожидания цепочки
  function updateLive(payload) {
    live = { ...live, ...payload };
    draw();
  }

  function priceToR(price, trade) {
    const risk = Math.abs(trade.entry - trade.stop) || 1;
    return trade.direction === 'long' ? (price - trade.entry) / risk
                                      : (trade.entry - price) / risk;
  }
  function rToPrice(R, trade) {
    const risk = Math.abs(trade.entry - trade.stop) || 1;
    return trade.direction === 'long' ? trade.entry + R * risk
                                      : trade.entry - R * risk;
  }

  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data || !data.available || !data.snapshots?.length) return;

    const scale = data.scale || 1.0;
    const snaps = data.snapshots.slice(-9);
    const latest = snaps[snaps.length - 1];
    const trade = live.trade || data.trade;
    const price = live.price ?? data.price;

    // ---- домен X: зум в коридор сделки, иначе — зона массы плотности
    const ks = latest.density.strikes.map((k) => k * scale);
    const qs = latest.density.q;
    const qmax = Math.max(...qs);
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < ks.length; i++)
      if (qs[i] > qmax * 0.002) { lo = Math.min(lo, ks[i]); hi = Math.max(hi, ks[i]); }
    if (trade) {
      const pts = [trade.stop, trade.take, trade.entry, price].filter((x) => x != null);
      const tLo = Math.min(...pts), tHi = Math.max(...pts);
      const span = (tHi - tLo) || 1;
      lo = Math.max(lo, tLo - span * 1.3);
      hi = Math.min(hi, tHi + span * 1.3);
      if (lo >= hi) { lo = tLo - span * 1.3; hi = tHi + span * 1.3; }
    }
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;

    const padL = 64, padR = 18, padT = 28, oiH = 46, padB = 26;
    const plotW = w - padL - padR;
    const ridgeBottom = H - padB - oiH;
    const X = (p) => padL + ((p - lo) / (hi - lo)) * plotW;

    // ---- профиль объёма (open interest) по страйкам — нижняя полоса
    const oi = latest.oi_profile;
    if (oi && oi.strikes) {
      const inWin = oi.strikes
        .map((k, i) => ({ x: k * scale, c: oi.call_oi[i], p: oi.put_oi[i] }))
        .filter((o) => o.x >= lo && o.x <= hi);
      const oiMax = Math.max(1, ...inWin.map((o) => o.c + o.p));
      const bw = Math.max(2, plotW / Math.max(inWin.length, 1) - 1);
      const oiBase = H - padB;
      inWin.forEach((o) => {
        const hc = (o.c / oiMax) * (oiH - 4);
        const hp = (o.p / oiMax) * (oiH - 4);
        ctx.fillStyle = 'rgba(46,125,79,0.45)';
        ctx.fillRect(X(o.x) - bw / 2, oiBase - hc, bw, hc);
        ctx.fillStyle = 'rgba(198,55,60,0.40)';
        ctx.fillRect(X(o.x) - bw / 2, oiBase - hc - hp, bw, hp);
      });
      ctx.fillStyle = COLORS.dim;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('OPEN INTEREST · коллы(зел)/путы(крас)', padL, ridgeBottom + 10);
    }

    // ---- гряды плотностей (старые сверху, актуальная снизу, с заливкой)
    const rowGap = (ridgeBottom - padT) / snaps.length;
    const amp = rowGap * 2.2;
    snaps.forEach((s, i) => {
      const isLast = i === snaps.length - 1;
      const baseY = padT + rowGap * (i + 1);
      const kk = s.density.strikes.map((k) => k * scale);
      const qq = s.density.q;
      const qm = Math.max(...qq) || 1;
      ctx.beginPath();
      ctx.moveTo(X(Math.max(kk[0], lo)), baseY);
      for (let j = 0; j < kk.length; j++) {
        if (kk[j] < lo || kk[j] > hi) continue;
        ctx.lineTo(X(kk[j]), baseY - (qq[j] / qm) * amp);
      }
      ctx.lineTo(X(Math.min(kk[kk.length - 1], hi)), baseY);
      ctx.closePath();
      ctx.fillStyle = isLast ? 'rgba(20,20,15,0.06)' : 'rgba(255,255,255,0.9)';
      ctx.fill();
      ctx.strokeStyle = isLast ? COLORS.ink : COLORS.dim;
      ctx.lineWidth = isLast ? 2 : 0.8;
      ctx.globalAlpha = isLast ? 1 : 0.4 + 0.5 * (i / snaps.length);
      ctx.stroke();
      ctx.globalAlpha = 1; ctx.lineWidth = 1;
      const d = new Date(s.ts * 1000);
      ctx.fillStyle = isLast ? COLORS.ink : COLORS.dim;
      ctx.font = '9px "IBM Plex Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(d.toISOString().slice(11, 16), padL - 6, baseY - 2);
    });

    // ---- проекция МОДЕЛИ (per-tick): распределение исхода в шкале цены
    if (live.modelHist && trade) {
      const { edges, probs } = live.modelHist;
      const pm = Math.max(...probs) || 1;
      const baseY = ridgeBottom;
      ctx.beginPath();
      let started = false;
      for (let b = 0; b < probs.length; b++) {
        const rMid = (edges[b] + edges[b + 1]) / 2;
        const px = rToPrice(rMid, trade);
        if (px < lo || px > hi) continue;
        const x = X(px), y = baseY - (probs[b] / pm) * amp * 1.1;
        started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
      }
      ctx.strokeStyle = COLORS.red;
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    // ---- вертикальные маркеры
    function vline(p, color, dash, label, top) {
      if (p == null || p < lo || p > hi) return;
      const x = X(p);
      ctx.strokeStyle = color; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(x, padT - 4); ctx.lineTo(x, ridgeBottom); ctx.stroke();
      ctx.setLineDash([]);
      if (label) {
        ctx.fillStyle = color; ctx.font = '9px "IBM Plex Mono", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(label, x, top ? padT - 6 : ridgeBottom + 11);
      }
    }
    const gex = latest.gex || {};
    (gex.top || []).forEach((t) => vline(t.strike * scale, COLORS.dim, [4, 4], 'GEX', true));
    if (gex.zero_flip) vline(gex.zero_flip * scale, '#A87A18', [2, 3], 'FLIP', true);
    if (trade) {
      vline(trade.entry, COLORS.ink, [], 'ВХОД', false);
      vline(trade.stop, COLORS.red, [], 'СТОП', false);
      vline(trade.take, COLORS.green, [], 'ТЕЙК', false);
    }
    if (price != null && price >= lo && price <= hi) {
      const x = X(price);
      ctx.fillStyle = COLORS.ink;
      ctx.beginPath();
      ctx.moveTo(x - 4, padT - 12); ctx.lineTo(x + 4, padT - 12);
      ctx.lineTo(x, padT - 4); ctx.closePath(); ctx.fill();
    }

    // ---- выноска рынок vs модель
    if (data.rn_probs && trade) {
      const box = [
        `P(ЗА ТЕЙК) РЫНОК ${(data.rn_probs.p_beyond_take * 100).toFixed(1)}%`,
        `P(ЗА СТОП) РЫНОК ${(data.rn_probs.p_beyond_stop * 100).toFixed(1)}%`,
        live.modelProb != null ? `P МОДЕЛИ ${(live.modelProb * 100).toFixed(1)}%` : null,
      ].filter(Boolean);
      ctx.font = '9px "IBM Plex Mono", monospace';
      const bw = Math.max(...box.map((s) => ctx.measureText(s).width)) + 14;
      const bx = Math.min(X(trade.take) + 8, w - padR - bw);
      const by = padT + 4;
      ctx.fillStyle = 'rgba(255,255,255,0.95)';
      ctx.strokeStyle = COLORS.rule;
      ctx.fillRect(bx, by, bw, box.length * 13 + 8);
      ctx.strokeRect(bx, by, bw, box.length * 13 + 8);
      ctx.textAlign = 'left';
      box.forEach((s, i) => {
        ctx.fillStyle = i === 0 ? COLORS.green : i === 1 ? COLORS.red : COLORS.ink;
        ctx.fillText(s, bx + 7, by + 14 + i * 13);
      });
    }

    // ---- ось цены + верхняя шкала R
    ctx.strokeStyle = COLORS.rule;
    ctx.beginPath(); ctx.moveTo(padL, ridgeBottom); ctx.lineTo(w - padR, ridgeBottom); ctx.stroke();
    ctx.fillStyle = COLORS.dim;
    ctx.font = '9px "IBM Plex Mono", monospace';
    for (let i = 0; i <= 6; i++) {
      const p = lo + ((hi - lo) * i) / 6, x = X(p);
      ctx.textAlign = 'center';
      ctx.fillStyle = COLORS.dim;
      ctx.fillText(fmtPrice(p), x, H - 5);            // ценовая шкала внизу
      if (trade) {
        const r = priceToR(p, trade);
        ctx.fillText(`${r >= 0 ? '+' : ''}${r.toFixed(1)}R`, x, padT - 16);  // шкала R сверху
      }
    }
  }

  window.addEventListener('resize', draw);
  return { setData, updateLive, redraw: draw };
}
