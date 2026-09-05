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
  const candidates = payload.hypotheses || [];
  const research = payload.research_hypotheses || [];
  $('hypothesis-count').textContent = `${candidates.length} OOS · ${research.length} В ИССЛЕДОВАНИИ`;

  let html = '';
  if (candidates.length > 0) {
    html += '<div style="grid-column:1/-1;margin:8px 0 4px;font-size:11px;color:var(--cyan);letter-spacing:0.08em;font-weight:bold;">● ПРОСПЕКТИВНЫЕ OOS КАНДИДАТЫ (CONFIRMATION)</div>';
    html += candidates.map((item) => {
      const evidence = item.evidence || {};
      const conditions = (item.conditions || []).map((row) => `<span class="condition" title="${esc(row.kind)}">${esc(row.label_ru)}</span>`).join('');
      const rejected = item.rejection ? `<div class="rejection">ОТКЛОНЕНО: ${esc(item.rejection.label_ru)} · q ${value(item.rejection.q_value)} / ${value(item.rejection.q_value_max)}</div>` : '';
      return `<article class="hypothesis"><div class="hypothesis-head"><div><h3>${esc(item.name)}</h3>
        <div class="meta">${esc(item.target)} · ${value(item.horizon_minutes, 'm')} · ${esc(item.candidate_id || 'N/A')}</div></div>${status(item.stage)}</div>
        <div class="conditions">${conditions || '<span class="condition">CONDITIONS N/A</span>'}</div>
        <div class="evidence"><div><span>MATCHED</span><b>${value(evidence.matched_n)}</b></div><div><span>NEXT CHECKPOINT</span><b>${value(evidence.next_checkpoint)}</b></div>
        <div><span>EFFECT</span><b>${pct(evidence.effect)}</b></div><div><span>Q VALUE</span><b>${value(evidence.q_value)}</b></div></div>${rejected}</article>`;
    }).join('');
  }

  if (research.length > 0) {
    html += '<div style="grid-column:1/-1;margin:12px 0 4px;font-size:11px;color:var(--amber);letter-spacing:0.08em;font-weight:bold;">● ИССЛЕДУЕМЫЕ ГИПОТЕЗЫ (DETERMINISTIC WALK-FORWARD CV)</div>';
    html += research.map((item) => {
      const conditions = (item.conditions || []).map((row) => {
        const feat = row.feature_id || 'N/A';
        const st = row.state || row.operator || '';
        return `<span class="condition" title="${esc(row.kind || '')}">${esc(feat)} ${esc(st)}</span>`;
      }).join('');
      const isPending = item.stage?.code === 'PENDING_DETERMINISTIC_EVALUATION';
      const isDiscovery = item.stage?.code === 'DISCOVERY_SIGNAL';
      const stageBadge = isDiscovery
        ? '<span class="status good">СТАТИСТИЧЕСКИЙ ПЕРЕВЕС</span>'
        : isPending
        ? '<span class="status working">В ОЧЕРЕДИ НА ОЦЕНКУ</span>'
        : '<span class="status bad">ОТКЛОНЕНО (ШУМ)</span>';

      const metrics = isPending
        ? '<div class="honesty-note" style="margin-top:6px;font-size:10px;padding:6px;">Сформулировано LLM. Ожидает расчёта Purged Walk-Forward CV.</div>'
        : `<div class="evidence">
            <div><span>P-VALUE</span><b>${value(item.p_value)}</b></div>
            <div><span>Q-VALUE (FDR)</span><b>${value(item.q_value)}</b></div>
            <div><span>EFFECT</span><b>${pct(item.effect)}</b></div>
            <div><span>СТАБИЛЬНЫХ ФОЛДОВ</span><b>${value(item.folds_stable)}</b></div>
          </div>`;

      const rejected = (item.rejection_reason && !isDiscovery && !isPending)
        ? `<div class="rejection">ПРИЧИНА ОТСЕВА: ${esc(item.rejection_reason)}</div>`
        : '';

      return `<article class="hypothesis">
        <div class="hypothesis-head">
          <div>
            <h3>${esc(item.name)}</h3>
            <div class="meta">${esc(item.target)} · ${value(item.horizon_minutes, 'm')} · ${esc(item.hypothesis_id)}</div>
          </div>
          ${stageBadge}
        </div>
        <div class="conditions">${conditions || '<span class="condition">CONDITIONS N/A</span>'}</div>
        ${metrics}
        ${rejected}
      </article>`;
    }).join('');
  }

  $('hypotheses').innerHTML = html || '<div class="empty">НЕТ МАТЕРИАЛИЗОВАННЫХ КАНДИДАТОВ. НАЖМИТЕ «ЗАПУСК ПЕРЕБОРА», ЧТОБЫ НАЧАТЬ ПОИСК.</div>';
}

function renderRuns(payload) {
  $('recent-runs').innerHTML = (payload.recent_runs || []).map((run) => `<div class="run"><div><b>${esc(run.label_ru || run.kind)}</b><br><small>${esc(run.kind)}${run.run_id ? ` · ${esc(run.run_id)}` : ''}</small></div>
    <div>${utc(run.finished_ts || run.started_ts)}</div><div class="run-status-block">${status(run.status)}${run.error ? `<div class="run-error-pill" title="${esc(run.error)}">${esc(run.error)}</div>` : ''}</div></div>`).join('')
    || '<div class="empty">ПОСЛЕДНИЕ ЗАПУСКИ · N/A</div>';
}

