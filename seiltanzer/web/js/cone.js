// Probability Cone — НАСТОЯЩИЙ 3D (WebGL через Plotly gl3d).
//
// Это не изометрическая имитация: сцену можно вращать мышкой, зумить колесом,
// наводить курсор для точных значений. Поверхность = плотность вероятности
// исхода сделки (PDF surface): X = R (стоп −1 · 0 · тейк +T), Y = ВРЕМЯ (к
// развязке), Z = плотность живых (ещё не поглощённых) путей. Две СТЕНЫ-барьера
// (красная СТОП / зелёная ТЕЙК) — филенные ribbon'ы Mesh3d, высота которых по
// времени = накопленная вероятность дойти; у дальней грани = P(стоп)/P(тейк).
// Оранжевый луч — текущая цена (r); тёмный пунктир на дальней грани — плотность
// РЫНКА на экспирации.
//
// Plotly подключается как глобальный скрипт (window.Plotly), т.к. это UMD-бандл,
// а не ES-модуль. Данные конуса приходят с бэкенда (prob.cone_surface).

const PAPER = '#FFFFFF', SCENE_BG = '#FBFAF6', INK = '#14140F', RULE = '#D8D5CC';
const DIM = '#8A877D', ORANGE = '#E8622A', RED = '#C6373C', GREEN = '#2E7D4F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
// палитра поверхности: бумага -> оранжевый
const SURF_SCALE = [[0, SCENE_BG], [0.35, '#F3C4A6'], [0.7, '#EE8A54'], [1, ORANGE]];

