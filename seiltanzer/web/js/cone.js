// Probability Cone — НАСТОЯЩИЙ 3D (WebGL, Plotly gl3d), risk-neutral.
//
// Поверхность = плотность вероятности исхода сделки под ОПЦИОННУЮ волу + цену
// (НЕ винрейт). X = R (стоп −1 · 0 · тейк +T), Y = ВРЕМЯ (адаптивное: минуты у
// скальпа, дни у свинга; вола ДЫШИТ по term-structure опционов), Z = плотность
// живых путей. Красная/зелёная СТЕНЫ = P дойти до стопа/тейка к моменту t.
//
// ТОЧКА ЦЕНЫ едет ПО ПОВЕРХНОСТИ (билинейный сэмпл высоты), а не по её краю:
//   • ось R (X)         — где цена относительно стоп/тейк;
//   • ось ВРЕМЯ (Y)     — «прогресс к развязке»: чем ближе цена к барьеру, тем
//                          глубже точка уходит к развязке (близость к барьеру ≈
//                          насколько сделка уже решена). Это второе измерение с
//                          практическим смыслом, а не подпорка вдоль одной оси;
//   • высота (Z)        — плотность рынка в этой точке: видно, насколько текущее
//                          место «ожидаемо». За точкой тянется след по времени.
//
// ПЛАВНОСТЬ 60fps: данные приходят раз в ~1–2 с, но форма конуса не прыгает —
// поверхность/стены/точка МОРФятся покадрово (Plotly.restyle с экспоненциальным
// сглаживанием), а не пересобираются через react. Полный пересбор — только при
// смене каркаса (другая сделка/таймфрейм). Поэтому вид НЕ отскакивает никогда.

import { approach } from './anim.js';

const PAPER = '#FFFFFF', SCENE_BG = '#FBFAF6', INK = '#14140F', RULE = '#D8D5CC';
const DIM = '#8A877D', ORANGE = '#E8622A', RED = '#C6373C', GREEN = '#2E7D4F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
const SURF_SCALE = [[0, '#F8F3EC'], [0.18, '#F3D7C5'], [0.45, '#EDA777'],
                    [0.72, '#E8753C'], [1, '#A9342C']];

// индексы трасс (порядок фиксирован в buildTraces)
const SURF = 0, MESH_STOP = 1, MESH_TAKE = 2, EDGE_STOP = 3, EDGE_TAKE = 4,
      MODE = 5, Q20 = 6, Q80 = 7, TRAIL = 8, BALL = 9;

