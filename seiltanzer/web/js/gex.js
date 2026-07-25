// GEX Evolution — эволюция дилерской гаммы по страйкам во времени.
//
// КАК ЧИТАТЬ:
//   Зелёные полосы = дилеры лонг-гамма (пиннинг: рынок притягивает к страйку).
//   Красные полосы = дилеры шорт-гамма (усиление движения: пробой ускоряется).
//   Жёлтый пунктир = текущая цена.
//   Правый столбик = СУММАРНЫЙ NET GEX: зелёный = рынок в пиннинге, красный = разгон.
//
// ПРИМЕНЕНИЕ:
//   Если крупный зелёный уровень выше текущей цены — ждать торможения роста.
//   Если тейк стоит ЗА красной зоной — пробой ускорится, можно держать дальше.
import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let data = null;
let statePhase = 0;
// Для плавного анимирования текущего нетто-бара
let smoothedNetGex = 0;

export function initGex() {
    canvas = $('#gex-evol-canvas');
    emptyEl = $('#gex-evol-empty');
    statusEl = $('#gex-evol-status');
    if (!canvas) return;
    requestAnimationFrame(renderLoop);
}

export function updateGex(ridgePayload) {
    if (!ridgePayload || !ridgePayload.available || !ridgePayload.snapshots || ridgePayload.snapshots.length < 2) {
        data = null;
        if (emptyEl) emptyEl.style.display = 'flex';
        if (canvas) canvas.style.display = 'none';
        if (statusEl) statusEl.textContent = 'o ИСТОРИЯ GEX НЕДОСТУПНА (нужно ≥2 снапшота)';
        return;
    }
    data = {
        snaps: ridgePayload.snapshots,
        scale: ridgePayload.scale || 1.0,
        price: ridgePayload.price
    };
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
    if (statusEl) statusEl.textContent = `● LIVE (${data.snaps.length} снапш.)`;
}

