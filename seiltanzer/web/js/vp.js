import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl;
let payload = null;
let state = {
    bins: [], // { targetVol, currentVol, price }
    poc: null,
    maxVol: 0,
    phase: 0
};
let particles = [];

export function initVp() {
    canvas = $('#vp-canvas');
    emptyEl = $('#vp-empty');
    if (!canvas) return;
    requestAnimationFrame(renderLoop);
}

export function updateVp(p) {
    if (!p || !p.bins || p.bins.length === 0) {
        payload = null;
        if (emptyEl) emptyEl.style.display = 'flex';
        if (canvas) canvas.style.display = 'none';
        return;
    }
    payload = p;
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';

    // Синхронизируем стейт бинов
    let maxV = 0;
    p.bins.forEach(b => {
        if (b.volume > maxV) maxV = b.volume;
        let existing = state.bins.find(x => x.price === b.price);
        if (existing) {
            existing.targetVol = b.volume;
        } else {
            state.bins.push({ price: b.price, targetVol: b.volume, currentVol: 0, yOffset: 0 });
        }
    });
    // Удаляем старые бины
    state.bins = state.bins.filter(sb => p.bins.some(b => b.price === sb.price));
    state.maxVol = maxV;
    state.poc = p.poc;
    state.is_tpo = p.is_tpo;
}

function renderLoop(time) {
    requestAnimationFrame(renderLoop);
    if (!payload || !canvas || canvas.style.display === 'none') return;

    state.phase += 0.03; // Для синусоиды

    const { ctx, w, h } = setupCanvas(canvas, 190);
    ctx.clearRect(0, 0, w, h);

    if (state.bins.length === 0) return;

    // Определяем вертикальные координаты (Y = Цена)
    const minP = Math.min(...state.bins.map(b => b.price));
    const maxP = Math.max(...state.bins.map(b => b.price));
    
    // Функция маппинга цены в Y (перевернутая ось: maxP сверху, minP снизу)
    const padY = 10;
    const getY = (p) => {
        if (maxP === minP) return h / 2;
        return h - padY - ((p - minP) / (maxP - minP)) * (h - padY * 2);
    };

    let binH = 4;
    if (state.bins.length > 1) {
        // сортируем по цене
        state.bins.sort((a, b) => a.price - b.price);
        const step = state.bins[1].price - state.bins[0].price;
        binH = Math.max(2, Math.abs(getY(minP) - getY(minP + step)));
    }

    const maxW = w - 10; // максимальная длина бара

    // Рисуем бары (жидкая гистограмма)
    state.bins.forEach((b, i) => {
        // Spring physics для объема
        b.currentVol = approach(b.currentVol, b.targetVol, 0.03, 3);
        
        let barW = (b.currentVol / state.maxVol) * maxW;
        if (isNaN(barW) || barW < 0) barW = 0;
        
        // Колыхание правого края (жидкий эффект)
        const wave = Math.sin(state.phase + i * 0.5) * (barW * 0.05); 
        const drawW = Math.max(1, barW + wave);

        const y = getY(b.price);

        // Градиент заливки
        let grad = ctx.createLinearGradient(0, y, drawW, y);
        grad.addColorStop(0, 'rgba(46,125,79,0.05)'); // темный у корня
        
        if (b.price === state.poc) {
            // POC Glow
            grad.addColorStop(1, 'rgba(232,98,42,0.8)');
            ctx.shadowColor = 'rgba(232,98,42,0.6)';
            ctx.shadowBlur = 10;
            ctx.fillStyle = grad;
        } else {
            grad.addColorStop(1, 'rgba(46,125,79,0.5)'); // яркий на конце
            ctx.fillStyle = grad;
            ctx.shadowBlur = 0;
        }

        ctx.beginPath();
        // Скругленный край бара справа
        ctx.roundRect(0, y - binH/2, drawW, binH - 1, [0, 4, 4, 0]);
        ctx.fill();
        ctx.shadowBlur = 0;
    });

    // Рисуем POC линию и текст
    if (state.poc != null) {
        const y = getY(state.poc);
        ctx.fillStyle = '#E8622A';
        ctx.font = '8px "IBM Plex Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillText(`POC${state.is_tpo ? '(TPO)' : ''}`, 4, y - binH);
        
        ctx.strokeStyle = 'rgba(232,98,42,0.4)';
        ctx.setLineDash([2, 2]);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    // Частицы
    updateAndDrawParticles(ctx, w, h, getY);
}

function updateAndDrawParticles(ctx, w, h, getY) {
    // Спавн частиц (дрейф к POC или толстым зонам)
    if (Math.random() < 0.3 && state.maxVol > 0) {
        // Спавним на случайной высоте
        const rp = Math.random() * (Math.max(...state.bins.map(b => b.price)) - Math.min(...state.bins.map(b => b.price))) + Math.min(...state.bins.map(b => b.price));
        particles.push({
            price: rp,
            x: Math.random() * (w * 0.5),
            life: 1.0,
            speed: (Math.random() * 0.5 + 0.2)
        });
    }

    ctx.fillStyle = 'rgba(255,255,255,0.4)';
    for (let i = particles.length - 1; i >= 0; i--) {
        let p = particles[i];
        
        // Притяжение к POC
        if (state.poc != null) {
            p.price = approach(p.price, state.poc, 0.005, p.speed);
        }
        p.x += Math.random() * 0.5; // небольшой дрейф вправо
        p.life -= 0.01;

        if (p.life <= 0 || p.x > w) {
            particles.splice(i, 1);
            continue;
        }

        ctx.globalAlpha = p.life;
        ctx.beginPath();
        ctx.arc(p.x, getY(p.price), 0.8, 0, Math.PI * 2);
        ctx.fill();
    }
    ctx.globalAlpha = 1.0;
}
