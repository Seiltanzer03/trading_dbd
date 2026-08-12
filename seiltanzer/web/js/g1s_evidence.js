// G.1S evidence panel. Presentation only: all numbers come from bounded,
// materialized research APIs. There is no simulated/fake animation.

const HORIZONS = [15, 30, 60, 120, 240];
const RAW_TARGET = 1000;
const EFFECTIVE_TARGET = 400;
const POLL_MS = 60_000;

function el(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== '') node.textContent = text;
  return node;
}

function value(v, digits = 3) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits) : '—';
}

function verdictClass(v) {
  if (v === 'YES' || v === 'NOT_WORSE') return 'g1s-ev-good';
  if (v === 'NO' || v === 'CONTRADICTED') return 'g1s-ev-bad';
  return 'g1s-ev-warn';
}

function progress(current, target) {
  const n = Number(current);
  if (!Number.isFinite(n) || target <= 0) return 0;
  return Math.max(0, Math.min(100, (n / target) * 100));
}

function metricCard(label, primary, secondary, state = '') {
  const card = el('div', 'g1s-ev-metric');
  card.append(el('div', 'g1s-ev-label', label));
  const main = el('div', 'g1s-ev-value ' + verdictClass(state), primary);
  card.append(main);
  if (secondary) card.append(el('div', 'g1s-ev-sub', secondary));
  return card;
}

function firstModel(report) {
  const items = Array.isArray(report?.items) ? report.items : [];
  if (!items.length) return null;
  return items
    .filter((item) => item && item.oos)
    .sort((a, b) => Number(b.oos?.effective_n || 0) - Number(a.oos?.effective_n || 0))[0] || null;
}

function firstCalibration(report) {
  const items = Array.isArray(report?.items) ? report.items : [];
  if (!items.length) return null;
  return items
    .filter((item) => item)
    .sort((a, b) => Number(b.effective_n || 0) - Number(a.effective_n || 0))[0] || null;
}

function injectStyles() {
  if (document.getElementById('g1s-evidence-style')) return;
  const style = document.createElement('style');
  style.id = 'g1s-evidence-style';
  style.textContent = `
    .g1s-ev-body{padding:14px 16px 16px;display:grid;gap:14px}
    .g1s-ev-summary{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}
    .g1s-ev-metric{border:1px solid var(--border,#d9dde2);padding:10px;min-height:68px;background:rgba(127,127,127,.025)}
    .g1s-ev-label{font-size:10px;letter-spacing:.06em;color:#747b84;margin-bottom:7px}
    .g1s-ev-value{font-size:17px;font-weight:650;line-height:1.1;overflow-wrap:anywhere}
    .g1s-ev-sub{font-size:10px;color:#7a818a;margin-top:6px;line-height:1.35}
    .g1s-ev-good{color:#1a8b52}.g1s-ev-bad{color:#bd3944}.g1s-ev-warn{color:#9b741b}
    .g1s-ev-horizons{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}
    .g1s-ev-horizon{border:1px solid var(--border,#d9dde2);padding:9px}
    .g1s-ev-h-title{display:flex;justify-content:space-between;font-size:11px;margin-bottom:7px}
    .g1s-ev-track{height:4px;background:rgba(127,127,127,.16);margin:5px 0;overflow:hidden}
    .g1s-ev-fill{height:100%;background:currentColor}
    .g1s-ev-row{font-size:10px;color:#6f767f;display:flex;justify-content:space-between;gap:8px}
    .g1s-ev-blockers{font-size:10px;color:#7a818a;line-height:1.45;border-top:1px solid var(--border,#d9dde2);padding-top:10px}
    .g1s-ev-meta{display:flex;gap:12px;flex-wrap:wrap;font-size:10px;color:#7a818a}
    @media(max-width:1100px){.g1s-ev-summary{grid-template-columns:repeat(3,1fr)}}
    @media(max-width:760px){.g1s-ev-summary{grid-template-columns:repeat(2,1fr)}.g1s-ev-horizons{grid-template-columns:1fr}}
  `;
  document.head.append(style);
}

function makePanel() {
  const journal = document.getElementById('panel-journal');
  if (!journal || document.getElementById('panel-g1s-evidence')) return null;
  const panel = el('section', 'card panel');
  panel.id = 'panel-g1s-evidence';
  const head = el('div', 'panel-head');
  head.append(el('h2', '', 'EDGE EVIDENCE · G.1S / G.1-M.1'));
  const right = el('div', 'panel-head-right');
  const badge = el('span', 'badge', '○ ЗАГРУЗКА');
  badge.id = 'g1s-ev-status';
  right.append(badge);
  head.append(right);
  const human = el('div', 'analytics-human-line', 'EDGE STATE: WAITING FOR MATERIALIZED EVIDENCE');
  human.id = 'g1s-ev-human';
  panel.append(head, human);
  const body = el('div', 'g1s-ev-body');
  body.id = 'g1s-ev-body';
  panel.append(body);
  const foot = el('div', 'panel-foot dim');
  foot.textContent = 'Research-only. YES требует серьёзного prospective OOS + непротиворечивой экономики; INSUFFICIENT не является сигналом. Production authority не меняется.';
  panel.append(foot);
  journal.parentNode.insertBefore(panel, journal);
  return panel;
}

