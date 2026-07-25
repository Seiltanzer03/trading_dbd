// CROSS-ASSET CORRELATION — Матрица связей Spot+Vol активов.
// Показывает не только корреляцию между ценами, но и аномалии режима.
// СИГНАЛЫ ДЛЯ ТРЕЙДЕРА:
//   VIX↑ + SPX↑ = слом риска (обычно обратные) → выход из лонгов
//   VXN↓ + NAS↑ = здоровый рост → тренд можно держать
//   OVX↑↑ + GVZ↑↑ = cross-asset стресс → любые направленные позиции рискованы
import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl, signalEl;
let payload = null;
let state = { matrix: [], phase: 0 };

// Пары с "нормальной" корреляцией (для сигнализации аномалии)
// [i, j, normalSign] — если sign() не совпадает → аномалия
const NORMAL_SIGNS = [
    [0, 1, -1], // NAS↔VXN обычно отрицательная
    [2, 3, -1], // SP500↔VIX обычно отрицательная
    [4, 5, -1], // GOLD↔GVZ — менее предсказуемо
    [6, 7, -1], // OIL↔OVX обычно отрицательная
    [0, 2, +1], // NAS↔SP500 обычно положительная
];

export function initCorrelation() {
    canvas = $('#corr-canvas');
    emptyEl = $('#corr-empty');
    statusEl = $('#corr-status');
    signalEl = $('#corr-signal'); // опциональный элемент для сигнала
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

    const n = p.matrix.length;
    if (state.matrix.length !== n) {
        state.matrix = p.matrix.map(row => [...row]);
    }
    
    // Обновляем сигнал
    const anomalies = detectAnomalies(p);
    if (statusEl) {
        if (anomalies.length > 0) {
            statusEl.textContent = `⚠ АНОМАЛИЯ: ${anomalies[0]}`;
            statusEl.className = 'badge warn';
        } else {
            statusEl.textContent = '● НОРМ. РЕЖИМ';
            statusEl.className = 'badge live';
        }
    }
}

function detectAnomalies(p) {
    if (!p || !p.matrix || !p.assets) return [];
    const anomalies = [];
    for (const [i, j, ns] of NORMAL_SIGNS) {
        if (i >= p.matrix.length || j >= p.matrix.length) continue;
        const v = p.matrix[i][j];
        if (Math.abs(v) > 0.25) {
            const actualSign = v > 0 ? +1 : -1;
            if (actualSign !== ns && Math.abs(v) > 0.35) {
                anomalies.push(`${p.assets[i]}↔${p.assets[j]}: ${v > 0 ? '+' : ''}${v.toFixed(2)}`);
            }
        }
    }
    return anomalies;
}

function getSignalText(p) {
    if (!p || !p.matrix || !p.assets || p.matrix.length < 4) return null;
    const mat = p.matrix;
    // VIX + SP500
    const vixSp = mat[3][2]; // VIX row vs SP500 col (indices 3,2)
    // VXN + NAS
    const vxnNas = mat[1][0];
    // General cross-vol stress
    const vxnVix = mat[1][3];
    const ovxGvz = mat.length > 7 ? mat[7][5] : 0;
    
    if (vixSp > 0.3) return { text: '⚠ VIX+SP500 сонаправлены → слом режима!', color: '#C6373C', bg: 'rgba(198,55,60,0.1)' };
    if (vxnVix > 0.6) return { text: '⚠ VXN+VIX всплеск → кросс-актив стресс', color: '#C6373C', bg: 'rgba(198,55,60,0.1)' };
    if (ovxGvz > 0.5) return { text: '⚡ OVX+GVZ ↑↑ → боятся везде, рынок нестабилен', color: '#E8622A', bg: 'rgba(232,98,42,0.1)' };
    if (vxnNas < -0.5) return { text: '✅ VXN↓ + NAS↑ = здоровый тренд, держи лонги', color: '#2E7D4F', bg: 'rgba(46,125,79,0.1)' };
    return { text: '— Корреляции в норме', color: '#8A877D', bg: 'transparent' };
}