function fmtTime(years) {
  if (years == null) return '—';
  const min = years * 365 * 24 * 60;
  if (min < 1) return '<1 мин';
  if (min < 90) return `${Math.round(min)} мин`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)} ч`;
  return `${(h / 24).toFixed(1)} дн`;
}

function fmtProb(p) {
  if (p == null || !Number.isFinite(p)) return '—';
  const pct = p * 100;
  if (pct < 0.1) return '<0.1%';
  return `${pct < 10 ? pct.toFixed(1) : pct.toFixed(0)}%`;
}

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let hasPlot = false, listenersOn = false;
  let structSig = null, pendingStruct = false;
  let curR = null, lastDotR = null;
  let interacting = false, interactTimer = null, pointerHeld = false;
  let lastYTitle = null, lastNames = null;
  const live = { r: null };

  // цель (из сервера) и отображаемое (плавно морфится к цели)
  const tgt = { z: null, pStop: null, pTake: null, xs: null, ys: null,
                edges: null, T: 2.5, r0: 0, nS: 0, nB: 0, hy: null,
                median: null, term_slope: 0, structSig: null,
                probabilityAvailable: false, conditional: null, survival: null,
                modeX: null, q20X: null, q80X: null,
                touchTake: null, touchStop: null, noTouch: null };
  const disp = { z: null, pStop: null, pTake: null };

  // Камера: развернута на 180 градусов (вид спереди/сзади)
  const INIT_CAM = { eye: { x: 0.15, y: 2.3, z: 0.65 }, up: { x: 0, y: 0, z: 1 } };

  const ready = () => typeof window !== 'undefined' && window.Plotly && el;

  // ---------------------------------------------------- взаимодействие/камера
  function attachListeners() {
    if (listenersOn || !el.on) return;
    listenersOn = true;
    el.on('plotly_relayouting', () => {
      interacting = true;
      if (interactTimer) clearTimeout(interactTimer);
      if (!pointerHeld) interactTimer = setTimeout(() => { interacting = false; flush(); }, 300);
    });
    el.on('plotly_relayout', () => {
      if (interactTimer) clearTimeout(interactTimer);
      if (!pointerHeld) interactTimer = setTimeout(() => { interacting = false; flush(); }, 140);
    });
    const begin = () => {
      pointerHeld = true;
      interacting = true;
      if (interactTimer) clearTimeout(interactTimer);
    };
    const release = () => {
      if (!pointerHeld) return;
      pointerHeld = false;
      if (interactTimer) clearTimeout(interactTimer);
      requestAnimationFrame(() => {
        interactTimer = setTimeout(() => { interacting = false; flush(); }, 140);
      });
    };
    if (window.PointerEvent) {
      el.addEventListener('pointerdown', begin, true);
      window.addEventListener('pointerup', release, true);
      window.addEventListener('pointercancel', release, true);
    } else {
      el.addEventListener('mousedown', begin, true);
      el.addEventListener('touchstart', begin, { passive: true, capture: true });
      window.addEventListener('mouseup', release, true);
      window.addEventListener('touchend', release, { passive: true, capture: true });
      window.addEventListener('touchcancel', release, { passive: true, capture: true });
    }
  }
  function flush() {
    if (!ready() || !hasPlot) return;
    if (pendingStruct) { pendingStruct = false; snapDisp(); render(); }
  }

  // ------------------------------------------------------------- геометрия
  // билинейная высота поверхности в точке (R, yFrac) — точка садится ТОЧНО на
  // отображаемую поверхность, а не на вычисленную «иглу» у переднего края.
  function surfZ(R, yf) {
    const z = disp.z; if (!z || !z.length) return 0.04;
    const nS = z.length, nB = z[0].length, T = tgt.T;
    const frac = (R + 1) / (T + 1);
    const fc = frac * nB - 0.5;
    const b0 = Math.max(0, Math.min(nB - 1, Math.floor(fc)));
    const b1 = Math.max(0, Math.min(nB - 1, b0 + 1));
    const tb = Math.max(0, Math.min(1, fc - b0));
    const y = Math.max(tgt.ys[0], Math.min(tgt.ys[tgt.ys.length - 1], yf));
    let j1 = 1;
    while (j1 < nS && tgt.ys[j1] < y) j1++;
    j1 = Math.max(0, Math.min(nS - 1, j1));
    const j0 = Math.max(0, j1 - 1);
    const tj = j0 === j1 ? 0
      : (y - tgt.ys[j0]) / Math.max(tgt.ys[j1] - tgt.ys[j0], 1e-12);
    const zx0 = z[j0][b0] + (z[j0][b1] - z[j0][b0]) * tb;
    const zx1 = z[j1][b0] + (z[j1][b1] - z[j1][b0]) * tb;
    return zx0 + (zx1 - zx0) * tj;
  }
  // положение точки цены: X=R, Y=прогресс к развязке (близость к барьеру),
  // Z=высота поверхности там; плюс след по поверхности от «сейчас» до точки.
  function dotCoords(rRaw) {
    const T = tgt.T;
    const Rp = Math.max(-1, Math.min(T, rRaw == null ? tgt.r0 : rRaw));
    const u = (Rp + 1) / (T + 1);                 // 0 у стопа, 1 у тейка
    const near = Math.min(u, 1 - u);              // 0..0.5 — до ближнего барьера
    const prog = Math.max(0, Math.min(1, 1 - near / 0.5));
    const yFrac = 0.08 + 0.86 * prog;             // центр→фронт, у барьера→глубоко
    const K = 12, tx = [], ty = [], tz = [];
    for (let i = 0; i <= K; i++) {
      const yf = yFrac * (i / K);
      tx.push(Rp); ty.push(yf); tz.push(surfZ(Rp, yf) + 0.004);
    }
    return { Rp, yFrac, dotZ: surfZ(Rp, yFrac) + 0.006, tx, ty, tz };
  }
  function wallVZ(series) {                        // интерливленый z для mesh3d
    const out = [];
    for (let j = 0; j < series.length; j++) { out.push(0, series[j]); }
    return out;
  }
  function wallMesh(xConst, series, color) {
    const vx = [], vy = [], vz = [], I = [], J = [], K = [], ys = tgt.ys, nS = tgt.nS;
    for (let j = 0; j < nS; j++) { vx.push(xConst, xConst); vy.push(ys[j], ys[j]); vz.push(-0.02, series[j]); }
    for (let j = 0; j < nS - 1; j++) {
      const b0 = 2 * j, t0 = 2 * j + 1, b1 = 2 * j + 2, t1 = 2 * j + 3;
      I.push(b0, t0); J.push(t0, b1); K.push(b1, t1);
    }
    return { type: 'mesh3d', x: vx, y: vy, z: vz, i: I, j: J, k: K,
             color, opacity: 0.35, flatshading: true, hoverinfo: 'skip', showlegend: false };
  }
  function wallEdge(xConst, series, color, label) {
    const pLabel = tgt.probabilityAvailable
      ? `${label} TOUCH≤H ${fmtProb(series[series.length - 1])}`
      : `${label} · БАРЬЕР`;
    const hover = tgt.probabilityAvailable
      ? `${label}: first-touch к опционному горизонту = %{z:.1%}<extra></extra>`
      : `${label}: доля сценарных путей у барьера = %{z:.0%}<extra></extra>`;
    return { type: 'scatter3d', mode: 'lines',
      x: Array(tgt.nS).fill(xConst), y: tgt.ys, z: series,
      line: { color, width: 6 }, name: pLabel, hovertemplate: hover };
  }

  // -------------------------------------------------------------- цель/каркас
  function smooth(row, passes = 2) {
    let out = row.slice();
    for (let k = 0; k < passes; k++) {
      out = out.map((v, i) =>
        0.18 * (out[i - 2] ?? out[i - 1] ?? v)
        + 0.24 * (out[i - 1] ?? v)
        + 0.36 * v
        + 0.16 * (out[i + 1] ?? v)
        + 0.06 * (out[i + 2] ?? out[i + 1] ?? v));
    }
    return out;
  }
  function normalize(row) {
    const clean = row.map((v) => Number.isFinite(v) && v > 0 ? v : 0);
    const total = clean.reduce((a, b) => a + b, 0);
    return total > 1e-12 ? clean.map((v) => v / total)
      : clean.map(() => 1 / Math.max(clean.length, 1));
  }
  function interp1(xs, ys, x) {
    if (!xs.length) return 0;
    if (x <= xs[0]) return ys[0] || 0;
    if (x >= xs[xs.length - 1]) return ys[ys.length - 1] || 0;
    let hi = 1;
    while (hi < xs.length && xs[hi] < x) hi++;
    const lo = hi - 1;
    const f = (x - xs[lo]) / Math.max(xs[hi] - xs[lo], 1e-12);
    return (ys[lo] || 0) + ((ys[hi] || 0) - (ys[lo] || 0)) * f;
  }
  function terminalShape(cone, xs) {
    const probs = Array.isArray(cone.market_terminal) ? cone.market_terminal.slice() : null;
    const edges = Array.isArray(cone.market_edges) ? cone.market_edges : null;
    if (!probs || !edges || edges.length !== probs.length + 1) return null;
    // Tail mass is already represented by the red/green barrier walls. Remove it
    // from the endpoint buckets before using the option RND inside the corridor.
    probs[0] = Math.max(0, probs[0] - Number(cone.market_p_stop || 0));
    probs[probs.length - 1] = Math.max(
      0, probs[probs.length - 1] - Number(cone.market_p_take || 0));
    const mids = probs.map((_, i) => (edges[i] + edges[i + 1]) / 2);
    const widths = probs.map((_, i) => Math.max(edges[i + 1] - edges[i], 1e-9));
    const dens = probs.map((p, i) => p / widths[i]);
    return normalize(smooth(xs.map((x) => interp1(mids, dens, x)), 3));
  }
  function analyticConditional(cone, xs, t, binW) {
    // Continuous killed-diffusion approximation. The sine term is the
    // survival eigenfunction between absorbing barriers; it prevents fake
    // endpoint spikes when Monte Carlo survivors become sparse.
    const mu = cone.r0 + Number(cone.drift_R || 0) * t;
    const sd = Math.max(binW * 0.70, Number(cone.sigma_R || 1) * Math.sqrt(Math.max(t, 0.002)));
    const skew = Math.max(-0.45, Math.min(0.45, Number(cone.skew || 0)));
    const raw = xs.map((x) => {
      const sideScale = x < mu ? 1 + Math.max(skew, 0) : 1 + Math.max(-skew, 0);
      const g = Math.exp(-0.5 * ((x - mu) / (sd * sideScale)) ** 2);
      const u = Math.max(1e-4, Math.min(1 - 1e-4, (x + 1) / (cone.T + 1)));
      return g * Math.pow(Math.sin(Math.PI * u), 0.45);
    });
    return normalize(raw);
  }
  function quantileX(row, xs, q) {
    let c = 0;
    for (let i = 0; i < row.length; i++) {
      c += row[i];
      if (c >= q) return xs[i];
    }
    return xs[xs.length - 1];
  }
  function buildTarget(cone) {
    const T = cone.T;
    const edges = cone.edges, nB = edges.length - 1;
    const xs = Array.from({ length: nB }, (_, b) => (edges[b] + edges[b + 1]) / 2);
    const binW = (T + 1) / Math.max(nB, 1);
    const tf = cone.times_frac || cone.density.map((_, j) => (j + 1) / cone.density.length);
    const ys = [0, ...tf];
    const rawRows = [
      analyticConditional(cone, xs, 0.001, binW),
      ...cone.density.map((row) => row.slice()),
    ];
    const survival = [1, ...cone.density.map((row) =>
      Math.max(0, Math.min(1, row.reduce((a, b) => a + Number(b || 0), 0))))];
    const optShape = terminalShape(cone, xs);
    const conditional = rawRows.map((raw, j) => {
      const t = j === 0 ? 0 : tf[j - 1];
      const prior = analyticConditional(cone, xs, Math.max(t, 0.001), binW);
      const mass = survival[j];
      const empirical = normalize(smooth(raw, 2));
      // Below ~4% surviving mass the MC histogram is too sparse. Blend toward
      // the continuous killed-diffusion density instead of showing sampling
      // spikes or an empty plane.
      const mcConfidence = j === 0 ? 0 : Math.max(0, Math.min(1, mass / 0.04));
      let shape = normalize(empirical.map((v, i) =>
        mcConfidence * v + (1 - mcConfidence) * prior[i]));
      // The delayed option snapshot anchors the shape, while every live tick
      // still moves the current-price point. It never replaces barrier masses.
      if (optShape && j > 0) {
        const optionWeight = 0.55 * Math.pow(Math.max(t, 0), 0.80);
        shape = normalize(shape.map((v, i) =>
          (1 - optionWeight) * v + optionWeight * optShape[i]));
      }
      return shape;
    });
    // Geometry is a CONDITIONAL density surface. Survival is deliberately not
    // multiplied into Z: it belongs on the stop/take walls. Scale every time
    // row by one common peak. Scaling each row to its own peak made every slice
    // equally tall and turned the widening distribution into a curved awning.
    // Geometry is a CONDITIONAL density surface.
    const floor = 0.0; // Set to 0.0 to fix the gap between surface and the floor axis.
    const globalPeak = Math.max(...conditional.flatMap((row) => row), 1e-12);
    const z = conditional.map((row) => {
      return row.map((v) =>
        floor + (1 - floor) * Math.pow(Math.min(1, v / globalPeak), 0.62));
    });
    const nS = conditional.length;
    const stopPath = cone.p_stop_by_t;
    const takePath = cone.p_take_by_t;
    tgt.z = z; tgt.pStop = [0, ...stopPath]; tgt.pTake = [0, ...takePath];
    tgt.xs = xs; tgt.ys = ys; tgt.edges = edges; tgt.T = T; tgt.r0 = cone.r0;
    tgt.nS = nS; tgt.nB = nB; tgt.hy = cone.horizon_years;
    tgt.median = cone.median_years; tgt.term_slope = cone.term_slope || 0;
    tgt.touchTake = cone.p_take;
    tgt.touchStop = cone.p_stop;
    tgt.noTouch = cone.unresolved;
    tgt.conditional = conditional; tgt.survival = survival;
    tgt.modeX = conditional.map((row) => xs[row.indexOf(Math.max(...row))]);
    tgt.q20X = conditional.map((row) => quantileX(row, xs, 0.20));
    tgt.q80X = conditional.map((row) => quantileX(row, xs, 0.80));
    tgt.probabilityAvailable = !!(cone.option_anchored && cone.probability_available !== false);
    tgt.structSig = `${nB}|${nS}|${(+T).toFixed(2)}|${tgt.probabilityAvailable ? 1 : 0}`;
  }
  function snapDisp() {                            // отобразить цель немедленно (пересбор)
    disp.z = tgt.z.map((row) => row.slice());
    disp.pStop = tgt.pStop.slice();
    disp.pTake = tgt.pTake.slice();
  }

  function buildTraces() {
    const customdata = tgt.conditional.map((row, j) =>
      row.map((p) => [p, tgt.survival[j]]));
    const surface = { type: 'surface', x: tgt.xs, y: tgt.ys, z: disp.z,
      colorscale: SURF_SCALE, showscale: false, opacity: 1.0, name: 'плотность',
      customdata,
      contours: {
        x: { show: true, color: 'rgba(255,255,255,0.15)', width: 1, project: { x: true } },
        y: { show: true, color: 'rgba(255,255,255,0.15)', width: 1, project: { y: true } },
        z: { show: true, usecolormap: true, width: 2, project: { z: true } },
      },
      lighting: { ambient: 0.8, diffuse: 0.6, specular: 0.1, roughness: 0.7 },
      hovertemplate: 'R=%{x:+.2f}<br>RND=%{z:.2f}<extra></extra>' };
    const ridge = (xs, color, width, name, showlegend) => ({
      type: 'scatter3d', mode: 'lines', x: xs, y: tgt.ys,
      z: xs.map((x, j) => surfZ(x, tgt.ys[j]) + 0.008),
      line: { color, width }, name, showlegend, hoverinfo: 'skip',
    });
    const d = dotCoords(live.r != null ? live.r : tgt.r0);
    curR = d.Rp; lastDotR = d.Rp;
    const trail = { type: 'scatter3d', mode: 'lines', x: d.tx, y: d.ty, z: d.tz,
      line: { color: ORANGE, width: 4 }, name: 'цена → развязка',
      hoverinfo: 'skip', showlegend: false };
    const ball = { type: 'scatter3d', mode: 'markers', x: [d.Rp], y: [d.yFrac], z: [d.dotZ],
      marker: { size: 7, color: ORANGE, line: { color: '#fff', width: 1 } },
      name: 'цена (r)', hovertemplate: 'цена r=%{x:+.2f}<br>прогресс к развязке=%{y:.0%}<extra></extra>' };
    return [surface,
      wallMesh(tgt.xs[0], disp.pStop, RED), wallMesh(tgt.xs[tgt.xs.length - 1], disp.pTake, GREEN),
      wallEdge(tgt.xs[0], disp.pStop, RED, 'СТОП'), wallEdge(tgt.xs[tgt.xs.length - 1], disp.pTake, GREEN, 'ТЕЙК'),
      ridge(tgt.modeX, INK, 3, 'OPTION MODE', true),
      ridge(tgt.q20X, 'rgba(20,20,15,0.35)', 2, 'Q20', false),
      ridge(tgt.q80X, 'rgba(20,20,15,0.35)', 2, 'Q80', false),
      trail, ball];
  }

  function layoutFor() {
    const hy = tgt.hy, T = tgt.T;
    const termNote = tgt.term_slope > 0.03 ? ' · контанго (вола дышит позже)'
      : tgt.term_slope < -0.03 ? ' · бэквордация (движение скоро)' : '';
    const medText = tgt.median != null ? `медиана ≈ ${fmtTime(tgt.median)}`
      : hy ? `медиана касания > ${fmtTime(hy)}` : 'медиана н/д';
    const yTitle = (hy ? `ВРЕМЯ → развязка · ${medText}`
      : 'ВРЕМЯ → развязка (модельное)') + termNote;
    const yTicktext = hy ? ['сейчас', fmtTime(hy * 0.5), fmtTime(hy)]
      : ['сейчас', '50%', 'развязка'];
    lastYTitle = yTitle;
    return {
      autosize: true, height: 430, margin: { l: 0, r: 0, t: 8, b: 0 },
      uirevision: 'probability-cone-ui-v3',
      paper_bgcolor: PAPER, font: { family: FONT, color: INK, size: 11 },
      showlegend: true,
      legend: { orientation: 'h', x: 0, y: 1.07, font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      scene: {
        uirevision: 'probability-cone-camera-v3',
        dragmode: 'orbit',
        bgcolor: SCENE_BG, aspectmode: 'manual', aspectratio: { x: 1.72, y: 1.38, z: 0.76 },
        xaxis: { title: { text: 'R  (стоп −1 · 0 · тейк)', font: { size: 10, color: DIM } },
          range: [-1, T], gridcolor: RULE, zerolinecolor: RULE,
          tickvals: [-1, 0, T], ticktext: ['СТОП −1R', '0', `ТЕЙК +${T.toFixed(1)}R`],
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: false },
        yaxis: { title: { text: yTitle, font: { size: 10, color: DIM } },
          range: [0, 1], gridcolor: RULE, tickvals: [0, 0.5, 1], ticktext: yTicktext,
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: false },
        zaxis: { title: { text: 'условная RND · форма', font: { size: 10, color: DIM } },
          range: [0, 1.05], autorange: false, tickfont: { size: 9, color: DIM },
          backgroundcolor: SCENE_BG, showbackground: false, showgrid: false, zeroline: false },
      },
      annotations: [{
        showarrow: false, x: 0.05, y: 0.95, xref: 'paper', yref: 'paper',
        text: 'Загрузка EV...', font: { size: 12, color: ORANGE, family: FONT },
        bgcolor: 'rgba(20,20,15,0.8)', bordercolor: ORANGE, borderwidth: 1, borderpad: 6
      }]
    };
  }

  function render() {                              // полный (пере)сбор — редко
    const P = window.Plotly;
    const config = { responsive: true, displaylogo: false,
      modeBarButtonsToRemove: ['toImage'], doubleClick: 'reset', scrollZoom: true };
    const layout = layoutFor();
    const traces = buildTraces();
    lastNames = [traces[EDGE_STOP].name, traces[EDGE_TAKE].name];
    const finalStopP = disp.pStop[disp.pStop.length - 1];
    const finalTakeP = disp.pTake[disp.pTake.length - 1];
    const liveEV = (tgt.touchTake * tgt.T) - tgt.touchStop;
    const evColor = liveEV >= 0 ? '#2EB44F' : '#E64650';
    const evText = `<b>FIRST-TOUCH≤H</b><br>`
      + `ТЕЙК ${fmtProb(finalTakeP)} · СТОП ${fmtProb(finalStopP)} · NO-TOUCH ${fmtProb(tgt.noTouch)}<br>`
      + `BARRIER EV≤H: ${liveEV > 0 ? '+' : ''}${liveEV.toFixed(2)}R`;
    layout.annotations[0].text = evText;
    layout.annotations[0].font.color = evColor;
    layout.annotations[0].bordercolor = evColor;
    
    if (hasPlot && el._fullLayout && el._fullLayout.scene && el._fullLayout.scene.camera) {
      layout.scene.camera = JSON.parse(JSON.stringify(el._fullLayout.scene.camera));
    } else {
      layout.scene.camera = INIT_CAM;
    }

    if (!hasPlot) {
      P.newPlot(el, traces, layout, config);
      hasPlot = true; attachListeners();
    } else {
      P.react(el, traces, layout, config);
    }
    structSig = tgt.structSig;
  }

  // ---------------------------------------------------------- морф (покадрово)
  function easeGridToward(dt) {
    if (!disp.z || !tgt.z || disp.z.length !== tgt.z.length
        || disp.z[0].length !== tgt.z[0].length) { snapDisp(); return true; }
    const k = 1 - Math.exp(-7 * dt);
    let maxd = 0;
    for (let j = 0; j < tgt.z.length; j++) {
      const dr = disp.z[j], tr = tgt.z[j];
      for (let b = 0; b < tr.length; b++) {
        const nd = dr[b] + (tr[b] - dr[b]) * k;
        const dd = Math.abs(nd - dr[b]); if (dd > maxd) maxd = dd; dr[b] = nd;
      }
    }
    for (let j = 0; j < tgt.pStop.length; j++) {
      const ns = disp.pStop[j] + (tgt.pStop[j] - disp.pStop[j]) * k;
      const nt = disp.pTake[j] + (tgt.pTake[j] - disp.pTake[j]) * k;
      maxd = Math.max(maxd, Math.abs(ns - disp.pStop[j]), Math.abs(nt - disp.pTake[j]));
      disp.pStop[j] = ns; disp.pTake[j] = nt;
    }
    return maxd > 1e-4;
  }
  function applyMorph() {
    window.Plotly.restyle(el,
      { z: [disp.z, wallVZ(disp.pStop), wallVZ(disp.pTake), disp.pStop, disp.pTake] },
      [SURF, MESH_STOP, MESH_TAKE, EDGE_STOP, EDGE_TAKE]);
  }
  function applyDot() {
    const d = dotCoords(curR);
    window.Plotly.restyle(el,
      { x: [d.tx, [d.Rp]], y: [d.ty, [d.yFrac]], z: [d.tz, [d.dotZ]] },
      [TRAIL, BALL]);
  }
  // редкое обновление «хрома» (заголовок оси = медиана, легенда = проценты) без
  // пересбора; камера пиннится, чтобы relayout не сбросил вид.
  function updateChrome() {
    const P = window.Plotly;
    const termNote = tgt.term_slope > 0.03 ? ' · контанго (вола дышит позже)'
      : tgt.term_slope < -0.03 ? ' · бэквордация (движение скоро)' : '';
    const medText = tgt.median != null ? `медиана ≈ ${fmtTime(tgt.median)}`
      : tgt.hy ? `медиана касания > ${fmtTime(tgt.hy)}` : 'медиана н/д';
    const yTitle = (tgt.hy ? `ВРЕМЯ → развязка · ${medText}`
      : 'ВРЕМЯ → развязка (модельное)') + termNote;
      
    const finalStopP = disp.pStop[disp.pStop.length - 1] || 0;
    const finalTakeP = disp.pTake[disp.pTake.length - 1] || 0;
    const liveEV = (tgt.touchTake * tgt.T) - tgt.touchStop;
    const evColor = liveEV >= 0 ? '#2EB44F' : '#E64650';
    const evText = tgt.probabilityAvailable 
        ? `<b>FIRST-TOUCH≤H</b><br>`
          + `ТЕЙК ${fmtProb(finalTakeP)} · СТОП ${fmtProb(finalStopP)} · NO-TOUCH ${fmtProb(tgt.noTouch)}<br>`
          + `BARRIER EV≤H: ${liveEV > 0 ? '+' : ''}${liveEV.toFixed(2)}R`
        : `<b>LIVE EV: НЕТ ОПЦИОНОВ</b><br>P(Take): — | P(Stop): —`;
        
    let relayoutData = {};
    if (yTitle !== lastYTitle) {
      lastYTitle = yTitle;
      const yTicktext = tgt.hy ? ['сейчас', fmtTime(tgt.hy * 0.5), fmtTime(tgt.hy)]
        : ['сейчас', '50%', 'развязка'];
      relayoutData['scene.yaxis.title.text'] = yTitle;
      relayoutData['scene.yaxis.ticktext'] = yTicktext;
    }
    relayoutData['annotations[0].text'] = evText;
    relayoutData['annotations[0].font.color'] = evColor;
    relayoutData['annotations[0].bordercolor'] = evColor;
    P.relayout(el, relayoutData);
    
    const nStop = tgt.probabilityAvailable
      ? `СТОП TOUCH≤H ${fmtProb(tgt.pStop[tgt.pStop.length - 1])}`
      : 'СТОП · БАРЬЕР';
    const nTake = tgt.probabilityAvailable
      ? `ТЕЙК TOUCH≤H ${fmtProb(tgt.pTake[tgt.pTake.length - 1])}`
      : 'ТЕЙК · БАРЬЕР';
    if (!lastNames || nStop !== lastNames[0] || nTake !== lastNames[1]) {
      lastNames = [nStop, nTake];
      P.restyle(el, { name: [nStop, nTake] }, [EDGE_STOP, EDGE_TAKE]);
    }
    P.restyle(el, { customdata: [tgt.conditional.map((row, j) =>
      row.map((p) => [p, tgt.survival[j]]))] }, [SURF]);
    const railZ = (xs) => xs.map((x, j) => surfZ(x, tgt.ys[j]) + 0.008);
    P.restyle(el, {
      x: [tgt.modeX, tgt.q20X, tgt.q80X],
      y: [tgt.ys, tgt.ys, tgt.ys],
      z: [railZ(tgt.modeX), railZ(tgt.q20X), railZ(tgt.q80X)],
    }, [MODE, Q20, Q80]);
  }

  // ------------------------------------------------------------- публичное API
  function setData(cone, extra) {
    if (extra) Object.assign(live, extra);
    if (!ready()) return;
    if (!cone || !cone.available) return;         // НЕ рушим сцену (оверлей закрывает)
    buildTarget(cone);
    if (!hasPlot) { snapDisp(); render(); return; }
    if (tgt.structSig !== structSig) {            // другой каркас — полный пересбор
      if (interacting) { pendingStruct = true; return; }
      snapDisp(); render(); return;
    }
    if (interacting) return;
    updateChrome();                                // тот же каркас — морф в loop
  }
  function updateLive(p) { if (p) Object.assign(live, p); }

  // ------------------------------------------------------------- цикл 60fps
  let last = performance.now();
  function frame(now) {
    requestAnimationFrame(frame);
    if (!ready() || !hasPlot) { last = now; return; }
    const dt = Math.min((now - last) / 1000, 0.05); last = now;
    if (interacting) return;                        // во время вращения сцену не трогаем
    const gridChanged = easeGridToward(dt);
    if (gridChanged) applyMorph();
    const target = live.r != null ? live.r : tgt.r0;
    const nR = approach(curR, target, dt, 6);
    if (curR == null) { curR = nR; applyDot(); }
    else if (gridChanged || Math.abs(nR - (lastDotR == null ? -999 : lastDotR)) > 0.001) {
      curR = nR; lastDotR = nR; applyDot();
    } else { curR = nR; }
  }
  requestAnimationFrame(frame);

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => { 
      if (ready() && hasPlot) {
        if (el._fullLayout && el._fullLayout.scene && el._fullLayout.scene.camera) {
          if (!el.layout) el.layout = {};
          if (!el.layout.scene) el.layout.scene = {};
          el.layout.scene.camera = JSON.parse(JSON.stringify(el._fullLayout.scene.camera));
        }
        window.Plotly.Plots.resize(el);
      }
    });
  }
  return { setData, updateLive };
}
