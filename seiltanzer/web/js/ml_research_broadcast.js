// Honest ML/research observability. This script only polls the read-only compositor.
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]));
const value = (item, suffix = '') => item == null ? 'N/A' : `${item}${suffix}`;
const pct = (number) => number == null ? 'N/A' : `${(Number(number) * 100).toFixed(1)}%`;
const utc = (stamp) => stamp == null ? 'N/A' : new Date(Number(stamp) * 1000).toISOString().replace('T', ' ').replace('.000Z', 'Z');
const status = (item) => `<span class="status ${esc(item?.tone || 'muted')}">${esc(item?.label_ru || 'N/A')}</span>`;

function renderPipeline(payload) {
  $('pipeline').innerHTML = (payload.pipeline || []).map((stage) => `<div class="stage">
    <div class="stage-id">${esc(stage.id)}</div>
    <div class="stage-value">${value(stage.value)}<small class="muted"> / ${value(stage.total)}</small></div>
    <div class="stage-label">${esc(stage.label_ru)}</div>${status(stage.status)}
  </div>`).join('') || '<div class="empty">PIPELINE N/A</div>';
}

function renderWorker(payload) {
  const worker = payload.worker || {};
  const node = $('worker-state');
  node.classList.toggle('live', Boolean(worker.activity_indicator_allowed));
  if (!worker.available) node.textContent = 'WORKER · N/A';
  else node.textContent = `WORKER · ${worker.current_phase?.label_ru || 'N/A'}`;
}

function renderTraining(payload) {
  const training = payload.training || {};
  $('training-total').textContent = `${value(training.models_total)} MODELS · ${value(training.resolved_total)} RESOLVED`;
  $('training-explanation').textContent = training.explanation_ru || '';
  $('training-horizons').innerHTML = (training.horizons || []).map((row) => {
    const resolved = Number(row.raw_resolved || 0);
    const effective = Number(row.effective_n || 0);
    const share = resolved > 0 ? Math.max(0, Math.min(100, effective / resolved * 100)) : 0;
    const blockers = (row.blockers || []).map((item) => item.label_ru).join(' · ');
    return `<div class="horizon"><strong>${value(row.horizon_minutes, 'm')}</strong><div>
      ${status(row.stage)}<div class="bar"><i style="width:${share.toFixed(1)}%"></i></div>
      <div class="detail">RAW ${value(row.raw_resolved)} · EFFECTIVE ${value(row.effective_n)} · + ${value(row.positive_n)} / − ${value(row.negative_n)} · PENDING ${value(row.pending)}</div>
      ${blockers ? `<div class="blockers">${esc(blockers)}</div>` : ''}</div><b>${value(row.model_n)} mdl</b></div>`;
  }).join('') || '<div class="empty">G1S HORIZONS · N/A</div>';
}

function renderSummary(payload) {
  const data = payload.researcher_summary || {};
  const rows = [
    ['PROPOSAL RUNS', data.proposal_runs], ['HYPOTHESES', data.hypotheses],
    ['DISCOVERY', data.discovery_signals], ['LIVE OOS', data.collecting],
    ['VALIDATED', data.validated], ['FAILED LIVE', data.failed_live],
    ['RESEARCH REJECTED', data.rejected_research], ['ACTIVE EDGE', data.active_edge],
  ];
  $('researcher-total').textContent = `${value(data.new_resolved_t0_since_last_run)} / ${value(data.required_new_resolved_t0)} NEW T0`;
  $('researcher-summary').innerHTML = rows.map(([key, number]) => `<div class="summary"><span>${esc(key)}</span><b>${value(number)}</b></div>`).join('');
}

function renderHypotheses(payload) {
  const items = payload.hypotheses || [];
  $('hypothesis-count').textContent = `${items.length} MATERIALIZED`;
  $('hypotheses').innerHTML = items.map((item) => {
    const evidence = item.evidence || {};
    const conditions = (item.conditions || []).map((row) => `<span class="condition" title="${esc(row.kind)}">${esc(row.label_ru)}</span>`).join('');
    const rejected = item.rejection ? `<div class="rejection">ОТКЛОНЕНО: ${esc(item.rejection.label_ru)} · q ${value(item.rejection.q_value)} / ${value(item.rejection.q_value_max)}</div>` : '';
    return `<article class="hypothesis"><div class="hypothesis-head"><div><h3>${esc(item.name)}</h3>
      <div class="meta">${esc(item.target)} · ${value(item.horizon_minutes, 'm')} · ${esc(item.candidate_id || 'N/A')}</div></div>${status(item.stage)}</div>
      <div class="conditions">${conditions || '<span class="condition">CONDITIONS N/A</span>'}</div>
      <div class="evidence"><div><span>MATCHED</span><b>${value(evidence.matched_n)}</b></div><div><span>NEXT CHECKPOINT</span><b>${value(evidence.next_checkpoint)}</b></div>
      <div><span>EFFECT</span><b>${pct(evidence.effect)}</b></div><div><span>Q VALUE</span><b>${value(evidence.q_value)}</b></div></div>${rejected}</article>`;
  }).join('') || '<div class="empty">НЕТ МАТЕРИАЛИЗОВАННЫХ КАНДИДАТОВ. ЭТО НЕ ОЗНАЧАЕТ, ЧТО ИДЁТ СКРЫТЫЙ ПЕРЕБОР.</div>';
}

function renderRuns(payload) {
  $('recent-runs').innerHTML = (payload.recent_runs || []).map((run) => `<div class="run"><div><b>${esc(run.label_ru || run.kind)}</b><br><small>${esc(run.kind)}${run.run_id ? ` · ${esc(run.run_id)}` : ''}</small></div>
    <div>${utc(run.finished_ts || run.started_ts)}</div><div>${status(run.status)}${run.error ? `<small class="rejection">${esc(run.error)}</small>` : ''}</div></div>`).join('')
    || '<div class="empty">ПОСЛЕДНИЕ ЗАПУСКИ · N/A</div>';
}

function render(payload) {
  const freshness = payload.freshness || {};
  const banner = $('freshness-banner');
  banner.className = `truth-banner ${freshness.stale ? 'bad' : 'good'}`;
  banner.textContent = `${freshness.label_ru || payload.status?.label_ru || 'N/A'} · AGE ${freshness.age_sec == null ? 'N/A' : `${Math.round(freshness.age_sec)}s`} · ОБНОВЛЕНО ${utc(freshness.lifecycle_updated_ts)}`;
  renderWorker(payload); renderPipeline(payload); renderTraining(payload); renderSummary(payload); renderHypotheses(payload); renderRuns(payload);
}

async function refresh() {
  try {
    const response = await fetch('/api/research/ml-broadcast', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    const banner = $('freshness-banner');
    banner.className = 'truth-banner bad';
    banner.textContent = `ML RESEARCH STATE N/A · ${error.message}`;
  }
}

refresh();
setInterval(refresh, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
