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
        instrument: ridgePayload.instrument || null,
    };
    smoothedLevels = {};
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
    const priceStrike = liveData.price || data.price || 0;

    const { ctx, w, h } = setupCanvas(canvas, 180);
    ctx.clearRect(0, 0, w, h);

    const padLeft = 66;  // подписи страйков
    const padRight = 54; // инфо-столбик
    const padTop = 22;
    const padBot = 6;
    const barAreaW = w - padLeft - padRight;
    const plotTop = padTop + 3, plotBot = h - padBot;
    const plotH = plotBot - plotTop;

    // Непрерывное локальное окно вокруг текущей сделки. Далёкий крупный OI
    // больше не сжимает stop/price/take в одну строку.
    const sortedStrikes = [...strikes].filter(Number.isFinite).sort((a, b) => a - b);
    const gaps = sortedStrikes.slice(1).map((v, i) => v - sortedStrikes[i])
        .filter((v) => v > 0).sort((a, b) => a - b);
    const strikeStep = gaps.length ? gaps[Math.floor(gaps.length / 2)]
        : Math.max(Math.abs(priceStrike) * 0.001, 1);
    const tr = liveData.trade;
    const core = [priceStrike, tr?.entry, tr?.stop, tr?.take]
        .filter((v) => Number.isFinite(v) && v > 0);
    if (!core.length && sortedStrikes.length) {
        core.push(sortedStrikes[Math.floor(sortedStrikes.length / 2)]);
    }
    const coreLo = Math.min(...core), coreHi = Math.max(...core);
    const risk = tr?.entry && tr?.stop ? Math.abs(tr.entry - tr.stop) : strikeStep;
    const flank = Math.max(strikeStep * 2.4, risk * 1.6, (coreHi - coreLo) * 0.20);
    let viewLo = coreLo - flank, viewHi = coreHi + flank;
    if (!(viewHi > viewLo)) { viewLo = priceStrike - flank; viewHi = priceStrike + flank; }
    const priceToY = (p) => plotTop + ((viewHi - p) / (viewHi - viewLo)) * plotH;

    const indexed = net.map((v, i) => ({
        s: strikes[i], v, rank: 0,
        prox: Math.abs(strikes[i] - priceStrike) / Math.max(viewHi - viewLo, 1),
    })).filter((x) => Number.isFinite(x.s) && Number.isFinite(x.v));
    const globalRank = [...indexed].sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
    globalRank.forEach((x, i) => { x.rank = i; });
    const local = indexed.filter((x) => x.s >= viewLo && x.s <= viewHi);
    const nearest = [...indexed].sort((a, b) => a.prox - b.prox).slice(0, 5);
    const pool = [...new Set([...local, ...nearest])];
    const top = pool.sort((a, b) => {
        const scoreA = Math.abs(a.v) / Math.max(...net.map(Math.abs), 1e-9) - 0.12 * a.prox;
        const scoreB = Math.abs(b.v) / Math.max(...net.map(Math.abs), 1e-9) - 0.12 * b.prox;
        return scoreB - scoreA;
    }).slice(0, 14).sort((a, b) => b.s - a.s);
    if (!top.length) return;
    const maxAbsNet = Math.max(...top.map((x) => Math.abs(x.v)), 1e-9);
    const barH = Math.max(4, Math.min(9, plotH / Math.max(top.length * 1.35, 1)));

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
        const y = priceToY(st);
        const key = st.toFixed(4);
        
        // Плавная анимация bar width
        const targetNorm = gv / maxAbsNet; // [-1..1]
        if (!smoothedLevels[key]) smoothedLevels[key] = 0;
        smoothedLevels[key] = approach(smoothedLevels[key], targetNorm, 0.04, 3);
        const norm = smoothedLevels[key];
        const barHalf = Math.abs(norm) * (barAreaW / 2);
        
        const isAtm = priceStrike > 0 && Math.abs(st - priceStrike) < strikeStep * 0.65;
        const isAbovePrice = priceStrike > 0 && st > priceStrike;
        
        // Фон строки
        ctx.fillStyle = isAtm
            ? 'rgba(232,98,42,0.07)'
            : (i % 2 === 0 ? 'rgba(216,213,204,0.05)' : 'transparent');
        ctx.fillRect(padLeft, y - barH / 2, barAreaW, barH);

        // Gamma Heatmap Profile (Вместо баров)
        // Рисуем непрерывное облако (glow area)
        // Но так как цикл рисует по уровням, мы сделаем градиентные эллипсы,
        // которые сливаются друг с другом в единый профиль.
        ctx.globalCompositeOperation = 'screen';
        const isPOC = top[i].rank < 2;
        const breathe = isPOC ? 0.8 + 0.2 * Math.abs(Math.sin(statePhase * (i + 1))) : 1.0;
        const alpha = (0.35 + 0.5 * Math.abs(norm)) * breathe;
        
        ctx.beginPath();
        const yRadius = barH * 2.5; // Делаем "облака" выше, чтобы они пересекались
        ctx.ellipse(centerX + (gv > 0 ? barHalf/2 : -barHalf/2), y, barHalf, yRadius, 0, 0, Math.PI * 2);
        
        if (gv > 0) {
            // PIN (Зеленое облако)
            const grad = ctx.createRadialGradient(centerX + barHalf/2, y, 0, centerX + barHalf/2, y, barHalf);
            grad.addColorStop(0, `rgba(46,180,79,${alpha})`);
            grad.addColorStop(1, `rgba(46,125,79,0)`);
            ctx.fillStyle = grad;
        } else {
            // PUSH (Красное облако)
            const grad = ctx.createRadialGradient(centerX - barHalf/2, y, 0, centerX - barHalf/2, y, barHalf);
            grad.addColorStop(0, `rgba(230,70,80,${alpha})`);
            grad.addColorStop(1, `rgba(198,55,60,0)`);
            ctx.fillStyle = grad;
        }
        ctx.fill();
        ctx.globalCompositeOperation = 'source-over';
        ctx.shadowBlur = 0;

        // Метка страйка слева
        ctx.font = isAtm ? 'bold 9px "IBM Plex Mono",monospace' : '8px "IBM Plex Mono",monospace';
        ctx.fillStyle = isAtm ? '#E8622A' : (isAbovePrice ? '#5A87A0' : '#A09D94');
        ctx.textAlign = 'right';
        ctx.fillText(st.toFixed(priceStrike < 10 ? 4 : priceStrike < 100 ? 2 : 0), padLeft - 5, y + 3);

        // Метка PIN/PUSH и значение справа
        const typeLabel = gv > 0 ? 'PIN' : 'PUSH';
        const sizeLabel = fmtVal(gv);
        ctx.fillStyle = gv > 0 ? 'rgba(46,125,79,0.85)' : 'rgba(198,55,60,0.85)';
        ctx.font = 'bold 8px "IBM Plex Mono",monospace';
        ctx.textAlign = 'left';
        ctx.fillText(typeLabel, padLeft + barAreaW + 5, y - 1);
        ctx.font = '7px "IBM Plex Mono",monospace';
        ctx.fillStyle = '#8A877D';
        ctx.fillText(sizeLabel, padLeft + barAreaW + 5, y + 7);
    }

    function drawLine(yPos, color, text, dash) {
        if (yPos >= plotTop && yPos <= plotBot) {
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
        } else {
            const topSide = yPos < plotTop;
            const yy = topSide ? plotTop + 2 : plotBot - 2;
            ctx.fillStyle = color;
            ctx.font = 'bold 8px "IBM Plex Mono",monospace';
            ctx.textAlign = 'left';
            ctx.fillText(`${topSide ? '↑' : '↓'} ${text}`, padLeft + 4, yy + (topSide ? 8 : -3));
        }
    }

    // == Рисуем стоп, тейк и цену ==
    if (top.length > 0) {
        if (tr) {
            if (tr.stop) drawLine(priceToY(tr.stop), 'rgba(198,55,60,0.9)', 'STOP', null);
            if (tr.entry) drawLine(priceToY(tr.entry), 'rgba(20,20,15,0.7)', 'ENTRY', [2, 3]);
            if (tr.take) drawLine(priceToY(tr.take), 'rgba(46,125,79,0.9)', 'TAKE', null);
        }
        if (priceStrike > 0) {
            drawLine(priceToY(priceStrike), 'rgba(232,98,42,0.9)', priceStrike.toFixed(0), [6, 4]);
        }
    }

    // Самые сильные глобальные уровни вне локального окна не теряются:
    // показываем их компактными edge-маркерами без изменения масштаба.
    const far = globalRank.filter((x) => x.s < viewLo || x.s > viewHi).slice(0, 2);
    far.forEach((x, i) => {
        const above = x.s > viewHi;
        ctx.fillStyle = x.v > 0 ? 'rgba(46,125,79,0.8)' : 'rgba(198,55,60,0.8)';
        ctx.font = '7px "IBM Plex Mono",monospace';
        ctx.textAlign = 'center';
        ctx.fillText(`${above ? '↑' : '↓'} GEX ${x.s.toFixed(priceStrike < 100 ? 2 : 0)}`,
            centerX + (i ? 58 : -58), above ? plotTop + 8 : plotBot - 3);
    });

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
    const netMag = Math.abs(smoothedNetGex) / (maxAbsNet * Math.min(indexed.length, 10));
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
