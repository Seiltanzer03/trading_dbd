// Compact read-only LLM Edge Researcher lifecycle for the existing Universe page.
// This module reads only the materialized lifecycle endpoint; it never triggers research.

const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]));

function row(key, value, tone = '') {
  return `<div class="metric-row"><span class="k">${esc(key)}</span><span class="v ${tone}">${esc(value)}</span></div>`;
}

function stateLabel(candidate) {
  const state = String(candidate?.state || 'UNKNOWN');
  if (candidate?.active_edge_eligible || String(candidate?.active_edge_status || '').startsWith('PROMOTED')) return 'ACTIVE';
  if (state === 'VALIDATED') return 'CONFIRMED';
  if (state === 'FAILED_LIVE') return 'REJECTED';
  if (state === 'FROZEN_FOR_VALIDATION' || state === 'LIVE_VALIDATING') return 'COLLECTING';
  return state;
}

function render(payload) {
  const summary = $('llm-edge-researcher-summary');
  const candidates = $('llm-edge-researcher-candidates');
  const researcher = payload?.researcher || {};
  const quality = payload?.research_quality || {};
  const automation = payload?.automation || {};

  if (summary) {
    summary.innerHTML = [
      row('HYPOTHESES', researcher.hypotheses ?? 0),
      row('DISCOVERY', researcher.discovery_signals ?? 0),
      row('PROSPECTIVE', researcher.collecting ?? 0),
      row('CONFIRMED', researcher.prospective_pass ?? 0),
      row('ACTIVE', researcher.active_edge ?? 0),
      row('REJECTED', researcher.rejected ?? 0),
      row('SURVIVAL', quality.llm_discovery_to_prospective_survival_rate == null
        ? '—' : `${(Number(quality.llm_discovery_to_prospective_survival_rate) * 100).toFixed(1)}%`),
      row('AUTO', automation.enabled === false ? 'OFF'
        : `${automation.new_resolved_t0_since_last_run ?? 0}/${automation.required_new_resolved_t0 ?? 100} T0`),
    ].join('');
  }

  if (candidates) {
    const visible = (payload?.candidates || []).slice(0, 6);
    candidates.innerHTML = visible.map((candidate) => {
      const prospective = candidate?.prospective || {};
      const checkpoint = prospective.next_checkpoint;
      const sample = checkpoint == null
        ? (prospective.decision || '—')
        : `${prospective.matched_n ?? 0}/${checkpoint}`;
      return `<div class="feature-row" title="${esc(candidate?.candidate_id || '')}">`
        + `<span class="id">${esc(candidate?.name || candidate?.target || 'candidate')} · ${esc(candidate?.horizon ?? '—')}m</span>`
        + `<span class="fv">${esc(stateLabel(candidate))} · ${esc(sample)}</span></div>`;
    }).join('') || '<div class="feature-row"><span class="id">research lifecycle</span><span class="fv">NO CANDIDATES</span></div>';
  }
}

async function refresh() {
  try {
    const response = await fetch('/api/research/g1s/edge-researcher/lifecycle', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    const summary = $('llm-edge-researcher-summary');
    if (summary) summary.innerHTML = row('RESEARCHER', 'MATERIALIZED STATE UNAVAILABLE', 'warn');
    console.warn('[universe] llm edge researcher lifecycle refresh failed', error);
  }
}

refresh();
setInterval(refresh, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