function renderLoop() {
    requestAnimationFrame(renderLoop);
    if (!data || !canvas || canvas.style.display === 'none') return;
    statePhase += 0.025;

    const { ctx, w, h } = setupCanvas(canvas, 140);
    ctx.clearRect(0, 0, w, h);

    // Берём ПОСЛЕДНИЙ снапшот как наиболее актуальный
    const snap = data.snaps[data.snaps.length - 1];
    if (!snap || !snap.gex || !snap.gex.strikes || snap.gex.strikes.length === 0) return;

    const strikes = snap.gex.strikes.map(s => s * data.scale);
    const net = snap.gex.net;
    const maxAbsNet = Math.max(...net.map(Math.abs));
    if (maxAbsNet === 0) return;

    // Рисуем горизонтальный профиль GEX
    const padLeft = 58;  // для подписей страйков
    const padRight = 48; // для нетто-бара справа
    const padTop = 14;
    const barAreaW = w - padLeft - padRight;
    const rowH = Math.max(3, (h - padTop) / strikes.length);

    // Находим ценовую ось для маркера
    const priceStrike = data.price || 0;
    const minS = Math.min(...strikes), maxS = Math.max(...strikes);

    // Шрифт
    ctx.font = '8px "IBM Plex Mono",monospace';

    // Заголовки колонок
    ctx.fillStyle = '#8A877D';
    ctx.textAlign = 'center';
    ctx.fillText('NET GEX PROFILE', padLeft + barAreaW / 2, 9);
    ctx.fillText('НЕТТО', w - padRight / 2, 9);

    for (let i = 0; i < strikes.length; i++) {
        const st = strikes[i];
        const gv = net[i];
        const y = padTop + i * rowH;
        const norm = gv / maxAbsNet; // [-1..1]
        const barW = Math.abs(norm) * barAreaW;
        const breathe = 0.75 + 0.25 * Math.sin(statePhase + i * 0.4);
        const alpha = (0.4 + 0.5 * Math.abs(norm)) * breathe;

        // Фоновая полоса строки
        if (i % 2 === 0) {
            ctx.fillStyle = 'rgba(216,213,204,0.08)';
            ctx.fillRect(padLeft, y, barAreaW, rowH);
        }

        // Бар (от центра или от левого края?)
        // Зелёный = лонг гамма (пиннинг), рисуем вправо от центра
        // Красный = шорт гамма (разгон), рисуем влево
        if (gv > 0) {
            const grad = ctx.createLinearGradient(padLeft, 0, padLeft + barW, 0);
            grad.addColorStop(0, `rgba(46,125,79,${alpha})`);
            grad.addColorStop(1, `rgba(46,155,79,${alpha * 0.4})`);
            ctx.fillStyle = grad;
            ctx.shadowColor = `rgba(46,125,79,${alpha * 0.5})`;
            ctx.shadowBlur = 6;
            ctx.fillRect(padLeft, y + 1, barW, rowH - 2);
        } else {
            const grad = ctx.createLinearGradient(padLeft + barAreaW - barW, 0, padLeft + barAreaW, 0);
            grad.addColorStop(0, `rgba(198,55,60,${alpha * 0.4})`);
            grad.addColorStop(1, `rgba(198,55,60,${alpha})`);
            ctx.fillStyle = grad;
            ctx.shadowColor = `rgba(198,55,60,${alpha * 0.5})`;
            ctx.shadowBlur = 6;
            ctx.fillRect(padLeft + barAreaW - barW, y + 1, barW, rowH - 2);
        }
        ctx.shadowBlur = 0;

        // Подписи страйка слева
        const isAtm = priceStrike > 0 && Math.abs(st - priceStrike) < (maxS - minS) / strikes.length;
        ctx.fillStyle = isAtm ? '#E8622A' : '#8A877D';
        ctx.font = isAtm ? 'bold 8px "IBM Plex Mono",monospace' : '8px "IBM Plex Mono",monospace';
        ctx.textAlign = 'right';
        ctx.fillText(st.toFixed(0), padLeft - 3, y + rowH / 2 + 3);

        // Значение нетто справа (в упрощённом формате)
        const absMn = Math.abs(gv);
        let valStr;
        if (absMn >= 1e9) valStr = (gv / 1e9).toFixed(1) + 'B';
        else if (absMn >= 1e6) valStr = (gv / 1e6).toFixed(1) + 'M';
        else if (absMn >= 1e3) valStr = (gv / 1e3).toFixed(0) + 'K';
        else valStr = gv.toFixed(0);
        ctx.fillStyle = gv > 0 ? 'rgba(46,125,79,0.8)' : 'rgba(198,55,60,0.8)';
        ctx.font = '7px "IBM Plex Mono",monospace';
        ctx.textAlign = 'left';
        ctx.fillText(valStr, w - padRight + 3, y + rowH / 2 + 2);
    }

    // Линия текущей цены (горизонтальная, оранжевая)
    if (priceStrike > 0 && maxS > minS) {
        const priceNorm = (priceStrike - minS) / (maxS - minS);
        const py = padTop + priceNorm * (strikes.length * rowH);
        ctx.strokeStyle = 'rgba(232,98,42,0.9)';
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 4]);
        ctx.beginPath(); ctx.moveTo(padLeft, py); ctx.lineTo(padLeft + barAreaW, py); ctx.stroke();
        ctx.setLineDash([]);
        // Метка цены
        ctx.fillStyle = '#E8622A';
        ctx.font = 'bold 8px "IBM Plex Mono",monospace';
        ctx.textAlign = 'right';
        ctx.fillText(priceStrike.toFixed(0), padLeft - 3, py + 3);
    }

    // Суммарный NET GEX — анимированный бар справа
    const totalNet = net.reduce((a, b) => a + b, 0);
    smoothedNetGex = approach(smoothedNetGex, totalNet, 0.04, 2);
    const netNorm = Math.max(-1, Math.min(1, smoothedNetGex / (maxAbsNet * strikes.length)));
    const netBarH = Math.abs(netNorm) * (h - padTop - 20);
    const netBarY = netNorm > 0
        ? padTop + (h - padTop) / 2 - netBarH
        : padTop + (h - padTop) / 2;
    const netBarX = w - padRight + 5;
    const netBarW = padRight - 10;
    const netGlow = 0.6 + 0.4 * Math.abs(Math.sin(statePhase));
    ctx.fillStyle = totalNet > 0
        ? `rgba(46,125,79,${netGlow})`
        : `rgba(198,55,60,${netGlow})`;
    ctx.shadowColor = totalNet > 0 ? 'rgba(46,125,79,0.7)' : 'rgba(198,55,60,0.7)';
    ctx.shadowBlur = 12;
    ctx.beginPath();
    ctx.roundRect(netBarX, netBarY, netBarW, netBarH, 3);
    ctx.fill();
    ctx.shadowBlur = 0;

    // Нулевая линия в нетто-баре
    const midY = padTop + (h - padTop) / 2;
    ctx.strokeStyle = 'rgba(20,20,15,0.4)'; ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(netBarX - 3, midY); ctx.lineTo(w - 3, midY); ctx.stroke();
    ctx.fillStyle = totalNet > 0 ? 'rgba(46,125,79,0.9)' : 'rgba(198,55,60,0.9)';
    ctx.font = 'bold 7px "IBM Plex Mono",monospace'; ctx.textAlign = 'center';
    ctx.fillText(totalNet > 0 ? 'PIN' : 'PUSH', netBarX + netBarW / 2, netBarY + netBarH / 2 + 3);
}
