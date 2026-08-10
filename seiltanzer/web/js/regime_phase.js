import { $ } from './util.js';
import { createPlotlyCameraGuard } from './plotly_camera_guard.js';
import { subscribeMarketTick } from './market_bus.js';
import { ensurePremiumAnalyticsTheme } from './premium_analytics_theme.js';
import { isAnalyticsMobile } from './analytics_mobile.js';
import { attachTerminal3DToolbar } from './plotly_terminal_toolbar.js';

let plotEl;
let statusEl;
let emptyEl;
let currentHorizon = '6H';
let regimeData = null;
let cameraGuard = null;
let refreshTimer = null;
let unsubscribeTick = null;
let anchorPrice = null;
let liveProbe = null;
let liveTarget = null;
let probeRaf = null;
let lastRestyle = 0;
let lastProbeFrame = 0;

const INIT_CAM = {
  eye: { x: 1.72, y: -1.86, z: 1.28 },
  up: { x: 0, y: 0, z: 1 },
};

export function initRegimePhase() {
  ensurePremiumAnalyticsTheme();
  plotEl = $('#regime-phase-plot');
  statusEl = $('#regime-status');
  emptyEl = $('#regime-phase-empty');
  if (plotEl) {
    plotEl.style.touchAction = 'none';
    cameraGuard = createPlotlyCameraGuard(plotEl, INIT_CAM);
  }
  $('#btn-regime-6h')?.addEventListener('click', () => setHorizon('6H'));
  $('#btn-regime-24h')?.addEventListener('click', () => setHorizon('24H'));
  $('#btn-regime-3d')?.addEventListener('click', () => setHorizon('3D'));
  window.addEventListener?.('seiltanzer:analytics-mobile-resize', () => renderRegimePlot());
  unsubscribeTick = subscribeMarketTick(onMarketTick);
  fetchRegimePhase();
  refreshTimer = setInterval(fetchRegimePhase, 300000);
}

function setHorizon(horizon) {
  currentHorizon = horizon;
  $('#btn-regime-6h')?.classList.toggle('active', horizon === '6H');
  $('#btn-regime-24h')?.classList.toggle('active', horizon === '24H');
  $('#btn-regime-3d')?.classList.toggle('active', horizon === '3D');
  renderRegimePlot();
}

export async function fetchRegimePhase() {
  try {
    const res = await fetch('/api/analytics/regime-phase', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    regimeData = await res.json();
    anchorPrice = null;
    liveProbe = null;
    liveTarget = null;
    renderRegimePlot();
  } catch (err) {
    console.warn('Regime phase fetch error:', err);
    if (statusEl) statusEl.textContent = '○ MACRO REGIME OFFLINE';
  }
}

export function updateLiveRegimePhase() {}

function onMarketTick(tick) {
  if (!regimeData?.available || !Number.isFinite(tick.price)) return;
  if (!anchorPrice) anchorPrice = tick.price;
  const c = regimeData.current || {};
  const logMove = Math.log(tick.price / Math.max(anchorPrice, 1e-9));
  const xMicro = clamp(logMove * 170, -0.58, 0.58);
  const impulse = clamp(Number(tick.impulse || 0), 0, 1);
  const speed = clamp(Math.abs(Number(tick.speedBpSec || 0)) / 5, 0, 1);
  liveTarget = {
    x: Number(c.x_trend || 0) + xMicro,
    y: Number(c.y_vol || 0) + impulse * 0.20,
    z: Math.max(0, Number(c.z_stress || 0) + impulse * 0.26 + speed * 0.12),
    impulse,
    tick,
  };
  if (!liveProbe) liveProbe = { ...liveTarget };
  ensureProbeAnimation();
}

function ensureProbeAnimation() {
  if (probeRaf || !liveTarget) return;
  const animate = (now) => {
    if (!liveTarget || !liveProbe) { probeRaf = null; return; }
    const mobile = isAnalyticsMobile();
    const minFrame = mobile ? 32 : 15;
    if (now - lastProbeFrame >= minFrame) {
      lastProbeFrame = now;
      const k = mobile ? 0.22 : 0.16;
      liveProbe.x += (liveTarget.x - liveProbe.x) * k;
      liveProbe.y += (liveTarget.y - liveProbe.y) * k;
      liveProbe.z += (liveTarget.z - liveProbe.z) * k;
      const restyleEvery = mobile ? 50 : 65;
      if (now - lastRestyle > restyleEvery) {
        restyleLiveProbe();
        lastRestyle = now;
      }
    }
    const err = Math.hypot(liveTarget.x - liveProbe.x, liveTarget.y - liveProbe.y, liveTarget.z - liveProbe.z);
    if (err > 0.002 || Number(liveTarget.impulse || 0) > 0.02) {
      liveTarget.impulse *= mobile ? 0.955 : 0.965;
      probeRaf = requestAnimationFrame(animate);
    } else {
      probeRaf = null;
    }
  };
  probeRaf = requestAnimationFrame(animate);
}

function restyleLiveProbe() {
  if (!plotEl || !window.Plotly || !plotEl.data?.length || !liveProbe) return;
  const idx = plotEl.data.findIndex((t) => t.name === 'LIVE MICRO-PROBE');
  const halo = plotEl.data.findIndex((t) => t.name === 'LIVE PROBE HALO');
  if (idx >= 0) window.Plotly.restyle(plotEl, { x: [[liveProbe.x]], y: [[liveProbe.y]], z: [[liveProbe.z]] }, [idx]);
  if (halo >= 0) {
    const size = (isAnalyticsMobile() ? 13 : 18) + (isAnalyticsMobile() ? 10 : 16) * Number(liveProbe.impulse || 0);
    window.Plotly.restyle(plotEl, { x: [[liveProbe.x]], y: [[liveProbe.y]], z: [[liveProbe.z]], 'marker.size': [[size]] }, [halo]);
  }
  updateLiveHud();
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, Number(v))); }

