import { $, fmtPct, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let payload = null;

// Стейт анимации (spring / approach)
let state = {
    iv: 0,
    rv: 0,
    vrp: 0,
    vrp_pct: 0,
    phase: 0 // для течения градиента
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
        if (statusEl) statusEl.textContent = '○ НЕТ ДАННЫХ';
        return;
    }
    payload = p;
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
    
    // Инициализация стейта, если первый раз
    if (state.iv === 0 && state.rv === 0) {
        state.iv = p.iv;
        state.rv = p.rv;
        state.vrp = p.vrp;
        state.vrp_pct = p.vrp_pct;
    }
    
    if (statusEl) {
        let label = `(RV: ${fmtPct(p.rv)} → IV: ${fmtPct(p.iv)})`;
        if (p.regime === 'перегрев') label += ' 🔥 ПЕРЕГРЕВ';
        else if (p.regime === 'недооценка') label += ' ❄️ НЕДООЦЕНКА';
        else label += ' ⚖️ НОРМА';
        statusEl.textContent = label;
    }
}

function renderLoop(time) {
    requestAnimationFrame(renderLoop);
    if (!payload || !canvas || canvas.style.display === 'none') return;

    // Плавная интерполяция к целевым значениям
    state.iv = approach(state.iv, payload.iv, 0.016, 4);
    state.rv = approach(state.rv, payload.rv, 0.016, 4);
    state.vrp_pct = approach(state.vrp_pct, payload.vrp_pct, 0.016, 4);
    state.phase += 0.02; // анимация "течения"

    const { ctx, w, h } = setupCanvas(canvas, 36);
    ctx.clearRect(0, 0, w, h);

    // Центр термометра (0 VRP)
    const midX = w / 2;
    // Диапазон: например, -30% до +30%
    const maxRange = 0.3;
    let clampedPct = Math.max(-maxRange, Math.min(maxRange, state.vrp_pct));
    // Нормализуем в [-1, 1]
    let valNorm = clampedPct / maxRange;

    // Рисуем трек (фон)
    const trackH = 8;
    const trackY = h / 2 - trackH / 2;
    ctx.fillStyle = 'rgba(216, 213, 204, 0.4)'; // rule-цвет с прозрачностью
    ctx.beginPath();
    ctx.roundRect(0, trackY, w, trackH, 4);
    ctx.fill();

    // Рисуем заполненный градиент от центра
    const barW = Math.abs(valNorm) * (w / 2);
    const startX = valNorm > 0 ? midX : midX - barW;

    if (barW > 1) {
        let grad = ctx.createLinearGradient(startX, 0, startX + barW, 0);
        if (valNorm > 0) {
            // Перегрев: тёплые цвета (желтый -> оранжевый -> красный), дышит
            let rPulse = Math.sin(state.phase) * 20;
            grad.addColorStop(0, `rgba(255, 179, 71, 0.8)`);
            grad.addColorStop(1, `rgba(${220 + rPulse}, 80, 60, 0.9)`);
        } else {
            // Недооценка: холодные (бирюзовый -> синий)
            let bPulse = Math.sin(state.phase) * 20;
            grad.addColorStop(0, `rgba(0, 206, 209, 0.8)`);
            grad.addColorStop(1, `rgba(60, 100, ${220 + bPulse}, 0.9)`);
        }
        ctx.fillStyle = grad;
        ctx.shadowColor = valNorm > 0 ? 'rgba(220, 80, 60, 0.5)' : 'rgba(0, 206, 209, 0.5)';
        ctx.shadowBlur = 8;
        ctx.beginPath();
        ctx.roundRect(startX, trackY, barW, trackH, 4);
        ctx.fill();
        ctx.shadowBlur = 0; // reset
    }

    // Маркер нуля (центр)
    ctx.fillStyle = '#14140F';
    ctx.fillRect(midX - 1, trackY - 2, 2, trackH + 4);

    // Маркер текущего значения
    const markerX = midX + valNorm * (w / 2);
    ctx.beginPath();
    ctx.arc(markerX, h / 2, 6 + Math.sin(state.phase*2)*1, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();
    ctx.strokeStyle = valNorm > 0 ? '#C6373C' : '#2E7D4F';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Afterglow / trail вокруг маркера
    ctx.beginPath();
    ctx.arc(markerX, h / 2, 12, 0, Math.PI * 2);
    ctx.fillStyle = valNorm > 0 ? 'rgba(198, 55, 60, 0.2)' : 'rgba(46, 125, 79, 0.2)';
    ctx.fill();
}