async function getJson(path) {
  const response = await fetch(path, {cache: 'no-store'});
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function renderHorizons(container, status, finalReport) {
  const statusItems = Array.isArray(status?.horizons) ? status.horizons : [];
  const finalItems = Array.isArray(finalReport?.samples_per_horizon) ? finalReport.samples_per_horizon : [];
  const map = new Map();
  for (const item of statusItems) map.set(Number(item.horizon_minutes), item);
  for (const item of finalItems) map.set(Number(item.horizon_minutes), {...(map.get(Number(item.horizon_minutes)) || {}), ...item});
  const grid = el('div', 'g1s-ev-horizons');
  for (const h of HORIZONS) {
    const item = map.get(h) || {};
    const raw = Number(item.raw_resolved ?? item.raw_n ?? 0);
    const eff = Number(item.effective_n ?? 0);
    const card = el('div', 'g1s-ev-horizon');
    const title = el('div', 'g1s-ev-h-title');
    title.append(el('b', '', `H${h}`), el('span', 'dim', item.state || 'COLLECTING'));
    card.append(title);
    const rawRow = el('div', 'g1s-ev-row'); rawRow.append(el('span', '', 'RAW'), el('span', '', `${raw}/${RAW_TARGET}`)); card.append(rawRow);
    const rawTrack = el('div', 'g1s-ev-track g1s-ev-warn'); const rawFill = el('div', 'g1s-ev-fill'); rawFill.style.width = `${progress(raw, RAW_TARGET)}%`; rawTrack.append(rawFill); card.append(rawTrack);
    const effRow = el('div', 'g1s-ev-row'); effRow.append(el('span', '', 'EFFECTIVE'), el('span', '', `${Math.round(eff)}/${EFFECTIVE_TARGET}`)); card.append(effRow);
    const effTrack = el('div', 'g1s-ev-track g1s-ev-good'); const effFill = el('div', 'g1s-ev-fill'); effFill.style.width = `${progress(eff, EFFECTIVE_TARGET)}%`; effTrack.append(effFill); card.append(effTrack);
    grid.append(card);
  }
  container.append(grid);
}

function render(payload) {
  const {status, finalReport, probability, continuous, calibration, materialization} = payload;
  const body = document.getElementById('g1s-ev-body');
  if (!body) return;
  body.replaceChildren();

  const overall = finalReport?.does_model_beat_baseline_oos || finalReport?.evidence_status || 'INSUFFICIENT';
  const statistical = finalReport?.oos_status?.statistical_combined || 'INSUFFICIENT';
  const economic = finalReport?.economic_plausibility || 'INSUFFICIENT';
  const qCounts = finalReport?.q_maturity?.counts || {};
  const qDue = Number(qCounts.DUE_BUT_NOT_RESOLVED || 0) + Number(qCounts.RESOLUTION_BLOCKED || 0) + Number(qCounts.CONTRACT_REJECTED || 0);
  const management = finalReport?.g1m_local || {};
  const trade = finalReport?.real_trade_relevance || {};

  const pModel = firstModel(probability);
  const cModel = firstModel(continuous);
  const calModel = firstCalibration(calibration);
  const pOos = pModel?.oos || {};
  const cOos = cModel?.oos || {};
  const probabilityRepresentation = calibration?.does_best_probability_representation_beat_baselines_oos || finalReport?.oos_status?.best_probability_representation || 'INSUFFICIENT';
  const calibrationValue = calibration?.does_calibration_add_value_oos || finalReport?.oos_status?.calibration_value_added || 'INSUFFICIENT';
  const selectedRepresentation = calModel?.selected_probability_representation || '—';
  const selectedBrier = selectedRepresentation === 'CALIBRATED' ? calModel?.calibrated_brier : (calModel?.raw_brier ?? pOos.brier);
  const selectedLogLoss = selectedRepresentation === 'CALIBRATED' ? calModel?.calibrated_log_loss : (calModel?.raw_log_loss ?? pOos.log_loss);

  const summary = el('div', 'g1s-ev-summary');
  summary.append(
    metricCard('FINAL EDGE', overall, 'OOS + real-trade + G1-M.1', overall),
    metricCard('STATISTICAL OOS', statistical, `P*=${probabilityRepresentation} · R=${continuous?.does_continuous_model_beat_baseline_oos || '—'} · rawP=${probability?.does_model_beat_baseline_oos || '—'}`, statistical),
    metricCard('ECONOMIC CHECK', economic, `trades ${trade.unique_trades ?? 0} · G1M ${management.unique_trades ?? 0}`, economic),
    metricCard('PROBABILITY · SELECTED', selectedBrier == null ? '—' : `Brier ${value(selectedBrier, 4)}`, `${selectedRepresentation} · logloss ${value(selectedLogLoss, 4)} · n_eff ${calModel?.effective_n ?? pOos.effective_n ?? 0}`, probabilityRepresentation),
    metricCard('CONTINUOUS RETURN', cOos.mae == null ? '—' : `MAE ${value(cOos.mae, 5)}`, `RMSE ${value(cOos.rmse, 5)} · n_eff ${cOos.effective_n ?? 0}`, cModel?.does_continuous_model_beat_baseline_oos || 'INSUFFICIENT'),
    metricCard('CALIBRATION VALUE', calModel?.calibrated_ece == null ? '—' : `ECE ${value(calModel.calibrated_ece, 4)}`, `value-add ${calibrationValue} · raw/cal Brier ${value(calModel?.raw_brier, 4)} / ${value(calModel?.calibrated_brier, 4)}`, calibrationValue)
  );
  body.append(summary);
  renderHorizons(body, status, finalReport);

  const blockers = [];
  for (const item of finalReport?.samples_per_horizon || []) {
    for (const blocker of item.oos_candidate_blockers || []) blockers.push(`H${item.horizon_minutes}: ${blocker}`);
  }
  if (qDue > 0) blockers.push(`Q maturity blockers: ${qDue}`);
  if (finalReport?.status === 'BUILDING') blockers.push('Final evidence snapshot is building');
  const blockerLine = el('div', 'g1s-ev-blockers');
  blockerLine.textContent = blockers.length ? `BLOCKERS · ${blockers.slice(0, 12).join(' · ')}` : 'BLOCKERS · нет активных контрактных блокеров в текущем materialized snapshot';
  body.append(blockerLine);

  const reports = Array.isArray(materialization?.reports) ? materialization.reports : [];
  const ages = reports.map((r) => Number(r.age_sec)).filter(Number.isFinite);
  const maxAge = ages.length ? Math.max(...ages) : null;
  const meta = el('div', 'g1s-ev-meta');
  meta.append(
    el('span', '', `Q: not_due ${qCounts.NOT_DUE_YET || 0} · due ${qCounts.DUE_BUT_NOT_RESOLVED || 0} · blocked ${qCounts.RESOLUTION_BLOCKED || 0}`),
    el('span', '', `G1-M.1: ${management.status || 'INSUFFICIENT'} · ${management.unique_trades || 0}/${management.required_unique_trades || 20} trades`),
    el('span', '', `snapshot age: ${maxAge == null ? '—' : Math.round(maxAge) + 's'}`),
    el('span', '', `T0 V2: ${status?.t0_feature_contract_v2?.v2_observations ?? 0} obs · ${status?.t0_feature_contract_v2?.v2_models ?? 0} models`)
  );
  body.append(meta);

  const badge = document.getElementById('g1s-ev-status');
  const human = document.getElementById('g1s-ev-human');
  if (badge) {
    badge.className = `badge ${verdictClass(overall)}`;
    badge.textContent = `● ${overall}`;
  }
  if (human) human.textContent = `EDGE STATE: ${overall} · STAT ${statistical} · ECON ${economic} · AUTHORITY RESEARCH ONLY`;
}

let refreshBusy = false;
async function refresh() {
  if (refreshBusy || document.hidden) return;
  refreshBusy = true;
  try {
    const [status, finalReport, probability, continuous, calibration, materialization] = await Promise.all([
      getJson('/api/research/g1s/status'),
      getJson('/api/research/g1s/final-report'),
      getJson('/api/research/g1s/oos'),
      getJson('/api/research/g1s/continuous-oos'),
      getJson('/api/research/g1s/calibration-oos'),
      getJson('/api/research/g1s/evidence-materialization'),
    ]);
    render({status, finalReport, probability, continuous, calibration, materialization});
  } catch (error) {
    const badge = document.getElementById('g1s-ev-status');
    const human = document.getElementById('g1s-ev-human');
    if (badge) { badge.className = 'badge g1s-ev-bad'; badge.textContent = '○ DATA ERROR'; }
    if (human) human.textContent = `EDGE STATE: DATA ERROR · ${error?.message || 'unknown'}`;
  } finally {
    refreshBusy = false;
  }
}

export function mountG1SEvidencePanel() {
  injectStyles();
  const panel = makePanel();
  if (!panel) return;
  refresh();
  setInterval(refresh, POLL_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}
