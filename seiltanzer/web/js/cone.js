// Probability Cone — НАСТОЯЩИЙ 3D (WebGL через Plotly gl3d).
//
// Сцену можно вращать мышкой, зумить колесом, наводить курсор для точных значений.
// Поверхность = плотность вероятности исхода сделки (PDF surface): X = R (стоп −1 ·
// 0 · тейк +T), Y = ВРЕМЯ (к развязке), Z = плотность живых (ещё не поглощённых)
// путей. Две СТЕНЫ-барьера (красная СТОП / зелёная ТЕЙК) — Mesh3d-ribbon'ы, высота
// которых по времени = накопленная вероятность дойти; у дальней грани = P(стоп)/
// P(тейк). Оранжевый луч — текущая цена (r); тёмный пунктир на дальней грани —
// плотность РЫНКА на экспирации.
//
// ВАЖНО про плавность вращения: пока пользователь крутит/зумит сцену, мы НЕ трогаем
// её (никаких react/restyle) — иначе перерисовка сбивает захват мыши и «сбрасывает»
// вид. Обновления копятся и применяются, когда взаимодействие затихло (debounce).

const PAPER = '#FFFFFF', SCENE_BG = '#FBFAF6', INK = '#14140F', RULE = '#D8D5CC';
const DIM = '#8A877D', ORANGE = '#E8622A', RED = '#C6373C', GREEN = '#2E7D4F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
const SURF_SCALE = [[0, SCENE_BG], [0.35, '#F3C4A6'], [0.7, '#EE8A54'], [1, ORANGE]];

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let hasPlot = false, listenersOn = false;
  let sig = null, pendingCone = null, pendingSig = null;
  let beamIdx = null, lastBeamR = null;
  let curT = 2.5;
  // камеру задаём ЯВНО на каждом рендере (react без camera сбрасывает её в дефолт).
  // Стартовый вид — спереди: СТОП слева · ТЕЙК справа · время вдаль. Поворот
  // пользователя перехватываем из события plotly_relayout и храним тут.
  let currentCam = { eye: { x: 0.15, y: -2.25, z: 0.72 }, up: { x: 0, y: 0, z: 1 } };
  // взаимодействие (вращение/зум): пока идёт — сцену не перестраиваем
  let interacting = false, interactTimer = null;
  const live = { r: null };

  const ready = () => typeof window !== 'undefined' && window.Plotly && el;
  const clampR = (r) => Math.max(-1, Math.min(curT, r));
  const coarseSig = (c) =>
    `${Math.round(c.r0 * 20)}|${Math.round(c.p_take * 50)}|${Math.round(c.p_stop * 50)}`
    + `|${c.times.length}|${(+c.T).toFixed(2)}|${!!c.market_terminal}`;

  function markInteract() {
    interacting = true;
    if (interactTimer) clearTimeout(interactTimer);
    interactTimer = setTimeout(() => { interacting = false; flush(); }, 350);
  }

  function attachListeners() {
    if (listenersOn || !el.on) return;
    listenersOn = true;
    // финальная камера после поворота/зума — сохраняем, чтобы вид не «прыгал»
    el.on('plotly_relayout', (d) => {
      const cam = (d && d['scene.camera']) || el._fullLayout?.scene?.camera;
      if (cam) currentCam = cam;
    });
    el.on('plotly_relayouting', markInteract);
    el.addEventListener('mousedown', markInteract);
    el.addEventListener('touchstart', markInteract, { passive: true });
    el.addEventListener('wheel', markInteract, { passive: true });
  }

  function flush() {
    if (!ready() || !hasPlot) return;
    if (pendingCone) { sig = pendingSig; const c = pendingCone; pendingCone = null; render(c); }
    updateBeam();
  }

  function setData(cone, extra) {
    if (extra) Object.assign(live, extra);
    if (!ready()) return;
    if (!cone || !cone.available) {
      if (hasPlot) { window.Plotly.purge(el); hasPlot = false; sig = null; }
      return;
    }
    const s = coarseSig(cone);
    if (s === sig && hasPlot) return;         // форма не изменилась
    if (interacting) { pendingCone = cone; pendingSig = s; return; }  // не мешаем вращать
    sig = s; pendingCone = null;
    render(cone);
  }

  // луч цены (r) двигается каждый тик — дёшево (restyle одного трейса), но НЕ во
  // время вращения (иначе перерисовка сбивает захват мыши)
  function updateLive(p) {
    if (p) Object.assign(live, p);
    if (interacting) return;
    updateBeam();
  }
  function updateBeam() {
    if (!ready() || !hasPlot || beamIdx == null || live.r == null) return;
    const r = clampR(live.r);
    if (lastBeamR != null && Math.abs(r - lastBeamR) < 0.002) return;
    lastBeamR = r;
    window.Plotly.restyle(el, { x: [[r, r]] }, [beamIdx]);
  }

  function render(cone) {
    const P = window.Plotly;
    const T = cone.T; curT = T;
    const edges = cone.edges, nB = edges.length - 1, nS = cone.density.length;
    const rMid = (b) => (edges[b] + edges[b + 1]) / 2;
    const xs = Array.from({ length: nB }, (_, b) => rMid(b));
    const ys = Array.from({ length: nS }, (_, j) => j / (nS - 1));

    // нормировка к [0,1] по глобальному максимуму + мягкое сжатие высоты (^0.6):
    // стартовый пик «сейчас» иначе давит и прячет расплывание плато во времени
    let gmax = 1e-9;
    for (const row of cone.density) for (const v of row) if (v > gmax) gmax = v;
    const z = cone.density.map((row) => row.map((v) => Math.pow(v / gmax, 0.6)));

    const surface = {
      type: 'surface', x: xs, y: ys, z,
      colorscale: SURF_SCALE, showscale: false, opacity: 0.94, name: 'плотность',
      contours: { z: { show: true, usecolormap: true, width: 1, project: { z: false } } },
      lighting: { ambient: 0.75, diffuse: 0.5, specular: 0.08, roughness: 0.9 },
      hovertemplate: 'R=%{x:+.2f}<br>время=%{y:.0%}<br>плотн.(норм.)=%{z:.2f}<extra></extra>',
    };

    function wallMesh(xConst, series, color) {
      const vx = [], vy = [], vz = [], I = [], J = [], K = [];
      for (let j = 0; j < nS; j++) { vx.push(xConst, xConst); vy.push(ys[j], ys[j]); vz.push(0, series[j]); }
      for (let j = 0; j < nS - 1; j++) {
        const b0 = 2 * j, t0 = 2 * j + 1, b1 = 2 * j + 2, t1 = 2 * j + 3;
        I.push(b0, t0); J.push(t0, b1); K.push(b1, t1);
      }
      return { type: 'mesh3d', x: vx, y: vy, z: vz, i: I, j: J, k: K,
               color, opacity: 0.35, flatshading: true, hoverinfo: 'skip', showlegend: false };
    }
    function wallEdge(xConst, series, color, label) {
      return { type: 'scatter3d', mode: 'lines+markers',
        x: Array(nS).fill(xConst), y: ys, z: series,
        line: { color, width: 6 }, marker: { size: 2, color },
        name: `${label} ${(series[nS - 1] * 100).toFixed(0)}%`,
        hovertemplate: `${label}: дойти к %{y:.0%} = %{z:.0%}<extra></extra>` };
    }
    const traces = [surface,
      wallMesh(-1, cone.p_stop_by_t, RED), wallMesh(T, cone.p_take_by_t, GREEN),
      wallEdge(-1, cone.p_stop_by_t, RED, 'СТОП'), wallEdge(T, cone.p_take_by_t, GREEN, 'ТЕЙК')];

    if (cone.market_terminal) {
      const medges = cone.market_edges || edges;
      const mMid = (b) => (medges[b] + medges[b + 1]) / 2;
      let mmax = 1e-9; for (const v of cone.market_terminal) if (v > mmax) mmax = v;
      traces.push({ type: 'scatter3d', mode: 'lines',
        x: cone.market_terminal.map((_, b) => mMid(b)),
        y: Array(cone.market_terminal.length).fill(1),
        z: cone.market_terminal.map((v) => v / mmax),
        line: { color: INK, width: 4, dash: 'dash' }, name: 'рынок · экспирация',
        hovertemplate: 'рынок R=%{x:+.2f}<br>плотн.=%{z:.2f}<extra></extra>' });
    }

    const r0 = live.r != null ? clampR(live.r) : cone.r0;
    lastBeamR = r0;
    beamIdx = traces.length;
    traces.push({ type: 'scatter3d', mode: 'lines',
      x: [r0, r0], y: [0, 0], z: [0, 1.02], line: { color: ORANGE, width: 8 },
      name: 'цена (r)', hovertemplate: 'цена r=%{x:+.2f}<extra></extra>' });

    const layout = {
      autosize: true, height: 420, margin: { l: 0, r: 0, t: 8, b: 0 },
      paper_bgcolor: PAPER, font: { family: FONT, color: INK, size: 11 },
      showlegend: true,
      legend: { orientation: 'h', x: 0, y: 1.06, font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      scene: {
        bgcolor: SCENE_BG, aspectmode: 'manual', aspectratio: { x: 1.75, y: 1.2, z: 0.7 },
        camera: currentCam,
        xaxis: { title: { text: 'R  (стоп −1 · 0 · тейк)', font: { size: 10, color: DIM } },
          range: [-1, T], gridcolor: RULE, zerolinecolor: RULE,
          tickvals: [-1, 0, T], ticktext: ['СТОП −1R', '0', `ТЕЙК +${T.toFixed(1)}R`],
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: true },
        yaxis: { title: { text: 'ВРЕМЯ → развязка', font: { size: 10, color: DIM } },
          range: [0, 1], gridcolor: RULE, tickformat: '.0%',
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: true },
        zaxis: { title: { text: 'плотность / P дойти', font: { size: 10, color: DIM } },
          range: [0, 1.05], gridcolor: RULE, tickfont: { size: 9, color: DIM },
          backgroundcolor: SCENE_BG, showbackground: true },
      },
    };
    const config = { responsive: true, displaylogo: false,
      modeBarButtonsToRemove: ['toImage'], doubleClick: 'reset' };

    // сохранить актуальный поворот пользователя перед перестройкой
    if (hasPlot && el._fullLayout?.scene?.camera) currentCam = el._fullLayout.scene.camera;
    layout.scene.camera = currentCam;
    if (!hasPlot) { P.newPlot(el, traces, layout, config); hasPlot = true; attachListeners(); }
    else { P.react(el, traces, layout, config); }
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => { if (ready() && hasPlot) window.Plotly.Plots.resize(el); });
  }

  return { setData, updateLive };
}
