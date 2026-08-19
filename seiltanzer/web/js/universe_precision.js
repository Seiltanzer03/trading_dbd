// Universe precision/semantics refinement.
// The primary scene renderer intentionally remains isolated. This small companion
// only improves textual fidelity: tiny observed fractions must not look like zero,
// and non-directional current-T0 matches must not look like a broken vote counter.

const finite = (value) => value != null && Number.isFinite(Number(value));

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
  // Returns, slopes and accelerations are raw fractions. Scientific notation is
  // more truthful than 0.0000 and avoids pretending the feature is inactive.
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
    const raw = typeof source.value === 'number' ? String(source.value) : String(source.value ?? '');
    valueNode.title = `${featureId} · raw=${raw}`;
  }
}

function patchEdgeSemantics(payload) {
  const active = payload?.active_edge || {};
  const profile = payload?.production_weight || {};
  const matched = Number(active.matched_structured_signal_n || 0);
  const directional = Number(active.directional_matched_signal_n || 0);
  const nonDirectional = Number(active.non_directional_matched_signal_n || 0);
  const matchedGroups = Number(active.matched_group_n || 0);
  const directionalGroups = Number(active.directional_matched_group_n || 0);

  const buckets = document.getElementById('edge-buckets');
  const matchedGroupsNode = document.getElementById('edge-matched-groups');
  const nonDirectionalNode = document.getElementById('edge-nondirectional');
  if (buckets) buckets.textContent = String(directionalGroups);
  if (matchedGroupsNode) matchedGroupsNode.textContent = String(matchedGroups);
  if (nonDirectionalNode) nonDirectionalNode.textContent = String(nonDirectional);

  const reason = profile.decision_reason || {};
  const inferredReason = directional === 0
    ? 'NON-DIR ONLY'
    : Number(profile.independent_bucket_n || directionalGroups || 0) <= 0 || Number(profile.weight_fraction || 0) <= 0
      ? 'ZERO NET'
      : 'ACTIVE MATCH';
  const reasonLabel = reason.label || inferredReason;
  const status = document.getElementById('edge-status');
  if (status && matched > 0) {
    status.className = 'status-pill live';
    status.textContent = `● ${matched} CURRENT-T0 MATCHES · ${reasonLabel}`;
  }

  const readout = document.getElementById('edge-readout');
  if (!readout || matched <= 0) return;
  const weight = finite(profile.weight_fraction)
    ? `${(Number(profile.weight_fraction) * 100).toFixed(1)}%` : '0.0%';
  if (directional === 0) {
    readout.textContent = `CURRENT-T0 · ${matched} MATCHES · ${nonDirectional} NON-DIRECTIONAL · SOFT WEIGHT ${weight} · НЕ ОШИБКА АГРЕГАТОРА`;
    readout.title = 'Совпали условия edge-кандидатов, но их target/prediction shift не задаёт LONG/SHORT bias; поэтому они не имеют права создавать directional soft-weight.';
  } else {
    readout.textContent = `CURRENT-T0 · ${matched} MATCHES · ${directional} DIRECTIONAL · ${directionalGroups} DIR FAMILY×HORIZON BUCKETS · WEIGHT ${weight}`;
    readout.title = '';
  }
}

let busy = false;
async function refreshPrecision() {
  if (busy || document.visibilityState === 'hidden') return;
  busy = true;
  try {
    const response = await fetch('/api/visual/edge-universe', { cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json();
    patchFeatures(payload);
    patchEdgeSemantics(payload);
  } catch (_) {
    // Primary Universe renderer owns transport/error UX. This helper is fail-soft.
  } finally {
    busy = false;
  }
}

const observer = new MutationObserver(() => {
  window.clearTimeout(observer._timer);
  observer._timer = window.setTimeout(refreshPrecision, 120);
});
const featureRoot = document.getElementById('edge-features');
// The primary renderer replaces this root's children. Watching child-list changes
// is sufficient and avoids a refresh loop when this helper updates text nodes.
if (featureRoot) observer.observe(featureRoot, { childList: true });

refreshPrecision();
window.setInterval(refreshPrecision, 12000);
