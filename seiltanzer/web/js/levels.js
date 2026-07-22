// Карта уровней — коридор цены вокруг позиции.
// Домен оси зумируется в сделку (вход/стоп/тейк/цена/implied-коридор); остальные
// уровни (FVG-зоны, GEX, VWAP, дневной диапазон) клипуются в это окно, чтобы один
// далёкий уровень не растягивал шкалу и не слепял всё в край.

import { COLORS, setupCanvas, fmtPrice } from './util.js';
import { approach } from './anim.js';

const H = 190;

export function initLevels(canvas) {
  let data = null;
  let curPrice = null;
  function setData(levels) { data = levels; }

  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data) return;
    const pnow = (curPrice != null && isFinite(curPrice)) ? curPrice : data.price;

    // домен — ТОЛЬКО по уровням сделки и опционному коридору
    const core = [data.entry, data.stop, data.take, pnow];
    if (data.implied_band) core.push(data.implied_band.low, data.implied_band.high);
    const valid = core.filter((x) => x != null && isFinite(x));
    let lo = Math.min(...valid), hi = Math.max(...valid);
    if (!(hi > lo)) { hi = lo + 1; }
    const pad = (hi - lo) * 0.14;
    lo -= pad; hi += pad;

    const padL = 16, padR = 16, plotW = w - padL - padR;
    const X = (p) => padL + ((p - lo) / (hi - lo)) * plotW;
    const inRange = (p) => p != null && isFinite(p) && p >= lo && p <= hi;
    const axisY = H - 34;
    const risk = Math.abs(data.entry - data.stop) || 1;
    const rOf = (p) => (data.direction === 'long' ? (p - data.entry) / risk
                                                  : (data.entry - p) / risk);

    // implied move ±1σ — затенённый коридор рынка (ключевая надбавленная ценность)
    if (data.implied_band) {
      const x0 = X(Math.max(data.implied_band.low, lo));
      const x1 = X(Math.min(data.implied_band.high, hi));
      ctx.fillStyle = 'rgba(46,125,79,0.08)';
      ctx.fillRect(x0, 20, x1 - x0, axisY - 20);
      ctx.strokeStyle = 'rgba(46,125,79,0.4)';
      ctx.setLineDash([2, 3]);
      ctx.strokeRect(x0, 20, x1 - x0, axisY - 20);
      ctx.setLineDash([]);
      ctx.fillStyle = COLORS.green;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('IMPLIED ±1σ' + (data.implied_band.demo ? ' ◆' : ''), x0 + 3, 30);
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
    function edgeArrow(price, color, label) {
      if (price == null || !isFinite(price) || inRange(price)) return;
      const left = price < lo;
      const x = left ? padL + 6 : w - padR - 6;
      ctx.fillStyle = color; ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = left ? 'left' : 'right';
      ctx.fillText(`${label} ${left ? '←' : '→'}`, x, axisY - 4);
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
    if (data.vwap != null) marker(data.vwap, '#5B6C9E', 'VWAP', 74, [1, 2], 1);
    if (inRange(data.day_low)) marker(data.day_low, COLORS.dim, 'LO', 86, [1, 3], 1);
    if (inRange(data.day_high)) marker(data.day_high, COLORS.dim, 'HI', 86, [1, 3], 1);

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
