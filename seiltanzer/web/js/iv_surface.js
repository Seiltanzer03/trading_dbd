// IV Surface (3D): real delayed option snapshot + active price-linked local view.
//
// The option quotes remain delayed snapshots. Price ticks animate the current
// moneyness slice, a short history wake and the near-term ribbon. LOCAL 24H is a
// total-variance projection of real expiries, not synthetic live option quotes.

import { approach } from './anim.js';

const RULE = 'rgba(180,180,180,0.5)';
const ORANGE = '#E8622A';
const INK = '#14140F';
const FONT = 'IBM Plex Mono, ui-monospace, monospace';
const LOCAL_HOURS = [1, 2, 4, 8, 12, 18, 24];
const MORPH_MS = 1500;
const WAKE_MS = 30000;
const MAX_WAKE = 14;

const SURFACE = 0;
const SNAPSHOT_ATM = 1;
const WAKE = 2;
const LIVE_RIBBON = 3;
const LIVE_CURTAIN = 4;
const LIVE_RIDGE = 5;
const LIVE_DOT = 6;
const VELOCITY = 7;

const SURF_SCALE = [
  [0.0, '#1A1F3A'],
  [0.2, '#1B6CA8'],
  [0.45, '#2ECC71'],
  [0.65, '#F4CE14'],
  [0.85, '#E8622A'],
  [1.0, '#C6373C'],
];

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function interp(xs, ys, x, clampEnds = false) {
  if (!xs.length) return null;
  if (x <= xs[0]) return clampEnds ? ys[0] : (x === xs[0] ? ys[0] : null);
  if (x >= xs[xs.length - 1])
    return clampEnds ? ys[ys.length - 1] : (x === xs[xs.length - 1] ? ys[ys.length - 1] : null);
  let hi = 1;
  while (hi < xs.length && xs[hi] < x) hi++;
  const lo = hi - 1;
  const span = xs[hi] - xs[lo];
  if (!span) return ys[lo];
  const f = (x - xs[lo]) / span;
  return ys[lo] + (ys[hi] - ys[lo]) * f;
}

export function projectTotalVariance(samples, targetDays) {
  const pts = (samples || [])
    .filter((p) => Number.isFinite(p?.days) && p.days > 0
      && Number.isFinite(p?.ivPct) && p.ivPct > 0)
    .sort((a, b) => a.days - b.days);
  if (!pts.length || !(targetDays > 0)) return null;
  const tau = targetDays / 365;
  const tw = pts.map((p) => ({
    tau: p.days / 365,
    w: Math.pow(p.ivPct / 100, 2) * (p.days / 365),
  }));
  let w;
  if (tw.length === 1 || tau <= tw[0].tau) {
    w = tw[0].w * tau / tw[0].tau;
  } else if (tau >= tw[tw.length - 1].tau) {
    const last = tw[tw.length - 1];
    const prev = tw[tw.length - 2];
    const slope = Math.max(0, (last.w - prev.w) / Math.max(last.tau - prev.tau, 1e-9));
    w = last.w + slope * (tau - last.tau);
  } else {
    let hi = 1;
    while (hi < tw.length && tw[hi].tau < tau) hi++;
    const lo = hi - 1;
    const f = (tau - tw[lo].tau) / Math.max(tw[hi].tau - tw[lo].tau, 1e-9);
    w = tw[lo].w + (tw[hi].w - tw[lo].w) * f;
  }
  return Math.sqrt(Math.max(w, 1e-12) / tau) * 100;
}

export function smileMetrics(moneyPct, zRows, liveX) {
  const near = zRows?.[0] || [];
  if (!near.length) return { atm: null, skew: null, curvature: null };
  const atm = interp(moneyPct, near, liveX, true);
  const put = interp(moneyPct, near, liveX - 5, true);
  const call = interp(moneyPct, near, liveX + 5, true);
  return {
    atm,
    skew: put != null && call != null ? put - call : null,
    curvature: put != null && call != null && atm != null
      ? put + call - 2 * atm : null,
  };
}

export function buildLocalProjection(rawRows, moneyPct, hours = LOCAL_HOURS) {
  return hours.map((h) => moneyPct.map((x) => {
    const samples = rawRows.map((row) => ({
      days: row.days,
      ivPct: interp(row.xs, row.ys, x, true),
    }));
    const iv = projectTotalVariance(samples, h / 24);
    return iv == null ? null : +iv.toFixed(3);
  }));
}

