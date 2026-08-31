// SEILTANZER UNIVERSE LAB — isolated, read-only visual scenes.
// Geometry is deterministic and changes only when observed data changes.

const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Number(v)));
const finite = (v) => v != null && Number.isFinite(Number(v));
const fmt = (v, digits = 2) => finite(v) ? Number(v).toFixed(digits) : '—';
const fmtPct = (v, digits = 1) => finite(v) ? `${(Number(v) * 100).toFixed(digits)}%` : '—';
const fmtBp = (v) => finite(v) ? `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(1)} bp` : '—';
const fmtSigned = (v, digits = 2) => finite(v) ? `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(digits)}` : '—';
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]));

const FEATURE_COLORS = {
  price: '#73d8ff', vol: '#5f8dff', option: '#b89cff', option_dynamics: '#d59cff',
  cross: '#49d79a', regime: '#f6b85f', rates: '#7fd6c2', quality: '#8995a3', other: '#8995a3',
};

const U = {
  tick: null, rates: null, edge: null,
  ratesBusy: false, edgeBusy: false, ratesNext: 0, edgeNext: 0, lastInstrument: null,
  edgeFrameKey: null, edgeCoords: {},
  // Session-local toggles only. Every page load starts visible by design.
  ratesEnabled: true, edgeEnabled: true,
};

function badge(id, tone, text) {
  const node = $(id); if (!node) return;
  node.className = `status-pill ${tone}`; node.textContent = text;
}
function metricRow(key, value, tone = '') {
  return `<div class="metric-row"><span class="k">${esc(key)}</span><span class="v ${tone}">${esc(value)}</span></div>`;
}
function path(obj, dotted) { return dotted.split('.').reduce((value, key) => value == null ? undefined : value[key], obj); }
function displayValue(value, digits = 3) {
  if (value == null) return '—';
  if (typeof value === 'number') return Number.isFinite(value) ? value.toFixed(digits) : '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}
function plotAvailable() { return !!(window.Plotly && typeof window.Plotly.react === 'function'); }
function plotConfig() { return { responsive: true, displaylogo: false, displayModeBar: false, scrollZoom: true }; }
function sceneLayout(camera = { eye: { x: 1.45, y: 1.35, z: .95 } }) {
  const axis = { showgrid: true, gridcolor: 'rgba(150,175,205,.08)', zerolinecolor: 'rgba(150,175,205,.14)', showticklabels: false, title: '', backgroundcolor: 'rgba(0,0,0,0)' };
  return {
    margin: { l: 0, r: 0, t: 0, b: 0 }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)', showlegend: false,
    hoverlabel: { bgcolor: '#080c12', bordercolor: 'rgba(170,195,220,.25)', font: { family: 'IBM Plex Mono, monospace', size: 10, color: '#dce6ef' } },
    scene: { bgcolor: 'rgba(0,0,0,0)', xaxis: axis, yaxis: axis, zaxis: axis, aspectmode: 'cube', camera, dragmode: 'orbit' },
    uirevision: 'universe-v2',
  };
}
function purge(kind) {
  if (!plotAvailable()) return;
  const node = kind === 'rates' ? $('rates-orbit-chart') : $('edge-universe-chart');
  if (node) window.Plotly.purge(node);
}
function ringTrace(radius, z = 0, color = 'rgba(115,216,255,.14)') {
  const x = [], y = [], zz = [];
  for (let i = 0; i <= 72; i += 1) { const a = i / 72 * Math.PI * 2; x.push(radius * Math.cos(a)); y.push(radius * Math.sin(a)); zz.push(z); }
  return { type: 'scatter3d', mode: 'lines', x, y, z: zz, hoverinfo: 'skip', line: { color, width: 2 } };
}
function segmentTrace(segments, color, width = 3, opacity = .55) {
  const x = [], y = [], z = [];
  for (const [a, b] of segments) { x.push(a[0], b[0], null); y.push(a[1], b[1], null); z.push(a[2], b[2], null); }
  return { type: 'scatter3d', mode: 'lines', x, y, z, hoverinfo: 'skip', opacity, line: { color, width } };
}