function fmtAge(seconds) {
  const s = Number(seconds || 0);
  if (s < 3600) return `${Math.max(0, Math.round(s / 60))}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function regimeColor(regime, alpha = 1) {
  const rgb = {
    'VOL SHOCK': [242, 82, 102], 'TREND EXPANSION': [240, 171, 74],
    'CALM TREND': [67, 215, 159], 'COMPRESSION': [75, 143, 226],
    'RECOVERY': [183, 130, 238], 'CHOP': [138, 154, 168],
  }[regime] || [138, 154, 168];
  return alpha >= 1 ? `rgb(${rgb.join(',')})` : `rgba(${rgb.join(',')},${alpha})`;
}

function rangeFor(values, fallback, minSpan = 1.4, lowerBound = null) {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return fallback;
  let lo = Math.min(...finite), hi = Math.max(...finite);
  const centre = (lo + hi) / 2;
  const span = Math.max(hi - lo, minSpan);
  lo = centre - span * .68; hi = centre + span * .68;
  if (lowerBound != null) lo = Math.max(lowerBound, lo);
  return [Math.max(fallback[0], lo), Math.min(fallback[1], hi)];
}

function stressBar(label, value, max = 3) {
  const v = Math.max(0, Math.min(max, Number(value || 0)));
  const pct = Math.round(v / max * 100);
  const color = pct > 65 ? '#d94255' : pct > 35 ? '#d99535' : '#4d7eaa';
  return `<div style="display:grid;grid-template-columns:88px 1fr 34px;gap:6px;align-items:center;margin:4px 0"><span>${label}</span><span style="height:6px;background:#e7e4dd;display:block;position:relative;border-radius:4px;overflow:hidden"><i style="display:block;height:100%;width:${pct}%;background:${color};box-shadow:0 0 8px ${color}66"></i></span><b>${v.toFixed(2)}</b></div>`;
}

function buildTimeline(traj) {
  if (!traj?.length) return '';
  const segments = []; let last = null;
  for (const p of traj) { if (!last || last.regime !== p.regime) { last = { regime: p.regime || 'CHOP', count: 1 }; segments.push(last); } else last.count++; }
  const total = Math.max(1, traj.length);
  return `<div style="margin-top:9px;border-top:1px solid #d9d6ce;padding-top:8px"><div style="font-size:9px;color:#777;margin-bottom:5px">REGIME TRANSITION · 24H</div><div style="height:12px;display:flex;overflow:hidden;border-radius:999px;background:#eee">${segments.map((s) => `<span title="${s.regime}" style="width:${100 * s.count / total}%;background:${regimeColor(s.regime)}"></span>`).join('')}</div><div style="display:flex;justify-content:space-between;font-size:9px;color:#777;margin-top:4px"><span>−24H</span><span>NOW</span></div></div>`;
}

function ensureExtraSummary(summary) {
  const card = $('#regime-summary-card'); if (!card) return;
  let extra = $('#regime-extra-metrics');
  if (!extra) { extra = document.createElement('div'); extra.id = 'regime-extra-metrics'; extra.style.cssText = 'border-top:1px solid #d9d6ce;padding-top:8px;margin-top:4px;line-height:1.65'; card.appendChild(extra); }
  const vol = summary.vol_index || {}; const c = summary.stress_components || {};
  extra.innerHTML = `<div class="analytics-metric-grid"><div class="analytics-metric-tile"><small>REGIME AGE</small><b>${fmtAge(summary.regime_age_seconds)}</b></div><div class="analytics-metric-tile"><small>TRANSITION v</small><b>${Number(summary.transition_velocity || 0).toFixed(2)}/h</b></div><div class="analytics-metric-tile"><small>ACCELERATION</small><b>${Number(summary.transition_acceleration || 0).toFixed(2)}/h²</b></div><div class="analytics-metric-tile"><small>VOL INDEX</small><b>${vol.key ? vol.key.toUpperCase() : '—'} ${vol.value == null ? '—' : Number(vol.value).toFixed(1)}</b></div></div><div style="margin-top:8px;font-size:9px;color:#777">STRESS DECOMPOSITION</div>${stressBar('CROSS-ASSET', c.cross_asset)}${stressBar('VOL IMPULSE', c.realized_impulse)}${stressBar('SHOCK', c.shock)}${stressBar('DISLOCATION', c.trend_dislocation)}<div id="regime-live-probe-hud" style="margin-top:8px;padding:7px;border-radius:6px;background:#f2f5f6;border:1px solid #dce2e4;color:#50606d">LIVE MICRO-PROBE · waiting for ticks</div>${buildTimeline(regimeData?.trajectory_24h || [])}<div style="margin-top:7px;color:#777;font-size:9px">${summary.source?.source || '—'} · ${summary.stress_source || '—'}</div>`;
}

function updateLiveHud() {
  const el = $('#regime-live-probe-hud'); if (!el || !liveProbe || !anchorPrice) return;
  const t = liveProbe.tick || liveTarget?.tick || {};
  el.innerHTML = `<b>LIVE MICRO-PROBE</b> · X ${liveProbe.x.toFixed(2)} · Y ${liveProbe.y.toFixed(2)} · Z ${liveProbe.z.toFixed(2)}<br>tick ${Number(t.retBp || 0) >= 0 ? '+' : ''}${Number(t.retBp || 0).toFixed(2)}bp · impulse ${Math.round(Number(liveProbe.impulse || 0) * 100)}% · derived from live price, not a new policy vote`;
}

function boxMesh(cx, cy, cz, sx, sy, sz, color, name, opacity = .085) {
  const x = [], y = [], z = [];
  for (const dx of [-1, 1]) for (const dy of [-1, 1]) for (const dz of [-1, 1]) { x.push(cx + dx * sx / 2); y.push(cy + dy * sy / 2); z.push(Math.max(0, cz + dz * sz / 2)); }
  const faces = [[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],[2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]];
  return { type: 'mesh3d', name, x, y, z, i: faces.map((f) => f[0]), j: faces.map((f) => f[1]), k: faces.map((f) => f[2]), color, opacity, hoverinfo: 'skip', showlegend: false, flatshading: false, lighting: { ambient: .82, diffuse: .24, roughness: .96 } };
}

function trajectoryForHorizon() {
  const key = currentHorizon === '24H' ? 'trajectory_24h' : currentHorizon === '3D' ? 'trajectory_3d' : 'trajectory_6h';
  const raw = regimeData?.[key] || [];
  if (!isAnalyticsMobile() || raw.length <= 72) return raw;
  const stride = Math.max(1, Math.ceil(raw.length / 72));
  return raw.filter((_, i) => i % stride === 0 || i === raw.length - 1);
}

function renderRegimePlot() {
  if (!plotEl || !window.Plotly) return;
  if (!regimeData?.available) {
    if (emptyEl) { emptyEl.style.display = 'flex'; emptyEl.textContent = `○ ${regimeData?.reason || 'PHASE SPACE UNAVAILABLE'}`; }
    if (statusEl) statusEl.textContent = '○ НЕДОСТАТОЧНО РЕАЛЬНОЙ ИСТОРИИ';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  const mobile = isAnalyticsMobile();
  const summary = regimeData.summary || {}; const current = regimeData.current || {};
  if ($('#regime-val-label')) { $('#regime-val-label').textContent = current.regime || 'CHOP'; $('#regime-val-label').style.color = regimeColor(current.regime); }
  if ($('#regime-val-x')) $('#regime-val-x').textContent = Number(current.x_trend || 0).toFixed(2);
  if ($('#regime-val-y')) $('#regime-val-y').textContent = Number(current.y_vol || 0).toFixed(2);
  if ($('#regime-val-z')) $('#regime-val-z').textContent = Number(current.z_stress || 0).toFixed(2);
  if ($('#regime-val-conf')) $('#regime-val-conf').textContent = `${Number(current.confidence || 0).toFixed(0)}%`;
  if ($('#regime-val-dist')) $('#regime-val-dist').textContent = Number(summary.boundary_distance || 0).toFixed(2);
  if (statusEl) statusEl.textContent = `● ${current.regime || 'CHOP'} · Z ${Number(current.z_stress || 0).toFixed(2)} · ${currentHorizon}`;
  const headline = $('#regime-human-line');
  if (headline) {
    const vector = current.velocity_vector || {};
    const acceleration = Number(summary.transition_acceleration || 0);
    const target = Number(vector.y || 0) > .08 || Number(vector.z || 0) > .08
      ? 'VOL SHOCK' : Math.abs(Number(vector.x || 0)) > .12 ? 'TREND EXPANSION' : (current.regime || 'CHOP');
    headline.textContent = `REGIME MOVING TOWARD ${target} · SPEED ${Number(vector.speed || 0) < .01 ? 'FLAT' : acceleration > .08 ? '↑' : '→'}`;
  }
  ensureExtraSummary(summary);

  const traj = trajectoryForHorizon();
  const x = traj.map((p) => Number(p.x)), y = traj.map((p) => Number(p.y)), z = traj.map((p) => Number(p.z));
  const labels = traj.map((p) => `${p.regime || '—'} · ${new Date(Number(p.ts) * 1000).toISOString().slice(5,16).replace('T',' ')} UTC`);
  const speeds = traj.map((p, i) => { if (!i) return 0; const a = traj[i - 1], dt = Math.max((Number(p.ts) - Number(a.ts)) / 3600, 1 / 12); return Math.hypot(Number(p.x)-Number(a.x), Number(p.y)-Number(a.y), Number(p.z)-Number(a.z)) / dt; });
  const maxSpeed = Math.max(.001, ...speeds); const ages = traj.map((_, i) => i / Math.max(1, traj.length - 1));
  const traces = [
    boxMesh(1.55, 0.0, 0.36, 1.35, 1.45, .72, '#2e9f74', 'CALM +', mobile?.06:.085),
    boxMesh(-1.55, 0.0, 0.36, 1.35, 1.45, .72, '#2e9f74', 'CALM -', mobile?.06:.085),
    boxMesh(0.0, -1.35, .28, 1.45, 1.0, .56, '#477fba', 'COMPRESSION', mobile?.06:.085),
    boxMesh(1.55, 1.05, .8, 1.5, 1.15, 1.0, '#d88b2c', 'TREND EXP +', mobile?.06:.085),
    boxMesh(-1.55, 1.05, .8, 1.5, 1.15, 1.0, '#d88b2c', 'TREND EXP -', mobile?.06:.085),
    boxMesh(0.0, 1.9, 2.0, 2.6, 1.1, 1.7, '#b92f44', 'VOL SHOCK', mobile?.075:.105),
  ];
  if (!mobile) traces.push({ type: 'scatter3d', mode: 'lines', name: 'TRAIL HALO', x, y, z, line: { color: 'rgba(61,156,205,.13)', width: 15 }, hoverinfo: 'skip', showlegend: false });
  traces.push(
    { type: 'scatter3d', mode: 'lines+markers', name: 'REAL TRAJECTORY', x, y, z, text: labels, hovertemplate: mobile ? undefined : '%{text}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>', hoverinfo: mobile ? 'skip' : undefined, line: { color: '#58b9df', width: mobile ? 4 : 6 }, marker: { size: speeds.map((s, i) => (mobile?2:2.5) + (mobile?2.8:4.2) * Math.min(1, s / maxSpeed) + (mobile?.8:1.5) * ages[i]), color: z, colorscale: [[0,'#2b7091'],[.4,'#3ec3ad'],[.72,'#e6a947'],[1,'#ef5268']], cmin: 0, cmax: Math.max(1, ...z), showscale: false, line: { color: 'rgba(255,255,255,.5)', width: .4 }, opacity: .92 } },
    { type: 'scatter3d', mode: 'lines', name: 'FLOOR SHADOW', x, y, z: z.map(() => 0), line: { color: 'rgba(107,171,202,.18)', width: mobile ? 2 : 3, dash: 'dot' }, hoverinfo: 'skip', showlegend: false },
    { type: 'scatter3d', mode: 'lines', name: 'STRESS STEM', x: [Number(current.x_trend || 0), Number(current.x_trend || 0)], y: [Number(current.y_vol || 0), Number(current.y_vol || 0)], z: [0, Number(current.z_stress || 0)], line: { color: 'rgba(239,82,104,.48)', width: mobile ? 3 : 5, dash: 'dot' }, hoverinfo: 'skip', showlegend: false },
    { type: 'scatter3d', mode: 'markers', name: 'LIVE PROBE HALO', x: [Number(current.x_trend || 0)], y: [Number(current.y_vol || 0)], z: [Number(current.z_stress || 0)], marker: { size: mobile ? 13 : 18, color: 'rgba(255,187,80,.16)', line: { color: 'rgba(255,187,80,.42)', width: 1 } }, hoverinfo: 'skip', showlegend: false },
    { type: 'scatter3d', mode: 'markers+text', name: 'LIVE MICRO-PROBE', x: [Number(current.x_trend || 0)], y: [Number(current.y_vol || 0)], z: [Number(current.z_stress || 0)], marker: { size: mobile ? 5 : 7, color: '#ffbb50', line: { color: '#fff', width: 1 } }, text: ['LIVE'], textposition: 'top center', hoverinfo: 'skip', showlegend: false },
    { type: 'scatter3d', mode: 'markers+text', name: 'MODEL STATE', x: [Number(current.x_trend || 0)], y: [Number(current.y_vol || 0)], z: [Number(current.z_stress || 0)], marker: { size: mobile ? 7 : 9, color: regimeColor(current.regime), line: { color: '#fff', width: 1.4 } }, text: [mobile ? 'STATE' : (current.regime || 'STATE')], textposition: 'bottom center', hoverinfo: 'skip', showlegend: false },
  );

  const references = regimeData.reference_points || {};
  const addReference = (point, name, color, symbol) => {
    if (!point || ![point.x, point.y, point.z].every((value) => Number.isFinite(Number(value)))) return;
    traces.push({type:'scatter3d',mode:'markers+text',name,x:[Number(point.x)],y:[Number(point.y)],z:[Number(point.z)],marker:{size:mobile?5:7,color,symbol,line:{color:'#fff',width:1}},text:[mobile?name.split(' ')[0]:name],textposition:'top center',hoverinfo:'skip',showlegend:false});
  };
  addReference(references.entry, 'ENTRY', '#d7dce0', 'diamond');
  addReference(references.previous_ai_review, 'PREV AI', '#c79cff', 'square');

  const vv = current.velocity_vector || summary.velocity_vector || {}; const speed = Number(vv.speed || 0);
  if (speed > .001) { const s = .72 / speed; traces.push({ type: 'cone', showlegend: false, hoverinfo: 'skip', anchor: 'tail', sizemode: 'absolute', sizeref: mobile ? .23 : .30, x: [Number(current.x_trend || 0)], y: [Number(current.y_vol || 0)], z: [Number(current.z_stress || 0)], u: [Number(vv.x || 0) * s], v: [Number(vv.y || 0) * s], w: [Number(vv.z || 0) * s], colorscale: [[0,'#ffbb50'],[1,'#ef5268']], showscale: false }); }
  const av = current.acceleration_vector || summary.acceleration_vector || {}; const accel = Number(av.magnitude || 0);
  if (accel > .001) { const s = .48 / accel; traces.push({type:'cone',name:'ACCELERATION',showlegend:false,hoverinfo:'skip',anchor:'tail',sizemode:'absolute',sizeref:mobile?.17:.22,x:[Number(current.x_trend||0)],y:[Number(current.y_vol||0)],z:[Number(current.z_stress||0)],u:[Number(av.x||0)*s],v:[Number(av.y||0)*s],w:[Number(av.z||0)*s],colorscale:[[0,'#b88cff'],[1,'#f0c4ff']],showscale:false}); }

  const xr = rangeFor([...x, Number(current.x_trend || 0)], [-3, 3], 2.4), yr = rangeFor([...y, Number(current.y_vol || 0)], [-3, 3], 2.4);
  const maxZ = Math.max(0, ...z.filter(Number.isFinite), Number(current.z_stress || 0)); const zr = [0, Math.min(3, Math.max(1.45, maxZ + .62))];
  traces.push(
    {type:'scatter3d',mode:'lines',name:'XZ GHOST',x,y:y.map(()=>yr[0]),z,line:{color:'rgba(90,172,208,.15)',width:mobile?1:2,dash:'dot'},hoverinfo:'skip',showlegend:false},
    {type:'scatter3d',mode:'lines',name:'YZ GHOST',x:x.map(()=>xr[0]),y,z,line:{color:'rgba(90,172,208,.15)',width:mobile?1:2,dash:'dot'},hoverinfo:'skip',showlegend:false},
  );
  const layout = { margin: { l: 0, r: 0, b: 0, t: 0 }, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', showlegend: false, hovermode: mobile ? false : undefined, uirevision: 'macro-phase-premium-live-v3', scene: { xaxis: { title: mobile ? 'TREND' : 'TREND · X', range: xr, gridcolor: 'rgba(182,207,222,.16)', zerolinecolor: 'rgba(230,238,243,.38)', color: '#b8cad5', showspikes: false, tickfont:{size:mobile?8:10} }, yaxis: { title: mobile ? 'VOL' : 'VOL REGIME · Y', range: yr, gridcolor: 'rgba(182,207,222,.16)', zerolinecolor: 'rgba(230,238,243,.38)', color: '#b8cad5', showspikes: false, tickfont:{size:mobile?8:10} }, zaxis: { title: mobile ? 'STRESS' : 'FRAGILITY / STRESS · Z', range: zr, gridcolor: 'rgba(182,207,222,.14)', zerolinecolor: 'rgba(230,238,243,.32)', color: '#b8cad5', showspikes: false, tickfont:{size:mobile?8:10} }, bgcolor: 'rgba(4,12,20,0)', aspectmode: 'manual', aspectratio: mobile ? { x: 1.08, y: 1.0, z: .94 } : { x: 1.25, y: 1.08, z: .86 } } };
  cameraGuard?.beforeWrite?.();
  window.Plotly.react(plotEl, traces, layout, { responsive: false, displayModeBar: false, scrollZoom: !mobile });
  cameraGuard?.afterWrite?.();
  attachTerminal3DToolbar({
    plot: plotEl, container: plotEl.parentElement, guard: cameraGuard,
    homeCamera: INIT_CAM, key: 'macro-regime',
  });
  if (!liveProbe) liveProbe = { x: Number(current.x_trend || 0), y: Number(current.y_vol || 0), z: Number(current.z_stress || 0), impulse: 0 };
  updateLiveHud();
}