function sameShape(a, b) {
  return Array.isArray(a) && Array.isArray(b)
    && a.length === b.length
    && a.every((row, i) => Array.isArray(row) && Array.isArray(b[i])
      && row.length === b[i].length);
}

function blendGrid(a, b, t) {
  return b.map((row, r) => row.map((v, c) => {
    const av = Number(a?.[r]?.[c]);
    const bv = Number(v);
    if (!Number.isFinite(bv)) return Number.isFinite(av) ? av : null;
    if (!Number.isFinite(av)) return bv;
    return av + (bv - av) * t;
  }));
}

function fmtAge(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '—';
  if (sec < 90) return `${Math.round(sec)}с`;
  if (sec < 5400) return `${Math.round(sec / 60)}м`;
  return `${(sec / 3600).toFixed(1)}ч`;
}

function buildRawModel(payload) {
  const surfaceData = payload.value || [];
  const firstStrikes = surfaceData[0]?.strikes || [];
  const snapshotSpot = Number(surfaceData[0]?.spot_at_snapshot)
    || Number(firstStrikes[Math.floor(firstStrikes.length / 2)]);
  if (!(snapshotSpot > 0) || !firstStrikes.length) return null;

  const rawRows = surfaceData.map((row) => {
    const rowSpot = Number(row.spot_at_snapshot) || snapshotSpot;
    const pairs = (row.strikes || []).map((strike, i) => ({
      x: (Number(strike) / rowSpot - 1) * 100,
      iv: Number(row.ivs?.[i]) * 100,
    })).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.iv)
      && p.iv > 0 && p.iv < 200).sort((a, b) => a.x - b.x);
    return {
      days: Number(row.days),
      expiry: row.expiry,
      xs: pairs.map((p) => p.x),
      ys: pairs.map((p) => p.iv),
    };
  }).filter((r) => Number.isFinite(r.days) && r.days > 0 && r.xs.length >= 3)
    .sort((a, b) => a.days - b.days);
  if (!rawRows.length) return null;

  let xLo = Math.max(-15, ...rawRows.map((r) => r.xs[0]));
  let xHi = Math.min(15, ...rawRows.map((r) => r.xs[r.xs.length - 1]));
  if (!(xHi > xLo + 1)) {
    xLo = Math.max(-15, rawRows[0].xs[0]);
    xHi = Math.min(15, rawRows[0].xs[rawRows[0].xs.length - 1]);
  }
  if (!(xHi > xLo + 1)) return null;
  const moneyPct = Array.from(
    { length: 41 },
    (_, i) => +(xLo + (xHi - xLo) * i / 40).toFixed(3));
  return { payload, snapshotSpot, rawRows, moneyPct, xLo, xHi };
}

function buildView(raw, mode) {
  const realZ = raw.rawRows.map((row) =>
    raw.moneyPct.map((x) => +interp(row.xs, row.ys, x, true).toFixed(3)));
  let yDte;
  let yTickText;
  let targetZ;
  let kind;
  if (mode === 'real') {
    yDte = raw.rawRows.map((r) => r.days);
    yTickText = raw.rawRows.map((r) => {
      const d = r.days;
      if (d < 1) return `${(d * 24).toFixed(1)}h`;
      if (d < 7) return `${d.toFixed(d < 2 ? 1 : 0)}d`;
      if (d < 28) return `${Math.round(d / 7)}W`;
      return `${Math.round(d / 30)}M`;
    });
    targetZ = realZ;
    kind = 'REAL EXPIRIES';
  } else {
    yDte = LOCAL_HOURS.map((h) => h / 24);
    yTickText = LOCAL_HOURS.map((h) => `${h}h`);
    targetZ = buildLocalProjection(raw.rawRows, raw.moneyPct);
    kind = 'LOCAL 24H';
  }
  const allZ = targetZ.flat().filter(Number.isFinite);
  if (!allZ.length) return null;
  const zMin = Math.min(...allZ);
  const rawMax = Math.max(...allZ);
  return {
    ...raw,
    mode,
    kind,
    yDte,
    yTickText,
    targetZ,
    displayZ: targetZ.map((r) => [...r]),
    zMin,
    zMax: rawMax > zMin ? rawMax : zMin + 0.01,
  };
}

