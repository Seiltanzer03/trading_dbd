// GEX КЛЮЧЕВЫЕ УРОВНИ — контекст концентрации OI × модельной gamma.
// Бесплатная цепочка не раскрывает знак реальной позиции дилеров, поэтому слой
// не считается наблюдаемым options flow и не получает веса в verdict.

import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let data = null;
let statePhase = 0;
let smoothedNetGex = 0;
let smoothedLevels = {}; // для плавной анимации каждого бара
let liveData = { price: 0, proxyPrice: 0, trade: null };

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
    const latest = ridgePayload.snapshots[ridgePayload.snapshots.length - 1];
    if (!latest?.gex?.available || !latest.gex.strikes?.length) {
        data = null;
        if (emptyEl) {
            emptyEl.style.display = 'flex';
            emptyEl.textContent = '○ GEX КОНТЕКСТ ОТКЛЮЧЁН ДЛЯ ЭТОГО PROXY';
        }
        if (canvas) canvas.style.display = 'none';
        if (statusEl) statusEl.textContent = '○ GEX CONTEXT ONLY';
        return;
    }
    data = {
        snaps: ridgePayload.snapshots,
        scale: ridgePayload.scale || 1.0,
        price: ridgePayload.price,
        proxyPrice: ridgePayload.proxy_spot_current,
        transform: ridgePayload.proxy_transform || 'direct',
    };
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
    if (statusEl) statusEl.textContent = '◐ OI-GEX HEURISTIC';
}

export function updateLiveGex(live) {
    if (live.price !== undefined) liveData.price = live.price;
    if (live.proxyPrice !== undefined) liveData.proxyPrice = live.proxyPrice;
    if (live.trade !== undefined) liveData.trade = live.trade;
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

    const instrumentFactor = (liveData.price && data.price)
        ? liveData.price / data.price : 1.0;
    const proxyFactor = (liveData.proxyPrice && data.proxyPrice)
        ? liveData.proxyPrice / data.proxyPrice : 1.0;
    const liveMap = data.transform === 'inverse'
        ? instrumentFactor * proxyFactor : instrumentFactor / proxyFactor;
    const strikes = snap.gex.strikes.map(s => s * data.scale * liveMap);
    const net = snap.gex.net;
    const maxAbsNet = Math.max(...net.map(Math.abs), 1e-9);
    const priceStrike = liveData.price || data.price || 0;

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

    // Функция для маппинга любой цены на Y координату среди дискретных рядов
    function priceToY(p) {
        if (p >= top[0].s) {
            const diff = top[0].s - top[1].s;
            return padTop - (diff ? (p - top[0].s)/diff * rowH : 0);
        }
        if (p <= top[top.length - 1].s) {
            const diff = top[top.length - 2].s - top[top.length - 1].s;
            const baseY = padTop + (top.length - 1) * rowH;
            return baseY + (diff ? (top[top.length - 1].s - p)/diff * rowH : 0);
        }
        for (let i = 0; i < top.length - 1; i++) {
            if (p <= top[i].s && p >= top[i+1].s) {
                const range = top[i].s - top[i+1].s;
                const frac = range ? (top[i].s - p) / range : 0;
                return padTop + (i + frac) * rowH + rowH/2; // rowH/2 to align with center of row
            }
        }
        return padTop;
    }

    // == Заголовки ==
    ctx.font = 'bold 9px "IBM Plex Mono",monospace';
    ctx.fillStyle = '#8A877D';
    ctx.textAlign = 'center';
    ctx.fillText('OI × GAMMA · ЭВРИСТИЧЕСКИЕ УРОВНИ', w / 2, 12);
    
    // PIN/PUSH — только названия условных сценариев принятого знака позиции.
    ctx.font = '8px "IBM Plex Mono",monospace';
    ctx.fillStyle = 'rgba(46,125,79,0.8)';
    ctx.textAlign = 'left';
    ctx.fillText('▮ усл. PIN', padLeft, 21);
    ctx.fillStyle = 'rgba(198,55,60,0.8)';
    ctx.textAlign = 'right';
    ctx.fillText('усл. PUSH ▮', padLeft + barAreaW, 21);

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

    function drawLine(yPos, color, text, dash) {
        if (yPos > padTop && yPos < h - padBot) {
            ctx.strokeStyle = color;
            ctx.lineWidth = 1.5;
            if (dash) ctx.setLineDash(dash); else ctx.setLineDash([]);
            ctx.beginPath(); ctx.moveTo(padLeft, yPos); ctx.lineTo(padLeft + barAreaW, yPos); ctx.stroke();
            ctx.setLineDash([]);
            
            const lw = ctx.measureText(text).width + 10;
            ctx.fillStyle = color;
            ctx.beginPath(); ctx.roundRect(padLeft - lw - 2, yPos - 8, lw, 14, 3); ctx.fill();
            ctx.fillStyle = '#FFFFFF';
            ctx.font = 'bold 8px "IBM Plex Mono",monospace';
            ctx.textAlign = 'right';
            ctx.fillText(text, padLeft - 5, yPos + 3);
        }
    }

    // == Рисуем стоп, тейк и цену ==
    if (top.length > 1) {
        if (liveData.trade) {
            if (liveData.trade.stop) drawLine(priceToY(liveData.trade.stop), 'rgba(198,55,60,0.9)', 'STOP', null);
            if (liveData.trade.take) drawLine(priceToY(liveData.trade.take), 'rgba(46,125,79,0.9)', 'TAKE', null);
        }
        if (priceStrike > 0) {
            drawLine(priceToY(priceStrike), 'rgba(232,98,42,0.9)', priceStrike.toFixed(0), [6, 4]);
        }
    }

    // == Условная OI×gamma сводка (не наблюдаемый flow) ==
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
    ctx.fillText(isPin ? 'PIN?' : 'PUSH?', summaryX + summaryW / 2, summaryY + summaryH / 2 - 5);
    ctx.font = '7px "IBM Plex Mono",monospace';
    ctx.fillStyle = '#8A877D';
    ctx.fillText('условно', summaryX + summaryW / 2, summaryY + summaryH / 2 + 7);
    ctx.fillText(fmtVal(smoothedNetGex), summaryX + summaryW / 2, summaryY + summaryH / 2 + 17);
}
