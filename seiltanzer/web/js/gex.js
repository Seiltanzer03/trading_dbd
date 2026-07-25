// GEX КЛЮЧЕВЫЕ УРОВНИ — опционная карта ликвидности.
// Показывает только ЗНАЧИМЫЕ страйки (топ-10 по абс. значению NET GEX).
// Это то, что профессионалы называют "options flow wall" или "dealer positioning".
//
// КАК ЧИТАТЬ ДЛЯ CFD ТРЕЙДЕРА:
//   🟢 Зелёный длинный бар (PIN) = дилеры длинная гамма на этом уровне.
//      Рынок ТОРМОЗИТ у этого страйка → хороший уровень для тейка/разворота.
//   🔴 Красный длинный бар (PUSH) = дилеры короткая гамма.
//      Рынок УСКОРЯЕТСЯ при пробое → не ставь тейк здесь, жди за уровнем.
//   🟡 Пунктир = текущая цена.
//   → СТРАТЕГИЯ: ищи сетапы где тейк стоит у крупного PIN-уровня.
import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let data = null;
let statePhase = 0;
let smoothedNetGex = 0;
let smoothedLevels = {}; // для плавной анимации каждого бара

export function initGex() {
    canvas = $('#gex-evol-canvas');
    emptyEl = $('#gex-evol-empty');
    statusEl = $('#gex-evol-status');
    if (!canvas) return;
    requestAnimationFrame(renderLoop);
}

export function updateGex(ridgePayload) {
    if (!ridgePayload || !ridgePayload.available || !ridgePayload.snapshots || ridgePayload.snapshots.length < 1) {
        data = null;
        if (emptyEl) emptyEl.style.display = 'flex';
        if (canvas) canvas.style.display = 'none';
        if (statusEl) statusEl.textContent = 'o GEX НЕДОСТУПЕН';
        return;
    }
    data = {
        snaps: ridgePayload.snapshots,
        scale: ridgePayload.scale || 1.0,
        price: ridgePayload.price
    };
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
    if (statusEl) statusEl.textContent = '● OPTIONS FLOW';
}

function fmtVal(v) {
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(0) + 'K';
    return v.toFixed(0);
}