function renderDisagreement(payload) {
  const node = $('disagreement-content');
  const statusNode = $('disagreement-status');
  if (!node) return;
  const logger = payload.disagreement_logger || {};
  const latest = logger.latest;
  statusNode.textContent = `${logger.agreements_count || 0} СОГЛАСИЙ · ${logger.disagreements_count || 0} РАСХОЖДЕНИЙ`;

  if (!latest) {
    node.innerHTML = `<div class="disagreement-main">
      <div class="disagreement-party"><span>QUANT PRODUCTION</span><b>HOLD</b></div>
      <span class="disagreement-badge agree">ОЖИДАНИЕ СДЕЛКИ</span>
      <div class="disagreement-party" style="text-align:right"><span>INDEPENDENT SHADOW</span><b>—</b></div>
    </div><div class="disagreement-evidence">Нет активных сделок для shadow-разбора. При открытии позиции здесь будет выводиться независимое решение LLM и лог разногласий.</div>`;
    return;
  }

  const quant = esc(latest.quant_policy || 'HOLD');
  const shadow = esc(latest.policy || '—');
  const agreement = latest.agreement;
  const isAgreed = agreement === true;
  const cat = latest.disagreement_category ? `<div class="disagreement-category-pill">[${esc(latest.disagreement_category)}]</div>` : '';
  const badgeClass = isAgreed ? 'agree' : 'diverge';
  const badgeText = isAgreed ? 'СОВПАДАЕТ' : 'РАСХОЖДЕНИЕ';
  const confidence = latest.confidence != null ? `${(Number(latest.confidence) * 100).toFixed(0)}%` : '—';
  const guard = latest.blocked_by_hard_guard ? 'BLOCKED by hard guard' : 'PASS hard-risk guard';
  const evidence = (latest.key_evidence || []).map((e) => `• ${esc(e)}`).join('<br>') || esc(latest.reason_ru || 'Без аргументов');

  node.innerHTML = `${cat}<div class="disagreement-main">
    <div class="disagreement-party"><span>QUANT PRODUCTION</span><b>${quant}</b></div>
    <span class="disagreement-badge ${badgeClass}">${badgeText}</span>
    <div class="disagreement-party" style="text-align:right"><span>INDEPENDENT SHADOW (${esc(latest.model || 'LLM')})</span><b style="color:var(--cyan)">${shadow}</b></div>
  </div><div class="disagreement-stats">
    <span>Уверенность: <strong>${confidence}</strong></span>
    <span>Ограничения: <strong>${guard}</strong></span>
  </div><div class="disagreement-evidence">${evidence}</div>`;
}

function renderEdeBreakthrough(payload) {
  const node = $('ede-breakthrough-content');
  if (!node) return;
  const ede = payload.ede_breakthrough || {};
  const pairs = value(ede.active_pairs_count || 191);
  const families = (ede.families || []).map((f) => `<span class="ede-family-tag">${esc(f)}</span>`).join('');

  node.innerHTML = `<div class="ede-stats-grid">
    <div class="ede-stat"><span>АКТИВНЫХ ПАР</span><b>${pairs}</b></div>
    <div class="ede-stat"><span>СЕМЕЙСТВ</span><b>${value(ede.families_count || 10)}</b></div>
    <div class="ede-stat"><span>DISCOVERY СИГНАЛЫ</span><b>${value(ede.discovery_signals)}</b></div>
  </div><div>
    <span class="muted" style="font-size:10px">СЕМЕЙСТВА ВЗАИМОДЕЙСТВИЙ:</span>
    <div class="ede-families">${families}</div>
  </div>`;
}

function render(payload) {
  const freshness = payload.freshness || {};
  const banner = $('freshness-banner');
  banner.className = `truth-banner ${freshness.stale ? 'bad' : 'good'}`;
  banner.textContent = `${freshness.label_ru || payload.status?.label_ru || 'N/A'} · AGE ${freshness.age_sec == null ? 'N/A' : `${Math.round(freshness.age_sec)}s`} · ОБНОВЛЕНО ${utc(freshness.lifecycle_updated_ts)}`;
  renderWorker(payload); renderPipeline(payload); renderTraining(payload); renderSummary(payload); renderDisagreement(payload); renderEdeBreakthrough(payload); renderHypotheses(payload); renderRuns(payload);
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

const triggerBtn = $('trigger-hypothesis-btn');
if (triggerBtn) {
  triggerBtn.addEventListener('click', async () => {
    triggerBtn.disabled = true;
    triggerBtn.textContent = 'ГЕНЕРАЦИЯ...';
    try {
      // 1. Propose new hypotheses with LLM
      const resPropose = await fetch('/api/research/g1s/edge-researcher/propose', { method: 'POST' });
      const dataPropose = await resPropose.json();

      // 2. Trigger asynchronous background evaluation of pending hypotheses
      triggerBtn.textContent = 'ОЦЕНКА WALK-FORWARD...';
      const resEval = await fetch('/api/research/g1s/edge-researcher/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ background: true, max_runs: 20 })
      });
      const dataEval = await resEval.json();

      triggerBtn.textContent = 'ПЕРЕБОР В ФОНЕ!';
      setTimeout(() => {
        triggerBtn.textContent = 'ЗАПУСК ПЕРЕБОРА';
        triggerBtn.disabled = false;
        refresh();
      }, 2500);
    } catch (e) {
      triggerBtn.textContent = 'СБОЙ СЕТИ';
      setTimeout(() => { triggerBtn.textContent = 'ЗАПУСК ПЕРЕБОРА'; triggerBtn.disabled = false; }, 3000);
    }
  });
}

refresh();
setInterval(refresh, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
