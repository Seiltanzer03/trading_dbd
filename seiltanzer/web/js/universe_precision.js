// Universe precision/semantics refinement.
// Keeps tiny real values visible and, critically, keeps missing Active Edge
// evidence distinct from a measured zero.

const finite = (value) => value != null && Number.isFinite(Number(value));
const pct = (value, digits = 1) => finite(value)
  ? `${(Number(value) * 100).toFixed(digits)}%` : '—';
const signed = (value, digits = 2) => finite(value)
  ? `${Number(value) >= 0 ? '+' : ''}${Number(value).toFixed(digits)}` : '—';
const count = (value) => finite(value) ? String(Math.trunc(Number(value))) : '—';

function formatObserved(featureId, value) {
  if (value == null) return 'N/A';
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (!finite(value)) return 'N/A';
  const number = Number(value);
  if (Object.is(number, -0) || number === 0) return '0';
  const magnitude = Math.abs(number);
  if (magnitude >= 100) return number.toFixed(2);
  if (magnitude >= 1) return number.toFixed(4);
  if (magnitude >= 0.01) return number.toFixed(5);
  if (magnitude >= 0.0001) return number.toFixed(6);
  return number.toExponential(3);
}

function patchFeatures(payload) {
  const items = payload?.canonical_features?.items || {};
  const root = document.getElementById('edge-features');
  if (!root) return;
  for (const row of root.querySelectorAll('.feature-row')) {
    const idNode = row.querySelector('.id');
    const valueNode = row.querySelector('.fv');
    if (!idNode || !valueNode) continue;
    const featureId = idNode.textContent?.trim();
    const source = items[featureId];
    if (!source || !source.available || source.stale) continue;
    valueNode.textContent = formatObserved(featureId, source.value);
    const raw = typeof source.value === 'number'
      ? String(source.value) : String(source.value ?? '');
    valueNode.title = `${featureId} · raw=${raw}`;
  }
}

function setText(id, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
}

function patchEdgeUnavailableTransport(label = 'EDGE API ERROR') {
  for (const id of [
    'edge-weight', 'edge-cap', 'edge-direction', 'edge-strict',
    'edge-buckets', 'edge-matched-groups', 'edge-nondirectional',
  ]) setText(id, '—');
  setText('edge-votes', '— / —');

  const status = document.getElementById('edge-status');
  if (status) {
    status.className = 'status-pill no-data';
    status.textContent = `○ ${label}`;
  }
  const readout = document.getElementById('edge-readout');
  if (readout) {
    readout.textContent = `ACTIVE EDGE N/A · ${label}`;
    readout.title = 'Active Edge endpoint недоступен; ранее показанные значения не считаются текущим измерением.';
  }
  const empty = document.getElementById('edge-empty');
  if (empty) {
    empty.style.display = 'flex';
    empty.textContent = `○ ${label}`;
  }
  const chart = document.getElementById('edge-universe-chart');
  if (chart && window.Plotly && typeof window.Plotly.purge === 'function') {
    window.Plotly.purge(chart);
  }
}

