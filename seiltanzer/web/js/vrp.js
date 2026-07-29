// VRP Термометр — Volatility Risk Premium (IV - RV).
// Положительный VRP = рынок переплачивает за волу (опционы дорогие).
// Отрицательный VRP = рынок недооценивает риск (опционы дёшевы).
// Шкала в процентных пунктах IV−RV; отношение IV/RV показывается отдельно.
import { $, fmtPct, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let payload = null;

let state = {
    iv: 0, rv: 0, vrp: 0,
    history: [],
    histMin: -0.1,
    histMax: 0.1,
    lastSample: null,
};

export function initVrp() {
    canvas = $('#vrp-canvas');
    emptyEl = $('#vrp-empty');
    statusEl = $('#vrp-status');
    if (!canvas) return;
    requestAnimationFrame(renderLoop);
}

export function updateVrp(p) {
    if (!p || !p.available) {
        payload = null;
        if (emptyEl) emptyEl.style.display = 'flex';
        if (canvas) canvas.style.display = 'none';
        if (statusEl) statusEl.textContent = 'o НЕТ ДАННЫХ';
        return;
    }
    payload = p;
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';

    if (state.iv === 0 && state.rv === 0) {
        state.iv = p.iv; state.rv = p.rv; state.vrp = p.vrp ?? (p.iv - p.rv);
    }

    // Одна точка на новый option snapshot, а не 80 копий одного значения,
    // добавленных websocket-тиками цены.
    const sampleId = p.snapshot_ts ?? `${p.iv}|${p.rv}`;
    if (sampleId !== state.lastSample) {
        state.lastSample = sampleId;
        state.history.push(p.vrp ?? (p.iv - p.rv));
        if (state.history.length > 80) state.history.shift();
    }
    if (state.history.length) {
        const maxAbs = Math.max(0.10, ...state.history.map((v) => Math.abs(v))) * 1.18;
        state.histMin = -maxAbs;
        state.histMax = maxAbs;
    }

    if (statusEl) {
        const spread = p.vrp ?? (p.iv - p.rv);
        const pp = (spread * 100).toFixed(1);
        const sign = spread >= 0 ? '+' : '';
        const ratio = p.iv_rv_ratio ?? (p.rv > 0 ? p.iv / p.rv : null);
        let label = `VRP ${sign}${pp} п.п. · IV/RV ${ratio == null ? '—' : `${ratio.toFixed(2)}×`} · `
          + `IV ${fmtPct(p.iv)} vs RV ${fmtPct(p.rv)}`;
        if (p.regime === 'iv_premium') label += ' · опционы закладывают больше движения; НАПРАВЛЕНИЕ НЕ ОПРЕДЕЛЯЕТ';
        else if (p.regime === 'iv_discount') label += ' · realized выше implied; риск продолжения расширения';
        else label += ' · IV и RV близки';
        statusEl.textContent = label;
        statusEl.dataset.tip = 'VRP показан в процентных пунктах (IV − RV). Отношение IV/RV вынесено отдельно, поэтому +34 п.п. больше не выглядит как ошибочные 151%.';
    }
}

function renderLoop() {
    requestAnimationFrame(renderLoop);
    if (!payload || !canvas || canvas.style.display === 'none') return;

    state.iv = approach(state.iv, payload.iv, 0.016, 4);
    state.rv = approach(state.rv, payload.rv, 0.016, 4);
    state.vrp = approach(state.vrp, payload.vrp ?? (payload.iv - payload.rv), 0.016, 4);

    const { ctx, w, h } = setupCanvas(canvas, 54);
    ctx.clearRect(0, 0, w, h);

    const padX = 38, trackH = 11;
    const trackW = w - padX * 2;
    const trackY = 18;

    const rMin = state.histMin, rMax = state.histMax, rTot = rMax - rMin;
    if (rTot <= 0) return;

    const zeroNorm = (-rMin) / rTot;
    const zeroX = padX + zeroNorm * trackW;
    const curNorm = Math.max(0, Math.min(1, (state.vrp - rMin) / rTot));
    const markerX = padX + curNorm * trackW;

    // Зонный фон
    const lgL = ctx.createLinearGradient(padX, 0, zeroX, 0);
    lgL.addColorStop(0, 'rgba(70,130,180,0.45)'); lgL.addColorStop(1, 'rgba(70,130,180,0.05)');
    ctx.fillStyle = lgL;
    ctx.beginPath(); ctx.roundRect(padX, trackY, Math.max(0, zeroX - padX), trackH, [4,0,0,4]); ctx.fill();

    const lgR = ctx.createLinearGradient(zeroX, 0, padX + trackW, 0);
    lgR.addColorStop(0, 'rgba(198,55,60,0.05)'); lgR.addColorStop(1, 'rgba(198,55,60,0.45)');
    ctx.fillStyle = lgR;
    ctx.beginPath(); ctx.roundRect(zeroX, trackY, Math.max(0, padX + trackW - zeroX), trackH, [0,4,4,0]); ctx.fill();

    ctx.strokeStyle = 'rgba(216,213,204,0.5)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.roundRect(padX, trackY, trackW, trackH, 4); ctx.stroke();

    // Мини-гистограмма истории внизу
    const histY = trackY + trackH + 3, histH = 8;
    const bw = trackW / Math.max(state.history.length, 1);
    for (let i = 0; i < state.history.length; i++) {
        const v = state.history[i];
        const a = 0.15 + 0.55 * (i / state.history.length);
        const hb = Math.min(histH, Math.abs(v) / Math.max(Math.abs(rMin), rMax, 0.01) * histH);
        ctx.fillStyle = v >= 0 ? `rgba(198,55,60,${a})` : `rgba(70,130,180,${a})`;
        ctx.fillRect(padX + i * bw, histY + histH - hb, Math.max(bw - 1, 1), hb);
    }

    // Маркер нуля
    ctx.fillStyle = 'rgba(20,20,15,0.6)';
    ctx.fillRect(zeroX - 1, trackY - 3, 2, trackH + 6);
    ctx.fillStyle = '#8A877D'; ctx.font = '7px "IBM Plex Mono",monospace'; ctx.textAlign = 'center';
    ctx.fillText('0', zeroX, trackY - 5);

    // Метки крайних значений (динамические)
    ctx.fillStyle = 'rgba(70,130,180,0.8)'; ctx.font = '8px "IBM Plex Mono",monospace';
    ctx.textAlign = 'left'; ctx.fillText((rMin * 100).toFixed(0) + 'п.п.', 1, trackY + trackH / 2 + 3);
    ctx.fillStyle = 'rgba(198,55,60,0.8)'; ctx.textAlign = 'right';
    ctx.fillText('+' + (rMax * 100).toFixed(0) + 'п.п.', w - 1, trackY + trackH / 2 + 3);
    ctx.fillStyle = 'rgba(70,130,180,0.55)'; ctx.font = '7px "IBM Plex Mono",monospace';
    ctx.textAlign = 'left'; ctx.fillText('ДЁШЕВО', padX + 3, trackY + trackH / 2 + 3);
    ctx.fillStyle = 'rgba(198,55,60,0.55)'; ctx.textAlign = 'right';
    ctx.fillText('ДОРОГО', padX + trackW - 3, trackY + trackH / 2 + 3);

    // Пульсирующий маркер
    ctx.beginPath(); ctx.arc(markerX, trackY + trackH / 2, 7, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.shadowColor = state.vrp > 0 ? 'rgba(198,55,60,0.8)' : 'rgba(70,130,180,0.8)';
    ctx.shadowBlur = 10; ctx.fill();
    ctx.strokeStyle = state.vrp > 0 ? '#C6373C' : '#2E7D4F';
    ctx.lineWidth = 2.5; ctx.stroke(); ctx.shadowBlur = 0;

    // Подпись над маркером
    const vrpLabel = `${state.vrp >= 0 ? '+' : ''}${(state.vrp * 100).toFixed(1)}п.п.`;
    ctx.fillStyle = state.vrp > 0 ? '#C6373C' : '#2E7D4F';
    ctx.textAlign = 'center'; ctx.font = 'bold 8px "IBM Plex Mono",monospace';
    ctx.fillText(vrpLabel, markerX, trackY - 4);
}