function geometryFor(model, liveX, wake) {
  const x = clamp(liveX, model.xLo, model.xHi);
  const z = model.displayZ.map((row) => interp(model.moneyPct, row, x, true) ?? model.zMin);
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

  const ribbonHalf = Math.max((model.xHi - model.xLo) / 80, 0.12);
  const ribbonX = [
    Array(model.yDte.length).fill(clamp(x - ribbonHalf, model.xLo, model.xHi)),
    Array(model.yDte.length).fill(clamp(x + ribbonHalf, model.xLo, model.xHi)),
  ];
  const ribbonY = [model.yDte, model.yDte];
  const ribbonZ = ribbonX.map((col) => col.map((vx, r) =>
    interp(model.moneyPct, model.displayZ[r], vx, true) ?? model.zMin));

  const wakeX = [], wakeY = [], wakeZ = [];
  for (const p of wake) {
    const wx = clamp(p.x, model.xLo, model.xHi);
    for (let r = 0; r < model.yDte.length; r++) {
      wakeX.push(wx);
      wakeY.push(model.yDte[r]);
      wakeZ.push(interp(model.moneyPct, model.displayZ[r], wx, true) ?? model.zMin);
    }
    wakeX.push(null); wakeY.push(null); wakeZ.push(null);
  }

  const nearY = model.yDte[0];
  const recent = wake.length > 1 ? wake[wake.length - 2] : { x };
  const oldX = clamp(recent.x, model.xLo, model.xHi);
  const velocityX = [oldX, x];
  const velocityY = [nearY, nearY];
  const velocityZ = [
    interp(model.moneyPct, model.displayZ[0], oldX, true) ?? model.zMin,
    z[0],
  ];

  return {
    x, z, wallX, wallY, wallZ, i, j, k,
    ribbonX, ribbonY, ribbonZ,
    wakeX, wakeY, wakeZ,
    velocityX, velocityY, velocityZ,
  };
}

