// Strike Landscape — гряда risk-neutral плотностей (реальные снапшоты цепочки).
//
// Верхние гряды — история интрадей-снапшотов (каждые ~10 мин), нижняя жирная —
// актуальная. Ось X — цена в шкале инструмента (страйки прокси × scale),
// сверху — вторая шкала в R текущей сделки. Вертикали: вход/стоп/тейк/GEX.

import { COLORS, setupCanvas, fmtPrice } from './util.js';

const H = 340;

export function initRidge(canvas) {
  let data = null;   // ridge payload с сервера
  let modelP = null; // модельная P для сравнения в выноске

  function setData(ridgePayload, modelProb) {
    data = ridgePayload;
    modelP = modelProb;
    draw();
  }

  function priceToR(price, trade) {
    const risk = Math.abs(trade.entry - trade.stop);
    if (!risk) return null;
    return trade.direction === 'long'
      ? (price - trade.entry) / risk
      : (trade.entry - price) / risk;
  }

  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data || !data.available || !data.snapshots?.length) return;

    const scale = data.scale || 1.0;
    const snaps = data.snapshots.slice(-9);
    const latest = snaps[snaps.length - 1];

    // домен X: зона массы актуальной плотности + уровни сделки
    const ks = latest.density.strikes.map((k) => k * scale);
    const qs = latest.density.q;
    const qmax = Math.max(...qs);
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < ks.length; i++) {
      if (qs[i] > qmax * 0.002) { lo = Math.min(lo, ks[i]); hi = Math.max(hi, ks[i]); }
    }
    if (data.trade) {
      // при открытой сделке зумируемся в её коридор: уровни ± 1.2 диапазона
      const pts = [data.trade.stop, data.trade.take, data.trade.entry, data.price]
        .filter((x) => x != null);
      const tLo = Math.min(...pts), tHi = Math.max(...pts);
      const span = (tHi - tLo) || 1;
      lo = Math.max(lo, tLo - span * 1.2);
      hi = Math.min(hi, tHi + span * 1.2);
      if (lo >= hi) { lo = tLo - span * 1.2; hi = tHi + span * 1.2; }
    }
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;

    const padL = 64, padR = 18, padT = 30, padB = 26;
    const plotW = w - padL - padR;
    const X = (price) => padL + ((price - lo) / (hi - lo)) * plotW;

    const rowGap = (H - padT - padB) / snaps.length;
    const amp = rowGap * 2.1; // гряды перекрываются — классический ridgeline

    // --- гряды: старые сверху, актуальная снизу
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
      ctx.fillStyle = isLast ? 'rgba(255,255,255,0.97)' : 'rgba(255,255,255,0.88)';
      ctx.fill();
      ctx.strokeStyle = isLast ? COLORS.ink : COLORS.dim;
      ctx.lineWidth = isLast ? 2 : 0.8;
      ctx.globalAlpha = isLast ? 1 : 0.55 + 0.45 * (i / snaps.length);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.lineWidth = 1;
      // метка времени снапшота
      const d = new Date(s.ts * 1000);
      ctx.fillStyle = isLast ? COLORS.ink : COLORS.dim;
      ctx.font = '9px "IBM Plex Mono", monospace';
      ctx.textAlign = 'right';
      ctx.fillText(d.toISOString().slice(11, 16), padL - 6, baseY - 2);
    });

    // --- вертикальные маркеры (GEX подписываем сверху, сделку — снизу,
    //     чтобы подписи не наезжали друг на друга)
    function vline(price, color, dash, label, labelTop) {
      if (price == null || price < lo || price > hi) return;
      const x = X(price);
      ctx.strokeStyle = color;
      ctx.setLineDash(dash);
      ctx.beginPath();
      ctx.moveTo(x, padT - 4);
      ctx.lineTo(x, H - padB);
      ctx.stroke();
      ctx.setLineDash([]);
      if (label) {
        ctx.fillStyle = color;
        ctx.font = '9px "IBM Plex Mono", monospace';
        ctx.textAlign = 'center';
        ctx.fillText(label, x, labelTop ? padT - 6 : H - padB + 12);
      }
    }

    const gex = latest.gex || {};
    (gex.top || []).forEach((t) => vline(t.strike * scale, COLORS.dim, [4, 4], 'GEX', true));
    if (gex.zero_flip) vline(gex.zero_flip * scale, '#A87A18', [2, 3], 'FLIP', true);

    if (data.trade) {
      vline(data.trade.entry, COLORS.ink, [], 'ВХОД');
      vline(data.trade.stop, COLORS.red, [], 'СТОП');
      vline(data.trade.take, COLORS.green, [], 'ТЕЙК');
    }
    if (data.price != null) {
      const x = X(data.price);
      if (x >= padL && x <= w - padR) {
        ctx.fillStyle = COLORS.ink;
        ctx.beginPath();
        ctx.moveTo(x - 4, padT - 10);
        ctx.lineTo(x + 4, padT - 10);
        ctx.lineTo(x, padT - 3);
        ctx.closePath();
        ctx.fill();
      }
    }

    // --- выноска на актуальной гряде: рынок vs модель
    if (data.rn_probs && data.trade) {
      const box = [
        `P(ЗА ТЕЙК) РЫНОК ${(data.rn_probs.p_beyond_take * 100).toFixed(1)}%`,
        `P(ЗА СТОП) РЫНОК ${(data.rn_probs.p_beyond_stop * 100).toFixed(1)}%`,
        modelP != null ? `P МОДЕЛИ ${(modelP * 100).toFixed(1)}%` : null,
      ].filter(Boolean);
      ctx.font = '9px "IBM Plex Mono", monospace';
      const bw = Math.max(...box.map((s) => ctx.measureText(s).width)) + 14;
      const bx = Math.min(X(data.trade.take) + 8, w - padR - bw);
      const by = padT + 6;
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

    // --- ось X (цена) и верхняя шкала R
    ctx.strokeStyle = COLORS.rule;
    ctx.beginPath();
    ctx.moveTo(padL, H - padB);
    ctx.lineTo(w - padR, H - padB);
    ctx.stroke();
    ctx.fillStyle = COLORS.dim;
    ctx.font = '9px "IBM Plex Mono", monospace';
    const nTicks = 6;
    for (let i = 0; i <= nTicks; i++) {
      const price = lo + ((hi - lo) * i) / nTicks;
      const x = X(price);
      ctx.textAlign = 'center';
      ctx.fillText(fmtPrice(price), x, H - 6);
      if (data.trade) {
        const r = priceToR(price, data.trade);
        if (r != null) {
          ctx.fillText(`${r >= 0 ? '+' : ''}${r.toFixed(1)}R`, x, padT - 16);
        }
      }
    }
  }

  window.addEventListener('resize', draw);
  return { setData, redraw: draw };
}
