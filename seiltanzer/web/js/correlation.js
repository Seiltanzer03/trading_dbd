import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let payload = null;
let state = {
    matrix: [], // текущие сглаженные значения корреляции
    phase: 0
};

export function initCorrelation() {
    canvas = $('#corr-canvas');
    emptyEl = $('#corr-empty');
    statusEl = $('#corr-status');
    if (!canvas) return;
    requestAnimationFrame(renderLoop);
}

export function updateCorrelation(p) {
    if (!p || !p.matrix || p.matrix.length === 0) {
        payload = null;
        if (emptyEl) emptyEl.style.display = 'flex';
        if (canvas) canvas.style.display = 'none';
        if (statusEl) statusEl.textContent = '○ НЕТ ДАННЫХ';
        return;
    }
    payload = p;
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
    if (statusEl) statusEl.textContent = '● LIVE (30D)';

    const n = p.matrix.length;
    // Инициализация матрицы состояний (если её нет или размер изменился)
    if (state.matrix.length !== n) {
        state.matrix = [];
        for (let i = 0; i < n; i++) {
            let row = [];
            for (let j = 0; j < n; j++) {
                row.push(p.matrix[i][j]);
            }
            state.matrix.push(row);
        }
    }
}

function renderLoop(time) {
    requestAnimationFrame(renderLoop);
    if (!payload || !canvas || canvas.style.display === 'none') return;

    state.phase += 0.02; // анимация пульсации
    
    const { ctx, w, h } = setupCanvas(canvas, 250);
    ctx.clearRect(0, 0, w, h);

    const n = payload.matrix.length;
    const assets = payload.assets;
    
    // Вычисляем размеры сетки (оставляем место слева и сверху для подписей)
    const padX = 40;
    const padY = 30;
    const cellW = (w - padX) / n;
    const cellH = (h - padY) / n;

    ctx.font = '9px "IBM Plex Mono", monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            // Lerp значения (плавное перетекание)
            state.matrix[i][j] = approach(state.matrix[i][j], payload.matrix[i][j], 0.05, 3);
            const val = state.matrix[i][j];

            const x = padX + j * cellW;
            const y = padY + i * cellH;

            // Вычисляем цвет
            // Синий (-1) -> Чёрный (0) -> Оранжевый (+1)
            let r=0, g=0, b=0, a=0.8;
            if (val > 0) {
                // Оранжевый 232, 98, 42
                r = Math.floor(val * 232);
                g = Math.floor(val * 98);
                b = Math.floor(val * 42);
                // Диагональ (val = 1) пульсирует
                if (i === j) {
                    a = 0.8 + 0.2 * Math.sin(state.phase + i);
                    ctx.shadowColor = 'rgba(232,98,42,0.8)';
                    ctx.shadowBlur = 10 * Math.sin(state.phase + i);
                } else {
                    ctx.shadowBlur = 0;
                }
            } else {
                // Синий 70, 130, 180 (SteelBlue)
                let absVal = Math.abs(val);
                r = Math.floor(absVal * 70);
                g = Math.floor(absVal * 130);
                b = Math.floor(absVal * 180);
                ctx.shadowBlur = 0;
            }

            // Рендер скругленного квадратика
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
            const m = 1; // margin
            ctx.beginPath();
            ctx.roundRect(x + m, y + m, cellW - m*2, cellH - m*2, 3);
            ctx.fill();
            ctx.shadowBlur = 0;

            // Текст внутри квадратика (если cell достаточно большой)
            if (i !== j && Math.abs(val) > 0.3) {
                ctx.fillStyle = 'rgba(255,255,255,0.7)';
                ctx.fillText(val.toFixed(2), x + cellW/2, y + cellH/2);
            }
        }

        // Подписи по Y (слева)
        ctx.fillStyle = '#A09D94';
        ctx.textAlign = 'right';
        ctx.fillText(assets[i], padX - 6, padY + i * cellH + cellH/2);
        
        // Подписи по X (сверху)
        ctx.save();
        ctx.translate(padX + i * cellW + cellW/2, padY - 6);
        ctx.rotate(-Math.PI / 4);
        ctx.textAlign = 'left';
        ctx.fillText(assets[i], 0, 0);
        ctx.restore();
    }
}