function patchEdgeSemantics(payload) {
  const active = payload?.active_edge || {};
  const profile = payload?.production_weight || {};
  const reason = profile.decision_reason || {};
  const measurementAvailable = active.measurement_available !== false
    && profile.measurement_available !== false;
  const reportState = active.report_state || profile.report_state || 'UNKNOWN';
  const sourceReports = finite(active.source_report_n)
    ? Number(active.source_report_n) : null;
  const expectedReports = finite(active.expected_report_n)
    ? Number(active.expected_report_n) : null;
  const reportCount = sourceReports != null && expectedReports != null
    ? `${sourceReports}/${expectedReports}` : '—';

  if (!measurementAvailable) {
    for (const id of [
      'edge-weight', 'edge-cap', 'edge-direction', 'edge-strict',
      'edge-buckets', 'edge-matched-groups', 'edge-nondirectional',
    ]) setText(id, '—');
    setText('edge-votes', '— / —');
    const status = document.getElementById('edge-status');
    if (status) {
      status.className = 'status-pill no-data';
      status.textContent = `○ ${reason.label || 'EDGE N/A'} · REPORTS ${reportCount}`;
    }
    const readout = document.getElementById('edge-readout');
    if (readout) {
      readout.textContent = `ACTIVE EDGE N/A · ${reportState} · CURRENT-SHA REPORTS ${reportCount}`;
      readout.title = 'Нет полного набора Active Edge отчётов для текущего deployed SHA; нулевой edge не подставляется.';
    }
    return;
  }

  const matched = finite(active.matched_structured_signal_n)
    ? Number(active.matched_structured_signal_n) : null;
  const supporting = finite(active.supporting_position_n)
    ? Number(active.supporting_position_n) : null;
  const opposing = finite(active.opposing_position_n)
    ? Number(active.opposing_position_n) : null;
  const directional = finite(active.directional_matched_signal_n)
    ? Number(active.directional_matched_signal_n)
    : supporting != null && opposing != null ? supporting + opposing : null;
  const nonDirectional = finite(active.non_directional_matched_signal_n)
    ? Number(active.non_directional_matched_signal_n)
    : matched != null && directional != null ? Math.max(0, matched - directional) : null;
  const matchedGroups = finite(active.matched_group_n)
    ? Number(active.matched_group_n) : null;
  const directionalGroups = finite(active.directional_matched_group_n)
    ? Number(active.directional_matched_group_n)
    : finite(profile.independent_bucket_n) ? Number(profile.independent_bucket_n) : null;

  setText('edge-weight', pct(profile.weight_fraction, 1));
  setText('edge-cap', pct(profile.max_weight_fraction, 1));
  setText('edge-direction', signed(profile.direction_score, 2));
  setText('edge-votes', supporting != null && opposing != null
    ? `${supporting} / ${opposing}` : '— / —');
  setText('edge-strict', pct(profile.strict_directional_share, 0));
  setText('edge-buckets', count(directionalGroups));
  setText('edge-matched-groups', count(matchedGroups));
  setText('edge-nondirectional', count(nonDirectional));

  const reasonLabel = reason.label
    || (matched != null && matched > 0 ? 'ACTIVE MATCH' : 'NO ACTIVE EDGE');
  const status = document.getElementById('edge-status');
  if (status) {
    const activeMatch = reason.code === 'ACTIVE_MATCH';
    status.className = `status-pill ${activeMatch ? 'live' : 'delayed'}`;
    status.textContent = matched != null && matched > 0
      ? `${activeMatch ? '●' : '◐'} ${matched} CURRENT-T0 MATCHES · ${reasonLabel}`
      : `◐ ${reasonLabel} · REPORTS ${reportCount}`;
  }

  const readout = document.getElementById('edge-readout');
  if (!readout) return;
  if (matched == null) {
    readout.textContent = `CURRENT-SHA REPORTS COMPLETE · ${reasonLabel}`;
    readout.title = '';
  } else if (matched <= 0) {
    readout.textContent = `CURRENT-SHA REPORTS COMPLETE · 0 CURRENT-T0 MATCHES · ${reasonLabel}`;
    readout.title = 'Это измеренный ноль по полному набору отчётов текущего SHA, а не отсутствие данных.';
  } else if (directional === 0) {
    readout.textContent = `CURRENT-T0 · ${matched} MATCHES · ${nonDirectional ?? '—'} NON-DIRECTIONAL · SOFT WEIGHT ${pct(profile.weight_fraction, 1)} · НЕ ОШИБКА АГРЕГАТОРА`;
    readout.title = 'Совпали условия edge-кандидатов, но target/prediction shift не задаёт LONG/SHORT bias; directional soft-weight не создаётся.';
  } else {
    readout.textContent = `CURRENT-T0 · ${matched} MATCHES · ${directional ?? '—'} DIRECTIONAL · ${directionalGroups ?? '—'} DIR FAMILY×HORIZON BUCKETS · WEIGHT ${pct(profile.weight_fraction, 1)}`;
    readout.title = '';
  }
}

let busy = false;
async function refreshPrecision() {
  if (busy || document.visibilityState === 'hidden') return;
  busy = true;
  try {
    const response = await fetch('/api/visual/edge-universe', { cache: 'no-store' });
    if (!response.ok) {
      patchEdgeUnavailableTransport(`EDGE API HTTP ${response.status}`);
      return;
    }
    const payload = await response.json();
    patchFeatures(payload);
    patchEdgeSemantics(payload);
  } catch (_) {
    patchEdgeUnavailableTransport('EDGE API ERROR');
  } finally {
    busy = false;
  }
}

const observer = new MutationObserver(() => {
  window.clearTimeout(observer._timer);
  observer._timer = window.setTimeout(refreshPrecision, 0);
});
const featureRoot = document.getElementById('edge-features');
if (featureRoot) observer.observe(featureRoot, { childList: true });

refreshPrecision();
window.setInterval(refreshPrecision, 12000);