function rateCoord(row) {
  const maturity = Math.max(.01, Number(row.maturity_years));
  const yieldPct = Number(row.yield_pct); const changeBp = finite(row.change_bps) ? Number(row.change_bps) : 0;
  const angle = -Math.PI / 2 + (Math.log1p(maturity) / Math.log(31)) * Math.PI * 2;
  const radius = .82 + clamp(yieldPct, 0, 8) * .16; const z = clamp(changeBp / 12, -1.65, 1.65);
  return [radius * Math.cos(angle), radius * Math.sin(angle), z];
}
function renderRatesChart() {
  const payload = U.rates; const empty = $('rates-empty');
  if (!U.ratesEnabled) { if (empty) { empty.style.display = 'flex'; empty.textContent = '○ RATES ORBITAL SYSTEM ОТКЛЮЧЕН'; } purge('rates'); return; }
  const rows = (payload?.series || []).filter((row) => row.available && finite(row.yield_pct));
  if (!plotAvailable()) { if (empty) { empty.style.display = 'flex'; empty.textContent = '○ LOCAL PLOTLY НЕ ЗАГРУЖЕН'; } return; }
  if (!payload?.available || !rows.length) { if (empty) { empty.style.display = 'flex'; empty.textContent = '○ НЕТ ДОСТУПНЫХ UST YIELD SERIES'; } purge('rates'); return; }
  if (empty) empty.style.display = 'none';
  const sorted = rows.slice().sort((a, b) => Number(a.maturity_years) - Number(b.maturity_years));
  const coords = sorted.map(rateCoord); const instrument = U.tick?.instrument || 'MARKET';
  const traces = [ringTrace(1.0), ringTrace(1.45, 0, 'rgba(184,156,255,.11)')];
  traces.push({ type: 'scatter3d', mode: 'lines', x: coords.map((v) => v[0]), y: coords.map((v) => v[1]), z: coords.map((v) => v[2]), hoverinfo: 'skip', line: { color: '#8998aa', width: 5 }, opacity: .65 });
  traces.push({
    type: 'scatter3d', mode: 'markers+text', x: coords.map((v) => v[0]), y: coords.map((v) => v[1]), z: coords.map((v) => v[2]),
    text: sorted.map((row) => row.label), textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 11, color: '#dce7f2' },
    marker: { size: sorted.map((row) => 6 + Math.min(7, Math.abs(Number(row.change_bps || 0)) * .14)), color: sorted.map((row) => Number(row.change_bps || 0) > .25 ? '#ff6b72' : Number(row.change_bps || 0) < -.25 ? '#49d79a' : '#73d8ff'), line: { color: 'rgba(255,255,255,.65)', width: 1 }, opacity: .96 },
    customdata: sorted.map((row) => [row.ticker, row.yield_pct, row.change_bps, row.maturity_years]),
    hovertemplate: '<b>%{text} · %{customdata[0]}</b><br>yield %{customdata[1]:.3f}%<br>Δ %{customdata[2]:+.1f} bp<br>maturity %{customdata[3]:.2f}y<extra></extra>',
  });
  traces.push({ type: 'scatter3d', mode: 'markers+text', x: [0], y: [0], z: [0], text: [instrument], textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 10, color: '#f6d08f' }, marker: { size: 9, color: '#f6b85f', line: { color: '#ffe0a5', width: 2 } }, hovertemplate: `<b>${esc(instrument)}</b><br>current terminal instrument<extra></extra>` });
  window.Plotly.react($('rates-orbit-chart'), traces, sceneLayout(), plotConfig());
}
function renderRatesHud() {
  const payload = U.rates; const yields = $('rates-yields'); const spreads = $('rates-spreads');
  if (yields) yields.innerHTML = (payload?.series || []).map((row) => {
    const available = row.available && finite(row.yield_pct);
    return `<div class="metric-tile"><div class="k">${esc(row.label)} · ${esc(row.ticker)}</div><div class="v">${available ? `${fmt(row.yield_pct, 3)}%` : '—'}</div><div class="s">${esc(available ? fmtBp(row.change_bps) : 'NO DATA')}</div></div>`;
  }).join('');
  if (spreads) {
    const labels = { UST_13W: '13W', UST_5Y: '5Y', UST_10Y: '10Y', UST_30Y: '30Y' };
    spreads.innerHTML = (payload?.spreads || []).map((row) => metricRow(`${labels[row.from] || row.from} → ${labels[row.to] || row.to}`, `${fmtSigned(row.spread_bps, 1)} bp · Δ ${fmtBp(row.change_bps)}`, Number(row.spread_bps) < 0 ? 'warn' : '')).join('') || metricRow('curve', 'недостаточно данных', 'warn');
  }
  const source = $('rates-source'); if (source) { const stamp = finite(payload?.asof) ? new Date(Number(payload.asof) * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC' : '—'; source.textContent = `${payload?.source || '—'} · asof ${stamp} · synthetic fallback: OFF`; }
  const read = $('rates-readout'); if (read) read.textContent = payload?.available ? `${payload.curve_state || 'CURVE'} · ${(payload.series || []).filter((r) => r.available).length}/${(payload.series || []).length} OBSERVED NODES` : 'КРИВАЯ ОЖИДАЕТ НАБЛЮДАЕМЫЕ ДОХОДНОСТИ';
  badge('rates-status', payload?.available ? 'delayed' : 'no-data', payload?.available ? `◐ ${payload.curve_state || 'DELAYED'}` : '○ НЕТ ДАННЫХ');
}

function featureFamily(featureId) { const prefix = String(featureId || '').split('.')[0] || 'other'; return FEATURE_COLORS[prefix] ? prefix : 'other'; }
function median(values) { if (!values.length) return 1; const a = values.slice().sort((x, y) => x - y); const m = Math.floor(a.length / 2); return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; }
function buildFeatureGeometry(items) {
  const rows = Object.values(items || {}).filter((row) => row && typeof row === 'object');
  const families = [...new Set(rows.map((row) => featureFamily(row.feature_id)))].sort();
  const familyIndex = Object.fromEntries(families.map((name, i) => [name, i])); const anchors = [], nodes = [], links = [];
  for (const family of families) {
    const subset = rows.filter((row) => featureFamily(row.feature_id) === family).sort((a, b) => String(a.feature_id).localeCompare(String(b.feature_id)));
    const numeric = subset.filter((row) => row.available && !row.stale && finite(row.value));
    const visible = subset.filter((row) => row.available || row.stale);
    const magnitude = Math.max(1e-8, median(numeric.map((row) => Math.abs(Number(row.value))).filter((v) => v > 0)) || 1);
    const angle0 = (familyIndex[family] / Math.max(1, families.length)) * Math.PI * 2 - Math.PI / 2;
    const familyZ = numeric.length ? numeric.reduce((sum, row) => sum + Math.tanh(Number(row.value) / (magnitude * 3)), 0) / numeric.length : 0;
    const anchor = [.52 * Math.cos(angle0), .52 * Math.sin(angle0), familyZ * .55];
    anchors.push({ family, coord: anchor, n: subset.length, color: FEATURE_COLORS[family] || FEATURE_COLORS.other });
    visible.forEach((row, idx) => {
      const slot = visible.length <= 1 ? 0 : (idx / (visible.length - 1) - .5); const angle = angle0 + slot * .56;
      const radius = .82 + (idx % 4) * .12 + Math.floor(idx / 4) * .035;
      const numericValue = row.available && finite(row.value) ? Number(row.value) : null;
      const z = numericValue == null ? 0 : Math.tanh(numericValue / (magnitude * 3)) * 1.15;
      const coord = [radius * Math.cos(angle), radius * Math.sin(angle), z];
      const shock = numericValue == null ? 0 : Math.abs(numericValue) / magnitude;
      nodes.push({ row, family, coord, shock, color: FEATURE_COLORS[family] || FEATURE_COLORS.other }); links.push([anchor, coord]);
    });
  }
  for (const node of nodes) node.label = String(node.row.feature_id).split('.').pop();
  const unavailableN = rows.filter((row) => !row.available && !row.stale).length;
  return { anchors, nodes, links, unavailableN };
}
function edgeColor(ratio) { return Number(ratio) > .02 ? '#49d79a' : Number(ratio) < -.02 ? '#ff6b72' : '#8995a3'; }
function renderEdgeChart() {
  const payload = U.edge || {}; const empty = $('edge-empty');
  if (!U.edgeEnabled) { if (empty) { empty.style.display = 'flex'; empty.textContent = '○ EDGE NEURAL UNIVERSE ОТКЛЮЧЕН'; } purge('edge'); return; }
  if (!plotAvailable()) { if (empty) { empty.style.display = 'flex'; empty.textContent = '○ LOCAL PLOTLY НЕ ЗАГРУЖЕН'; } return; }
  const featureGeo = buildFeatureGeometry(payload.canonical_features?.items || {});
  const activeGroups = (payload.active_edge?.matched_groups || []).filter((row) => finite(row.net_vote_ratio));
  if (!featureGeo.nodes.length && !activeGroups.length && !featureGeo.unavailableN) { if (empty) { empty.style.display = 'flex'; empty.textContent = '○ НЕТ CURRENT-T0 FEATURE STATE'; } purge('edge'); return; }
  if (empty) empty.style.display = 'none';
  const traces = [ringTrace(.52, 0, 'rgba(246,184,95,.10)'), ringTrace(1.05, 0, 'rgba(115,216,255,.08)')];
  const center = [0, 0, clamp(Number(payload.production_weight?.direction_score || 0), -1, 1) * .45];
  traces.push(segmentTrace(featureGeo.anchors.map((a) => [center, a.coord]), '#657489', 4, .48));
  traces.push(segmentTrace(featureGeo.links, '#5d6b7d', 2.5, .36));
  if (featureGeo.anchors.length) traces.push({
    type: 'scatter3d', mode: 'markers+text', x: featureGeo.anchors.map((v) => v.coord[0]), y: featureGeo.anchors.map((v) => v.coord[1]), z: featureGeo.anchors.map((v) => v.coord[2]),
    text: featureGeo.anchors.map((v) => v.family.toUpperCase()), textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 9, color: '#aebcca' },
    marker: { size: featureGeo.anchors.map((v) => 5 + Math.min(5, Math.sqrt(v.n))), color: featureGeo.anchors.map((v) => v.color), opacity: .88 },
    customdata: featureGeo.anchors.map((v) => [v.family, v.n]), hovertemplate: '<b>%{customdata[0]}</b><br>%{customdata[1]} canonical features<extra></extra>',
  });
  if (featureGeo.nodes.length) traces.push({
    type: 'scatter3d', mode: 'markers+text', x: featureGeo.nodes.map((v) => v.coord[0]), y: featureGeo.nodes.map((v) => v.coord[1]), z: featureGeo.nodes.map((v) => v.coord[2]),
    text: featureGeo.nodes.map((v) => v.label), textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 8, color: '#9eacbb' },
    marker: { size: featureGeo.nodes.map((v) => v.row.available && !v.row.stale ? 5 + Math.min(7, Math.log1p(v.shock) * 2.2) : 4.5), color: featureGeo.nodes.map((v) => v.row.stale ? '#f6b85f' : v.color), opacity: featureGeo.nodes.map((v) => v.row.available && !v.row.stale ? .94 : .55), line: { color: 'rgba(255,255,255,.42)', width: 1 } },
    customdata: featureGeo.nodes.map((v) => [v.row.feature_id, displayValue(v.row.value, 5), v.row.available ? 'available' : 'N/A', v.row.stale ? 'stale' : 'fresh']),
    hovertemplate: '<b>%{customdata[0]}</b><br>value %{customdata[1]}<br>%{customdata[2]} · %{customdata[3]}<extra></extra>',
  });
  if (featureGeo.unavailableN > 0) traces.push({
    type: 'scatter3d', mode: 'markers+text', x: [-1.28], y: [-1.18], z: [-.82],
    text: [`N/A · ${featureGeo.unavailableN}`], textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 10, color: '#76818d' },
    marker: { size: 7 + Math.min(8, Math.sqrt(featureGeo.unavailableN)), color: '#3f4853', opacity: .62, line: { color: '#76818d', width: 1.5 } },
    customdata: [[`${featureGeo.unavailableN} canonical features`, 'not observed at current T0']],
    hovertemplate: '<b>N/A AGGREGATE</b><br>%{customdata[0]}<br>%{customdata[1]} · not zero<extra></extra>',
  });
  if (activeGroups.length) {
    const targetFamilies = [...new Set(activeGroups.map((row) => row.target_family || 'OTHER'))].sort();
    const targetIndex = Object.fromEntries(targetFamilies.map((name, i) => [name, i]));
    const edgeNodes = activeGroups.map((row) => { const family = row.target_family || 'OTHER'; const baseAngle = (targetIndex[family] / Math.max(1, targetFamilies.length)) * Math.PI * 2 - Math.PI / 2; const horizon = Math.max(0, Number(row.signal_horizon_minutes || 0)); const radius = 1.48 + clamp(horizon / 240, 0, 1.5) * .72; const ratio = clamp(Number(row.net_vote_ratio || 0), -1, 1); return { row, ratio, coord: [radius * Math.cos(baseAngle), radius * Math.sin(baseAngle), ratio * 1.5] }; });
    traces.push(ringTrace(1.72, 0, 'rgba(73,215,154,.07)'));
    for (const edgeNode of edgeNodes) {
      const matchedMass = Math.log2(1 + Math.max(0, Number(edgeNode.row.matched_n || 0)));
      const relationshipStrength = Math.abs(edgeNode.ratio) * 4 + matchedMass;
      traces.push(segmentTrace(
        [[center, edgeNode.coord]], edgeColor(edgeNode.ratio),
        3 + Math.min(6, relationshipStrength), Math.abs(edgeNode.ratio) > .02 ? .72 : .42,
      ));
    }
    traces.push({ type: 'scatter3d', mode: 'markers+text', x: edgeNodes.map((v) => v.coord[0]), y: edgeNodes.map((v) => v.coord[1]), z: edgeNodes.map((v) => v.coord[2]), text: edgeNodes.map((v) => `${v.row.target_family || 'OTHER'} · ${v.row.signal_horizon_minutes || 0}m`), textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 9, color: '#c5d0dc' }, marker: { size: edgeNodes.map((v) => 6 + Math.min(8, Math.log2(1 + Number(v.row.matched_n || 0)) * 1.8)), color: edgeNodes.map((v) => edgeColor(v.ratio)), opacity: .95, line: { color: edgeNodes.map((v) => Number(v.row.strict_matched_n || 0) > 0 ? '#ffffff' : '#66717d'), width: 1.2 } }, customdata: edgeNodes.map((v) => [v.row.target_family || 'OTHER', v.row.signal_horizon_minutes || 0, v.row.target_id || '—', v.row.matched_n || 0, v.row.supporting_n || 0, v.row.opposing_n || 0, v.row.net_vote_ratio || 0, v.row.strict_matched_n || 0]), hovertemplate: '<b>%{customdata[0]} · %{customdata[1]}m</b><br>target %{customdata[2]}<br>matched %{customdata[3]}<br>support %{customdata[4]} / oppose %{customdata[5]}<br>net %{customdata[6]:+.3f}<br>strict %{customdata[7]}<extra></extra>' });
  }
  const instrument = payload.instrument || U.tick?.instrument || 'POSITION';
  traces.push({ type: 'scatter3d', mode: 'markers+text', x: [center[0]], y: [center[1]], z: [center[2]], text: [`PM · ${instrument}`], textposition: 'top center', textfont: { family: 'IBM Plex Mono, monospace', size: 10, color: '#ffe0a5' }, marker: { size: 10, color: '#f6b85f', line: { color: '#ffe0a5', width: 2 } }, hovertemplate: `<b>Position Manager · ${esc(instrument)}</b><br>soft weight ${fmtPct(payload.production_weight?.weight_fraction, 1)}<extra></extra>` });
  const chart = $('edge-universe-chart');
  const frameKey = String(payload.canonical_features?.observation_t0 ?? payload.captured_ts ?? '');
  const nextCoords = Object.fromEntries(featureGeo.nodes.map((node) => [node.row.feature_id, node.coord]));
  let maxMove = 0;
  for (const [id, coord] of Object.entries(nextCoords)) {
    const prior = U.edgeCoords[id];
    if (prior) maxMove = Math.max(maxMove, Math.hypot(coord[0] - prior[0], coord[1] - prior[1], coord[2] - prior[2]));
  }
  const realT0Changed = U.edgeFrameKey != null && frameKey && frameKey !== U.edgeFrameKey;
  U.edgeFrameKey = frameKey; U.edgeCoords = nextCoords;
  const layout = sceneLayout({ eye: { x: 1.55, y: 1.35, z: 1.05 } });
  if (realT0Changed && chart?.data?.length === traces.length && typeof window.Plotly.animate === 'function') {
    const duration = Math.round(clamp(850 - maxMove * 300, 350, 850));
    window.Plotly.animate(chart, { data: traces, layout }, { transition: { duration, easing: 'cubic-in-out' }, frame: { duration, redraw: true }, mode: 'immediate' })
      .catch(() => window.Plotly.react(chart, traces, layout, plotConfig()));
  } else {
    window.Plotly.react(chart, traces, layout, plotConfig());
  }
}
function renderEdgeSummary() {
  const payload = U.edge || {}; const active = payload.active_edge || {}; const profile = payload.production_weight || {};
  $('edge-weight').textContent = fmtPct(profile.weight_fraction || 0, 1); $('edge-cap').textContent = fmtPct(profile.max_weight_fraction || profile.high_risk_only_cap || .30, 1); $('edge-direction').textContent = fmtSigned(profile.direction_score || 0, 2); $('edge-votes').textContent = `${active.supporting_position_n || 0} / ${active.opposing_position_n || 0}`; $('edge-strict').textContent = fmtPct(profile.strict_directional_share || 0, 0); $('edge-buckets').textContent = String(profile.independent_bucket_n || 0);
  const matched = Number(active.matched_structured_signal_n || 0); const availableFeatures = Number(payload.canonical_features?.available_n || 0); const reason = profile.decision_reason || {}; const activeMatch = reason.code === 'ACTIVE_MATCH'; const tone = activeMatch ? 'live' : availableFeatures > 0 ? 'delayed' : 'no-data';
  const reasonLabel = reason.label || (matched > 0 ? 'ACTIVE MATCH' : 'NO MATCH');
  badge('edge-status', tone, activeMatch ? `● ${matched} CURRENT-T0 MATCHES · ${reasonLabel}` : availableFeatures > 0 ? `◐ ${availableFeatures} T0 FEATURES · ${reasonLabel}` : `○ ${reasonLabel}`);
  const read = $('edge-readout'); if (read) read.textContent = activeMatch ? `CURRENT-T0 · WEIGHT ${fmtPct(profile.weight_fraction || 0, 1)} · ${profile.independent_bucket_n || 0} FAMILY×HORIZON BUCKETS` : `${availableFeatures} AVAILABLE CANONICAL T0 FEATURES · ${reasonLabel}`;
}

