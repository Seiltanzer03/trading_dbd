// Карта уровней — горизонтальная шкала цены вокруг позиции.
// FVG-зоны пользователя, GEX-уровни, implied move ±1σ до конца сессии,
// VWAP дня, вход/стоп/тейк, текущая цена.

import { COLORS, setupCanvas, fmtPrice } from './util.js';

const H = 175;

export function initLevels(canvas) {
  let data = null;

  function setData(levels) {
    data = levels;
    draw();
  }

  function draw() {
    const { ctx, w } = setupCanvas(canvas, H);
    ctx.clearRect(0, 0, w, H);
    if (!data) return;

    const pts = [data.entry, data.stop, data.take, data.price];
    (data.zones || []).forEach((z) => { pts.push(z.low, z.high); });
    if (data.implied_band) pts.push(data.implied_band.low, data.implied_band.high);
    if (data.gex?.top) data.gex.top.forEach((t) => pts.push(t.price));
    if (data.gex?.zero_flip) pts.push(data.gex.zero_flip);
    if (data.vwap != null) pts.push(data.vwap);
    if (data.day_low != null) pts.push(data.day_low, data.day_high);
    const valid = pts.filter((x) => x != null && isFinite(x));
    let lo = Math.min(...valid), hi = Math.max(...valid);
    const pad = (hi - lo) * 0.05 || 1;
    lo -= pad; hi += pad;

    const padL = 16, padR = 16;
    const plotW = w - padL - padR;
    const X = (p) => padL + ((p - lo) / (hi - lo)) * plotW;
    const axisY = H - 34;

    // implied move ±1σ — затенённый диапазон (единственная «подложка»)
    if (data.implied_band) {
      const x0 = X(Math.max(data.implied_band.low, lo));
      const x1 = X(Math.min(data.implied_band.high, hi));
      ctx.fillStyle = 'rgba(46,125,79,0.07)';
      ctx.fillRect(x0, 18, x1 - x0, axisY - 18);
      ctx.strokeStyle = 'rgba(46,125,79,0.35)';
      ctx.setLineDash([2, 3]);
      ctx.strokeRect(x0, 18, x1 - x0, axisY - 18);
      ctx.setLineDash([]);
      ctx.fillStyle = COLORS.green;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'left';
      ctx.fillText('±1σ СЕССИЯ' + (data.implied_band.demo ? ' ◆' : ''), x0 + 3, 26);
    }

    // FVG-зоны пользователя
    (data.zones || []).forEach((z) => {
      if (z.low == null || z.high == null) return;
      const x0 = X(z.low), x1 = X(z.high);
      ctx.fillStyle = 'rgba(138,135,125,0.16)';
      ctx.fillRect(x0, 36, x1 - x0, axisY - 52);
      ctx.strokeStyle = COLORS.dim;
      ctx.strokeRect(x0, 36, x1 - x0, axisY - 52);
      ctx.fillStyle = COLORS.ink;
      ctx.font = '8px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(`FVG ${z.tf || ''}`, (x0 + x1) / 2, 46);
    });

    // ось
    ctx.strokeStyle = COLORS.ink;
    ctx.beginPath();
    ctx.moveTo(padL, axisY);
    ctx.lineTo(w - padR, axisY);
    ctx.stroke();
    ctx.fillStyle = COLORS.dim;
    ctx.font = '9px "IBM Plex Mono", monospace';
    for (let i = 0; i <= 6; i++) {
      const p = lo + ((hi - lo) * i) / 6;
      const x = X(p);
      ctx.strokeStyle = COLORS.rule;
      ctx.beginPath();
      ctx.moveTo(x, axisY);
      ctx.lineTo(x, axisY + 4);
      ctx.stroke();
      ctx.textAlign = 'center';
      ctx.fillText(fmtPrice(p), x, axisY + 15);
    }

    const risk = Math.abs(data.entry - data.stop) || 1;
    const rOf = (p) => (data.direction === 'long' ? (p - data.entry) / risk
                                                  : (data.entry - p) / risk);

    function marker(price, color, label, top = 8, dash = []) {
      if (price == null || price < lo || price > hi) return;
      const x = X(price);
      ctx.strokeStyle = color;
      ctx.setLineDash(dash);
      ctx.lineWidth = dash.length ? 1 : 1.6;
      ctx.beginPath();
      ctx.moveTo(x, top + 8);
      ctx.lineTo(x, axisY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 1;
      ctx.fillStyle = color;
      ctx.font = '8.5px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(label, x, top + 5);
    }

    // GEX-уровни + флип (эвристика — пунктиром)
    if (data.gex?.top) {
      data.gex.top.forEach((t) =>
        marker(t.price, COLORS.dim, 'GEX' + (data.gex.demo ? '◆' : ''), 52, [4, 4]));
    }
    if (data.gex?.zero_flip) marker(data.gex.zero_flip, '#A87A18', 'FLIP', 52, [2, 3]);
    if (data.vwap != null) marker(data.vwap, '#5B6C9E', 'VWAP', 30, [1, 2]);

    marker(data.entry, COLORS.ink, `ВХОД 0R`, 8);
    marker(data.stop, COLORS.red, `СТОП −1R`, 8);
    marker(data.take, COLORS.green, `ТЕЙК +${rOf(data.take).toFixed(2)}R`, 8);

    // текущая цена — курсор-треугольник
    if (data.price != null && data.price >= lo && data.price <= hi) {
      const x = X(data.price);
      ctx.fillStyle = COLORS.ink;
      ctx.beginPath();
      ctx.moveTo(x - 5, axisY + 20);
      ctx.lineTo(x + 5, axisY + 20);
      ctx.lineTo(x, axisY + 12);
      ctx.closePath();
      ctx.fill();
      ctx.font = '10px "IBM Plex Mono", monospace';
      ctx.textAlign = 'center';
      ctx.fillText(fmtPrice(data.price), x, axisY + 31);
    }
  }

  window.addEventListener('resize', draw);
  return { setData };
}
