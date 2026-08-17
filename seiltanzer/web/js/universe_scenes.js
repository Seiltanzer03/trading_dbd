// SEILTANZER UNIVERSE LAB — two isolated, read-only visual scenes.
// No random motion: geometry changes only after a new market/research observation.

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

const FAMILY_COLORS = {
  PATH_FIRST_TOUCH: '#73d8ff',
  PATH_EXCURSION: '#b89cff',
  RETURN: '#49d79a',
  DIRECTION: '#f6b85f',
  VOLATILITY: '#5f8dff',
  OTHER: '#8995a3',
};

const U = {
  tick: null,
  rates: null,
  edge: null,
  ratesChart: null,
  edgeChart: null,
  ratesBusy: false,
  edgeBusy: false,
  ratesNext: 0,
  edgeNext: 0,
  lastInstrument: null,
  ratesEnabled: localStorage.getItem('universe.rates.enabled') !== '0',
  edgeEnabled: localStorage.getItem('universe.edge.enabled') !== '0',
};

function badge(id, tone, text) {
  const node = $(id);
  if (!node) return;
  node.className = `status-pill ${tone}`;
  node.textContent = text;
}

function metricRow(key, value, tone = '') {
  return `<div class="metric-row"><span class="k">${esc(key)}</span><span class="v ${tone}">${esc(value)}</span></div>`;
}

function path(obj, dotted) {
  return dotted.split('.').reduce((value, key) => value == null ? undefined : value[key], obj);
}

