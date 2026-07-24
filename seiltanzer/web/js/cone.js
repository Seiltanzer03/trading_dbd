// Probability Cone — НАСТОЯЩИЙ 3D (WebGL, Plotly gl3d), risk-neutral.
//
// Поверхность = плотность вероятности исхода сделки под ОПЦИОННУЮ волу + цену
// (НЕ винрейт). X = R (стоп −1 · 0 · тейк +T), Y = ВРЕМЯ (адаптивное: минуты у
// скальпа, дни у свинга), Z = плотность живых путей. Красная/зелёная СТЕНЫ = P
// дойти до стопа/тейка к моменту t. Оранжевый луч = текущая цена (r); тёмный
// пунктир на дальней грани = RND рынка на экспирации.
//
// Вращение НЕ сбрасывается: layout.uirevision — Plotly сам сохраняет поворот
// камеры при обновлении данных. Плюс пока идёт взаимодействие, сцену не трогаем.

const PAPER = '#FFFFFF', SCENE_BG = '#FBFAF6', INK = '#14140F', RULE = '#D8D5CC';
const DIM = '#8A877D', ORANGE = '#E8622A', RED = '#C6373C', GREEN = '#2E7D4F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
const SURF_SCALE = [[0, SCENE_BG], [0.35, '#F3C4A6'], [0.7, '#EE8A54'], [1, ORANGE]];

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
  let sig = null, pendingCone = null, pendingSig = null;
  let beamIdx = null, ballIdx = null, lastBeamR = null;
  let curT = 2.5;
  let interacting = false, interactTimer = null;
  const live = { r: null };
  const INIT_CAM = { eye: { x: 0.15, y: -2.25, z: 0.72 }, up: { x: 0, y: 0, z: 1 } };
  // текущий поворот камеры: держим сами и ставим на КАЖДЫЙ render, чтобы вид
  // не «отскакивал» при обновлении данных (uirevision игнорит стартовую камеру
  // и не считает синтетику поворотом, поэтому — ручное сохранение).
  let currentCam = JSON.parse(JSON.stringify(INIT_CAM));

  const ready = () => typeof window !== 'undefined' && window.Plotly && el;
  const clampR = (r) => Math.max(-1, Math.min(curT, r));
  const coarseSig = (c) =>
    `${Math.round(c.r0 * 50)}|${Math.round(c.p_take * 50)}|${Math.round(c.p_stop * 50)}`
    + `|${c.times_frac.length}|${(+c.T).toFixed(2)}|${Math.round((c.horizon_years || 0) * 3650)}`
    + `|${!!c.market_terminal}`;

  function markInteract() {
    interacting = true;
    if (interactTimer) clearTimeout(interactTimer);
    interactTimer = setTimeout(() => { interacting = false; flush(); }, 300);
  }
  function grabCam() {
    const c = el._fullLayout?.scene?.camera;
    if (c && c.eye) currentCam = c;         // ловим живой поворот пользователя
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
    if (pendingCone) { sig = pendingSig; const c = pendingCone; pendingCone = null; render(c); }
    updateBeam();
  }

  function setData(cone, extra) {
    if (extra) Object.assign(live, extra);
    if (!ready()) return;
    if (!cone || !cone.available) {
      // НЕ уничтожаем сцену на транзиентном пропуске данных (иначе следующий тик
      // построит заново и вид «отскочит» в исходный). Оверлей «нет сделки»
      // (#cone-empty) и так закрывает панель без сделки.
      return;
    }
    const s = coarseSig(cone);
    if (s === sig && hasPlot) return;
    if (interacting) { pendingCone = cone; pendingSig = s; return; }
    sig = s; pendingCone = null;
    render(cone);
  }

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
    // двигаем и опору-линию, и точку цены (высота обновится на следующем render)
    window.Plotly.restyle(el, { x: [[r, r]] }, [beamIdx]);
    window.Plotly.restyle(el, { x: [[r]] }, [ballIdx]);
  }

  function render(cone) {
    const P = window.Plotly;
    const T = cone.T; curT = T;
    const edges = cone.edges, nB = edges.length - 1, nS = cone.density.length;
    const rMid = (b) => (edges[b] + edges[b + 1]) / 2;
    const xs = Array.from({ length: nB }, (_, b) => rMid(b));
    const ys = Array.from({ length: nS }, (_, j) => j / (nS - 1));   // глубина 0..1

    // поверхность = МК-плотность живых путей (тот вид конуса, что «стоит на полу»);
    // теперь АСИММЕТРИЧНА по скью (сторона страха шире). Мягкое сжатие высоты ^0.7.
    let gmax = 1e-9;
    for (const row of cone.density) for (const v of row) if (v > gmax) gmax = v;
    const z = cone.density.map((row) => row.map((v) => Math.pow(v / gmax, 0.7)));

    const surface = {
      type: 'surface', x: xs, y: ys, z,
      colorscale: SURF_SCALE, showscale: false, opacity: 0.95, name: 'плотность',
      contours: { z: { show: true, usecolormap: true, width: 1 } },
      lighting: { ambient: 0.78, diffuse: 0.5, specular: 0.06, roughness: 0.9 },
      hovertemplate: 'R=%{x:+.2f}<br>плотн.=%{z:.2f}<extra></extra>',
    };
    // высота поверхности у ближней грани (t=0) в точке текущего r — чтобы «цену»
    // посадить ТОЧКОЙ НА гору конуса, а не палкой рядом
    const heightAt = (R) => {
      for (let b = 0; b < nB; b++) if (R >= edges[b] && R < edges[b + 1]) return Math.pow(cone.density[0][b] / gmax, 0.7);
      return 0.04;
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
      return { type: 'scatter3d', mode: 'lines',
        x: Array(nS).fill(xConst), y: ys, z: series,
        line: { color, width: 6 }, name: `${label} ${(series[nS - 1] * 100).toFixed(0)}%`,
        hovertemplate: `${label}: дойти = %{z:.0%}<extra></extra>` };
    }
    const traces = [surface,
      wallMesh(-1, cone.p_stop_by_t, RED), wallMesh(T, cone.p_take_by_t, GREEN),
      wallEdge(-1, cone.p_stop_by_t, RED, 'СТОП'), wallEdge(T, cone.p_take_by_t, GREEN, 'ТЕЙК')];

    const rBeam = live.r != null ? clampR(live.r) : cone.r0;
    lastBeamR = rBeam;
    const hB = Math.max(heightAt(rBeam), 0.05);
    // тонкая опора от пола до точки + ЯРКАЯ ТОЧКА цены НА поверхности конуса
    beamIdx = traces.length;
    traces.push({ type: 'scatter3d', mode: 'lines',
      x: [rBeam, rBeam], y: [0, 0], z: [0, hB], line: { color: ORANGE, width: 4 },
      name: 'цена (r)', hoverinfo: 'skip', showlegend: false });
    ballIdx = traces.length;
    traces.push({ type: 'scatter3d', mode: 'markers',
      x: [rBeam], y: [0], z: [hB],
      marker: { size: 7, color: ORANGE, line: { color: '#fff', width: 1 } },
      name: 'цена (r)', hovertemplate: 'цена r=%{x:+.2f}<extra></extra>' });

    // ось времени — адаптивная (реальные единицы)
    const hy = cone.horizon_years;
    const yTitle = hy
      ? `ВРЕМЯ → развязка · медиана ≈ ${fmtTime(cone.median_years)}`
      : 'ВРЕМЯ → развязка (модельное)';
    const yTicktext = hy
      ? ['сейчас', fmtTime(hy * 0.5), fmtTime(hy)]
      : ['сейчас', '50%', 'развязка'];

    const layout = {
      autosize: true, height: 430, margin: { l: 0, r: 0, t: 8, b: 0 },
      paper_bgcolor: PAPER, font: { family: FONT, color: INK, size: 11 },
      showlegend: true,
      legend: { orientation: 'h', x: 0, y: 1.07, font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      scene: {
        camera: currentCam,              // ← ставим сохранённый поворот на каждый render
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
    const config = { responsive: true, displaylogo: false,
      modeBarButtonsToRemove: ['toImage'], doubleClick: 'reset' };

    if (hasPlot) grabCam();               // зафиксировать текущий поворот перед пересбором
    layout.scene.camera = currentCam;
    if (!hasPlot) {
      P.newPlot(el, traces, layout, config);
      hasPlot = true; attachListeners();
    } else {
      P.react(el, traces, layout, config);
    }
  }

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => { if (ready() && hasPlot) window.Plotly.Plots.resize(el); });
  }
  return { setData, updateLive };
}
