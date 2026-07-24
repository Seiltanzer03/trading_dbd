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
const SURF_SCALE = [[0, SCENE_BG], [0.35, '#F3C4A6'], [0.7, '#EE8A54'], [1, ORANGE]];

// индексы трасс (порядок фиксирован в buildTraces)
const SURF = 0, MESH_STOP = 1, MESH_TAKE = 2, EDGE_STOP = 3, EDGE_TAKE = 4,
      TRAIL = 5, BALL = 6;

function fmtTime(years) {
  if (years == null) return '—';
  const min = years * 365 * 24 * 60;
  if (min < 1) return '<1 мин';
  if (min < 90) return `${Math.round(min)} мин`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)} ч`;
  return `${(h / 24).toFixed(1)} дн`;
}

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let hasPlot = false, listenersOn = false;
  let structSig = null, pendingStruct = false;
  let curR = null, lastDotR = null;
  let interacting = false, interactTimer = null;
  let lastYTitle = null, lastNames = null;
  const live = { r: null };

  // цель (из сервера) и отображаемое (плавно морфится к цели)
  const tgt = { z: null, pStop: null, pTake: null, xs: null, ys: null,
                edges: null, T: 2.5, r0: 0, nS: 0, nB: 0, hy: null,
                median: null, term_slope: 0, structSig: null };
  const disp = { z: null, pStop: null, pTake: null };

  const INIT_CAM = { eye: { x: 0.15, y: -2.25, z: 0.72 }, up: { x: 0, y: 0, z: 1 } };
  // ручное сохранение поворота: ставим на каждый (редкий) пересбор, чтобы вид не
  // отскакивал; при морфе через restyle камера и так не трогается.
  let currentCam = JSON.parse(JSON.stringify(INIT_CAM));

  const ready = () => typeof window !== 'undefined' && window.Plotly && el;

  // ---------------------------------------------------- взаимодействие/камера
  function markInteract() {
    interacting = true;
    if (interactTimer) clearTimeout(interactTimer);
    interactTimer = setTimeout(() => { interacting = false; flush(); }, 250);
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
    const jc = Math.max(0, Math.min(1, yf)) * (nS - 1);
    const j0 = Math.max(0, Math.min(nS - 1, Math.floor(jc)));
    const j1 = Math.max(0, Math.min(nS - 1, j0 + 1));
    const tj = jc - j0;
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
      tx.push(Rp); ty.push(yf); tz.push(surfZ(Rp, yf) + 0.012);
    }
    return { Rp, yFrac, dotZ: surfZ(Rp, yFrac) + 0.02, tx, ty, tz };
  }
  function wallVZ(series) {                        // интерливленый z для mesh3d
    const out = [];
    for (let j = 0; j < series.length; j++) { out.push(0, series[j]); }
    return out;
  }
  function wallMesh(xConst, series, color) {
    const vx = [], vy = [], vz = [], I = [], J = [], K = [], ys = tgt.ys, nS = tgt.nS;
    for (let j = 0; j < nS; j++) { vx.push(xConst, xConst); vy.push(ys[j], ys[j]); vz.push(0, series[j]); }
    for (let j = 0; j < nS - 1; j++) {
      const b0 = 2 * j, t0 = 2 * j + 1, b1 = 2 * j + 2, t1 = 2 * j + 3;
      I.push(b0, t0); J.push(t0, b1); K.push(b1, t1);
    }
    return { type: 'mesh3d', x: vx, y: vy, z: vz, i: I, j: J, k: K,
             color, opacity: 0.35, flatshading: true, hoverinfo: 'skip', showlegend: false };
  }
  function wallEdge(xConst, series, color, label) {
    return { type: 'scatter3d', mode: 'lines',
      x: Array(tgt.nS).fill(xConst), y: tgt.ys, z: series,
      line: { color, width: 6 }, name: `${label} ${(series[series.length - 1] * 100).toFixed(0)}%`,
      hovertemplate: `${label}: дойти = %{z:.0%}<extra></extra>` };
  }

  // -------------------------------------------------------------- цель/каркас
  function buildTarget(cone) {
    const T = cone.T;
    const edges = cone.edges, nB = edges.length - 1, nS = cone.density.length;
    const xs = Array.from({ length: nB }, (_, b) => (edges[b] + edges[b + 1]) / 2);
    const ys = Array.from({ length: nS }, (_, j) => j / (nS - 1));
    let gmax = 1e-9;
    for (const row of cone.density) for (const v of row) if (v > gmax) gmax = v;
    const z = cone.density.map((row) => row.map((v) => Math.pow(v / gmax, 0.7)));
    tgt.z = z; tgt.pStop = cone.p_stop_by_t.slice(); tgt.pTake = cone.p_take_by_t.slice();
    tgt.xs = xs; tgt.ys = ys; tgt.edges = edges; tgt.T = T; tgt.r0 = cone.r0;
    tgt.nS = nS; tgt.nB = nB; tgt.hy = cone.horizon_years;
    tgt.median = cone.median_years; tgt.term_slope = cone.term_slope || 0;
    tgt.structSig = `${nB}|${nS}|${(+T).toFixed(2)}`;
  }
  function snapDisp() {                            // отобразить цель немедленно (пересбор)
    disp.z = tgt.z.map((row) => row.slice());
    disp.pStop = tgt.pStop.slice();
    disp.pTake = tgt.pTake.slice();
  }

  function buildTraces() {
    const surface = { type: 'surface', x: tgt.xs, y: tgt.ys, z: disp.z,
      colorscale: SURF_SCALE, showscale: false, opacity: 0.95, name: 'плотность',
      contours: { z: { show: true, usecolormap: true, width: 1 } },
      lighting: { ambient: 0.78, diffuse: 0.5, specular: 0.06, roughness: 0.9 },
      hovertemplate: 'R=%{x:+.2f}<br>плотн.=%{z:.2f}<extra></extra>' };
    const d = dotCoords(live.r != null ? live.r : tgt.r0);
    curR = d.Rp; lastDotR = d.Rp;
    const trail = { type: 'scatter3d', mode: 'lines', x: d.tx, y: d.ty, z: d.tz,
      line: { color: ORANGE, width: 4 }, name: 'цена → развязка',
      hoverinfo: 'skip', showlegend: false };
    const ball = { type: 'scatter3d', mode: 'markers', x: [d.Rp], y: [d.yFrac], z: [d.dotZ],
      marker: { size: 7, color: ORANGE, line: { color: '#fff', width: 1 } },
      name: 'цена (r)', hovertemplate: 'цена r=%{x:+.2f}<br>прогресс к развязке=%{y:.0%}<extra></extra>' };
    return [surface,
      wallMesh(-1, disp.pStop, RED), wallMesh(tgt.T, disp.pTake, GREEN),
      wallEdge(-1, disp.pStop, RED, 'СТОП'), wallEdge(tgt.T, disp.pTake, GREEN, 'ТЕЙК'),
      trail, ball];
  }

  function layoutFor() {
    const hy = tgt.hy, T = tgt.T;
    const termNote = tgt.term_slope > 0.03 ? ' · контанго (вола дышит позже)'
      : tgt.term_slope < -0.03 ? ' · бэквордация (движение скоро)' : '';
    const yTitle = (hy ? `ВРЕМЯ → развязка · медиана ≈ ${fmtTime(tgt.median)}`
      : 'ВРЕМЯ → развязка (модельное)') + termNote;
    const yTicktext = hy ? ['сейчас', fmtTime(hy * 0.5), fmtTime(hy)]
      : ['сейчас', '50%', 'развязка'];
    lastYTitle = yTitle;
    return {
      autosize: true, height: 430, margin: { l: 0, r: 0, t: 8, b: 0 },
      paper_bgcolor: PAPER, font: { family: FONT, color: INK, size: 11 },
      showlegend: true,
      legend: { orientation: 'h', x: 0, y: 1.07, font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      scene: {
        camera: currentCam,
        bgcolor: SCENE_BG, aspectmode: 'manual', aspectratio: { x: 1.75, y: 1.2, z: 0.72 },
        xaxis: { title: { text: 'R  (стоп −1 · 0 · тейк)', font: { size: 10, color: DIM } },
          range: [-1, T], gridcolor: RULE, zerolinecolor: RULE,
          tickvals: [-1, 0, T], ticktext: ['СТОП −1R', '0', `ТЕЙК +${T.toFixed(1)}R`],
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: true },
        yaxis: { title: { text: yTitle, font: { size: 10, color: DIM } },
          range: [0, 1], gridcolor: RULE, tickvals: [0, 0.5, 1], ticktext: yTicktext,
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: true },
        zaxis: { title: { text: 'плотность / P дойти', font: { size: 10, color: DIM } },
          range: [0, 1.08], gridcolor: RULE, tickfont: { size: 9, color: DIM },
          backgroundcolor: SCENE_BG, showbackground: true },
      },
    };
  }

  function render() {                              // полный (пере)сбор — редко
    const P = window.Plotly;
    const config = { responsive: true, displaylogo: false,
      modeBarButtonsToRemove: ['toImage'], doubleClick: 'reset' };
    if (hasPlot) grabCam();
    const layout = layoutFor();
    layout.scene.camera = currentCam;
    const traces = buildTraces();
    lastNames = [traces[EDGE_STOP].name, traces[EDGE_TAKE].name];
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
    window.Plotly.restyle(el, { x: [d.tx, [d.Rp]], y: [d.ty, [d.yFrac]], z: [d.tz, [d.dotZ]] },
      [TRAIL, BALL]);
  }
  // редкое обновление «хрома» (заголовок оси = медиана, легенда = проценты) без
  // пересбора; камера пиннится, чтобы relayout не сбросил вид.
  function updateChrome() {
    const P = window.Plotly;
    const termNote = tgt.term_slope > 0.03 ? ' · контанго (вола дышит позже)'
      : tgt.term_slope < -0.03 ? ' · бэквордация (движение скоро)' : '';
    const yTitle = (tgt.hy ? `ВРЕМЯ → развязка · медиана ≈ ${fmtTime(tgt.median)}`
      : 'ВРЕМЯ → развязка (модельное)') + termNote;
    if (yTitle !== lastYTitle) {
      lastYTitle = yTitle;
      const yTicktext = tgt.hy ? ['сейчас', fmtTime(tgt.hy * 0.5), fmtTime(tgt.hy)]
        : ['сейчас', '50%', 'развязка'];
      grabCam();
      P.relayout(el, { 'scene.yaxis.title.text': yTitle, 'scene.yaxis.ticktext': yTicktext,
                       'scene.camera': currentCam });
    }
    const nStop = `СТОП ${(tgt.pStop[tgt.pStop.length - 1] * 100).toFixed(0)}%`;
    const nTake = `ТЕЙК ${(tgt.pTake[tgt.pTake.length - 1] * 100).toFixed(0)}%`;
    if (!lastNames || nStop !== lastNames[0] || nTake !== lastNames[1]) {
      lastNames = [nStop, nTake];
      P.restyle(el, { name: [nStop, nTake] }, [EDGE_STOP, EDGE_TAKE]);
    }
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
    window.addEventListener('resize', () => { if (ready() && hasPlot) window.Plotly.Plots.resize(el); });
  }
  return { setData, updateLive };
}
