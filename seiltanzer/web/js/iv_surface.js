// IV Surface (3D) — WebGL, Plotly gl3d.
// Ось X = MONEYNESS (% от ATM), а не абсолютный страйк.
// Ось Y = DTE с человеческими метками (2d, 1W, 1M, ...).
// Добавлена анимированная ATM-линия (scatter3d).

const DIM = "#8A877D", RULE = "#D8D5CC", ORANGE = "#E8622A";
const FONT = "IBM Plex Mono, ui-monospace, monospace";

const SURF_SCALE = [
    [0.0, "#2E4057"],
    [0.25,"#1B6CA8"],
    [0.5, "#F4CE14"],
    [0.75,"#E8622A"],
    [1.0, "#C6373C"]
];

export function initIVSurface(elId) {
    const el = typeof elId === "string" ? document.querySelector(elId) : elId;
    let hasPlot = false;
    let atmAnimId = null;
    let atmPhase = 0;

    // Пульсирующая ATM-линия: обновляем цвет через relayout
    function startAtmPulse() {
        function tick() {
            atmPhase += 0.04;
            const a = (0.55 + 0.45 * Math.abs(Math.sin(atmPhase))).toFixed(2);
            try {
                Plotly.restyle(el, { "line.color": [`rgba(232,98,42,${a})`] }, [1]);
            } catch(_) {}
            atmAnimId = requestAnimationFrame(tick);
        }
        atmAnimId = requestAnimationFrame(tick);
    }

    function destroy() {
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

        // ATM = медиана страйков первой экспирации
        const strikes0 = surfaceData[0].strikes;
        const atm = strikes0[Math.floor(strikes0.length / 2)];

        // Ось X: moneyness %
        const moneyPct = strikes0.map(k => +((k / atm - 1) * 100).toFixed(2));

        // Ось Y: DTE числами
        const yDte = surfaceData.map(exp => exp.days);

        // Читаемые подписи DTE
        const yTickText = surfaceData.map(exp => {
            const d = exp.days;
            if (d < 1) return Math.round(d * 24) + "h";
            if (d < 7) return Math.round(d) + "d";
            if (d < 28) return Math.round(d / 7) + "W";
            return Math.round(d / 30) + "M";
        });

        // Z: IV в % (умножаем на 100)
        const zIvs = surfaceData.map(exp =>
            exp.ivs.map(v => (typeof v === "number" && v < 200 && v > 0) ? +(v * 100).toFixed(2) : null)
        );

        const allZ = zIvs.flat().filter(v => v !== null);
        const zMin = Math.min(...allZ);
        const zMax = Math.max(...allZ);

        const surface = {
            type: "surface",
            x: moneyPct,
            y: yDte,
            z: zIvs,
            colorscale: SURF_SCALE,
            cmin: zMin,
            cmax: zMax,
            showscale: true,
            colorbar: {
                thickness: 12, len: 0.7,
                tickfont: { family: FONT, size: 9, color: DIM },
                title: { text: "IV%", side: "right", font: { family: FONT, size: 9, color: DIM } },
                ticksuffix: "%"
            },
            contours: { z: { show: true, usecolormap: true, project: { z: false } } },
            opacity: 0.9,
            hovertemplate: "Moneyness: %{x:.1f}%<br>DTE: %{y:.1f}d<br>IV: %{z:.1f}%<extra></extra>"
        };

        // ATM вертикальная линия (moneyness = 0) — анимированная
        const atmLine = {
            type: "scatter3d",
            mode: "lines",
            x: [0, 0],
            y: [Math.min(...yDte), Math.max(...yDte)],
            z: [zMin + (zMax - zMin) * 0.1, zMax * 0.95],
            line: { color: "rgba(232,98,42,0.8)", width: 5 },
            hoverinfo: "skip",
            name: "ATM",
            showlegend: false
        };

        const layout = {
            uirevision: "iv-surface",
            margin: { t: 5, b: 5, l: 5, r: 50 },
            paper_bgcolor: "rgba(0,0,0,0)",
            plot_bgcolor: "rgba(0,0,0,0)",
            scene: {
                xaxis: {
                    title: { text: "MONEYNESS (%)", font: { family: FONT, size: 10, color: DIM } },
                    tickfont: { family: FONT, size: 9, color: DIM },
                    gridcolor: RULE, zeroline: true,
                    zerolinecolor: ORANGE, zerolinewidth: 2,
                    ticksuffix: "%"
                },
                yaxis: {
                    title: { text: "DTE", font: { family: FONT, size: 10, color: DIM } },
                    tickfont: { family: FONT, size: 9, color: DIM },
                    gridcolor: RULE, zeroline: false,
                    tickvals: yDte,
                    ticktext: yTickText
                },
                zaxis: {
                    title: { text: "IV (%)", font: { family: FONT, size: 10, color: DIM } },
                    tickfont: { family: FONT, size: 9, color: DIM },
                    gridcolor: RULE, zeroline: false,
                    ticksuffix: "%"
                },
                camera: { eye: { x: 1.4, y: -1.4, z: 0.7 }, up: { x: 0, y: 0, z: 1 } },
                aspectratio: { x: 1.2, y: 1, z: 0.7 },
                bgcolor: "rgba(248,246,242,0.5)"
            }
        };

        const config = { responsive: true, displayModeBar: false, scrollZoom: false };

        if (!hasPlot) {
            Plotly.newPlot(el, [surface, atmLine], layout, config).then(() => {
                hasPlot = true;
                startAtmPulse();
            });
        } else {
            Plotly.react(el, [surface, atmLine], layout, config);
        }
    }

    return { render, destroy };
}
