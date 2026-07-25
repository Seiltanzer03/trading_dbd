// IV Surface (3D) — Улыбка волатильности.
// Moneyness × DTE × IV с медленным авто-вращением + ATM-пульсация.
// Вращение приостанавливается при ручном взаимодействии.

const DIM = "#AAAAAA", RULE = "rgba(200,200,200,0.4)", ORANGE = "#E8622A";
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
    let autoRotateId = null;
    let atmAnimId = null;
    let theta = 0.8;
    let interacting = false;
    let interactTimer = null;
    const R = 2.1;

    function startAutoRotate() {
        if (autoRotateId) cancelAnimationFrame(autoRotateId);
        let last = performance.now();
        function tick(now) {
            if (interacting || !hasPlot) { autoRotateId = requestAnimationFrame(tick); return; }
            const dt = (now - last) / 1000;
            last = now;
            theta += 0.3 * dt; // 0.3 радиан/сек
            const eye = { x: R * Math.cos(theta), y: R * Math.sin(theta), z: 0.7 };
            try { Plotly.relayout(el, { "scene.camera.eye": eye }); } catch(_) {}
            autoRotateId = requestAnimationFrame(tick);
        }
        autoRotateId = requestAnimationFrame(tick);
    }

    function pauseAndResume() {
        interacting = true;
        if (interactTimer) clearTimeout(interactTimer);
        interactTimer = setTimeout(() => { interacting = false; }, 6000);
    }

    // ATM-линия пульсирует по яркости
    let atmPhase = 0;
    function startAtmPulse() {
        if (atmAnimId) cancelAnimationFrame(atmAnimId);
        function tick() {
            atmPhase += 0.03;
            const a = (0.6 + 0.4 * Math.abs(Math.sin(atmPhase))).toFixed(2);
            const w = 3 + 2 * Math.abs(Math.sin(atmPhase));
            try {
                Plotly.restyle(el, { "line.color": [`rgba(232,98,42,${a})`], "line.width": [w] }, [1]);
            } catch(_) {}
            atmAnimId = requestAnimationFrame(tick);
        }
        atmAnimId = requestAnimationFrame(tick);
    }

    function destroy() {
        if (autoRotateId) cancelAnimationFrame(autoRotateId);
        if (atmAnimId) cancelAnimationFrame(atmAnimId);
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
            if (d < 1) return Math.round(d * 24) + "h";
            if (d < 7) return Math.round(d) + "d";
            if (d < 28) return Math.round(d / 7) + "W";
            return Math.round(d / 30) + "M";
        });
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
                tickfont: { family: FONT, size: 10, color: "#333" },
                title: { text: "IV %", side: "right", font: { family: FONT, size: 10, color: "#555" } },
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
            line: { color: "rgba(232,98,42,0.85)", width: 4 },
            text: ["", "ATM"],
            textfont: { family: FONT, size: 10, color: ORANGE },
            textposition: "top center",
            hoverinfo: "skip",
            name: "ATM",
            showlegend: false
        };

        const layout = {
            uirevision: "iv-surface",
            margin: { t: 5, b: 5, l: 0, r: 55 },
            paper_bgcolor: "rgba(0,0,0,0)",
            plot_bgcolor: "rgba(0,0,0,0)",
            scene: {
                xaxis: {
                    title: { text: "MONEYNESS %", font: { family: FONT, size: 11, color: "#555" } },
                    tickfont: { family: FONT, size: 10, color: "#555" },
                    gridcolor: RULE, zeroline: true,
                    zerolinecolor: ORANGE, zerolinewidth: 2,
                    ticksuffix: "%", showbackground: true,
                    backgroundcolor: "rgba(240,238,232,0.4)"
                },
                yaxis: {
                    title: { text: "DTE", font: { family: FONT, size: 11, color: "#555" } },
                    tickfont: { family: FONT, size: 10, color: "#555" },
                    gridcolor: RULE, zeroline: false,
                    tickvals: yDte, ticktext: yTickText,
                    showbackground: true, backgroundcolor: "rgba(240,238,232,0.4)"
                },
                zaxis: {
                    title: { text: "IV %", font: { family: FONT, size: 11, color: "#555" } },
                    tickfont: { family: FONT, size: 10, color: "#555" },
                    gridcolor: RULE, zeroline: false,
                    ticksuffix: "%", showbackground: true,
                    backgroundcolor: "rgba(240,238,232,0.4)"
                },
                camera: {
                    eye: { x: R * Math.cos(theta), y: R * Math.sin(theta), z: 0.7 },
                    up: { x: 0, y: 0, z: 1 }
                },
                aspectratio: { x: 1.3, y: 1, z: 0.65 },
                bgcolor: "rgba(248,246,242,0.3)"
            }
        };

        const config = {
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ["toImage", "sendDataToCloud"],
            scrollZoom: false
        };

        if (!hasPlot) {
            Plotly.newPlot(el, [surface, atmLine], layout, config).then(() => {
                hasPlot = true;
                // Перехватываем взаимодействие пользователя
                el.on("plotly_relayout", () => pauseAndResume());
                startAutoRotate();
                startAtmPulse();
            });
        } else {
            Plotly.react(el, [surface, atmLine], layout, config);
        }
    }

    return { render, destroy };
}