function renderLoop() {
    requestAnimationFrame(renderLoop);
    if (!payload || !canvas || canvas.style.display === 'none') return;

    state.phase += 0.015;
    const { ctx, w, h } = setupCanvas(canvas, 270);
    ctx.clearRect(0, 0, w, h);

    const n = payload.matrix.length;
    const assets = payload.assets;
    const signal = getSignalText(payload);
    const anomalies = detectAnomalies(payload);

    // == Сигнальная строка вверху ==
    if (signal) {
        const sigH = 22;
        ctx.fillStyle = signal.bg;
        ctx.beginPath(); ctx.roundRect(4, 4, w - 8, sigH, 4); ctx.fill();
        ctx.fillStyle = signal.color;
        ctx.font = 'bold 9px "IBM Plex Mono",monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(signal.text, w / 2, 4 + sigH / 2);
    }

    // == Матрица ==
    const sigH = 30;
    const padX = 44;
    const padY = sigH;
    const legH = 16;
    const gridH = h - padY - legH - 4;
    const cellW = (w - padX) / n;
    const cellH = gridH / n;

    ctx.font = '8px "IBM Plex Mono", monospace';
    ctx.textBaseline = 'middle';

    for (let i = 0; i < n; i++) {
        for (let j = 0; j < n; j++) {
            state.matrix[i][j] = approach(state.matrix[i][j], payload.matrix[i][j], 0.04, 3);
            const val = state.matrix[i][j];
            const x = padX + j * cellW;
            const y = padY + i * cellH;

            // Проверяем аномальность этой пары
            let isAnomalous = false;
            for (const [ai, aj, ns] of NORMAL_SIGNS) {
                if ((ai === i && aj === j) || (ai === j && aj === i)) {
                    if (Math.abs(val) > 0.35 && (val > 0 ? 1 : -1) !== ns) {
                        isAnomalous = true;
                    }
                }
            }

            // Диагональ
            if (i === j) {
                const pulse = 0.75 + 0.25 * Math.abs(Math.sin(state.phase * 1.2 + i * 0.7));
                ctx.fillStyle = `rgba(232,98,42,${pulse})`;
                ctx.shadowColor = 'rgba(232,98,42,0.6)';
                ctx.shadowBlur = 8 * Math.abs(Math.sin(state.phase + i));
                ctx.beginPath(); ctx.roundRect(x + 2, y + 2, cellW - 4, cellH - 4, 4); ctx.fill();
                ctx.shadowBlur = 0;
                ctx.fillStyle = 'rgba(255,255,255,0.9)';
                ctx.textAlign = 'center';
                ctx.fillText(assets[i], x + cellW / 2, y + cellH / 2);
                continue;
            }

            // Цвет: синий (-1) → почти чёрный (0) → оранжевый (+1)
            let r, g, b, a = 0.82;
            if (val > 0) {
                r = Math.floor(val * 232); g = Math.floor(val * 98); b = Math.floor(val * 42);
            } else {
                const av = Math.abs(val);
                r = Math.floor(av * 70); g = Math.floor(av * 130); b = Math.floor(av * 200);
            }

            ctx.fillStyle = `rgba(${r},${g},${b},${a})`;
            // Аномальные пары светятся
            if (isAnomalous) {
                const gl = 0.5 + 0.5 * Math.abs(Math.sin(state.phase * 2));
                ctx.shadowColor = val > 0 ? `rgba(232,98,42,${gl})` : `rgba(70,130,200,${gl})`;
                ctx.shadowBlur = 10;
            } else {
                ctx.shadowBlur = 0;
            }
            ctx.beginPath(); ctx.roundRect(x + 2, y + 2, cellW - 4, cellH - 4, 3); ctx.fill();
            ctx.shadowBlur = 0;

            // Значение
            if (Math.abs(val) > 0.25) {
                ctx.fillStyle = 'rgba(255,255,255,0.85)';
                ctx.textAlign = 'center';
                ctx.font = Math.abs(val) > 0.55 ? 'bold 8px "IBM Plex Mono",monospace' : '7px "IBM Plex Mono",monospace';
                ctx.fillText(val.toFixed(2), x + cellW / 2, y + cellH / 2);
                ctx.font = '8px "IBM Plex Mono",monospace';
            }

            // Аномальный маркер ⚠
            if (isAnomalous) {
                ctx.fillStyle = '#FFD700';
                ctx.font = '9px monospace';
                ctx.textAlign = 'right';
                ctx.fillText('⚠', x + cellW - 2, y + 10);
                ctx.font = '8px "IBM Plex Mono",monospace';
            }
        }

        // Подписи Y
        ctx.fillStyle = '#7A7870';
        ctx.textAlign = 'right';
        ctx.font = '8px "IBM Plex Mono",monospace';
        ctx.fillText(assets[i], padX - 5, padY + i * cellH + cellH / 2);

        // Подписи X (сверху наклонно)
        ctx.save();
        ctx.translate(padX + i * cellW + cellW / 2, padY - 4);
        ctx.rotate(-Math.PI / 5);
        ctx.textAlign = 'left';
        ctx.fillText(assets[i], 0, 0);
        ctx.restore();
    }

    // == Легенда внизу ==
    const ly = h - legH;
    const lgW = 100, lgH = 8, lgX = padX;
    const lg = ctx.createLinearGradient(lgX, 0, lgX + lgW, 0);
    lg.addColorStop(0, 'rgba(70,130,200,0.85)');
    lg.addColorStop(0.5, 'rgba(30,30,30,0.3)');
    lg.addColorStop(1, 'rgba(232,98,42,0.85)');
    ctx.fillStyle = lg;
    ctx.beginPath(); ctx.roundRect(lgX, ly + 4, lgW, lgH, 3); ctx.fill();
    ctx.fillStyle = '#8A877D'; ctx.font = '7px "IBM Plex Mono",monospace';
    ctx.textAlign = 'left'; ctx.fillText('-1 обратная', lgX, ly + 2);
    ctx.textAlign = 'center'; ctx.fillText('0', lgX + lgW / 2, ly + 2);
    ctx.textAlign = 'right'; ctx.fillText('+1 прямая', lgX + lgW, ly + 2);
    ctx.textAlign = 'left'; ctx.fillStyle = '#FFD700';
    ctx.fillText('⚠ = аномальная корреляция (сигнал)', lgX + lgW + 8, ly + legH / 2);
}