function liveMetricRows() {
  const t = U.tick || {}; const cross = U.edge?.cross_asset?.summary || {}; const rows = []; const add = (k, v, tone = '') => rows.push(metricRow(k, v, tone));
  add('instrument / price', `${t.instrument || '—'} · ${fmt(path(t, 'feeds.price.value'), 3)}`); add('position r', finite(path(t, 'prob.r')) ? `${fmtSigned(path(t, 'prob.r'), 2)}R` : '—', Number(path(t, 'prob.r')) >= 0 ? 'good' : 'bad'); add('barrier EV≤H', finite(path(t, 'market.horizon_barrier_ev')) ? `${fmtSigned(path(t, 'market.horizon_barrier_ev'), 3)}R` : '—', Number(path(t, 'market.horizon_barrier_ev')) >= 0 ? 'good' : 'bad'); add('touch take / stop', `${fmtPct(path(t, 'market.p_take_horizon'))} / ${fmtPct(path(t, 'market.p_stop_horizon'))}`); add('no-touch≤H', fmtPct(path(t, 'market.p_unresolved_horizon'))); add('option P', fmtPct(path(t, 'prob.p'))); add('EV hold / ladder', `${finite(path(t, 'mc.ev_hold')) ? `${fmtSigned(path(t, 'mc.ev_hold'), 2)}R` : '—'} / ${finite(path(t, 'mc.ev_ladder')) ? `${fmtSigned(path(t, 'mc.ev_ladder'), 2)}R` : '—'}`); add('σ implied / baseline', `${fmtPct(path(t, 'sigma.sigma_implied'))} / ${fmtPct(path(t, 'sigma.sigma_baseline'))}`); add('σ ratio / phase', `${fmt(path(t, 'sigma.ratio'), 2)} · ${path(t, 'sigma.phase') || path(t, 'regime.phase') || '—'}`); add('ATR ratio / phase', `${fmt(path(t, 'atr.ratio'), 2)} · ${path(t, 'atr.phase') || '—'}`); add('VRP IV/RV', `${fmt(path(t, 'vrp.iv_rv_ratio'), 2)} · ${path(t, 'vrp.phase') || '—'}`); add('skew RR', finite(path(t, 'options_summary.skew.rr')) ? `${fmtSigned(Number(path(t, 'options_summary.skew.rr')) * 100, 2)} pp` : '—'); add('term slope', finite(path(t, 'options_summary.term.slope')) ? fmtPct(path(t, 'options_summary.term.slope'), 2) : '—'); add('implied move', `${fmtPct(path(t, 'options_summary.implied_move_frac'))} · ${fmt(path(t, 'options_summary.implied_move_abs_instr'), 2)}`); add('gamma regime', `${path(t, 'gamma.zone') || '—'} · ${fmtPct(path(t, 'gamma.strength'))}`); add('VIX / VXN / GVZ', `${fmt(path(t, 'feeds.vols.vix.value'), 2)} / ${fmt(path(t, 'feeds.vols.vxn.value'), 2)} / ${fmt(path(t, 'feeds.vols.gvz.value'), 2)}`); add('cross coupling', fmt(cross.systemic_coupling, 3)); add('network tension', fmt(cross.network_tension, 3), Number(cross.network_tension || 0) > .2 ? 'warn' : ''); add('fragmentation', fmt(cross.fragmentation, 3)); add('verdict', path(t, 'verdict.label') || '—'); return rows.join('');
}
function renderLiveMetrics() {
  const live = $('edge-live-metrics'); if (live) live.innerHTML = liveMetricRows();
  const ratesLive = $('rates-live-context'); const t = U.tick || {};
  if (ratesLive) ratesLive.innerHTML = [metricRow('active instrument', `${t.instrument || '—'} · ${fmt(path(t, 'feeds.price.value'), 3)}`), metricRow('VIX / VXN', `${fmt(path(t, 'feeds.vols.vix.value'), 2)} / ${fmt(path(t, 'feeds.vols.vxn.value'), 2)}`), metricRow('σ ratio', `${fmt(path(t, 'sigma.ratio'), 2)} · ${path(t, 'sigma.phase') || '—'}`), metricRow('VRP', `${fmt(path(t, 'vrp.iv_rv_ratio'), 2)} · ${path(t, 'vrp.phase') || '—'}`), metricRow('option barrier EV', finite(path(t, 'market.horizon_barrier_ev')) ? `${fmtSigned(path(t, 'market.horizon_barrier_ev'), 3)}R` : '—'), metricRow('regime', path(t, 'regime.phase') || path(t, 'regime.regime') || '—')].join('');
}
function renderAttribution() {
  const root = $('edge-attribution'); if (!root) return; const edge = U.edge || {}; const localStatus = edge.management_attribution?.status || {}; const localEdge = edge.management_attribution?.edge || {}; const horizons = edge.g1s?.horizons || []; const rows = [];
  rows.push(metricRow('G1-M status', localStatus.status || localStatus.maturity || (localStatus.available === false ? 'UNAVAILABLE' : '—')));
  for (const key of ['unique_trade_n', 'resolved_n', 'effective_n', 'observation_n']) if (key in localStatus) rows.push(metricRow(`G1-M ${key}`, displayValue(localStatus[key], 0)));
  for (const key of ['edge_r', 'lift_r', 'delta_r', 'sample_n', 'effective_n']) if (key in localEdge) rows.push(metricRow(`ATTR ${key}`, displayValue(localEdge[key], 3)));
  for (const h of horizons) { const hm = h.horizon_minutes ?? '?'; const bits = []; for (const key of ['resolved_n', 'effective_n', 'candidate_n', 'edge_candidate_n']) if (key in h) bits.push(`${key.replace('_n', '')}:${h[key]}`); rows.push(metricRow(`G1S ${hm}m`, bits.join(' · ') || h.status || h.edge_maturity || '—')); }
  root.innerHTML = rows.join('') || metricRow('attribution', 'ожидание prospective outcomes', 'warn');
}
function renderFeatures() {
  const root = $('edge-features'); const count = $('edge-feature-count'); if (!root) return; const block = U.edge?.canonical_features || {}; const items = block.items || {};
  const rows = Object.values(items).filter((row) => row && typeof row === 'object').sort((a, b) => { const aa = a.available && !a.stale ? 0 : a.stale ? 1 : 2; const bb = b.available && !b.stale ? 0 : b.stale ? 1 : 2; return aa - bb || String(a.feature_id).localeCompare(String(b.feature_id)); });
  if (count) count.textContent = `${block.available_n || 0}/${block.total_n || rows.length}`;
  root.innerHTML = rows.map((row) => { const state = row.stale ? 'stale' : row.available ? 'fresh' : 'unavailable'; const value = row.available ? displayValue(row.value, 4) : row.stale ? 'STALE' : 'N/A'; return `<div class="feature-row ${state}" title="${esc(row.feature_id)}"><span class="id">${esc(row.feature_id)}</span><span class="fv">${esc(value)}</span></div>`; }).join('') || '<div class="feature-row unavailable"><span class="id">canonical T0</span><span class="fv">N/A</span></div>';
}