function displayValue(value, digits = 3) {
  if (value == null) return '—';
  if (typeof value === 'number') return Number.isFinite(value) ? value.toFixed(digits) : '—';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

function chartBase() {
  return {
    backgroundColor: 'transparent',
    animation: true,
    animationDurationUpdate: 420,
    animationEasingUpdate: 'cubicOut',
    tooltip: {
      backgroundColor: 'rgba(7,10,15,.96)',
      borderColor: 'rgba(160,190,220,.24)',
      textStyle: { color: '#dce6ef', fontFamily: 'IBM Plex Mono', fontSize: 10 },
      extraCssText: 'box-shadow:0 12px 34px rgba(0,0,0,.35);',
    },
    xAxis3D: { type: 'value', min: -2.5, max: 2.5, show: false },
    yAxis3D: { type: 'value', min: -2.5, max: 2.5, show: false },
    zAxis3D: { type: 'value', min: -2.0, max: 2.0, show: false },
    grid3D: {
      boxWidth: 100,
      boxDepth: 100,
      boxHeight: 70,
      environment: '#080b10',
      axisPointer: { show: false },
      viewControl: {
        projection: 'perspective',
        autoRotate: false,
        damping: .88,
        distance: 135,
        alpha: 24,
        beta: 38,
        minDistance: 75,
        maxDistance: 230,
      },
      light: {
        main: { intensity: 1.05, shadow: false, alpha: 35, beta: 45 },
        ambient: { intensity: .42 },
      },
      postEffect: { enable: false },
      temporalSuperSampling: { enable: false },
    },
  };
}

function ensureChart(kind) {
  const chartKey = kind === 'rates' ? 'ratesChart' : 'edgeChart';
  const dom = kind === 'rates' ? $('rates-orbit-chart') : $('edge-universe-chart');
  if (!dom || !window.echarts) return null;
  if (!U[chartKey]) {
    U[chartKey] = window.echarts.init(dom, null, { renderer: 'canvas' });
    if ('ResizeObserver' in window) {
      const observer = new ResizeObserver(() => U[chartKey]?.resize());
      observer.observe(dom);
    }
  }
  return U[chartKey];
}

function ratePoint(row) {
  const maturity = Number(row.maturity_years);
  const yieldPct = Number(row.yield_pct);
  const changeBp = finite(row.change_bps) ? Number(row.change_bps) : 0;
  // Observable mapping: angular position uses log maturity, radius uses yield,
  // elevation uses daily change. Elevation is clipped only for camera stability;
  // the tooltip/HUD always shows the exact number.
  const angle = -Math.PI / 2 + (Math.log1p(maturity) / Math.log(31)) * Math.PI * 2;
  const radius = .82 + clamp(yieldPct, 0, 8) * .16;
  const z = clamp(changeBp / 12, -1.65, 1.65);
  return {
    name: row.label,
    value: [radius * Math.cos(angle), radius * Math.sin(angle), z],
    size: 13 + Math.min(11, Math.abs(changeBp) * .28),
    itemStyle: { color: changeBp > .25 ? '#ff6b72' : changeBp < -.25 ? '#49d79a' : '#73d8ff' },
    meta: row,
  };
}

function renderRatesChart() {
  const payload = U.rates;
  const empty = $('rates-empty');
  if (!U.ratesEnabled) {
    if (empty) { empty.style.display = 'flex'; empty.textContent = '○ RATES ORBITAL SYSTEM ОТКЛЮЧЕН'; }
    U.ratesChart?.clear();
    return;
  }
  const rows = (payload?.series || []).filter((row) => row.available && finite(row.yield_pct));
  if (!payload?.available || !rows.length || !window.echarts) {
    if (empty) { empty.style.display = 'flex'; empty.textContent = window.echarts ? '○ НЕТ ДОСТУПНЫХ UST YIELD SERIES' : '○ ECHARTS-GL НЕДОСТУПЕН'; }
    return;
  }
  if (empty) empty.style.display = 'none';
  const chart = ensureChart('rates');
  if (!chart) return;

  const points = rows.map(ratePoint).sort((a, b) => a.meta.maturity_years - b.meta.maturity_years);
  const instrument = U.tick?.instrument || 'MARKET';
  const curveCoords = points.map((point) => point.value);
  const guideRings = [1.0, 1.45].map((r) => ({
    coords: Array.from({ length: 65 }, (_, i) => {
      const a = i / 64 * Math.PI * 2;
      return [r * Math.cos(a), r * Math.sin(a), 0];
    }),
  }));
  const option = chartBase();
  option.tooltip.formatter = (params) => {
    const meta = params?.data?.meta;
    if (!meta) return `<b>${esc(params?.name || instrument)}</b>`;
    return `<b>${esc(meta.label)} · ${esc(meta.ticker)}</b><br>`
      + `yield ${fmt(meta.yield_pct, 3)}%<br>`
      + `Δ ${fmtBp(meta.change_bps)}<br>`
      + `maturity ${fmt(meta.maturity_years, 2)}y`;
  };
  option.series = [
    {
      type: 'lines3D', coordinateSystem: 'cartesian3D', polyline: true,
      data: guideRings,
      lineStyle: { color: 'rgba(115,216,255,.16)', width: 1, opacity: .35 },
      silent: true,
    },
    {
      type: 'line3D', coordinateSystem: 'cartesian3D', data: curveCoords,
      lineStyle: { color: '#8998aa', width: 2.2, opacity: .62 },
      silent: true,
    },
    {
      name: 'Treasury yields', type: 'scatter3D', coordinateSystem: 'cartesian3D',
      data: points,
      symbol: 'circle',
      symbolSize: (_value, params) => params?.data?.size || 16,
      label: {
        show: true, distance: 1,
        formatter: (params) => params.name,
        color: '#dce7f2', fontFamily: 'IBM Plex Mono', fontSize: 10,
      },
      emphasis: { itemStyle: { opacity: 1 } },
    },
    {
      name: instrument, type: 'scatter3D', coordinateSystem: 'cartesian3D',
      data: [{ name: instrument, value: [0, 0, 0], itemStyle: { color: '#f6b85f' } }],
      symbolSize: 20,
      label: { show: true, formatter: instrument, color: '#f6d08f', fontFamily: 'IBM Plex Mono', fontSize: 10 },
    },
  ];
  chart.setOption(option, true);
}

function renderRatesHud() {
  const payload = U.rates;
  const yields = $('rates-yields');
  const spreads = $('rates-spreads');
  if (yields) {
    yields.innerHTML = (payload?.series || []).map((row) => {
      const available = row.available && finite(row.yield_pct);
      const move = available ? fmtBp(row.change_bps) : 'NO DATA';
      return `<div class="metric-tile"><div class="k">${esc(row.label)} · ${esc(row.ticker)}</div>`
        + `<div class="v">${available ? `${fmt(row.yield_pct, 3)}%` : '—'}</div>`
        + `<div class="s">${esc(move)}</div></div>`;
    }).join('');
  }
  if (spreads) {
    const labels = { UST_13W: '13W', UST_5Y: '5Y', UST_10Y: '10Y', UST_30Y: '30Y' };
    spreads.innerHTML = (payload?.spreads || []).map((row) => metricRow(
      `${labels[row.from] || row.from} → ${labels[row.to] || row.to}`,
      `${fmtSigned(row.spread_bps, 1)} bp · Δ ${fmtBp(row.change_bps)}`,
      Number(row.spread_bps) < 0 ? 'warn' : '',
    )).join('') || metricRow('curve', 'недостаточно данных', 'warn');
  }
  const source = $('rates-source');
  if (source) {
    const stamp = finite(payload?.asof) ? new Date(Number(payload.asof) * 1000).toISOString().replace('T', ' ').slice(0, 19) + ' UTC' : '—';
    source.textContent = `${payload?.source || '—'} · asof ${stamp} · synthetic fallback: OFF`;
  }
  const read = $('rates-readout');
  if (read) read.textContent = payload?.available
    ? `${payload.curve_state || 'CURVE'} · ${payload.series.filter((r) => r.available).length}/${payload.series.length} OBSERVED NODES`
    : 'КРИВАЯ ОЖИДАЕТ НАБЛЮДАЕМЫЕ ДОХОДНОСТИ';
  badge('rates-status', payload?.available ? 'delayed' : 'no-data',
    payload?.available ? `◐ ${payload.curve_state || 'DELAYED'}` : '○ НЕТ ДАННЫХ');
}

function relationTone(ratio) {
  return Number(ratio) > .02 ? '#49d79a' : Number(ratio) < -.02 ? '#ff6b72' : '#7d8997';
}

function renderEdgeChart() {
  const payload = U.edge;
  const active = payload?.active_edge || {};
  const groups = (active.matched_groups || []).filter((row) => finite(row.net_vote_ratio));
  const empty = $('edge-empty');
  if (!U.edgeEnabled) {
    if (empty) { empty.style.display = 'flex'; empty.textContent = '○ EDGE NEURAL UNIVERSE ОТКЛЮЧЕН'; }
    U.edgeChart?.clear();
    return;
  }
  if (!groups.length || !window.echarts) {
    if (empty) { empty.style.display = 'flex'; empty.textContent = window.echarts ? '○ НЕТ НАПРАВЛЕННЫХ MATCHED EDGE GROUPS' : '○ ECHARTS-GL НЕДОСТУПЕН'; }
    U.edgeChart?.clear();
    return;
  }
  if (empty) empty.style.display = 'none';
  const chart = ensureChart('edge');
  if (!chart) return;

  const families = [...new Set(groups.map((row) => row.target_family || 'OTHER'))].sort();
  const familyIndex = Object.fromEntries(families.map((name, i) => [name, i]));
  const familyNodes = [];
  const childNodes = [];
  const positiveLinks = [];
  const negativeLinks = [];
  const neutralLinks = [];
  const familyCoords = {};

  for (const family of families) {
    const subset = groups.filter((row) => (row.target_family || 'OTHER') === family);
    const weightedN = subset.reduce((sum, row) => sum + Math.max(1, Number(row.matched_n || 0)), 0);
    const avg = subset.reduce((sum, row) => sum + Number(row.net_vote_ratio || 0) * Math.max(1, Number(row.matched_n || 0)), 0) / weightedN;
    const angle = (familyIndex[family] / Math.max(1, families.length)) * Math.PI * 2 - Math.PI / 2;
    const coord = [.62 * Math.cos(angle), .62 * Math.sin(angle), clamp(avg, -1, 1) * .7];
    familyCoords[family] = coord;
    familyNodes.push({
      name: family,
      value: coord,
      size: 13 + Math.min(10, Math.log2(1 + weightedN) * 2.2),
      itemStyle: { color: FAMILY_COLORS[family] || FAMILY_COLORS.OTHER },
      meta: { family, matched_n: weightedN, ratio: avg, kind: 'family' },
    });
  }

  for (const row of groups) {
    const family = row.target_family || 'OTHER';
    const angle0 = (familyIndex[family] / Math.max(1, families.length)) * Math.PI * 2 - Math.PI / 2;
    const horizon = Math.max(0, Number(row.signal_horizon_minutes || 0));
    const radial = .86 + clamp(horizon / 240, 0, 1.5) * 1.08;
    const targetHash = [...String(row.target_id || '')].reduce((a, ch) => a + ch.charCodeAt(0), 0);
    const deterministicOffset = ((targetHash % 13) - 6) * .018;
    const angle = angle0 + deterministicOffset;
    const ratio = clamp(Number(row.net_vote_ratio || 0), -1, 1);
    const strictShare = Number(row.matched_n || 0) > 0
      ? clamp(Number(row.strict_matched_n || 0) / Number(row.matched_n), 0, 1) : 0;
    const coord = [radial * Math.cos(angle), radial * Math.sin(angle), ratio * 1.45];
    const node = {
      name: `${family} · ${horizon}m`,
      value: coord,
      size: 10 + Math.min(13, Math.log2(1 + Number(row.matched_n || 0)) * 2.8) + strictShare * 4,
      itemStyle: { color: relationTone(ratio), opacity: .64 + strictShare * .34 },
      meta: { ...row, strict_share: strictShare, kind: 'group' },
    };
    childNodes.push(node);
    const link = { coords: [familyCoords[family], coord], meta: row };
    if (ratio > .02) positiveLinks.push(link);
    else if (ratio < -.02) negativeLinks.push(link);
    else neutralLinks.push(link);
  }

  const profile = payload.production_weight || {};
  const direction = clamp(Number(profile.direction_score || 0), -1, 1);
  const center = [0, 0, direction * .55];
  const centerLinks = familyNodes.map((node) => ({ coords: [center, node.value] }));
  const instrument = payload.instrument || U.tick?.instrument || 'POSITION';

  const option = chartBase();
  option.tooltip.formatter = (params) => {
    const meta = params?.data?.meta;
    if (!meta) return `<b>${esc(params?.name || instrument)}</b><br>Position Manager`;
    if (meta.kind === 'family') {
      return `<b>${esc(meta.family)}</b><br>matched ${meta.matched_n}<br>net ${fmtSigned(meta.ratio, 3)}`;
    }
    return `<b>${esc(meta.target_family || 'OTHER')} · ${esc(meta.signal_horizon_minutes)}m</b><br>`
      + `target ${esc(meta.target_id || '—')}<br>`
      + `matched ${meta.matched_n || 0}<br>`
      + `support ${meta.supporting_n || 0} / oppose ${meta.opposing_n || 0}<br>`
      + `net ${fmtSigned(meta.net_vote_ratio, 3)}<br>`
      + `strict share ${fmtPct(meta.strict_share, 0)}`;
  };
  option.series = [
    {
      type: 'lines3D', coordinateSystem: 'cartesian3D', data: centerLinks,
      lineStyle: { color: '#4f5e70', width: 1.4, opacity: .34 }, silent: true,
    },
    {
      type: 'lines3D', coordinateSystem: 'cartesian3D', data: positiveLinks,
      lineStyle: { color: '#49d79a', width: 2.0, opacity: .50 }, silent: true,
    },
    {
      type: 'lines3D', coordinateSystem: 'cartesian3D', data: negativeLinks,
      lineStyle: { color: '#ff6b72', width: 2.0, opacity: .50 }, silent: true,
    },
    {
      type: 'lines3D', coordinateSystem: 'cartesian3D', data: neutralLinks,
      lineStyle: { color: '#7d8997', width: 1.0, opacity: .22 }, silent: true,
    },
    {
      name: 'families', type: 'scatter3D', coordinateSystem: 'cartesian3D', data: familyNodes,
      symbolSize: (_value, params) => params?.data?.size || 15,
      label: { show: true, formatter: (params) => params.name, color: '#b9c7d4', fontFamily: 'IBM Plex Mono', fontSize: 8 },
    },
    {
      name: 'matched groups', type: 'scatter3D', coordinateSystem: 'cartesian3D', data: childNodes,
      symbolSize: (_value, params) => params?.data?.size || 12,
      label: { show: false },
    },
    {
      name: 'Position Manager', type: 'scatter3D', coordinateSystem: 'cartesian3D',
      data: [{ name: instrument, value: center, itemStyle: { color: '#f6b85f' } }],
      symbolSize: 24,
      label: { show: true, formatter: `PM · ${instrument}`, color: '#ffe0a5', fontFamily: 'IBM Plex Mono', fontSize: 9 },
    },
  ];
  chart.setOption(option, true);
}

function renderEdgeSummary() {
  const payload = U.edge || {};
  const active = payload.active_edge || {};
  const profile = payload.production_weight || {};
  $('edge-weight').textContent = fmtPct(profile.weight_fraction || 0, 1);
  $('edge-cap').textContent = fmtPct(profile.max_weight_fraction || profile.high_risk_only_cap || .30, 1);
  $('edge-direction').textContent = fmtSigned(profile.direction_score || 0, 2);
  $('edge-votes').textContent = `${active.supporting_position_n || 0} / ${active.opposing_position_n || 0}`;
  $('edge-strict').textContent = fmtPct(profile.strict_directional_share || 0, 0);
  $('edge-buckets').textContent = String(profile.independent_bucket_n || 0);
  const matched = Number(active.matched_structured_signal_n || 0);
  const tone = matched > 0 ? 'live' : active.available ? 'delayed' : 'no-data';
  badge('edge-status', tone, matched > 0 ? `● ${matched} CURRENT-T0 MATCHES` : active.available ? '◐ ACTIVE LIBRARY · NO MATCH' : '○ НЕТ ACTIVE EDGE');
  const read = $('edge-readout');
  if (read) {
    read.textContent = matched > 0
      ? `CURRENT-T0 · WEIGHT ${fmtPct(profile.weight_fraction || 0, 1)} · ${profile.independent_bucket_n || 0} FAMILY×HORIZON BUCKETS`
      : 'ACTIVE EDGE ОЖИДАЕТ CURRENT-T0 MATCH';
  }
}

function liveMetricRows() {
  const t = U.tick || {};
  const edgeCross = U.edge?.cross_asset?.summary || {};
  const rows = [];
  const add = (k, v, tone = '') => rows.push(metricRow(k, v, tone));
  add('instrument / price', `${t.instrument || '—'} · ${fmt(path(t, 'feeds.price.value'), 3)}`);
  add('position r', finite(path(t, 'prob.r')) ? `${fmtSigned(path(t, 'prob.r'), 2)}R` : '—', Number(path(t, 'prob.r')) >= 0 ? 'good' : 'bad');
  add('barrier EV≤H', finite(path(t, 'market.horizon_barrier_ev')) ? `${fmtSigned(path(t, 'market.horizon_barrier_ev'), 3)}R` : '—', Number(path(t, 'market.horizon_barrier_ev')) >= 0 ? 'good' : 'bad');
  add('touch take / stop', `${fmtPct(path(t, 'market.p_take_horizon'))} / ${fmtPct(path(t, 'market.p_stop_horizon'))}`);
  add('no-touch≤H', fmtPct(path(t, 'market.p_unresolved_horizon')));
  add('option P', fmtPct(path(t, 'prob.p')));
  add('EV hold / ladder', `${finite(path(t, 'mc.ev_hold')) ? `${fmtSigned(path(t, 'mc.ev_hold'), 2)}R` : '—'} / ${finite(path(t, 'mc.ev_ladder')) ? `${fmtSigned(path(t, 'mc.ev_ladder'), 2)}R` : '—'}`);
  add('σ implied / baseline', `${fmtPct(path(t, 'sigma.sigma_implied'))} / ${fmtPct(path(t, 'sigma.sigma_baseline'))}`);
  add('σ ratio / phase', `${fmt(path(t, 'sigma.ratio'), 2)} · ${path(t, 'sigma.phase') || path(t, 'regime.phase') || '—'}`);
  add('ATR ratio / phase', `${fmt(path(t, 'atr.ratio'), 2)} · ${path(t, 'atr.phase') || '—'}`);
  add('VRP IV/RV', `${fmt(path(t, 'vrp.iv_rv_ratio'), 2)} · ${path(t, 'vrp.phase') || '—'}`);
  add('skew RR', finite(path(t, 'options_summary.skew.rr')) ? `${fmtSigned(Number(path(t, 'options_summary.skew.rr')) * 100, 2)} pp` : '—');
  add('term slope', finite(path(t, 'options_summary.term.slope')) ? fmtPct(path(t, 'options_summary.term.slope'), 2) : '—');
  add('implied move', `${fmtPct(path(t, 'options_summary.implied_move_frac'))} · ${fmt(path(t, 'options_summary.implied_move_abs_instr'), 2)}`);
  add('gamma regime', `${path(t, 'gamma.zone') || '—'} · ${fmtPct(path(t, 'gamma.strength'))}`);
  add('VIX / VXN / GVZ', `${fmt(path(t, 'feeds.vols.vix.value'), 2)} / ${fmt(path(t, 'feeds.vols.vxn.value'), 2)} / ${fmt(path(t, 'feeds.vols.gvz.value'), 2)}`);
  add('cross coupling', fmt(edgeCross.systemic_coupling, 3));
  add('network tension', fmt(edgeCross.network_tension, 3), Number(edgeCross.network_tension || 0) > .2 ? 'warn' : '');
  add('fragmentation', fmt(edgeCross.fragmentation, 3));
  add('verdict', path(t, 'verdict.label') || '—');
  return rows.join('');
}

function renderLiveMetrics() {
  const live = $('edge-live-metrics');
  if (live) live.innerHTML = liveMetricRows();
  const ratesLive = $('rates-live-context');
  const t = U.tick || {};
  if (ratesLive) {
    ratesLive.innerHTML = [
      metricRow('active instrument', `${t.instrument || '—'} · ${fmt(path(t, 'feeds.price.value'), 3)}`),
      metricRow('VIX / VXN', `${fmt(path(t, 'feeds.vols.vix.value'), 2)} / ${fmt(path(t, 'feeds.vols.vxn.value'), 2)}`),
      metricRow('σ ratio', `${fmt(path(t, 'sigma.ratio'), 2)} · ${path(t, 'sigma.phase') || '—'}`),
      metricRow('VRP', `${fmt(path(t, 'vrp.iv_rv_ratio'), 2)} · ${path(t, 'vrp.phase') || '—'}`),
      metricRow('option barrier EV', finite(path(t, 'market.horizon_barrier_ev')) ? `${fmtSigned(path(t, 'market.horizon_barrier_ev'), 3)}R` : '—'),
      metricRow('regime', path(t, 'regime.phase') || path(t, 'regime.regime') || '—'),
    ].join('');
  }
}

function renderAttribution() {
  const root = $('edge-attribution');
  if (!root) return;
  const edge = U.edge || {};
  const localStatus = edge.management_attribution?.status || {};
  const localEdge = edge.management_attribution?.edge || {};
  const horizons = edge.g1s?.horizons || [];
  const rows = [];
  rows.push(metricRow('G1-M status', localStatus.status || localStatus.maturity || (localStatus.available === false ? 'UNAVAILABLE' : '—')));
  for (const key of ['unique_trade_n', 'resolved_n', 'effective_n', 'observation_n']) {
    if (key in localStatus) rows.push(metricRow(`G1-M ${key}`, displayValue(localStatus[key], 0)));
  }
  for (const key of ['edge_r', 'lift_r', 'delta_r', 'sample_n', 'effective_n']) {
    if (key in localEdge) rows.push(metricRow(`ATTR ${key}`, displayValue(localEdge[key], 3)));
  }
  for (const h of horizons) {
    const hm = h.horizon_minutes ?? '?';
    const bits = [];
    for (const key of ['resolved_n', 'effective_n', 'candidate_n', 'edge_candidate_n']) {
      if (key in h) bits.push(`${key.replace('_n', '')}:${h[key]}`);
    }
    rows.push(metricRow(`G1S ${hm}m`, bits.join(' · ') || h.status || h.edge_maturity || '—'));
  }
  root.innerHTML = rows.join('') || metricRow('attribution', 'ожидание prospective outcomes', 'warn');
}

function renderFeatures() {
  const root = $('edge-features');
  const count = $('edge-feature-count');
  if (!root) return;
  const block = U.edge?.canonical_features || {};
  const items = block.items || {};
  const rows = Object.values(items).filter((row) => row && typeof row === 'object').sort((a, b) => {
    const aa = a.available && !a.stale ? 0 : a.stale ? 1 : 2;
    const bb = b.available && !b.stale ? 0 : b.stale ? 1 : 2;
    return aa - bb || String(a.feature_id).localeCompare(String(b.feature_id));
  });
  if (count) count.textContent = `${block.available_n || 0}/${block.total_n || rows.length}`;
  root.innerHTML = rows.map((row) => {
    const cls = row.stale ? ' stale' : '';
    const value = row.available ? displayValue(row.value, 4) : row.stale ? 'STALE' : 'N/A';
    return `<div class="feature-row${cls}" title="${esc(row.feature_id)}"><span class="id">${esc(row.feature_id)}</span><span class="fv">${esc(value)}</span></div>`;
  }).join('') || '<div class="feature-row"><span class="id">canonical T0</span><span class="fv">N/A</span></div>';
}

async function refreshRates(force = false) {
  if (!U.ratesEnabled || U.ratesBusy) return;
  const now = Date.now();
  if (!force && now < U.ratesNext) return;
  U.ratesBusy = true;
  try {
    const response = await fetch('/api/visual/rates-orbit', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    U.rates = await response.json();
    U.ratesNext = now + (U.rates?.available ? 300000 : 60000);
    renderRatesHud();
    renderRatesChart();
  } catch (error) {
    U.ratesNext = now + 60000;
    badge('rates-status', 'no-data', '○ RATES API ERROR');
    console.warn('[universe] rates refresh failed', error);
  } finally {
    U.ratesBusy = false;
  }
}

async function refreshEdge(force = false) {
  if (!U.edgeEnabled || U.edgeBusy) return;
  const now = Date.now();
  if (!force && now < U.edgeNext) return;
  U.edgeBusy = true;
  try {
    const response = await fetch('/api/visual/edge-universe', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    U.edge = await response.json();
    U.edgeNext = now + 30000;
    renderEdgeSummary();
    renderAttribution();
    renderFeatures();
    renderEdgeChart();
    renderLiveMetrics();
  } catch (error) {
    U.edgeNext = now + 30000;
    badge('edge-status', 'no-data', '○ EDGE API ERROR');
    console.warn('[universe] edge refresh failed', error);
  } finally {
    U.edgeBusy = false;
  }
}

function onTick(tick) {
  U.tick = tick;
  renderLiveMetrics();
  const instrument = tick?.instrument || null;
  if (instrument !== U.lastInstrument) {
    U.lastInstrument = instrument;
    if (U.rates) renderRatesChart();
    // Active edge matching is instrument-specific; refresh immediately on change.
    U.edgeNext = 0;
    refreshEdge(true);
  }
  refreshRates(false);
  refreshEdge(false);
}

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => badge('universe-ws-status', 'live', '● WS ONLINE');
  ws.onmessage = (event) => {
    try { onTick(JSON.parse(event.data)); }
    catch (error) { console.warn('[universe] bad tick', error); }
  };
  ws.onerror = () => ws.close();
  ws.onclose = () => {
    badge('universe-ws-status', 'no-data', '○ WS OFFLINE');
    setTimeout(connectWS, 2000);
  };
}

function applyToggle(kind) {
  const enabled = kind === 'rates' ? U.ratesEnabled : U.edgeEnabled;
  const button = kind === 'rates' ? $('rates-toggle') : $('edge-toggle');
  if (button) {
    button.textContent = enabled ? 'ON' : 'OFF';
    button.classList.toggle('off', !enabled);
  }
  if (kind === 'rates') {
    $('rates-scene-card')?.classList.toggle('scene-disabled', !enabled);
    if (enabled) refreshRates(true); else renderRatesChart();
  } else {
    $('edge-scene-card')?.classList.toggle('scene-disabled', !enabled);
    if (enabled) refreshEdge(true); else renderEdgeChart();
  }
}

function initToggles() {
  $('rates-toggle')?.addEventListener('click', () => {
    U.ratesEnabled = !U.ratesEnabled;
    localStorage.setItem('universe.rates.enabled', U.ratesEnabled ? '1' : '0');
    applyToggle('rates');
  });
  $('edge-toggle')?.addEventListener('click', () => {
    U.edgeEnabled = !U.edgeEnabled;
    localStorage.setItem('universe.edge.enabled', U.edgeEnabled ? '1' : '0');
    applyToggle('edge');
  });
  applyToggle('rates');
  applyToggle('edge');
}

function boot() {
  initToggles();
  if (!window.echarts) {
    badge('rates-status', 'no-data', '○ ECHARTS-GL OFFLINE');
    badge('edge-status', 'no-data', '○ ECHARTS-GL OFFLINE');
  }
  connectWS();
  refreshRates(true);
  refreshEdge(true);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      refreshRates(false);
      refreshEdge(false);
      U.ratesChart?.resize();
      U.edgeChart?.resize();
    }
  });
}

boot();
