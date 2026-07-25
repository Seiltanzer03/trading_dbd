// VRP Термометр — Volatility Risk Premium (IV - RV).
// Положительный VRP = рынок переплачивает за волу (опционы дорогие).
// Отрицательный VRP = рынок недооценивает риск (опционы дёшевы).
// Шкала ДИНАМИЧЕСКАЯ — масштаб меняется под текущую историческую дисперсию.
import { $, fmtPct, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let payload = null;

let state = {
    iv: 0, rv: 0, vrp_pct: 0,
    phase: 0,
    history: [],
    histMin: -0.3,
    histMax: 0.3,
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
        state.iv = p.iv; state.rv = p.rv; state.vrp_pct = p.vrp_pct;
    }

    // История для динамической шкалы
    state.history.push(p.vrp_pct);
    if (state.history.length > 80) state.history.shift();
    if (state.history.length > 5) {
        const mg = 0.05;
        let mn = Math.min(...state.history) - mg;
        let mx = Math.max(...state.history) + mg;
        if (mx - mn < 0.4) { const mid = (mx + mn) / 2; mn = mid - 0.2; mx = mid + 0.2; }
        state.histMin = mn;
        state.histMax = mx;
    }

    if (statusEl) {
        const pp = ((p.iv - p.rv) * 100).toFixed(1);
        const sign = p.vrp_pct >= 0 ? '+' : '';
        let label = `VRP: ${sign}${pp}pp  (RV: ${fmtPct(p.rv)} → IV: ${fmtPct(p.iv)})`;
        if (p.regime === 'перегрев') label += '  🔥 IV > RV — рынок ЖДЁТ движение. Следи за направлением скью.';
        else if (p.regime === 'недооценка') label += '  ❄️ IV < RV — движение уже ИДЁТ, рынок не верит. Тренд может продолжиться.';
        else label += '  ⚖️ IV≈RV — рынок в балансе ожиданий';
        statusEl.textContent = label;
    }
}

function renderLoop() {
    requestAnimationFrame(renderLoop);
    if (!payload || !canvas || canvas.style.display === 'none') return;

    state.iv = approach(state.iv, payload.iv, 0.016, 4);
    state.rv = approach(state.rv, payload.rv, 0.016, 4);
    state.vrp_pct = approach(state.vrp_pct, payload.vrp_pct, 0.016, 4);
    state.phase += 0.025;

    const { ctx, w, h } = setupCanvas(canvas, 54);
    ctx.clearRect(0, 0, w, h);

    const padX = 38, trackH = 11;
    const trackW = w - padX * 2;
    const trackY = 18;

    const rMin = state.histMin, rMax = state.histMax, rTot = rMax - rMin;
    if (rTot <= 0) return;

    const zeroNorm = (-rMin) / rTot;
    const zeroX = padX + zeroNorm * trackW;
    const curNorm = Math.max(0, Math.min(1, (state.vrp_pct - rMin) / rTot));
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
    ctx.textAlign = 'left'; ctx.fillText((rMin * 100).toFixed(0) + '%', 1, trackY + trackH / 2 + 3);
    ctx.fillStyle = 'rgba(198,55,60,0.8)'; ctx.textAlign = 'right';
    ctx.fillText('+' + (rMax * 100).toFixed(0) + '%', w - 1, trackY + trackH / 2 + 3);
    ctx.fillStyle = 'rgba(70,130,180,0.55)'; ctx.font = '7px "IBM Plex Mono",monospace';
    ctx.textAlign = 'left'; ctx.fillText('ДЁШЕВО', padX + 3, trackY + trackH / 2 + 3);
    ctx.fillStyle = 'rgba(198,55,60,0.55)'; ctx.textAlign = 'right';
    ctx.fillText('ДОРОГО', padX + trackW - 3, trackY + trackH / 2 + 3);

    // Пульсирующий маркер
    const glow = Math.sin(state.phase * 2) * 2;
    ctx.beginPath(); ctx.arc(markerX, trackY + trackH / 2, 7 + glow, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.shadowColor = state.vrp_pct > 0 ? 'rgba(198,55,60,0.8)' : 'rgba(70,130,180,0.8)';
    ctx.shadowBlur = 10; ctx.fill();
    ctx.strokeStyle = state.vrp_pct > 0 ? '#C6373C' : '#2E7D4F';
    ctx.lineWidth = 2.5; ctx.stroke(); ctx.shadowBlur = 0;

    // Подпись над маркером
    const vrpLabel = (state.vrp_pct * 100).toFixed(1) + '%';
    ctx.fillStyle = state.vrp_pct > 0 ? '#C6373C' : '#2E7D4F';
    ctx.textAlign = 'center'; ctx.font = 'bold 8px "IBM Plex Mono",monospace';
    ctx.fillText(vrpLabel, markerX, trackY - 4);
}
