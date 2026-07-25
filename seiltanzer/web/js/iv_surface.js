// IV Surface (3D) — Улыбка волатильности.
// Moneyness × DTE × IV. 

const DIM = "#666666", RULE = "rgba(180,180,180,0.5)", ORANGE = "#E8622A";
const FONT = "IBM Plex Mono, ui-monospace, monospace";

const SURF_SCALE = [
    [0.0, "#1A1F3A"],
    [0.2, "#1B6CA8"],
    [0.45,"#2ECC71"],
    [0.65,"#F4CE14"],
    [0.85,"#E8622A"],
    [1.0, "#C6373C"]
];

export function initIVSurface(elId) {
    const el = typeof elId === "string" ? document.querySelector(elId) : elId;
    let hasPlot = false;
    let listenersOn = false;
    let interacting = false;
    let interactTimer = null;

    const INIT_CAM = { eye: { x: 1.4, y: -1.4, z: 0.7 }, up: { x: 0, y: 0, z: 1 } };
    let currentCam = JSON.parse(JSON.stringify(INIT_CAM));
    let skewHistory = [];

    function markInteract() {
        interacting = true;
        if (interactTimer) clearTimeout(interactTimer);
        interactTimer = setTimeout(() => { interacting = false; }, 300);
    }

    function grabCam() {
        const c = el._fullLayout?.scene?.camera;
        if (c && c.eye) currentCam = c;
    }

    function attachListeners() {
        if (listenersOn || !el.on) return;
        listenersOn = true;
        el.on('plotly_relayouting', () => { markInteract(); grabCam(); });
        el.on('plotly_relayout', grabCam);
        el.addEventListener('mousedown', markInteract);
        el.addEventListener('touchstart', markInteract, { passive: true });
        el.addEventListener('wheel', markInteract, { passive: true });
    }

    function destroy() {
        if (hasPlot) Plotly.purge(el);
        hasPlot = false;
    }

    function render(state, surfaceData) {
        if (!surfaceData || surfaceData.length === 0) {
            el.style.opacity = "0";
            document.getElementById("iv-surface-empty").style.display = "flex";
            document.getElementById("iv-surface-status").innerText = "○ ОЖИДАНИЕ ДАННЫХ";
            return;
        }

        // Если пользователь крутит график мышкой - пропускаем рендер, чтобы не сбросить зажатие
        if (interacting) return;

        el.style.opacity = "1";
        document.getElementById("iv-surface-empty").style.display = "none";
        document.getElementById("iv-surface-status").innerText = "● LIVE (SURFACE)";
        document.getElementById("iv-surface-status").className = "badge live";

        const strikes0 = surfaceData[0].strikes;
        const atm = strikes0[Math.floor(strikes0.length / 2)];
        const moneyPct = strikes0.map(k => +((k / atm - 1) * 100).toFixed(2));
        const yDte = surfaceData.map(e => e.days);
        const yTickText = surfaceData.map(e => {
            const d = e.days;
            if (d < 1) return (d * 24).toFixed(1) + "h";
            if (d < 7) return Math.round(d) + "d";
            if (d < 28) return Math.round(d / 7) + "W";
            return Math.round(d / 30) + "M";
        });

        // --- Skew Momentum ---
        const z0 = surfaceData[0].ivs;
        let leftIdx = 0, rightIdx = strikes0.length - 1;
        for (let i = 0; i < moneyPct.length; i++) {
            if (moneyPct[i] <= -5) leftIdx = i;
            if (moneyPct[i] >= 5 && rightIdx === strikes0.length - 1) rightIdx = i;
        }
        const skew = z0[leftIdx] - z0[rightIdx];
        
        const now = Date.now();
        skewHistory.push({ t: now, v: skew });
        skewHistory = skewHistory.filter(s => now - s.t < 120000); // 2 minutes window
        
        let skewMom = 0;
        if (skewHistory.length > 1) {
            const first = skewHistory[0];
            const last = skewHistory[skewHistory.length - 1];
            if (last.t > first.t) {
                skewMom = ((last.v - first.v) / (last.t - first.t)) * 100000; // arbitrary scale for readability
            }
        }
        
        const skewEl = document.getElementById("iv-skew-momentum");
        if (skewEl) {
            skewEl.style.display = "inline-block";
            const isPutRising = skewMom > 0.05;
            const isCallRising = skewMom < -0.05;
            
            if (isPutRising) {
                skewEl.style.backgroundColor = "rgba(198,55,60,0.15)";
                skewEl.style.color = "#C6373C";
                skewEl.style.border = "1px solid rgba(198,55,60,0.4)";
                skewEl.innerText = `SKEW MOM: +${skewMom.toFixed(1)} 🔴 ШОРТ (Путы дорожают)`;
            } else if (isCallRising) {
                skewEl.style.backgroundColor = "rgba(46,125,79,0.15)";
                skewEl.style.color = "#2E7D4F";
                skewEl.style.border = "1px solid rgba(46,125,79,0.4)";
                skewEl.innerText = `SKEW MOM: ${skewMom.toFixed(1)} 🟢 ЛОНГ (Коллы дорожают)`;
            } else {
                skewEl.style.backgroundColor = "transparent";
                skewEl.style.color = "#8A877D";
                skewEl.style.border = "1px solid rgba(138,135,125,0.4)";
                skewEl.innerText = `SKEW MOM: ${skewMom > 0 ? '+' : ''}${skewMom.toFixed(1)} ⚪ НЕЙТРАЛЬНО`;
            }
        }
        // ---------------------

        const zIvs = surfaceData.map(e =>
            e.ivs.map(v => (typeof v === "number" && v < 200 && v > 0) ? +(v * 100).toFixed(2) : null)
        );
        const allZ = zIvs.flat().filter(v => v !== null);
        const zMin = Math.min(...allZ), zMax = Math.max(...allZ);

        const surface = {
            type: "surface",
            x: moneyPct, y: yDte, z: zIvs,
            colorscale: SURF_SCALE,
            cmin: zMin, cmax: zMax,
            showscale: true,
            colorbar: {
                thickness: 14, len: 0.75, x: 1.01,
                bgcolor: "rgba(248,246,242,0.9)",
                bordercolor: "rgba(200,200,200,0.3)",
                borderwidth: 1,
                tickfont: { family: FONT, size: 11, color: "#111", weight: "bold" },
                title: { text: "IV %", side: "right", font: { family: FONT, size: 12, color: "#111", weight: "bold" } },
                ticksuffix: "%"
            },
            contours: {
                z: { show: true, usecolormap: true, project: { z: false }, width: 1 },
            },
            lighting: { ambient: 0.7, diffuse: 0.8, specular: 0.3, roughness: 0.5 },
            opacity: 0.92,
            hovertemplate: "<b>Moneyness:</b> %{x:.1f}%<br><b>DTE:</b> %{y}<br><b>IV:</b> %{z:.1f}%<extra></extra>"
        };

        const atmLine = {
            type: "scatter3d",
            mode: "lines+text",
            x: [0, 0],
            y: [Math.min(...yDte), Math.max(...yDte)],
            z: [zMin + (zMax - zMin) * 0.05, zMax * 0.98],
            line: { color: "rgba(232,98,42,0.9)", width: 5 },
            text: ["", "ATM"],
            textfont: { family: FONT, size: 12, color: ORANGE },
            textposition: "top center",
            hoverinfo: "skip",
            name: "ATM",
            showlegend: false
        };

        if (hasPlot) grabCam();

        const layout = {
            uirevision: "iv-surface",
            margin: { t: 5, b: 5, l: 0, r: 55 },
            paper_bgcolor: "rgba(0,0,0,0)",
            plot_bgcolor: "rgba(0,0,0,0)",
            scene: {
                xaxis: {
                    title: { text: "MONEYNESS %", font: { family: FONT, size: 13, color: "#111" } },
                    tickfont: { family: FONT, size: 11, color: "#222" },
                    gridcolor: RULE, zeroline: true,
                    zerolinecolor: ORANGE, zerolinewidth: 2,
                    ticksuffix: "%", showbackground: true,
                    backgroundcolor: "rgba(240,238,232,0.6)"
                },
                yaxis: {
                    title: { text: "DTE", font: { family: FONT, size: 13, color: "#111" } },
                    tickfont: { family: FONT, size: 11, color: "#222" },
                    gridcolor: RULE, zeroline: false,
                    tickvals: yDte, ticktext: yTickText,
                    showbackground: true, backgroundcolor: "rgba(240,238,232,0.6)"
                },
                zaxis: {
                    title: { text: "IV %", font: { family: FONT, size: 13, color: "#111" } },
                    tickfont: { family: FONT, size: 11, color: "#222" },
                    gridcolor: RULE, zeroline: false,
                    ticksuffix: "%", showbackground: true,
                    backgroundcolor: "rgba(240,238,232,0.6)"
                },
                camera: currentCam,
                aspectratio: { x: 1.3, y: 1, z: 0.65 },
                bgcolor: "rgba(248,246,242,0.3)"
            }
        };

        const config = {
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ["toImage", "sendDataToCloud"],
            scrollZoom: true
        };

        if (!hasPlot) {
            Plotly.newPlot(el, [surface, atmLine], layout, config).then(() => {
                hasPlot = true;
                attachListeners();
            });
        } else {
            Plotly.react(el, [surface, atmLine], layout, config);
        }
    }

    return { render, destroy };
}