function renderLoop() {
    requestAnimationFrame(renderLoop);
    if (!data || !canvas || canvas.style.display === 'none') return;
    statePhase += 0.018;

    const snap = data.snaps[data.snaps.length - 1];
    if (!snap || !snap.gex || !snap.gex.strikes || snap.gex.strikes.length === 0) return;

    const strikes = snap.gex.strikes.map(s => s * data.scale);
    const net = snap.gex.net;
    const maxAbsNet = Math.max(...net.map(Math.abs), 1e-9);
    const priceStrike = data.price || 0;

    // Выбираем топ-12 уровней по abs(net), отсортированных по страйку
    const indexed = net.map((v, i) => ({ s: strikes[i], v }));
    const topN = 12;
    const top = [...indexed]
        .sort((a, b) => Math.abs(b.v) - Math.abs(a.v))
        .slice(0, topN)
        .sort((a, b) => b.s - a.s); // по убыванию цены (сверху дорогие страйки)

    const { ctx, w, h } = setupCanvas(canvas, 180);
    ctx.clearRect(0, 0, w, h);

    const padLeft = 66;  // подписи страйков
    const padRight = 54; // инфо-столбик
    const padTop = 22;
    const padBot = 6;
    const barAreaW = w - padLeft - padRight;
    const rowH = Math.max(10, (h - padTop - padBot) / top.length);

    // == Заголовки ==
    ctx.font = 'bold 9px "IBM Plex Mono",monospace';
    ctx.fillStyle = '#8A877D';
    ctx.textAlign = 'center';
    ctx.fillText('OPTIONS FLOW · КЛЮЧЕВЫЕ УРОВНИ (NET GEX)', w / 2, 12);
    
    // Подпись: PIN = тормоз | PUSH = разгон
    ctx.font = '8px "IBM Plex Mono",monospace';
    ctx.fillStyle = 'rgba(46,125,79,0.8)';
    ctx.textAlign = 'left';
    ctx.fillText('▮ PIN = тормоз', padLeft, 21);
    ctx.fillStyle = 'rgba(198,55,60,0.8)';
    ctx.textAlign = 'right';
    ctx.fillText('PUSH = разгон ▮', padLeft + barAreaW, 21);

    // Центральная нулевая линия
    const centerX = padLeft + barAreaW / 2;
    ctx.strokeStyle = 'rgba(216,213,204,0.4)';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(centerX, padTop);
    ctx.lineTo(centerX, h - padBot);
    ctx.stroke();
    ctx.setLineDash([]);

    // == Строки уровней ==
    for (let i = 0; i < top.length; i++) {
        const { s: st, v: gv } = top[i];
        const y = padTop + i * rowH;
        const key = st.toFixed(0);
        
        // Плавная анимация bar width
        const targetNorm = gv / maxAbsNet; // [-1..1]
        if (!smoothedLevels[key]) smoothedLevels[key] = 0;
        smoothedLevels[key] = approach(smoothedLevels[key], targetNorm, 0.04, 3);
        const norm = smoothedLevels[key];
        const barHalf = Math.abs(norm) * (barAreaW / 2);
        
        const isAtm = priceStrike > 0 && Math.abs(st - priceStrike) < (top[0].s - top[top.length - 1].s) / (top.length * 1.5);
        const isAbovePrice = priceStrike > 0 && st > priceStrike;
        
        // Фон строки
        ctx.fillStyle = isAtm
            ? 'rgba(232,98,42,0.07)'
            : (i % 2 === 0 ? 'rgba(216,213,204,0.05)' : 'transparent');
        ctx.fillRect(padLeft, y, barAreaW, rowH - 1);

        // Пульсация только для крупных уровней
        const isPOC = i < 2;
        const breathe = isPOC ? 0.8 + 0.2 * Math.abs(Math.sin(statePhase * (i + 1))) : 1.0;
        const alpha = (0.55 + 0.45 * Math.abs(norm)) * breathe;

        // Бар от центра — зелёный вправо (PIN), красный влево (PUSH)
        if (gv > 0) {
            // PIN: бар вправо от центра
            const grad = ctx.createLinearGradient(centerX, 0, centerX + barHalf, 0);
            grad.addColorStop(0, `rgba(46,125,79,${alpha})`);
            grad.addColorStop(1, `rgba(46,180,79,${alpha * 0.3})`);
            ctx.fillStyle = grad;
            if (isPOC) { ctx.shadowColor = 'rgba(46,125,79,0.6)'; ctx.shadowBlur = 8; }
            ctx.fillRect(centerX, y + 2, barHalf, rowH - 4);
        } else {
            // PUSH: бар влево от центра
            const grad = ctx.createLinearGradient(centerX - barHalf, 0, centerX, 0);
            grad.addColorStop(0, `rgba(198,55,60,${alpha * 0.3})`);
            grad.addColorStop(1, `rgba(198,55,60,${alpha})`);
            ctx.fillStyle = grad;
            if (isPOC) { ctx.shadowColor = 'rgba(198,55,60,0.6)'; ctx.shadowBlur = 8; }
            ctx.fillRect(centerX - barHalf, y + 2, barHalf, rowH - 4);
        }
        ctx.shadowBlur = 0;

        // Метка страйка слева
        ctx.font = isAtm ? 'bold 9px "IBM Plex Mono",monospace' : '8px "IBM Plex Mono",monospace';
        ctx.fillStyle = isAtm ? '#E8622A' : (isAbovePrice ? '#5A87A0' : '#A09D94');
        ctx.textAlign = 'right';
        ctx.fillText(st.toFixed(0), padLeft - 5, y + rowH / 2 + 3);

        // Метка PIN/PUSH и значение справа
        const typeLabel = gv > 0 ? 'PIN' : 'PUSH';
        const sizeLabel = fmtVal(gv);
        ctx.fillStyle = gv > 0 ? 'rgba(46,125,79,0.85)' : 'rgba(198,55,60,0.85)';
        ctx.font = 'bold 8px "IBM Plex Mono",monospace';
        ctx.textAlign = 'left';
        ctx.fillText(typeLabel, padLeft + barAreaW + 5, y + rowH / 2 - 2);
        ctx.font = '7px "IBM Plex Mono",monospace';
        ctx.fillStyle = '#8A877D';
        ctx.fillText(sizeLabel, padLeft + barAreaW + 5, y + rowH / 2 + 7);
    }

    // == Текущая цена (горизонтальная оранжевая линия) ==
    if (priceStrike > 0 && top.length > 1) {
        const minSt = Math.min(...top.map(l => l.s));
        const maxSt = Math.max(...top.map(l => l.s));
        if (maxSt > minSt) {
            // Ищем позицию цены между уровнями
            const priceNorm = (maxSt - priceStrike) / (maxSt - minSt);
            const py = padTop + priceNorm * (top.length * rowH);
            if (py > padTop && py < h - padBot) {
                ctx.strokeStyle = 'rgba(232,98,42,0.9)';
                ctx.lineWidth = 1.5;
                ctx.setLineDash([6, 4]);
                ctx.beginPath(); ctx.moveTo(padLeft, py); ctx.lineTo(padLeft + barAreaW, py); ctx.stroke();
                ctx.setLineDash([]);
                // Пузырёк с ценой
                const priceLabel = priceStrike.toFixed(0);
                const lw = ctx.measureText(priceLabel).width + 10;
                ctx.fillStyle = 'rgba(232,98,42,0.9)';
                ctx.beginPath(); ctx.roundRect(padLeft - lw - 2, py - 8, lw, 14, 3); ctx.fill();
                ctx.fillStyle = '#FFFFFF';
                ctx.font = 'bold 8px "IBM Plex Mono",monospace';
                ctx.textAlign = 'right';
                ctx.fillText(priceLabel, padLeft - 5, py + 3);
            }
        }
    }

    // == NET FLOW сводка (правый блок) ==
    const totalNet = net.reduce((a, b) => a + b, 0);
    smoothedNetGex = approach(smoothedNetGex, totalNet, 0.04, 2);
    const isPin = smoothedNetGex > 0;
    const netGlow = 0.7 + 0.3 * Math.abs(Math.sin(statePhase * 1.5));
    
    const summaryX = w - padRight + 5;
    const summaryW = padRight - 8;
    const summaryH = h - padTop - padBot - 10;
    const summaryY = padTop + 5;
    
    // Фон блока
    ctx.fillStyle = 'rgba(216,213,204,0.08)';
    ctx.beginPath(); ctx.roundRect(summaryX, summaryY, summaryW, summaryH, 4); ctx.fill();
    
    // Заполненная часть
    const netMag = Math.abs(smoothedNetGex) / (maxAbsNet * Math.min(top.length, 10));
    const fillH = Math.min(summaryH * 0.9, summaryH * netMag);
    const fillY = summaryY + summaryH / 2 - fillH / 2;
    ctx.fillStyle = isPin ? `rgba(46,125,79,${netGlow})` : `rgba(198,55,60,${netGlow})`;
    ctx.shadowColor = isPin ? 'rgba(46,125,79,0.7)' : 'rgba(198,55,60,0.7)';
    ctx.shadowBlur = 14;
    ctx.beginPath(); ctx.roundRect(summaryX + 3, fillY, summaryW - 6, fillH, 3); ctx.fill();
    ctx.shadowBlur = 0;
    
    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 9px "IBM Plex Mono",monospace';
    ctx.textAlign = 'center';
    ctx.fillText(isPin ? 'PIN' : 'PUSH', summaryX + summaryW / 2, summaryY + summaryH / 2 - 5);
    ctx.font = '7px "IBM Plex Mono",monospace';
    ctx.fillStyle = '#8A877D';
    ctx.fillText(isPin ? 'пиннинг' : 'разгон', summaryX + summaryW / 2, summaryY + summaryH / 2 + 7);
    ctx.fillText(fmtVal(smoothedNetGex), summaryX + summaryW / 2, summaryY + summaryH / 2 + 17);
}