async function refreshRates(force = false) {
  if (!U.ratesEnabled || U.ratesBusy) return; const now = Date.now(); if (!force && now < U.ratesNext) return; U.ratesBusy = true;
  try { const response = await fetch('/api/visual/rates-orbit', { cache: 'no-store' }); if (!response.ok) throw new Error(`HTTP ${response.status}`); U.rates = await response.json(); U.ratesNext = now + (U.rates?.available ? 300000 : 60000); renderRatesHud(); renderRatesChart(); }
  catch (error) { U.ratesNext = now + 60000; badge('rates-status', 'no-data', '○ RATES API ERROR'); console.warn('[universe] rates refresh failed', error); }
  finally { U.ratesBusy = false; }
}
async function refreshEdge(force = false) {
  if (!U.edgeEnabled || U.edgeBusy) return; const now = Date.now(); if (!force && now < U.edgeNext) return; U.edgeBusy = true;
  try {
    const response = await fetch('/api/visual/edge-universe', { cache: 'no-store' });
    if (!response.ok) { const error = new Error(`HTTP ${response.status}`); error.edgeLabel = `EDGE API HTTP ${response.status}`; throw error; }
    U.edge = await response.json(); window.__edgeUniversePayload = U.edge;
    U.edgeNext = now + (U.edge?.transport?.cache_state === 'FRESH' ? 30000 : 2000);
    renderEdgeSummary(); renderAttribution(); renderFeatures(); renderEdgeChart(); renderLiveMetrics();
    window.applyEdgeUniversePrecision?.(U.edge);
  }
  catch (error) {
    U.edgeNext = now + 15000;
    const label = error?.edgeLabel || 'EDGE API ERROR';
    window.markEdgeUniverseTransportUnavailable?.(label);
    if (!window.markEdgeUniverseTransportUnavailable) badge('edge-status', 'no-data', `○ ${label}`);
    console.warn('[universe] edge refresh failed', error);
  }
  finally { U.edgeBusy = false; }
}
function onTick(tick) {
  U.tick = tick; renderLiveMetrics(); const instrument = tick?.instrument || null;
  if (U.lastInstrument == null) U.lastInstrument = instrument;
  else if (instrument !== U.lastInstrument) { U.lastInstrument = instrument; if (U.rates) renderRatesChart(); U.edgeNext = 0; refreshEdge(true); }
  refreshRates(false); refreshEdge(false);
}
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'; const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => badge('universe-ws-status', 'live', '● WS ONLINE');
  ws.onmessage = (event) => { try { onTick(JSON.parse(event.data)); } catch (error) { console.warn('[universe] bad tick', error); } };
  ws.onerror = () => ws.close(); ws.onclose = () => { badge('universe-ws-status', 'no-data', '○ WS OFFLINE'); setTimeout(connectWS, 2000); };
}
function applyToggle(kind) {
  const enabled = kind === 'rates' ? U.ratesEnabled : U.edgeEnabled; const button = kind === 'rates' ? $('rates-toggle') : $('edge-toggle');
  if (button) { button.textContent = enabled ? 'ON' : 'OFF'; button.classList.toggle('off', !enabled); }
  if (kind === 'rates') { if (enabled) refreshRates(true); else renderRatesChart(); }
  else { if (enabled) refreshEdge(true); else renderEdgeChart(); }
}
function initToggles() {
  // Old persistent OFF flags caused blank pages. Remove them once and keep controls session-local.
  try { localStorage.removeItem('universe.rates.enabled'); localStorage.removeItem('universe.edge.enabled'); } catch (_) { /* storage may be disabled */ }
  $('rates-toggle')?.addEventListener('click', () => { U.ratesEnabled = !U.ratesEnabled; applyToggle('rates'); });
  $('edge-toggle')?.addEventListener('click', () => { U.edgeEnabled = !U.edgeEnabled; applyToggle('edge'); });
  applyToggle('rates'); applyToggle('edge');
}
function boot() {
  initToggles();
  if (!plotAvailable()) { badge('rates-status', 'no-data', '○ LOCAL PLOTLY OFFLINE'); badge('edge-status', 'no-data', '○ LOCAL PLOTLY OFFLINE'); }
  connectWS(); refreshRates(true); refreshEdge(true);
  window.addEventListener('resize', () => { if (!plotAvailable()) return; if ($('rates-orbit-chart')) window.Plotly.Plots.resize($('rates-orbit-chart')); if ($('edge-universe-chart')) window.Plotly.Plots.resize($('edge-universe-chart')); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshRates(false); refreshEdge(false); if (U.rates) renderRatesChart(); if (U.edge) renderEdgeChart(); } });
}
boot();
