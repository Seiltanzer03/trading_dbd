// IV Surface (3D) — WebGL, Plotly gl3d.
//
// Трехмерная визуализация волатильности: Strike × Days × IV.
// Показывает "рентген" опционного рынка и структуру ожиданий во времени.

import { approach } from './anim.js';

const PAPER = '#FFFFFF', SCENE_BG = '#FBFAF6', INK = '#14140F', RULE = '#D8D5CC';
const DIM = '#8A877D', ORANGE = '#E8622A', RED = '#C6373C', GREEN = '#2E7D4F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
// Rainbow/Jet colorscale for highly visible temperature changes
const SURF_SCALE = [
    [0.0, '#00008F'],
    [0.2, '#0000FF'],
    [0.4, '#00FFFF'],
    [0.6, '#FFFF00'],
    [0.8, '#FF0000'],
    [1.0, '#800000']
];

export function initIVSurface(elId) {
    const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
    let hasPlot = false;
    let currentCam = { eye: { x: 1.5, y: -1.5, z: 0.5 }, up: { x: 0, y: 0, z: 1 } };
    
    function destroy() {
        if (hasPlot) Plotly.purge(el);
        hasPlot = false;
    }

    function render(state, surfaceData) {
        if (!surfaceData || surfaceData.length === 0) {
            el.style.opacity = '0';
            document.getElementById('iv-surface-empty').style.display = 'flex';
            document.getElementById('iv-surface-status').innerText = '○ ОЖИДАНИЕ ДАННЫХ';
            return;
        }

        el.style.opacity = '1';
        document.getElementById('iv-surface-empty').style.display = 'none';
        document.getElementById('iv-surface-status').innerText = '● LIVE (SURFACE)';
        document.getElementById('iv-surface-status').className = 'badge live';

        // Формируем сетку
        let allStrikes = new Set();
        surfaceData.forEach(exp => {
            exp.strikes.forEach(s => allStrikes.add(s));
        });
        const xStrikes = Array.from(allStrikes).sort((a, b) => a - b);
        const yDays = surfaceData.map(exp => exp.days);
        
        const zIvs = [];
        surfaceData.forEach(exp => {
            const row = [];
            // Linear interpolation for missing strikes for smooth surface
            xStrikes.forEach(s => {
                const idx = exp.strikes.indexOf(s);
                if (idx !== -1) {
                    row.push(exp.ivs[idx]);
                } else {
                    // Find closest available strikes
                    let leftIdx = -1, rightIdx = -1;
                    for (let i = 0; i < exp.strikes.length; i++) {
                        if (exp.strikes[i] < s) leftIdx = i;
                        if (exp.strikes[i] > s && rightIdx === -1) rightIdx = i;
                    }
                    if (leftIdx !== -1 && rightIdx !== -1) {
                        const lStr = exp.strikes[leftIdx];
                        const rStr = exp.strikes[rightIdx];
                        const t = (s - lStr) / (rStr - lStr);
                        row.push(exp.ivs[leftIdx] * (1 - t) + exp.ivs[rightIdx] * t);
                    } else if (leftIdx !== -1) {
                        row.push(exp.ivs[leftIdx]);
                    } else if (rightIdx !== -1) {
                        row.push(exp.ivs[rightIdx]);
                    } else {
                        row.push(null);
                    }
                }
            });
            zIvs.push(row);
        });

        const trace = {
            type: 'surface',
            x: xStrikes,
            y: yDays,
            z: zIvs,
            colorscale: SURF_SCALE,
            showscale: false,
            contours: {
                z: { show: true, usecolormap: true, highlightcolor: INK, project: {z: true} },
                x: { show: true, color: RULE, width: 1 },
                y: { show: true, color: RULE, width: 1 }
            },
            opacity: 0.95,
            hoverinfo: 'x+y+z',
            hovertemplate: 'Strike: %{x}<br>Days: %{y:.1f}<br>IV: %{z:.3f}<extra></extra>'
        };

        const layout = {
            uirevision: 'true',
            margin: { t: 0, b: 0, l: 0, r: 0 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            scene: {
                xaxis: { 
                    title: { text: 'STRIKE', font: { family: FONT, size: 10, color: DIM } },
                    tickfont: { family: FONT, size: 10, color: DIM },
                    gridcolor: RULE,
                    zeroline: false
                },
                yaxis: { 
                    title: { text: 'TIME TO EXPIRY (DTE)', font: { family: FONT, size: 10, color: DIM } },
                    tickfont: { family: FONT, size: 10, color: DIM },
                    gridcolor: RULE,
                    zeroline: false
                },
                zaxis: { 
                    title: { text: 'IMPLIED VOL', font: { family: FONT, size: 10, color: DIM } },
                    tickfont: { family: FONT, size: 10, color: DIM },
                    gridcolor: RULE,
                    zeroline: false
                },
                camera: currentCam,
                aspectratio: { x: 1, y: 1, z: 0.6 },
                bgcolor: SCENE_BG
            }
        };

        const config = {
            responsive: true,
            displayModeBar: false,
            scrollZoom: false
        };

        if (!hasPlot) {
            Plotly.newPlot(el, [trace], layout, config).then(() => {
                hasPlot = true;
            });
        } else {
            // Плавное обновление, камера не сбрасывается благодаря uirevision
            // Не передаем camera при обновлении, если хотим оставить текущую 
            const updateLayout = { ...layout };
            delete updateLayout.scene.camera; 
            Plotly.react(el, [trace], updateLayout, config);
        }
    }

    return { render, destroy };
}