export function initCone(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let hasPlot = false;
  let sig = null;          // подпись данных: react только при реальном изменении
  let beamIdx = null;      // индекс трейса луча цены (для restyle каждый тик)
  let curT = 2.5, curZmax = 1;
  // камеру задаём ЯВНО на каждом рендере: react без camera сбрасывает её в
  // дефолт. Стартовый вид — спереди (СТОП слева · ТЕЙК справа · время вдаль);
  // поворот пользователя сохраняем, считывая текущую камеру перед react.
  let currentCam = { eye: { x: 0.15, y: -2.25, z: 0.72 }, up: { x: 0, y: 0, z: 1 } };
  const live = { r: null };

  const ready = () => typeof window !== 'undefined' && window.Plotly && el;

  function setData(cone, extra) {
    if (extra) Object.assign(live, extra);
    if (!ready()) { return; }
    if (!cone || !cone.available) {
      if (hasPlot) { window.Plotly.purge(el); hasPlot = false; sig = null; }
      return;
    }
    const s = `${cone.r0}|${cone.p_take}|${cone.p_stop}|${cone.times.length}|`
            + `${cone.T}|${!!cone.market_terminal}`;
    if (s === sig) return;   // данные не изменились — не перерисовываем сцену
    sig = s;
    render(cone);
  }

  // луч цены двигается каждый тик (дёшево: restyle одного трейса)
  function updateLive(p) {
    if (p) Object.assign(live, p);
    if (!ready() || !hasPlot || beamIdx == null || live.r == null) return;
    const r = Math.max(-1, Math.min(curT, live.r));
    window.Plotly.restyle(el, { x: [[r, r]] }, [beamIdx]);
  }

  function render(cone) {
    const P = window.Plotly;
    const T = cone.T; curT = T;
    const edges = cone.edges, nB = edges.length - 1, nS = cone.density.length;
    const rMid = (b) => (edges[b] + edges[b + 1]) / 2;
    const xs = Array.from({ length: nB }, (_, b) => rMid(b));      // ось R
    const ys = Array.from({ length: nS }, (_, j) => j / (nS - 1)); // ось времени 0..1

    // нормировка к [0,1] по глобальному максимуму + мягкое сжатие высоты (^0.6):
    // стартовый пик «сейчас» иначе давит и прячет расплывание плато во времени
    let gmax = 1e-9;
    for (const row of cone.density) for (const v of row) if (v > gmax) gmax = v;
    const z = cone.density.map((row) => row.map((v) => Math.pow(v / gmax, 0.6)));
    curZmax = 1;

    // --- поверхность плотности (PDF surface)
    const surface = {
      type: 'surface', x: xs, y: ys, z,
      colorscale: SURF_SCALE, showscale: false, opacity: 0.94,
      name: 'плотность',
      contours: { z: { show: true, usecolormap: true, width: 1, project: { z: false } } },
      lighting: { ambient: 0.75, diffuse: 0.5, specular: 0.08, roughness: 0.9 },
      hovertemplate: 'R=%{x:+.2f}<br>время=%{y:.0%}<br>плотн.(норм.)=%{z:.2f}<extra></extra>',
    };

    // --- стена-барьер как филенный ribbon (Mesh3d): низ z=0, верх z=P(дойти к t)
    function wallMesh(xConst, series, color) {
      const vx = [], vy = [], vz = [], I = [], J = [], K = [];
      for (let j = 0; j < nS; j++) {
        vx.push(xConst, xConst); vy.push(ys[j], ys[j]); vz.push(0, series[j]);
      }
      for (let j = 0; j < nS - 1; j++) {
        const b0 = 2 * j, t0 = 2 * j + 1, b1 = 2 * j + 2, t1 = 2 * j + 3;
        I.push(b0, t0); J.push(t0, b1); K.push(b1, t1);
      }
      return {
        type: 'mesh3d', x: vx, y: vy, z: vz, i: I, j: J, k: K,
        color, opacity: 0.35, flatshading: true, hoverinfo: 'skip',
        name: xConst < 0 ? 'стена стоп' : 'стена тейк', showlegend: false,
      };
    }
    // --- жирная кромка стены (Scatter3d line) + маркер финальной высоты
    function wallEdge(xConst, series, color, label) {
      return {
        type: 'scatter3d', mode: 'lines+markers',
        x: Array(nS).fill(xConst), y: ys, z: series,
        line: { color, width: 6 }, marker: { size: 2, color },
        name: `${label} ${(series[nS - 1] * 100).toFixed(0)}%`,
        hovertemplate: `${label}: дойти к %{y:.0%} = %{z:.0%}<extra></extra>`,
      };
    }
    const stopMesh = wallMesh(-1, cone.p_stop_by_t, RED);
    const takeMesh = wallMesh(T, cone.p_take_by_t, GREEN);
    const stopEdge = wallEdge(-1, cone.p_stop_by_t, RED, 'СТОП');
    const takeEdge = wallEdge(T, cone.p_take_by_t, GREEN, 'ТЕЙК');

    // --- терминальная плотность РЫНКА на дальней грани (y=1)
    const traces = [surface, stopMesh, takeMesh, stopEdge, takeEdge];
    if (cone.market_terminal) {
      const medges = cone.market_edges || edges;
      const mMid = (b) => (medges[b] + medges[b + 1]) / 2;
      let mmax = 1e-9; for (const v of cone.market_terminal) if (v > mmax) mmax = v;
      traces.push({
        type: 'scatter3d', mode: 'lines',
        x: cone.market_terminal.map((_, b) => mMid(b)),
        y: Array(cone.market_terminal.length).fill(1),
        z: cone.market_terminal.map((v) => v / mmax),
        line: { color: INK, width: 4, dash: 'dash' },
        name: 'рынок · экспирация',
        hovertemplate: 'рынок R=%{x:+.2f}<br>плотн.=%{z:.2f}<extra></extra>',
      });
    }

    // --- луч цены (r) на ближней грани (y=0)
    const r0 = live.r != null ? Math.max(-1, Math.min(T, live.r)) : cone.r0;
    beamIdx = traces.length;
    traces.push({
      type: 'scatter3d', mode: 'lines',
      x: [r0, r0], y: [0, 0], z: [0, 1.02],
      line: { color: ORANGE, width: 8 },
      name: 'цена (r)', hovertemplate: 'цена r=%{x:+.2f}<extra></extra>',
    });

    const layout = {
      autosize: true, height: 420,
      margin: { l: 0, r: 0, t: 8, b: 0 },
      paper_bgcolor: PAPER, font: { family: FONT, color: INK, size: 11 },
      showlegend: true,
      legend: { orientation: 'h', x: 0, y: 1.06, font: { size: 10 }, bgcolor: 'rgba(0,0,0,0)' },
      scene: {
        bgcolor: SCENE_BG,
        aspectmode: 'manual', aspectratio: { x: 1.75, y: 1.2, z: 0.7 },
        xaxis: {
          title: { text: 'R  (стоп −1 · 0 · тейк)', font: { size: 10, color: DIM } },
          range: [-1, T], gridcolor: RULE, zerolinecolor: RULE,
          tickvals: [-1, 0, T], ticktext: ['СТОП −1R', '0', `ТЕЙК +${T.toFixed(1)}R`],
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: true,
        },
        yaxis: {
          title: { text: 'ВРЕМЯ → развязка', font: { size: 10, color: DIM } },
          range: [0, 1], gridcolor: RULE, tickformat: '.0%',
          tickfont: { size: 9, color: DIM }, backgroundcolor: SCENE_BG, showbackground: true,
        },
        zaxis: {
          title: { text: 'плотность / P дойти', font: { size: 10, color: DIM } },
          range: [0, 1.05], gridcolor: RULE, tickfont: { size: 9, color: DIM },
          backgroundcolor: SCENE_BG, showbackground: true,
        },
      },
    };
    const config = {
      responsive: true, displaylogo: false,
      modeBarButtonsToRemove: ['toImage'],
      doubleClick: 'reset',
    };

    // сохранить текущий поворот пользователя (если сцена уже есть), иначе старт-вид
    if (hasPlot && el._fullLayout?.scene?.camera) currentCam = el._fullLayout.scene.camera;
    layout.scene.camera = currentCam;
    if (!hasPlot) {
      P.newPlot(el, traces, layout, config);
      hasPlot = true;
    } else {
      P.react(el, traces, layout, config);
    }
  }

  // ресайз вместе с окном
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => {
      if (ready() && hasPlot) window.Plotly.Plots.resize(el);
    });
  }

  return { setData, updateLive };
}
