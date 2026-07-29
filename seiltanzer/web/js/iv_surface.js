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

    function interp(xs, ys, x) {
        if (!xs.length || x < xs[0] || x > xs[xs.length - 1]) return null;
        let hi = 1;
        while (hi < xs.length && xs[hi] < x) hi++;
        if (hi >= xs.length) return ys[ys.length - 1];
        const lo = hi - 1;
        const span = xs[hi] - xs[lo];
        if (!span) return ys[lo];
        const f = (x - xs[lo]) / span;
        return ys[lo] + (ys[hi] - ys[lo]) * f;
    }

    function render(state, surfacePayload) {
        const payload = Array.isArray(surfacePayload)
            ? { value: surfacePayload, status: "delayed" }
            : (surfacePayload || {});
        const surfaceData = payload.value;
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
        const status = document.getElementById("iv-surface-status");
        const isDemo = payload.status === "demo";
        const hasLiveSpot = payload.spot_status === "live";
        const hasIndicativeSpot = Number(payload.spot_current) > 0;
        status.innerText = isDemo ? "◆ DEMO SURFACE"
            : hasLiveSpot ? "● OPTIONS SNAPSHOT + LIVE PROXY"
            : hasIndicativeSpot ? "◐ OPTIONS SNAPSHOT + INDICATIVE PROXY"
            : "◐ OPTIONS SNAPSHOT · SNAPSHOT SPOT";
        status.className = "badge " + (isDemo ? "demo" : "delayed");

        // IV остаётся delayed-снимком. Динамика честная: живой тик proxy
        // сдвигает ATM/moneyness, но не изображает новые опционные котировки.
        const strikes0 = surfaceData[0].strikes || [];
        const fallbackSpot = surfaceData[0].spot_at_snapshot
            || strikes0[Math.floor(strikes0.length / 2)];
        const spot = Number(payload.spot_current) > 0
            ? Number(payload.spot_current) : fallbackSpot;
        if (!spot || !strikes0.length) return;
        // У разных экспираций разные страйки. Старая версия передавала всем
        // строкам x-сетку первой экспирации и могла геометрически исказить 3D.
        // Интерполируем каждую улыбку на общую live-moneyness сетку.
        const rows = surfaceData.map((row) => {
            const pairs = (row.strikes || []).map((k, i) => ({
                x: (k / spot - 1) * 100,
                iv: Number(row.ivs?.[i]) * 100,
            })).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.iv)
                && p.iv > 0 && p.iv < 200).sort((a, b) => a.x - b.x);
            return { row, pairs };
        }).filter((r) => r.pairs.length >= 3);
        if (!rows.length) return;
        let xLo = Math.max(-20, ...rows.map((r) => r.pairs[0].x));
        let xHi = Math.min(20, ...rows.map((r) => r.pairs[r.pairs.length - 1].x));
        if (!(xHi > xLo + 1)) {
            xLo = Math.max(-20, rows[0].pairs[0].x);
            xHi = Math.min(20, rows[0].pairs[rows[0].pairs.length - 1].x);
        }
        const moneyPct = Array.from(
            { length: 41 },
            (_, i) => +(xLo + (xHi - xLo) * i / 40).toFixed(2));
        const zIvs = rows.map(({ pairs }) => {
            const xs = pairs.map((p) => p.x);
            const ys = pairs.map((p) => p.iv);
            return moneyPct.map((x) => {
                const v = interp(xs, ys, x);
                return v == null ? null : +v.toFixed(2);
            });
        });
        const cleanData = rows.map((r) => r.row);
        const yDte = cleanData.map(e => e.days);
        const yTickText = cleanData.map(e => {
            const d = e.days;
            if (d < 1) return (d * 24).toFixed(1) + "h";
            if (d < 7) return Math.round(d) + "d";
            if (d < 28) return Math.round(d / 7) + "W";
            return Math.round(d / 30) + "M";
        });

        // Live tail-skew derived from a delayed smile at CURRENT moneyness.
        // Никакого фиктивного «momentum» из повторения одного snapshot.
        const nearest = (target) => moneyPct.reduce(
            (best, x, i) => Math.abs(x - target) < Math.abs(moneyPct[best] - target)
                ? i : best, 0);
        const leftIdx = nearest(-5), rightIdx = nearest(5);
        const skew = (zIvs[0][leftIdx] ?? 0) - (zIvs[0][rightIdx] ?? 0);
        const skewEl = document.getElementById("iv-skew-momentum");
        if (skewEl) {
            skewEl.style.display = "inline-block";
            if (skew > 1.0) {
                skewEl.style.backgroundColor = "rgba(198,55,60,0.15)";
                skewEl.style.color = "#C6373C";
                skewEl.style.border = "1px solid rgba(198,55,60,0.4)";
                skewEl.innerText = `TAIL SKEW: PUT WING +${skew.toFixed(1)}пп`;
            } else if (skew < -1.0) {
                skewEl.style.backgroundColor = "rgba(46,125,79,0.15)";
                skewEl.style.color = "#2E7D4F";
                skewEl.style.border = "1px solid rgba(46,125,79,0.4)";
                skewEl.innerText = `TAIL SKEW: CALL WING +${Math.abs(skew).toFixed(1)}пп`;
            } else {
                skewEl.style.backgroundColor = "transparent";
                skewEl.style.color = "#8A877D";
                skewEl.style.border = "1px solid rgba(138,135,125,0.4)";
                skewEl.innerText = `TAIL SKEW: ${skew >= 0 ? '+' : ''}${skew.toFixed(1)}пп · FLAT`;
            }
            const spotKind = isDemo ? "demo proxy" : hasLiveSpot ? "live proxy" : hasIndicativeSpot
                ? "indicative proxy" : "snapshot spot";
            skewEl.title = `Delayed IV snapshot, пересчитанный к ${spotKind} ${spot.toFixed(4)}. Контекст хвоста, не самостоятельный сигнал.`;
        }
        const allZ = zIvs.flat().filter(v => v !== null);
        if (!allZ.length) return;
        const zMin = Math.min(...allZ);
        const rawZMax = Math.max(...allZ);
        const zMax = rawZMax > zMin ? rawZMax : zMin + 0.01;

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