export function initIVSurface(elId) {
  const el = typeof elId === 'string' ? document.querySelector(elId) : elId;
  let hasPlot = false;
  let listenersOn = false;
  let rendering = false;
  let interacting = false, pointerHeld = false;
  let interactTimer = null;
  let pendingPayload = null;
  let lastPayload = null;
  let snapshotSig = null;
  let model = null;
  let mode = 'local';
  try {
    mode = localStorage.getItem('ivSurfaceMode') === 'real' ? 'real' : 'local';
  } catch { /* storage can be disabled */ }

  let targetLiveX = 0;
  let displayLiveX = 0;
  let lastFrame = performance.now();
  let lastLiveDraw = 0;
  let lastSurfaceDraw = 0;
  let lastStatusDraw = 0;
  let wake = [];
  let baseline = null;
  let baselineKey = null;
  let morphFrom = null;
  let morphStart = 0;

  const INIT_CAM = { eye: { x: 1.4, y: -1.4, z: 0.82 }, up: { x: 0, y: 0, z: 1 } };

  const statusEl = document.getElementById('iv-surface-status');
  const metricsEl = document.getElementById('iv-skew-momentum');
  const modeControls = ensureModeControls();

  function ensureModeControls() {
    if (!statusEl?.parentElement) return null;
    let box = document.getElementById('iv-surface-mode');
    if (box) return box;
    box = document.createElement('span');
    box.id = 'iv-surface-mode';
    box.style.display = 'inline-flex';
    box.style.gap = '4px';
    box.style.marginRight = '8px';
    const localBtn = document.createElement('button');
    const realBtn = document.createElement('button');
    localBtn.type = realBtn.type = 'button';
    localBtn.className = realBtn.className = 'btn btn-small';
    localBtn.textContent = 'LOCAL 24H';
    realBtn.textContent = 'REAL DTE';
    localBtn.dataset.mode = 'local';
    realBtn.dataset.mode = 'real';
    localBtn.addEventListener('click', () => setMode('local'));
    realBtn.addEventListener('click', () => setMode('real'));
    box.append(localBtn, realBtn);
    statusEl.parentElement.insertBefore(box, statusEl);
    return box;
  }

  function paintModeButtons() {
    if (!modeControls) return;
    [...modeControls.querySelectorAll('button')].forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.style.borderColor = active ? ORANGE : '';
      btn.style.color = active ? ORANGE : '';
      btn.style.background = active ? 'rgba(232,98,42,0.08)' : '';
    });
  }
  paintModeButtons();

  function setMode(next) {
    if (next === mode) return;
    mode = next;
    try { localStorage.setItem('ivSurfaceMode', mode); } catch { /* ignore */ }
    paintModeButtons();
    snapshotSig = null;
    wake = [];
    if (lastPayload) render(null, lastPayload, true);
  }

  function ready() {
    return typeof window !== 'undefined' && window.Plotly && el;
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
    return `${mode}|${payload.ts || ''}|${compact}`;
  }

  function normalizePayload(surfacePayload) {
    return Array.isArray(surfacePayload)
      ? { value: surfacePayload, status: 'delayed' }
      : (surfacePayload || {});
  }

  function currentContextKey() {
    const header = document.getElementById('hdr-setup')?.textContent || '';
    return header.trim() || 'idle';
  }

  function finishInteraction() {
    interacting = false;
    if (pendingPayload) {
      const p = pendingPayload;
      pendingPayload = null;
      render(null, p);
    }
  }

  function attachListeners() {
    if (listenersOn || !el?.on) return;
    listenersOn = true;
    el.on('plotly_relayouting', () => {
      interacting = true;
      if (interactTimer) clearTimeout(interactTimer);
      if (!pointerHeld) interactTimer = setTimeout(finishInteraction, 300);
    });
    el.on('plotly_relayout', () => {
      if (interactTimer) clearTimeout(interactTimer);
      if (!pointerHeld) interactTimer = setTimeout(finishInteraction, 140);
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
        interactTimer = setTimeout(finishInteraction, 140);
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

  function setLive(payload) {
    if (!model) return;
    model.payload = payload;
    const spot = Number(payload.spot_current) > 0
      ? Number(payload.spot_current) : model.snapshotSpot;
    const nextX = (spot / model.snapshotSpot - 1) * 100;
    targetLiveX = nextX;
    const now = performance.now();
    const last = wake.at(-1);
    if (!last || Math.abs(nextX - last.x) > 0.004 || now - last.ts > 900) {
      wake.push({ x: nextX, ts: now });
      wake = wake.filter((p) => now - p.ts <= WAKE_MS).slice(-MAX_WAKE);
    }
  }

  function velocityBpMin() {
    if (wake.length < 2) return 0;
    const end = wake.at(-1);
    const start = wake.find((p) => end.ts - p.ts <= 10000) || wake[0];
    const mins = Math.max((end.ts - start.ts) / 60000, 1 / 120);
    return (end.x - start.x) * 100 / mins;
  }

  function updateStatus(force = false) {
    if (!model) return;
    const now = performance.now();
    if (!force && now - lastStatusDraw < 500) return;
    lastStatusDraw = now;
    const g = geometryFor(model, displayLiveX, wake);
    const metrics = smileMetrics(model.moneyPct, model.displayZ, g.x);
    const key = currentContextKey();
    if (key !== baselineKey) {
      baselineKey = key;
      baseline = null;
      wake = wake.slice(-1);
    }
    if (!baseline && Number.isFinite(metrics.atm)) baseline = { ...metrics, x: g.x };

    const dAtm = Number.isFinite(metrics.atm) && Number.isFinite(baseline?.atm)
      ? metrics.atm - baseline.atm : null;
    const dSkew = Number.isFinite(metrics.skew) && Number.isFinite(baseline?.skew)
      ? metrics.skew - baseline.skew : null;
    const dCurv = Number.isFinite(metrics.curvature) && Number.isFinite(baseline?.curvature)
      ? metrics.curvature - baseline.curvature : null;
    const age = Date.now() / 1000 - Number(model.payload.ts || Date.now() / 1000);
    const vel = velocityBpMin();

    let volRegime = 'VOL STABLE';
    if (dAtm != null && dAtm > 0.45) volRegime = 'VOL EXPANSION';
    else if (dAtm != null && dAtm < -0.45) volRegime = 'VOL COMPRESSION';
    let skewRegime = '';
    if (dSkew != null && dSkew > 0.30) skewRegime = ' · PUT SKEW ↑';
    else if (dSkew != null && dSkew < -0.30) skewRegime = ' · CALL SKEW ↑';

    if (statusEl) {
      const live = model.payload.spot_status === 'live';
      const lead = live ? '●' : '◐';
      statusEl.innerText = `${lead} ${model.kind} · ${volRegime}${skewRegime} · SNAP ${fmtAge(age)}`;
      statusEl.className = `badge ${live ? 'live' : 'delayed'}`;
      statusEl.title = mode === 'local'
        ? 'LOCAL 24H — проекция полной дисперсии из реальных экспираций. Цена двигает live-срез, ribbon и wake; реальные IV меняются только при новом снимке.'
        : 'REAL DTE — реальные доступные экспирации Yahoo. Цена двигает live-срез и wake; поверхность морфится при новом снимке.';
    }

    if (metricsEl) {
      const f = (v, digits = 2) => Number.isFinite(v)
        ? `${v >= 0 ? '+' : ''}${v.toFixed(digits)}` : '—';
      metricsEl.style.display = 'inline-block';
      metricsEl.style.border = '1px solid rgba(138,135,125,0.4)';
      metricsEl.style.backgroundColor = Math.abs(dAtm || 0) > 0.45
        ? (dAtm > 0 ? 'rgba(198,55,60,0.10)' : 'rgba(46,125,79,0.10)')
        : 'transparent';
      metricsEl.style.color = '#8A877D';
      metricsEl.innerText = `LIVE ${g.x >= 0 ? '+' : ''}${g.x.toFixed(2)}%`
        + ` · ATM Δ${f(dAtm)}пп`
        + ` · SKEW Δ${f(dSkew)}пп`
        + ` · CURV Δ${f(dCurv)}пп`
        + ` · ${vel >= 0 ? '+' : ''}${Math.round(vel)}bp/min`;
      metricsEl.title = 'Изменения от начала текущей сделки/контекста. Они включают движение цены по задержанной улыбке и новые опционные снимки.';
    }
  }

  function tracesFor(g) {
    const surface = {
      type: 'surface',
      x: model.moneyPct,
      y: model.yDte,
      z: model.displayZ,
      colorscale: SURF_SCALE,
      cmin: model.zMin,
      cmax: model.zMax,
      showscale: true,
      opacity: 0.93,
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
      hovertemplate: '<b>Moneyness vs snapshot:</b> %{x:.1f}%<br><b>DTE:</b> %{y}'
        + '<br><b>IV:</b> %{z:.1f}%<extra></extra>',
      name: mode === 'local' ? 'LOCAL VARIANCE PROJECTION' : 'IV SNAPSHOT',
    };
    const atmZ = model.displayZ.map((row) => interp(model.moneyPct, row, 0, true) ?? model.zMin);
    const snapshotAtm = {
      type: 'scatter3d', mode: 'lines',
      x: Array(model.yDte.length).fill(0),
      y: model.yDte,
      z: atmZ,
      line: { color: 'rgba(20,20,15,0.45)', width: 3 },
      name: 'SNAPSHOT ATM',
      hoverinfo: 'skip',
    };
    const wakeTrace = {
      type: 'scatter3d', mode: 'lines',
      x: g.wakeX, y: g.wakeY, z: g.wakeZ,
      line: { color: 'rgba(232,98,42,0.18)', width: 2 },
      name: 'PRICE WAKE',
      hoverinfo: 'skip',
      showlegend: false,
    };
    const ribbon = {
      type: 'surface',
      x: g.ribbonX, y: g.ribbonY, z: g.ribbonZ,
      colorscale: [[0, ORANGE], [1, ORANGE]],
      showscale: false, opacity: 0.42,
      hoverinfo: 'skip',
      name: 'LIVE RIBBON',
    };
    const curtain = {
      type: 'mesh3d', x: g.wallX, y: g.wallY, z: g.wallZ,
      i: g.i, j: g.j, k: g.k,
      color: ORANGE, opacity: 0.14,
      flatshading: true, hoverinfo: 'skip', showlegend: false,
    };
    const ridge = {
      type: 'scatter3d', mode: 'lines',
      x: Array(model.yDte.length).fill(g.x),
      y: model.yDte,
      z: g.z,
      line: { color: ORANGE, width: 7 },
      name: 'LIVE SPOT SLICE',
      hovertemplate: 'live displacement=%{x:+.2f}%<br>DTE=%{y}<br>IV=%{z:.1f}%<extra></extra>',
    };
    const dot = {
      type: 'scatter3d', mode: 'markers',
      x: [g.x], y: [model.yDte[0]], z: [g.z[0] + 0.01],
      marker: { size: 7, color: ORANGE, line: { color: '#fff', width: 1 } },
      name: 'LIVE ATM', showlegend: false,
      hovertemplate: 'ближний срок<br>live displacement=%{x:+.2f}%<br>IV=%{z:.1f}%<extra></extra>',
    };
    const velocity = {
      type: 'scatter3d', mode: 'lines+markers',
      x: g.velocityX, y: g.velocityY, z: g.velocityZ,
      line: { color: 'rgba(232,98,42,0.72)', width: 5 },
      marker: { size: [2, 5], color: ORANGE },
      name: 'PRICE VECTOR',
      hoverinfo: 'skip', showlegend: false,
    };
    return [surface, snapshotAtm, wakeTrace, ribbon, curtain, ridge, dot, velocity];
  }

  function layoutFor() {
    return {
      margin: { t: 5, b: 5, l: 0, r: 55 },
      uirevision: 'iv-surface-ui-v4',
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { family: FONT, color: INK },
      legend: { orientation: 'h', x: 0, y: 1.03, font: { size: 9 } },
      scene: {
        dragmode: 'orbit',
        uirevision: 'iv-surface-camera-v4',
        xaxis: {
          title: { text: 'MONEYNESS VS SNAPSHOT %', font: { family: FONT, size: 12, color: '#111' } },
          tickfont: { family: FONT, size: 10, color: '#222' },
          gridcolor: RULE, zeroline: true, zerolinecolor: 'rgba(20,20,15,0.55)',
          zerolinewidth: 2, ticksuffix: '%', showbackground: true,
          backgroundcolor: 'rgba(240,238,232,0.6)',
        },
        yaxis: {
          title: { text: mode === 'local' ? 'LOCAL HORIZON 0–24H' : 'REAL DTE',
            font: { family: FONT, size: 12, color: '#111' } },
          tickfont: { family: FONT, size: 10, color: '#222' },
          gridcolor: RULE, zeroline: false,
          tickvals: model.yDte,
          ticktext: model.yTickText,
          showbackground: true,
          backgroundcolor: 'rgba(240,238,232,0.6)',
        },
        zaxis: {
          title: { text: 'IV %', font: { family: FONT, size: 12, color: '#111' } },
          tickfont: { family: FONT, size: 10, color: '#222' },
          gridcolor: RULE, zeroline: false, ticksuffix: '%',
          range: [model.zMin, model.zMax + Math.max((model.zMax - model.zMin) * 0.08, 0.1)],
          showbackground: true,
          backgroundcolor: 'rgba(240,238,232,0.6)',
        },
        aspectmode: 'manual',
        aspectratio: { x: 1.35, y: mode === 'local' ? 0.82 : 1, z: 0.78 },
        bgcolor: 'rgba(248,246,242,0.3)',
      },
    };
  }

  function applyDynamicGeometry(includeSurface = false) {
    if (!ready() || !hasPlot || !model || interacting || rendering) return;
    const g = geometryFor(model, displayLiveX, wake);
    const update = {
      x: [
        g.wakeX,
        g.ribbonX,
        g.wallX,
        Array(model.yDte.length).fill(g.x),
        [g.x],
        g.velocityX,
      ],
      y: [
        g.wakeY,
        g.ribbonY,
        g.wallY,
        model.yDte,
        [model.yDte[0]],
        g.velocityY,
      ],
      z: [
        g.wakeZ,
        g.ribbonZ,
        g.wallZ,
        g.z,
        [g.z[0] + 0.01],
        g.velocityZ,
      ],
    };
    window.Plotly.restyle(
      el, update, [WAKE, LIVE_RIBBON, LIVE_CURTAIN, LIVE_RIDGE, LIVE_DOT, VELOCITY]);
    if (includeSurface) {
      const atmZ = model.displayZ.map((row) =>
        interp(model.moneyPct, row, 0, true) ?? model.zMin);
      window.Plotly.restyle(el, {
        z: [model.displayZ, atmZ],
      }, [SURFACE, SNAPSHOT_ATM]);
    }
    updateStatus();
  }

  function render(_state, surfacePayload, force = false) {
    const payload = normalizePayload(surfacePayload);
    lastPayload = payload;
    const surfaceData = payload.value;
    if (!surfaceData || surfaceData.length === 0) {
      if (el) el.style.opacity = '0';
      const empty = document.getElementById('iv-surface-empty');
      if (empty) empty.style.display = 'flex';
      if (statusEl) statusEl.innerText = '○ ОЖИДАНИЕ ДАННЫХ';
      return;
    }
    const sig = payloadSignature(payload);
    if (rendering) {
      pendingPayload = payload;
      return;
    }
    if (!force && hasPlot && sig === snapshotSig) {
      setLive(payload);
      updateStatus();
      return;
    }
    if (interacting) {
      pendingPayload = payload;
      setLive(payload);
      return;
    }

    const raw = buildRawModel(payload);
    const nextModel = raw ? buildView(raw, mode) : null;
    if (!nextModel) return;
    const oldGrid = model?.displayZ;
    if (sameShape(oldGrid, nextModel.targetZ)) {
      nextModel.displayZ = oldGrid.map((r) => [...r]);
      morphFrom = oldGrid.map((r) => [...r]);
      morphStart = performance.now();
    } else {
      morphFrom = null;
      morphStart = 0;
    }
    model = nextModel;
    setLive(payload);
    if (!hasPlot) displayLiveX = targetLiveX;
    const g = geometryFor(model, displayLiveX, wake);

    if (el) el.style.opacity = '1';
    const empty = document.getElementById('iv-surface-empty');
    if (empty) empty.style.display = 'none';
    const layout = layoutFor();
    const config = {
      responsive: true,
      displayModeBar: true,
      displaylogo: false,
      modeBarButtonsToRemove: ['toImage', 'sendDataToCloud'],
      scrollZoom: true,
      doubleClick: 'reset',
    };
    rendering = true;
    const traces = tracesFor(g);
    const layout = layoutFor();
    if (hasPlot && el._fullLayout && el._fullLayout.scene && el._fullLayout.scene.camera) {
      layout.scene.camera = JSON.parse(JSON.stringify(el._fullLayout.scene.camera));
    } else {
      layout.scene.camera = INIT_CAM;
    }

    let write;
    if (!hasPlot) {
      write = Plotly.newPlot(el, traces, layout, config);
      hasPlot = true;
      attachListeners();
    } else {
      write = Plotly.react(el, traces, layout, config);
    }
    Promise.resolve(write).then(() => {
      rendering = false;
      hasPlot = true;
      snapshotSig = sig;
      attachListeners();
      updateStatus(true);
      if (pendingPayload) {
        const p = pendingPayload;
        pendingPayload = null;
        render(null, p);
      }
    }).catch((err) => {
      rendering = false;
      console.error('[seiltanzer] IV surface render failed', err);
    });
  }

  function frame(now) {
    requestAnimationFrame(frame);
    const dt = Math.min((now - lastFrame) / 1000, 0.05);
    lastFrame = now;
    if (!hasPlot || !model || interacting || rendering) return;

    const next = approach(displayLiveX, targetLiveX, dt, 7);
    const priceChanged = Math.abs(next - displayLiveX) > 0.00002;
    displayLiveX = next;

    let surfaceChanged = false;
    if (morphFrom && morphStart) {
      const t = clamp((now - morphStart) / MORPH_MS, 0, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      model.displayZ = blendGrid(morphFrom, model.targetZ, eased);
      surfaceChanged = true;
      if (t >= 1) {
        model.displayZ = model.targetZ.map((r) => [...r]);
        morphFrom = null;
        morphStart = 0;
      }
    }

    if (priceChanged && now - lastLiveDraw > 66) {
      lastLiveDraw = now;
      applyDynamicGeometry(false);
    }
    if (surfaceChanged && now - lastSurfaceDraw > 110) {
      lastSurfaceDraw = now;
      applyDynamicGeometry(true);
    }
    updateStatus();
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

  function destroy() {
    if (hasPlot) window.Plotly.purge(el);
    hasPlot = false;
  }

  return { render, updateLive: setLive, setMode, destroy };
}
