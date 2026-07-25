import { $, setupCanvas } from './util.js';
import { approach } from './anim.js';

let canvas, emptyEl, statusEl;
let data = null;
let statePhase = 0;

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
        if (statusEl) statusEl.textContent = '○ ИСТОРИЯ GEX НЕДОСТУПНА (мало снапшотов)';
        return;
    }
    
    data = {
        snaps: ridgePayload.snapshots,
        scale: ridgePayload.scale || 1.0,
        price: ridgePayload.price
    };
    
    if (emptyEl) emptyEl.style.display = 'none';
    if (canvas) canvas.style.display = 'block';
    if (statusEl) statusEl.textContent = `● LIVE (${data.snaps.length} СНАПШОТОВ)`;
}

function renderLoop(time) {
    requestAnimationFrame(renderLoop);
    if (!data || !canvas || canvas.style.display === 'none') return;
    
    statePhase += 0.03;
    
    const { ctx, w, h } = setupCanvas(canvas, 120);
    ctx.clearRect(0, 0, w, h);
    
    let minStrike = Infinity;
    let maxStrike = -Infinity;
    let maxAbsNet = 0;
    
    for (let s of data.snaps) {
        if (!s.gex || !s.gex.strikes) continue;
        for (let i = 0; i < s.gex.strikes.length; i++) {
            let st = s.gex.strikes[i] * data.scale;
            minStrike = Math.min(minStrike, st);
            maxStrike = Math.max(maxStrike, st);
            maxAbsNet = Math.max(maxAbsNet, Math.abs(s.gex.net[i]));
        }
    }
    
    if (maxAbsNet === 0 || minStrike === Infinity) return;
    
    let pad = (maxStrike - minStrike) * 0.1;
    if (pad === 0) pad = data.price * 0.05;
    minStrike -= pad;
    maxStrike += pad;
    
    let snapWidth = w / data.snaps.length;
    
    ctx.globalCompositeOperation = 'lighter';
    
    for (let xIdx = 0; xIdx < data.snaps.length; xIdx++) {
        let snap = data.snaps[xIdx];
        if (!snap.gex || !snap.gex.strikes) continue;
        
        let xPos = xIdx * snapWidth;
        
        for (let i = 0; i < snap.gex.strikes.length; i++) {
            let st = snap.gex.strikes[i] * data.scale;
            let net = snap.gex.net[i];
            if (Math.abs(net) < 1e-4) continue;
            
            let yNorm = (maxStrike - st) / (maxStrike - minStrike);
            let yPos = yNorm * h;
            
            let cellH = h / (snap.gex.strikes.length || 1) * 3; 
            if (cellH < 2) cellH = 2;
            
            let intensity = Math.pow(Math.abs(net) / maxAbsNet, 0.7);
            let breathe = 0.8 + 0.2 * Math.sin(statePhase + xIdx*0.5 + i*0.2);
            intensity *= breathe;
            
            // limit intensity
            intensity = Math.min(1.0, intensity);
            
            if (net > 0) {
                ctx.fillStyle = `rgba(46, 125, 79, ${intensity})`; // Green
                ctx.shadowColor = `rgba(46, 125, 79, ${intensity * 0.8})`;
            } else {
                ctx.fillStyle = `rgba(198, 55, 60, ${intensity})`; // Red
                ctx.shadowColor = `rgba(198, 55, 60, ${intensity * 0.8})`;
            }
            ctx.shadowBlur = 12;
            
            // Draw slightly wider to overlap and blend
            ctx.fillRect(xPos - 1, yPos - cellH/2, snapWidth + 2, cellH);
            ctx.shadowBlur = 0;
        }
    }
    
    ctx.globalCompositeOperation = 'source-over';
    
    if (data.price) {
        let yNorm = (maxStrike - data.price) / (maxStrike - minStrike);
        let yPos = yNorm * h;
        
        ctx.strokeStyle = 'rgba(255, 179, 71, 0.9)';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, yPos);
        ctx.lineTo(w, yPos);
        ctx.stroke();
        ctx.setLineDash([]);
        
        ctx.shadowColor = 'rgba(255, 179, 71, 0.6)';
        ctx.shadowBlur = 8;
        ctx.stroke();
        ctx.shadowBlur = 0;
    }
}
