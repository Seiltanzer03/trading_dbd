// Карта уровней — коридор цены вокруг позиции.
// Домен оси зумируется в сделку (вход/стоп/тейк/цена); остальные
// уровни (FVG-зоны, GEX, VWAP, дневной диапазон) клипуются в это окно, чтобы один
// далёкий уровень не растягивал шкалу и не слепял всё в край.

import { COLORS, setupCanvas, fmtPrice } from './util.js';
import { approach } from './anim.js';

const H = 190;

export function initLevels(canvas) {
  let data = null;
  let curPrice = null;
  let view = null;
  let geometrySig = null;
  let pricePath = [];

  function setData(levels) {
    if (!levels) { data = null; return; }
    const nextSig = [levels.entry, levels.stop, levels.take, levels.direction]
      .map((v) => String(v)).join('|');
    if (nextSig !== geometrySig) {
      geometrySig = nextSig;
      view = null;
      pricePath = [];
      curPrice = null;
    }
    const p = Number(levels.price);
    const last = pricePath[pricePath.length - 1];
    if (Number.isFinite(p) && (!last || Math.abs(p - last.p) > 1e-12)) {
      pricePath.push({ p, ts: performance.now() });
      if (pricePath.length > 56) pricePath.shift();
    }
    data = levels;
    updateView(p);
  }

  function desiredView(pnow) {
    const core = [data.entry, data.stop, data.take, pnow]
      .filter((x) => x != null && isFinite(x));
    let lo = Math.min(...core), hi = Math.max(...core);
    const risk = Math.abs(data.entry - data.stop)
      || Math.abs(pnow || data.entry || 1) * 0.001 || 1;
    const minSpan = Math.max(risk * 2.2, Math.abs(pnow || data.entry || 1) * 0.00035);
    if (!(hi > lo)) { lo -= minSpan / 2; hi += minSpan / 2; }
    else if (hi - lo < minSpan) {
      const mid = (lo + hi) / 2;
      lo = mid - minSpan / 2; hi = mid + minSpan / 2;
    }
    const pad = (hi - lo) * 0.12;
    return { lo: lo - pad, hi: hi + pad };
  }

  function updateView(pnow) {
    if (!data) return;
    const target = desiredView(pnow);
    if (!view) { view = target; return; }
    // Стоп/вход/тейк задают устойчивый viewport. Он не пересчитывается от
    // каждого микротика и сдвигается только если цена реально выходит за 8%
    // защитной зоны.
    const span = view.hi - view.lo;
    const guardLo = view.lo + span * 0.08;
    const guardHi = view.hi - span * 0.08;
    if (pnow < guardLo) {
      const shift = pnow - (view.lo + span * 0.16);
      view = { lo: view.lo + shift, hi: view.hi + shift };
    } else if (pnow > guardHi) {
      const shift = pnow - (view.hi - span * 0.16);
      view = { lo: view.lo + shift, hi: view.hi + shift };
    }
  }

  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data) return;
    const pnow = (curPrice != null && isFinite(curPrice)) ? curPrice : data.price;

    const risk = Math.abs(data.entry - data.stop) || Math.abs(pnow || 1) * 0.001 || 1;
    if (!view) updateView(pnow);
    const lo = view.lo, hi = view.hi;

    const padL = 16, padR = 16, plotW = w - padL - padR;
    const X = (p) => padL + ((p - lo) / (hi - lo)) * plotW;
    const inRange = (p) => p != null && isFinite(p) && p >= lo && p <= hi;
    const axisY = H - 34;
    const rOf = (p) => (data.direction === 'long' ? (p - data.entry) / risk
                                                  : (data.entry - p) / risk);

    // implied move ±1σ — затенённый коридор рынка (ключевая надбавленная ценность)
    if (data.implied_band) {
      const bandLo = Math.max(data.implied_band.low, lo);
      const bandHi = Math.min(data.implied_band.high, hi);
      const x0 = X(bandLo), x1 = X(bandHi);
      if (bandHi > bandLo) {
        ctx.fillStyle = 'rgba(46,125,79,0.08)';
        ctx.fillRect(x0, 20, x1 - x0, axisY - 20);
        ctx.strokeStyle = 'rgba(46,125,79,0.4)';
        ctx.setLineDash([2, 3]);
        ctx.strokeRect(x0, 20, x1 - x0, axisY - 20);
        ctx.setLineDash([]);
      }
      ctx.fillStyle = COLORS.green;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('IMPLIED ±1σ' + (data.implied_band.demo ? ' ◆' : ''), Math.max(padL + 3, x0 + 3), 30);
      if (data.implied_band.low < lo) {
        ctx.textAlign = 'left';
        ctx.fillText(`← −1σ ${fmtPrice(data.implied_band.low)}`, padL + 3, 42);
      }
      if (data.implied_band.high > hi) {
        ctx.textAlign = 'right';
        ctx.fillText(`+1σ ${fmtPrice(data.implied_band.high)} →`, w - padR - 3, 42);
      }
    }

    // FVG-зоны пользователя (клипуются)
    (data.zones || []).forEach((z) => {
      if (z.low == null || z.high == null) return;
      if (z.high < lo || z.low > hi) return;
      const x0 = X(Math.max(z.low, lo)), x1 = X(Math.min(z.high, hi));
      ctx.fillStyle = 'rgba(138,135,125,0.16)';
      ctx.fillRect(x0, 40, x1 - x0, axisY - 56);
      ctx.strokeStyle = COLORS.dim;
      ctx.strokeRect(x0, 40, x1 - x0, axisY - 56);
      ctx.fillStyle = COLORS.ink;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`FVG ${z.tf || ''}`, (x0 + x1) / 2, 50);
    });

    // ось + шкала цены и R
    ctx.strokeStyle = COLORS.ink;
    ctx.beginPath(); ctx.moveTo(padL, axisY); ctx.lineTo(w - padR, axisY); ctx.stroke();
    ctx.fillStyle = COLORS.dim;
    ctx.font = '9px "IBM Plex Mono", monospace';
    for (let i = 0; i <= 6; i++) {
      const p = lo + ((hi - lo) * i) / 6, x = X(p);
      ctx.strokeStyle = COLORS.rule;
      ctx.beginPath(); ctx.moveTo(x, axisY); ctx.lineTo(x, axisY + 4); ctx.stroke();
      ctx.textAlign = 'center';
      ctx.fillText(fmtPrice(p), x, axisY + 15);
    }

    // маркер уровня со ступенчатой подписью (top: высота подписи)
    function marker(price, color, label, labelY, dash = [], lw = 1.6) {
      if (!inRange(price)) return;
      const x = X(price);
      ctx.strokeStyle = color; ctx.setLineDash(dash); ctx.lineWidth = lw;
      ctx.beginPath(); ctx.moveTo(x, labelY + 4); ctx.lineTo(x, axisY); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 1;
      ctx.fillStyle = color; ctx.font = '8.5px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(label, x, labelY);
    }
    // уровень за окном — стрелка у края
    const edgeLanes = { left: 0, right: 0 };
    function edgeArrow(price, color, label) {
      if (price == null || !isFinite(price) || inRange(price)) return;
      const left = price < lo;
      const side = left ? 'left' : 'right';
      const y = axisY - 5 - Math.min(edgeLanes[side]++, 4) * 11;
      const x = left ? padL + 6 : w - padR - 6;
      ctx.fillStyle = color; ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = left ? 'left' : 'right';
      ctx.fillText(`${label} ${left ? '←' : '→'}`, x, y);
    }

    // GEX / VWAP / дневной диапазон — контекст (клип или стрелка)
    (data.gex?.top || []).forEach((t) => {
      marker(t.price, COLORS.dim, 'GEX' + (data.gex.demo ? '◆' : ''), 62, [4, 4], 1);
      edgeArrow(t.price, COLORS.dim, 'GEX');
    });
    if (data.gex?.zero_flip) {
      marker(data.gex.zero_flip, '#A87A18', 'FLIP', 62, [2, 3], 1);
      edgeArrow(data.gex.zero_flip, '#A87A18', 'FLIP');
    }
    if (data.vwap != null) {
      marker(data.vwap, '#5B6C9E', 'VWAP', 74, [1, 2], 1);
      edgeArrow(data.vwap, '#5B6C9E', 'VWAP');
    }
    marker(data.day_low, COLORS.dim, 'LO', 86, [1, 3], 1);
    marker(data.day_high, COLORS.dim, 'HI', 86, [1, 3], 1);
    edgeArrow(data.day_low, COLORS.dim, 'LO');
    edgeArrow(data.day_high, COLORS.dim, 'HI');

    // гамма-магнит (притяжение пиннинга) — оранжевый ромб
    const gm = data.gamma;
    if (gm && inRange(gm.magnet)) {
      const x = X(gm.magnet);
      ctx.strokeStyle = '#E8622A'; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(x, 62); ctx.lineTo(x, axisY); ctx.stroke();
      ctx.setLineDash([]); ctx.lineWidth = 1;
      ctx.fillStyle = '#E8622A';
      ctx.beginPath();
      ctx.moveTo(x, 54); ctx.lineTo(x + 5, 60); ctx.lineTo(x, 66); ctx.lineTo(x - 5, 60);
      ctx.closePath(); ctx.fill();
      ctx.font = '8px "IBM Plex Mono", monospace'; ctx.textAlign = 'center';
      ctx.fillText(`ГАММА-МАГНИТ ${gm.zone === 'positive' ? '(пиннинг)' : '(снос)'}`, x, 50);
    }
    if (gm) edgeArrow(gm.magnet, '#E8622A', 'ГАММА');

    // сделка — ступенчато по высоте, чтобы подписи не слипались при тесном стопе
    marker(data.stop, COLORS.red, 'СТОП −1R', 10);
    marker(data.entry, COLORS.ink, 'ВХОД 0R', 24);
    marker(data.take, COLORS.green, `ТЕЙК +${rOf(data.take).toFixed(2)}R`, 10);

    // текущая цена — курсор снизу (скользит)
    if (inRange(pnow)) {
      const x = X(pnow);
      ctx.fillStyle = COLORS.ink;
      ctx.beginPath();
      ctx.moveTo(x - 5, axisY + 20); ctx.lineTo(x + 5, axisY + 20);
      ctx.lineTo(x, axisY + 12); ctx.closePath(); ctx.fill();
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`${fmtPrice(pnow)}  (${rOf(pnow) >= 0 ? '+' : ''}${rOf(pnow).toFixed(2)}R)`,
                   x, axisY + 31);
    }

    // Реальный тик-трейл вместо случайных «частиц потока». Каждая точка — цена,
    // реально полученная от фида; возраст кодируется высотой и прозрачностью.
    const visiblePath = pricePath.filter((pt) => inRange(pt.p));
    if (visiblePath.length > 1) {
      ctx.lineWidth = 1.6;
      ctx.strokeStyle = 'rgba(232,98,42,0.58)';
      ctx.beginPath();
      visiblePath.forEach((pt, i) => {
        const age = (visiblePath.length - 1 - i) / Math.max(visiblePath.length - 1, 1);
        const y = axisY - 8 - age * 25;
        if (i === 0) ctx.moveTo(X(pt.p), y);
        else ctx.lineTo(X(pt.p), y);
      });
      ctx.stroke();
      for (let i = 0; i < visiblePath.length; i += 4) {
        const pt = visiblePath[i];
        const age = (visiblePath.length - 1 - i) / Math.max(visiblePath.length - 1, 1);
        ctx.globalAlpha = 0.18 + (1 - age) * 0.62;
        ctx.fillStyle = '#E8622A';
        ctx.beginPath(); ctx.arc(X(pt.p), axisY - 8 - age * 25, 1.4, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    // Детерминированный условный gamma-vector: показывает только геометрию
    // сценария от текущей цены к магниту, без выдуманного наблюдаемого потока.
    if (gm && inRange(gm.magnet) && inRange(pnow)) {
      const x0 = X(pnow), x1 = X(gm.magnet), y = axisY - 42;
      ctx.globalAlpha = 0.25 + 0.45 * Math.max(0, Math.min(1, gm.strength || 0));
      ctx.strokeStyle = '#E8622A'; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
      const dir = x1 >= x0 ? 1 : -1;
      ctx.fillStyle = '#E8622A';
      ctx.beginPath(); ctx.moveTo(x1, y); ctx.lineTo(x1 - dir * 6, y - 3);
      ctx.lineTo(x1 - dir * 6, y + 3); ctx.closePath(); ctx.fill();
      ctx.globalAlpha = 1;
    }
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (data && data.price != null) curPrice = approach(curPrice, data.price, dt, 6);
    draw();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  window.addEventListener('resize', draw);
  return { setData };
}
