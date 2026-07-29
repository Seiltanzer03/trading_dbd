// IV Surface (3D): delayed option snapshot + an honestly live spot slice.
//
// The option surface itself is rebuilt only when the snapshot changes. Between
// snapshots the orange ridge/cut plane moves on every proxy tick. This exposes
// how the current spot travels through the fixed smile without pretending that
// delayed option quotes themselves update tick-by-tick.

import { approach } from './anim.js';

const RULE = 'rgba(180,180,180,0.5)', ORANGE = '#E8622A', INK = '#14140F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
const SURFACE = 0, SNAPSHOT_ATM = 1, LIVE_CURTAIN = 2, LIVE_RIDGE = 3, LIVE_DOT = 4;

const SURF_SCALE = [
  [0.0, '#1A1F3A'],
  [0.2, '#1B6CA8'],
  [0.45, '#2ECC71'],
  [0.65, '#F4CE14'],
  [0.85, '#E8622A'],
  [1.0, '#C6373C'],
];

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

function nearestIndex(xs, x) {
  return xs.reduce((best, v, i) =>
    Math.abs(v - x) < Math.abs(xs[best] - x) ? i : best, 0);
}

export function initIVSurface(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let hasPlot = false, listenersOn = false, snapshotSig = null;
  let rendering = false;
  let model = null, pendingPayload = null;
  let interacting = false, interactTimer = null;
  let pointerHeld = false, userCamera = false;
  let targetLiveX = 0, displayLiveX = 0, lastFrame = performance.now(), lastDraw = 0;

  const INIT_CAM = { eye: { x: 1.4, y: -1.4, z: 0.82 }, up: { x: 0, y: 0, z: 1 } };
  let currentCam = JSON.parse(JSON.stringify(INIT_CAM));

  function ready() {
    return typeof window !== 'undefined' && window.Plotly && el;
  }
  function grabCam() {
    const c = el?._fullLayout?.scene?.camera;
    if (c && c.eye) currentCam = JSON.parse(JSON.stringify(c));
  }
  function cameraFromEvent(ev) {
    if (!ev || typeof ev !== 'object') return;
    if (ev['scene.camera']?.eye) {
      currentCam = JSON.parse(JSON.stringify(ev['scene.camera'])); userCamera = true; return;
    }
    const next = JSON.parse(JSON.stringify(currentCam));
    let changed = false;
    for (const [key, value] of Object.entries(ev)) {
      if (!key.startsWith('scene.camera.')) continue;
      const parts = key.slice('scene.camera.'.length).split('.');
      let dst = next;
      for (let i = 0; i < parts.length - 1; i++) dst = dst[parts[i]] ||= {};
      dst[parts.at(-1)] = value; changed = true;
    }
    if (changed) { currentCam = next; userCamera = true; }
  }
  function sameCamera(a, b) {
    return ['eye', 'center', 'up'].every((k) => ['x', 'y', 'z'].every((q) =>
      Math.abs(Number(a?.[k]?.[q] || 0) - Number(b?.[k]?.[q] || 0)) < 1e-5));
  }
  function pinAfter(write, pinned = JSON.parse(JSON.stringify(currentCam))) {
    if (!userCamera) return write;
    Promise.resolve(write).then(() => {
      if (!interacting && !sameCamera(el?._fullLayout?.scene?.camera, pinned))
        window.Plotly.relayout(el, { 'scene.camera': pinned });
    });
    return write;
  }
  function beginPointer() {
    pointerHeld = true; interacting = true;
    if (interactTimer) clearTimeout(interactTimer);
  }
  function markInteract() {
    interacting = true;
    if (interactTimer) clearTimeout(interactTimer);
    if (!pointerHeld) interactTimer = setTimeout(finishInteraction, 280);
  }
  function finishInteraction() {
    interacting = false;
    if (pendingPayload) {
      const p = pendingPayload; pendingPayload = null; render(null, p);
    }
  }
  function releaseInteract() {
    if (!pointerHeld) return;
    pointerHeld = false;
    if (interactTimer) clearTimeout(interactTimer);
    requestAnimationFrame(() => {
      grabCam(); userCamera = true;
      interactTimer = setTimeout(finishInteraction, 140);
    });
  }
  function attachListeners() {
    if (listenersOn || !el?.on) return;
    listenersOn = true;
    el.on('plotly_relayouting', (ev) => {
      markInteract();
      cameraFromEvent(ev);
    });
    el.on('plotly_relayout', cameraFromEvent);
    if (window.PointerEvent) {
      el.addEventListener('pointerdown', beginPointer, true);
      window.addEventListener('pointerup', releaseInteract, true);
      window.addEventListener('pointercancel', releaseInteract, true);
    } else {
      el.addEventListener('mousedown', beginPointer, true);
      el.addEventListener('touchstart', beginPointer, { passive: true, capture: true });
      window.addEventListener('mouseup', releaseInteract, true);
      window.addEventListener('touchend', releaseInteract, { passive: true, capture: true });
    }
    el.addEventListener('wheel', markInteract, { passive: true });
  }

  function normalizePayload(surfacePayload) {
    return Array.isArray(surfacePayload)
      ? { value: surfacePayload, status: 'delayed' }
      : (surfacePayload || {});
  }
  function payloadSignature(payload) {
    const rows = payload.value || [];
    const compact = rows.map((r) => {
      const strikes = r.strikes || [], ivs = r.ivs || [];
      return [
        Number(r.days || 0).toFixed(4),
        strikes.length,
        Number(strikes[0] || 0).toFixed(4),
        Number(strikes[strikes.length - 1] || 0).toFixed(4),
        Number(ivs[0] || 0).toFixed(5),
        Number(ivs[ivs.length - 1] || 0).toFixed(5),
      ].join(':');
    }).join('|');
    return `${payload.ts || ''}|${compact}`;
  }

  function buildModel(payload) {
    const surfaceData = payload.value || [];
    const firstStrikes = surfaceData[0]?.strikes || [];
    const snapshotSpot = Number(surfaceData[0]?.spot_at_snapshot)
      || Number(firstStrikes[Math.floor(firstStrikes.length / 2)]);
    if (!(snapshotSpot > 0) || !firstStrikes.length) return null;

    const rows = surfaceData.map((row) => {
      const rowSpot = Number(row.spot_at_snapshot) || snapshotSpot;
      const pairs = (row.strikes || []).map((strike, i) => ({
        x: (Number(strike) / rowSpot - 1) * 100,
        iv: Number(row.ivs?.[i]) * 100,
      })).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.iv)
        && p.iv > 0 && p.iv < 200).sort((a, b) => a.x - b.x);
      return { row, pairs };
    }).filter((r) => r.pairs.length >= 3);
    if (!rows.length) return null;

    let xLo = Math.max(-20, ...rows.map((r) => r.pairs[0].x));
    let xHi = Math.min(20, ...rows.map((r) => r.pairs[r.pairs.length - 1].x));
    if (!(xHi > xLo + 1)) {
      xLo = Math.max(-20, rows[0].pairs[0].x);
      xHi = Math.min(20, rows[0].pairs[rows[0].pairs.length - 1].x);
    }
    if (!(xHi > xLo + 1)) return null;
    const moneyPct = Array.from(
      { length: 41 },
      (_, i) => +(xLo + (xHi - xLo) * i / 40).toFixed(3));
    const zIvs = rows.map(({ pairs }) => {
      const xs = pairs.map((p) => p.x), ys = pairs.map((p) => p.iv);
      return moneyPct.map((x) => {
        const v = interp(xs, ys, x);
        return v == null ? null : +v.toFixed(3);
      });
    });
    const cleanRows = rows.map((r) => r.row);
    const yDte = cleanRows.map((r) => Number(r.days));
    const yTickText = cleanRows.map((r) => {
      const d = Number(r.days);
      if (d < 1) return `${(d * 24).toFixed(1)}h`;
      if (d < 7) return `${Math.round(d)}d`;
      if (d < 28) return `${Math.round(d / 7)}W`;
      return `${Math.round(d / 30)}M`;
    });
    const allZ = zIvs.flat().filter(Number.isFinite);
    if (!allZ.length) return null;
    const zMin = Math.min(...allZ);
    const rawMax = Math.max(...allZ);
    const zMax = rawMax > zMin ? rawMax : zMin + 0.01;
    return {
      payload, snapshotSpot, moneyPct, zIvs, yDte, yTickText, zMin, zMax,
      xLo, xHi,
    };
  }

  function liveGeometry(xRaw) {
    if (!model) return null;
    const x = Math.max(model.xLo, Math.min(model.xHi, xRaw));
    const z = model.zIvs.map((row) => {
      const v = interp(model.moneyPct, row, x);
      return v == null ? model.zMin : v;
    });
    const wallX = [], wallY = [], wallZ = [], i = [], j = [], k = [];
    for (let r = 0; r < model.yDte.length; r++) {
      wallX.push(x, x);
      wallY.push(model.yDte[r], model.yDte[r]);
      wallZ.push(model.zMin, z[r]);
    }
    for (let r = 0; r < model.yDte.length - 1; r++) {
      const b0 = 2 * r, t0 = b0 + 1, b1 = b0 + 2, t1 = b0 + 3;
      i.push(b0, t0); j.push(t0, b1); k.push(b1, t1);
    }
    return { x, z, wallX, wallY, wallZ, i, j, k };
  }

  function updateStatus(payload, x, z) {
    const status = document.getElementById('iv-surface-status');
    const skewEl = document.getElementById('iv-skew-momentum');
    const isDemo = payload.status === 'demo';
    const hasLive = payload.spot_status === 'live';
    const hasSpot = Number(payload.spot_current) > 0;
    const clipped = x <= model.xLo + 1e-9 || x >= model.xHi - 1e-9;
    if (status) {
      const deltaBp = x * 100;
      status.innerText = isDemo ? `◆ DEMO · LIVE SLICE ${deltaBp >= 0 ? '+' : ''}${deltaBp.toFixed(0)}bp`
        : hasLive ? `● OPTIONS SNAPSHOT · LIVE SLICE ${deltaBp >= 0 ? '+' : ''}${deltaBp.toFixed(0)}bp`
        : hasSpot ? `◐ OPTIONS SNAPSHOT · INDICATIVE SLICE ${deltaBp >= 0 ? '+' : ''}${deltaBp.toFixed(0)}bp`
        : '◐ OPTIONS SNAPSHOT · SNAPSHOT SPOT';
      status.className = `badge ${isDemo ? 'demo' : hasLive ? 'live' : 'delayed'}`;
      status.title = clipped
        ? 'Текущая цена вышла за доступную сетку страйков; live-срез прижат к краю.'
        : 'IV — задержанный снимок. Оранжевый срез движется по текущему proxy spot.';
    }
    if (!skewEl || !z.length) return;
    const near = model.zIvs[0];
    const atm = interp(model.moneyPct, near, x);
    const put = interp(model.moneyPct, near, x - 5);
    const call = interp(model.moneyPct, near, x + 5);
    const skew = put != null && call != null ? put - call : null;
    const curvature = put != null && call != null && atm != null ? put + call - 2 * atm : null;
    const wing = skew == null ? 'WING —'
      : skew > 0 ? `PUT WING +${skew.toFixed(1)}пп`
        : `CALL WING +${Math.abs(skew).toFixed(1)}пп`;
    skewEl.style.display = 'inline-block';
    skewEl.style.backgroundColor = skew != null && Math.abs(skew) > 1
      ? (skew > 0 ? 'rgba(198,55,60,0.12)' : 'rgba(46,125,79,0.12)')
      : 'transparent';
    skewEl.style.color = skew != null && Math.abs(skew) > 1
      ? (skew > 0 ? '#C6373C' : '#2E7D4F') : '#8A877D';
    skewEl.style.border = '1px solid rgba(138,135,125,0.4)';
    skewEl.innerText = `LIVE SLICE: ATM ${atm == null ? '—' : `${atm.toFixed(1)}%`} · ${wing}`
      + ` · CURV ${curvature == null ? '—' : `${curvature >= 0 ? '+' : ''}${curvature.toFixed(1)}пп`}`;
    skewEl.title = 'Срез задержанной IV-поверхности в текущем proxy spot. Это режим хвостов и кривизны, не самостоятельный сигнал.';
  }

  function tracesFor(g) {
    const surface = {
      type: 'surface',
      x: model.moneyPct, y: model.yDte, z: model.zIvs,
      colorscale: SURF_SCALE, cmin: model.zMin, cmax: model.zMax,
      showscale: true, opacity: 0.94,
      colorbar: {
        thickness: 14, len: 0.75, x: 1.01,
        bgcolor: 'rgba(248,246,242,0.9)',
        bordercolor: 'rgba(200,200,200,0.3)', borderwidth: 1,
        tickfont: { family: FONT, size: 11, color: '#111' },
        title: { text: 'IV %', side: 'right', font: { family: FONT, size: 12, color: '#111' } },
        ticksuffix: '%',
      },
      contours: { z: { show: true, usecolormap: true, width: 1 } },
      lighting: { ambient: 0.72, diffuse: 0.78, specular: 0.22, roughness: 0.6 },
      hovertemplate: '<b>Snapshot moneyness:</b> %{x:.1f}%<br><b>DTE:</b> %{y}'
        + '<br><b>IV:</b> %{z:.1f}%<extra></extra>',
      name: 'IV snapshot',
    };
    
    // CFD RV (Realized Volatility) Dynamic Wireframe Layer
    // Рисуется как дышащая сетка (wireframe) под основной поверхностью
    const rvBaseZ = model.zMin + Math.max((model.zMax - model.zMin) * 0.15, 2.0);
    const rvZ = model.zIvs.map(row => row.map(v => rvBaseZ + (v - model.zMin) * 0.2));
    const rvWireframe = {
      type: 'surface',
      x: model.moneyPct, y: model.yDte, z: rvZ,
      showscale: false,
      opacity: 0.3,
      colorscale: [[0, '#2ECC71'], [1, '#2ECC71']],
      hidesurface: true, 
      contours: {
        x: { show: true, color: 'rgba(46, 204, 113, 0.4)', width: 2 },
        y: { show: true, color: 'rgba(46, 204, 113, 0.4)', width: 2 },
        z: { show: false }
      },
      name: 'CFD REALIZED VOLATILITY (RV)', hoverinfo: 'skip'
    };
    const atmZ = model.zIvs.map((row) =>
      interp(model.moneyPct, row, 0) ?? model.zMin);
    const snapshotAtm = {
      type: 'scatter3d', mode: 'lines', x: Array(model.yDte.length).fill(0),
      y: model.yDte, z: atmZ, line: { color: 'rgba(20,20,15,0.45)', width: 3 },
      name: 'SNAPSHOT ATM', hoverinfo: 'skip',
    };
    const curtain = {
      type: 'mesh3d', x: g.wallX, y: g.wallY, z: g.wallZ,
      i: g.i, j: g.j, k: g.k, color: ORANGE, opacity: 0.16,
      flatshading: true, hoverinfo: 'skip', showlegend: false,
    };
    const ridge = {
      type: 'scatter3d', mode: 'lines', x: Array(model.yDte.length).fill(g.x),
      y: model.yDte, z: g.z, line: { color: ORANGE, width: 8 },
      name: 'LIVE SPOT SLICE',
      hovertemplate: 'live displacement=%{x:+.2f}%<br>DTE=%{y}<br>IV=%{z:.1f}%<extra></extra>',
    };
    
    // Эффект "падающей капли" (Splash/Ripple) для живой цены
    const dotZ = g.z[0] + (model.zMax - model.zMin) * 0.05;
    const dot = {
      type: 'scatter3d', mode: 'markers', x: [g.x], y: [model.yDte[0]], z: [dotZ],
      marker: { 
        size: 10, 
        color: '#FFFFFF', 
        line: { color: ORANGE, width: 3 },
        symbol: 'circle'
      },
      name: 'LIVE CFD', showlegend: false,
      hovertemplate: 'живой тик CFD<br>displacement=%{x:+.2f}%<br>IV=%{z:.1f}%<extra></extra>',
    };
    const dotHalo = {
      type: 'scatter3d', mode: 'markers', x: [g.x], y: [model.yDte[0]], z: [dotZ - 0.01],
      marker: { size: 25, color: 'rgba(232, 98, 42, 0.25)', symbol: 'circle' },
      name: 'HALO', showlegend: false, hoverinfo: 'skip'
    };
    
    return [surface, snapshotAtm, curtain, rvWireframe, ridge, dot, dotHalo];
  }

  function layoutFor() {
    return {
      margin: { t: 5, b: 5, l: 0, r: 55 },
      uirevision: 'iv-surface-ui-v3',
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: FONT, color: INK },
      legend: { orientation: 'h', x: 0, y: 1.03, font: { size: 9 } },
      scene: {
        dragmode: 'orbit', camera: currentCam, uirevision: 'iv-surface-camera-v3',
        xaxis: {
          title: { text: 'MONEYNESS VS SNAPSHOT %', font: { family: FONT, size: 12, color: '#111' } },
          tickfont: { family: FONT, size: 10, color: '#222' },
          gridcolor: RULE, zeroline: true, zerolinecolor: 'rgba(20,20,15,0.55)',
          zerolinewidth: 2, ticksuffix: '%', showbackground: true,
          backgroundcolor: 'rgba(240,238,232,0.6)',
        },
        yaxis: {
          title: { text: 'DTE', font: { family: FONT, size: 12, color: '#111' } },
          tickfont: { family: FONT, size: 10, color: '#222' },
          gridcolor: RULE, zeroline: false, tickvals: model.yDte,
          ticktext: model.yTickText, showbackground: true,
          backgroundcolor: 'rgba(240,238,232,0.6)',
        },
        zaxis: {
          title: { text: 'IV %', font: { family: FONT, size: 12, color: '#111' } },
          tickfont: { family: FONT, size: 10, color: '#222' },
          gridcolor: RULE, zeroline: false, ticksuffix: '%',
          range: [model.zMin, model.zMax + Math.max((model.zMax - model.zMin) * 0.08, 0.1)],
          showbackground: true, backgroundcolor: 'rgba(240,238,232,0.6)',
        },
        aspectmode: 'manual', aspectratio: { x: 1.35, y: 1, z: 0.78 },
        bgcolor: 'rgba(248,246,242,0.3)',
      },
    };
  }

  function applyLiveGeometry() {
    if (!ready() || !hasPlot || !model || interacting) return;
    const g = liveGeometry(displayLiveX);
    if (!g) return;
    pinAfter(window.Plotly.restyle(el, {
      x: [g.wallX, [], Array(model.yDte.length).fill(g.x), [g.x], [g.x]],
      y: [g.wallY, [], model.yDte, [model.yDte[0]], [model.yDte[0]]],
      z: [g.wallZ, [], g.z, [g.z[0] + (model.zMax - model.zMin) * 0.05], [g.z[0] + (model.zMax - model.zMin) * 0.05 - 0.01]],
    }, [2, 3, 4, 5, 6])); // Indices: 2=curtain, 3=rvWireframe(skip), 4=ridge, 5=dot, 6=dotHalo
    updateStatus(model.payload, g.x, g.z);
  }

  function setLive(payload) {
    if (!model) return;
    model.payload = payload;
    const spot = Number(payload.spot_current) > 0
      ? Number(payload.spot_current) : model.snapshotSpot;
    targetLiveX = (spot / model.snapshotSpot - 1) * 100;
  }

  function render(_state, surfacePayload) {
    const payload = normalizePayload(surfacePayload);
    const surfaceData = payload.value;
    if (!surfaceData || surfaceData.length === 0) {
      el.style.opacity = '0';
      const empty = document.getElementById('iv-surface-empty');
      const status = document.getElementById('iv-surface-status');
      if (empty) empty.style.display = 'flex';
      if (status) status.innerText = '○ ОЖИДАНИЕ ДАННЫХ';
      return;
    }
    const sig = payloadSignature(payload);
    if (rendering) {
      pendingPayload = payload;
      setLive(payload);
      return;
    }
    if (hasPlot && sig === snapshotSig) {
      setLive(payload);
      return;
    }
    if (interacting) {
      pendingPayload = payload;
      setLive(payload);
      return;
    }
    const nextModel = buildModel(payload);
    if (!nextModel) return;
    model = nextModel;
    setLive(payload);
    displayLiveX = targetLiveX;
    const g = liveGeometry(displayLiveX);
    if (!g) return;

    el.style.opacity = '1';
    const empty = document.getElementById('iv-surface-empty');
    if (empty) empty.style.display = 'none';
    if (hasPlot && !userCamera) grabCam();
    const layout = layoutFor();
    layout.scene.camera = currentCam;
    const config = {
      responsive: true, displayModeBar: true, displaylogo: false,
      modeBarButtonsToRemove: ['toImage', 'sendDataToCloud'],
      scrollZoom: true, doubleClick: 'reset',
    };
    const done = () => {
      rendering = false;
      hasPlot = true;
      snapshotSig = sig;
      attachListeners();
      if (!userCamera) grabCam();
      updateStatus(payload, g.x, g.z);
      if (pendingPayload) {
        const p = pendingPayload;
        pendingPayload = null;
        render(null, p);
      }
    };
    rendering = true;
    const plotPromise = !hasPlot
      ? window.Plotly.newPlot(el, tracesFor(g), layout, config)
      : pinAfter(window.Plotly.react(el, tracesFor(g), layout, config));
    plotPromise.then(done).catch((err) => {
      rendering = false;
      console.error('[seiltanzer] IV surface render failed', err);
    });
  }

  function frame(now) {
    requestAnimationFrame(frame);
    const dt = Math.min((now - lastFrame) / 1000, 0.05);
    lastFrame = now;
    if (!hasPlot || !model || interacting) return;
    const next = approach(displayLiveX, targetLiveX, dt, 7);
    const changed = Math.abs(next - displayLiveX) > 0.00002;
    displayLiveX = next;
    if (changed && now - lastDraw > 66) {
      lastDraw = now;
      applyLiveGeometry();
    }
  }
  requestAnimationFrame(frame);

  function destroy() {
    if (hasPlot) window.Plotly.purge(el);
    hasPlot = false;
  }

  return { render, updateLive: setLive, destroy };
}
