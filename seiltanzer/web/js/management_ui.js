import { mountG1SEvidencePanel } from './g1s_evidence.js';
import './trade_delete_ui_guard.js';

// Mount once from an already-loaded main-dashboard module. The evidence panel is
// independent from management execution and reads research-only bounded APIs.
mountG1SEvidencePanel();

const EDGE_STATUS_RU = {
  EARLY_ADVANTAGE: 'перевес',
  EARLY_DISADVANTAGE: 'гипотеза хуже базовой (не обратный сигнал)',
  EARLY_MIXED: 'смешанный результат',
  EARLY_UNDECIDED: 'результат пока не определён',
};

const EDGE_RELATION_RU = {
  SUPPORTS_POSITION: 'за текущую позицию',
  OPPOSES_POSITION: 'антисигнал для позиции',
  NON_DIRECTIONAL: 'без направления',
};

function finiteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function edgeShiftText(shift) {
  if (!shift || typeof shift !== 'object') return '';
  const scalar = finiteNumber(shift.candidate_minus_structural_baseline);
  if (scalar !== null) {
    const sign = scalar > 0 ? '+' : '';
    return `сдвиг прогноза ${sign}${scalar.toFixed(3)} ${shift.unit || ''}`.trim();
  }
  const strongest = finiteNumber(shift.strongest_shift);
  if (strongest !== null) {
    const sign = strongest > 0 ? '+' : '';
    return `сдвиг ${shift.strongest_class || 'класса'} ${sign}${strongest.toFixed(3)}`;
  }
  return '';
}

function appendTextLine(parent, className, text) {
  const line = document.createElement('div');
  line.className = className;
  line.textContent = text;
  parent.appendChild(line);
}

const COMPONENT_LABELS_RU = {
  quant: 'Количественная база', quantitative_base: 'Количественная база',
  mathematical_edge: 'Математический edge', active_edge: 'Active Edge',
  historical_llm: 'Исторические LLM-гипотезы',
  historical_llm_hypotheses: 'Исторические LLM-гипотезы', current_llm: 'Текущий LLM',
};

const componentLabel = (value) => COMPONENT_LABELS_RU[value] || value || '—';
const percent = (value) => {
  const number = finiteNumber(value);
  if (number === null) return '—';
  return `${0 < Math.abs(number) && Math.abs(number) < .001 ? (number * 100).toPrecision(3) : (number * 100).toFixed(1)}%`;
};
const signed = (value, unit = '') => {
  const number = finiteNumber(value);
  return number === null ? '—' : `${number >= 0 ? '+' : ''}${number.toFixed(3)}${unit}`;
};
const parameterText = (value) => value && typeof value === 'object'
  ? Object.entries(value).map(([key, item]) => `${key}=${item}`).join(', ') : '';
const contributionEntries = (value) => Array.isArray(value)
  ? value.map((row) => [row.component_id, row.contribution]) : Object.entries(value || {});
const contributionText = (value, label = componentLabel) => Array.isArray(value)
  ? value.map((row) => `${label(row.component_id)} ${signed(row.contribution)} (оценка ${signed(row.score)}, вес ${percent(row.effective_weight)})`).join(' · ')
  : contributionEntries(value).map(([key, score]) => `${label(key)} ${signed(score)}`).join(' · ');

function appendAuditTable(parent, headers, rows) {
  const scroller = document.createElement('div');
  scroller.className = 'ai-unified-audit-table';
  scroller.style.overflowX = 'auto';
  const table = document.createElement('table');
  table.className = 'tiny';
  const head = document.createElement('thead');
  const heading = document.createElement('tr');
  for (const text of headers) {
    const cell = document.createElement('th');
    cell.textContent = text;
    heading.appendChild(cell);
  }
  head.appendChild(heading);
  const body = document.createElement('tbody');
  for (const values of rows) {
    const row = document.createElement('tr');
    for (const value of values) {
      const cell = document.createElement('td');
      cell.textContent = value === null || value === undefined ? '—' : String(value);
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
  table.append(head, body);
  scroller.appendChild(table);
  parent.appendChild(scroller);
}

// Presentation only: the backend freezes eligibility, ranking and the single
// execution plan. A scenario score must never be displayed as historical P&L.
export function mountUnifiedEdgeEnsemble(container, audit) {
  container.replaceChildren();
  if (!audit || typeof audit !== 'object') return;
  const definitions = Array.isArray(audit.expert_registry?.definitions) ? audit.expert_registry.definitions : [];
  const expertLabels = new Map(definitions.map((row) => [row.expert_id, row.label || row.expert_id]));
  const label = (id) => expertLabels.get(id) || componentLabel(id);
  const panel = document.createElement('section');
  panel.className = 'ai-edge-management ai-unified-edge-ensemble';
  appendTextLine(panel, 'ai-execution-title', 'ЕДИНЫЙ ВЫБОР · ПЕРЕВЕСЫ В РЕШЕНИИ');
  const selected = audit.selected_candidate_id;
  const candidates = Array.isArray(audit.candidates) ? audit.candidates : [];
  const chosen = candidates.find((row) => row.candidate_id === selected) || {};
  appendTextLine(panel, 'ai-execution-instruction',
    `${audit.selected_policy || '—'} · ${parameterText(chosen.parameters) || selected || '—'} · схема ${audit.scheme || '—'} · ${audit.instrument || '—'} · режим ${audit.regime || '—'}`);
  appendTextLine(panel, 'tiny',
    `Модельная экономика после издержек: Expected ${signed(chosen.expected_net_r, 'R')} · CVaR10 ${signed(chosen.cvar10_net_r, 'R')} · ΔExpected к HOLD ${signed(chosen.delta_expected_r, 'R')}.`);
  if (audit.regime_context) appendTextLine(panel, 'tiny dim',
    `Применимость по режиму: ${audit.regime_context.reason || 'UNKNOWN'} · качество ${audit.regime_context.quality ?? 0}. Рабочая классификация без дополнительного голоса и динамической смены долей.`);
  appendTextLine(panel, 'tiny dim',
    'Баллы ранжируют допустимые действия; не являются прибылью или вероятностью. Expected/CVaR — модельные сценарии. Историческая прибыль и частота лишних вмешательств оцениваются отдельно.');
  appendTextLine(panel, 'tiny dim', audit.shared_scenario_bank
    ? 'Кандидаты с оценкой рассчитаны на общих сценариях.'
    : `Общий набор сценариев не подтверждён; расширенные действия используют парное сравнение с HOLD. ${audit.economics_scope || ''}`);
  const bank = audit.scenario_bank || audit.comparison_bank;
  if (bank) appendTextLine(panel, 'tiny dim',
    `Банк ${bank.bank_id || '—'} · источник ${bank.source || '—'} · исходный авторитетный банк: ${bank.exact_authoritative_bank ? 'использован' : 'не использован'} · исполнение ${bank.execution_assumption || '—'}.`);
  appendTextLine(panel, 'tiny dim',
    'Δ общего сравнения не заменяет исходные ограничения риска, источников и независимого допуска. Раздел «Независимая консервативная проверка допуска» показывает собственные HOLD/Δ и метод; его отказ сохраняется при большем приросте общей модели.');
  appendTextLine(panel, 'tiny dim',
    'Вес не отменяет hard-risk/CVaR. Исполняется единственный действующий план после обязательных ограничений риска.');
  const components = Array.isArray(audit.components) ? audit.components : [];
  appendAuditTable(panel, ['Компонент', 'Номинальный → фактический вес', 'Качество · возраст', 'Доступность / снижение влияния'],
    components.map((row) => [row.label || label(row.component_id),
      `${percent(row.nominal_weight)} → ${percent(row.effective_weight)}`,
      `${percent(row.quality)} · ${row.age_sec === null || row.age_sec === undefined ? '—' : row.age_sec} сек`,
      `${row.availability ?? row.available ?? '—'} · ${row.reason || 'доступен'}${Array.isArray(row.suppression_reasons) && row.suppression_reasons.length ? ' · ' + row.suppression_reasons.join(', ') : ''}`]));
  for (const row of components) {
    const source = (row.source_ids || []).join(', ') || '—';
    const family = (row.evidence_family_ids || []).join(', ') || '—';
    const freshness = finiteNumber(row.freshness_multiplier ?? row.freshness_factor);
    const dependence = finiteNumber(row.dependence_multiplier ?? row.duplicate_factor);
    const suppression = `${freshness !== null && freshness < 1 ? ' · свежесть ×' + freshness.toFixed(3) : ''}${dependence !== null && dependence < 1 ? ' · повторные доказательства ×' + dependence.toFixed(3) : ''}`;
    appendTextLine(panel, 'tiny dim', `${row.label || label(row.component_id)} · источники: ${source} · семьи доказательств: ${family}${suppression}.`);
  }
  const rawContributions = chosen.component_contributions || {};
  const contributions = contributionEntries(rawContributions);
  if (contributions.length) appendTextLine(panel, 'tiny',
    'Вклад в балл выбранного действия: ' + contributionText(rawContributions, label));

  const detail = document.createElement('details');
  const summary = document.createElement('summary');
  summary.textContent = `Все кандидаты и причины исключения (${candidates.length})`;
  detail.appendChild(summary);
  appendAuditTable(detail, ['Действие · параметры', 'Статус / причина', 'Expected net', 'CVaR10 net', 'ΔExpected/HOLD', 'Балл', 'Вклад компонентов'],
    candidates.map((row) => [
      `${row.policy || '—'} · ${parameterText(row.parameters) || row.candidate_id || '—'}`,
      `${row.candidate_id === selected ? 'выбран' : (row.ranking_eligible ?? row.eligible) ? 'допустим' : 'исключён'}${row.ranking_reason || row.reason ? ' · ' + (row.ranking_reason || row.reason) : ''}`,
      signed(row.expected_net_r, 'R'), signed(row.cvar10_net_r, 'R'),
      signed(row.delta_expected_r, 'R'), signed(row.score), contributionText(row.component_contributions, label),
    ]));
  panel.appendChild(detail);
  const counterfactuals = Array.isArray(audit.counterfactuals) ? audit.counterfactuals : [];
  appendAuditTable(panel, ['Решение без компонента', 'Действие', 'Изменение'],
    counterfactuals.map((row) => [label(row.excluded_component_id),
      `${row.selected_policy || '—'} · ${row.selected_candidate_id || '—'}`,
      !row.selected_candidate_id ? 'недоступно' : row.selected_candidate_id === selected ? 'не изменилось' : 'изменилось']));
  appendTextLine(panel, 'tiny', 'Сравнение схем при неизменной модельной экономике кандидатов:');
  const schemes = Array.isArray(audit.scheme_comparisons) ? audit.scheme_comparisons : [];
  appendAuditTable(panel, ['Схема', 'Выбранное действие', 'Expected net', 'CVaR10 net'],
    schemes.map((row) => [row.scheme, row.selected_policy, signed(row.expected_net_r, 'R'), signed(row.cvar10_net_r, 'R')]));
  const edgeFamilies = Array.isArray(audit.edge_families) ? audit.edge_families
    : Object.entries(audit.edge_families || {}).map(([family_id, row]) => ({family_id, ...row}));
  if (edgeFamilies.length) {
    appendTextLine(panel, 'tiny', 'Семейства edge:');
    appendTextLine(panel, 'tiny dim', 'Рабочая интерпретация LLM использует текущие факты и часть его общего веса. Историческая модель имеет отдельный допуск.');
    appendAuditTable(panel, ['Семейство', 'Входы / исторический прогноз', 'Рабочая оценка · доля · вклад', 'Статус / что требуется'], edgeFamilies.map((row) => [
      row.family_id || row.edge_family || row.family || row.name || '—',
      `${row.available === true ? 'получены' : 'недоступны'} / ${row.forecast_available === true ? 'доступен' : 'недоступен'}`,
      row.working_assessment_available === true
        ? `рабочая интерпретация LLM · ${signed(row.working_score)} · ${percent(row.working_effective_weight)} · ${signed(row.working_contribution)} · ${row.working_reason_ru || '—'}`
        : `недоступна · ${row.working_assessment_reason || 'NO_WORKING_FAMILY_ASSESSMENT'}`,
      `${row.readiness || row.status || 'UNAVAILABLE'} · ${row.reason || 'причина не сообщена'}${Array.isArray(row.needs_data) && row.needs_data.length ? ' · нужно: ' + row.needs_data.join(', ') : ''}`,
    ]));
    const familyCounterfactuals = Array.isArray(audit.family_counterfactuals) ? audit.family_counterfactuals : [];
    if (familyCounterfactuals.length) appendAuditTable(panel, ['Без рабочей оценки семейства', 'Решение', 'Изменение'],
      familyCounterfactuals.map((row) => [row.excluded_family_id,
        `${row.selected_policy || '—'} · ${row.selected_candidate_id || '—'}`,
        !row.selected_candidate_id ? 'недоступно' : row.selected_candidate_id === selected ? 'не изменилось' : 'изменилось']));
  }
  container.appendChild(panel);
}

// Human-readable explanation of the edge layer already used by the backend.
// It is informational: execution remains bound to management_decision below.
export function mountEdgeManagement(container, payload) {
  container.replaceChildren();
  if (payload?.unified_edge_ensemble) {
    mountUnifiedEdgeEnsemble(container, payload.unified_edge_ensemble);
    return;
  }
  if (!payload || !payload.available) return;

  const panel = document.createElement('section');
  panel.className = 'ai-edge-management';
  appendTextLine(panel, 'ai-execution-title', 'ПЕРЕВЕСЫ В РЕШЕНИИ');

  const weight = finiteNumber(payload.weights?.combined_base ?? payload.weights?.combined) || 0;
  const extendedWeight = finiteNumber(payload.weights?.combined_extended) || 0;
  appendTextLine(
    panel,
    'ai-execution-instruction',
    `Сейчас: ${payload.action_now || 'HOLD'} · ${payload.direction_ru || 'нет чистого направления'} · базовый вес ${(weight * 100).toFixed(1)}% · расширенный ${(extendedWeight * 100).toFixed(1)}%`,
  );

  const mathematical = payload.mathematical_edge || {};
  if (mathematical.instrument) {
    const base = finiteNumber(payload.weights?.mathematical_base) || 0;
    const extended = finiteNumber(payload.weights?.mathematical_extended) || 0;
    const probabilities = mathematical.probabilities || {};
    const probabilityText = (value) => {
      const number = finiteNumber(value);
      return number === null ? 'нет оценки' : `${(number * 100).toFixed(1)}%`;
    };
    appendTextLine(panel, 'tiny',
      `Математический edge · ${mathematical.instrument} · ${mathematical.horizon_minutes || '—'} мин: базовые решения ${(base * 100).toFixed(1)}%, расширенные ${(extended * 100).toFixed(1)}%.`);
    if (mathematical.role) {
      const role = mathematical.role === 'LOW_MOVEMENT_TIME_MANAGEMENT'
        ? 'вероятность движения: влияет на TIME_STOP/REDUCE_TAKE'
        : 'направление цены';
      appendTextLine(panel, 'tiny dim',
        `${role} · P(up | move) ${probabilityText(probabilities.direction)} · P(move >2bp) ${probabilityText(probabilities.movement)}.`);
    } else if (mathematical.reason) {
      appendTextLine(panel, 'tiny dim', `Причина нулевого веса: ${mathematical.reason}.`);
    }
  }

  const counterfactual = payload.counterfactual || {};
  if (counterfactual.raw_policy_without_edge && counterfactual.raw_policy_with_edge) {
    const changed = counterfactual.raw_policy_changed ? 'изменил первичный выбор' : 'не изменил первичный выбор';
    appendTextLine(
      panel,
      counterfactual.raw_policy_changed ? 'tiny amber' : 'tiny dim',
      `Без перевеса: ${counterfactual.raw_policy_without_edge} → с перевесом: ${counterfactual.raw_policy_with_edge} (${changed}).`,
    );
  }
  for (const [beforeKey, afterKey, stage] of [
    ['raw_policy_without_mathematical_edge', 'raw_policy_with_mathematical_edge', 'Базовый выбор'],
    ['extended_policy_without_mathematical_edge', 'extended_policy_with_mathematical_edge', 'Расширенный кандидат'],
  ]) {
    const before = counterfactual[beforeKey];
    const after = counterfactual[afterKey];
    if (before && after) {
      appendTextLine(panel, before === after ? 'tiny dim' : 'tiny amber',
        `${stage} без математического edge: ${before} → с ним: ${after}. Финальное действие определяется gate и арбитром.`);
    }
  }

  const signals = Array.isArray(payload.top_signals) ? payload.top_signals : [];
  if (signals.length) appendTextLine(panel, 'tiny', 'Совпавшие условия рынка:');
  signals.forEach((signal) => {
    const status = EDGE_STATUS_RU[signal.status] || signal.status || 'сигнал';
    const relation = EDGE_RELATION_RU[signal.position_relation] || signal.position_relation || 'без направления';
    const horizon = finiteNumber(signal.horizon_minutes);
    const sample = finiteNumber(signal.selected_effective_n) ?? finiteNumber(signal.selected_test_n);
    const folds = finiteNumber(signal.positive_fold_count);
    const totalFolds = finiteNumber(signal.evaluated_fold_count);
    const parts = [
      `${signal.target_family || signal.target_id || 'цель'}${horizon !== null ? ` · ${horizon} мин` : ''}`,
      status,
      relation,
    ];
    const shift = edgeShiftText(signal.prediction_shift);
    if (shift) parts.push(shift);
    if (sample !== null) parts.push(`Nэф=${sample.toFixed(0)}`);
    if (folds !== null && totalFolds !== null) parts.push(`фолды ${folds.toFixed(0)}/${totalFolds.toFixed(0)}`);
    appendTextLine(panel, 'tiny dim', `• ${parts.join(' · ')}`);
  });

  if (payload.blocked_reason) {
    appendTextLine(panel, 'tiny amber', `Ограничение: ${payload.blocked_reason}`);
  }
  appendTextLine(
    panel,
    'tiny dim',
    payload.measurement_note_ru || 'Перевес не отменяет hard-risk/CVaR и не исполняет действие автоматически.',
  );
  container.appendChild(panel);
}

// Optional LLM extended action. It is a separate manual lane and never replaces
// the authoritative quant management decision rendered below.
export function mountArmedShadowActions(container, actions, post, onApplied = () => {}) {
  container.replaceChildren();
  for (const action of actions || []) {
    if (action.status !== 'armed') continue;
    const panel = document.createElement('section');
    panel.className = 'ai-shadow-working-action';
    appendTextLine(panel, 'ai-execution-title',
      `${action.policy} · УСЛОВИЕ УСТАНОВЛЕНО У БРОКЕРА`);
    const status = document.createElement('div');
    status.className = 'tiny dim';
    const priceLabel = document.createElement('label');
    priceLabel.textContent = 'Фактическая цена исполнения у брокера: ';
    const priceInput = document.createElement('input');
    priceInput.type = 'number';
    priceInput.step = 'any';
    priceInput.min = '0';
    priceLabel.appendChild(priceInput);
    const yes = document.createElement('button');
    yes.className = 'btn btn-primary';
    yes.textContent = 'ИСПОЛНЕНО У БРОКЕРА';
    const no = document.createElement('button');
    no.className = 'btn';
    no.textContent = 'УСЛОВИЕ ОТМЕНЕНО';
    const submit = async (executed) => {
      const price = finiteNumber(priceInput.value);
      if (executed && (price === null || price <= 0)) {
        status.textContent = 'Укажите фактическую цену исполнения у брокера.';
        return;
      }
      yes.disabled = true; no.disabled = true;
      try {
        const result = await post('/api/ai/shadow-action/ack', {
          action_id: action.action_id, trade_id: action.trade_id, executed,
          ...(executed ? { execution_price: price } : {}),
        });
        yes.remove(); no.remove();
        status.textContent = executed ? 'Исполнение записано в остаток позиции.' : 'Условие отменено.';
        await onApplied(result);
      } catch (error) {
        status.textContent = error?.message || 'Не удалось сохранить исполнение.';
        yes.disabled = false; no.disabled = false;
      }
    };
    yes.addEventListener('click', () => submit(true));
    no.addEventListener('click', () => submit(false));
    panel.append(priceLabel, yes, no, status);
    container.appendChild(panel);
  }
}

export function mountShadowWorkingAction(container, shadow, post, onApplied = () => {}, decision = null) {
  container.replaceChildren();
  const action = shadow?.working_action;
  if (action?.action_id && action.action_id === decision?.decision_id) return;
  if (!action || !action.action_id || action.execution_status !== 'pending_execution' ||
      !action.manual_execution_required) return;

  const panel = document.createElement('section');
  panel.className = 'ai-shadow-working-action';
  appendTextLine(panel, 'ai-execution-title', 'РАСШИРЕННЫЙ ВАРИАНТ LLM · РУЧНО');
  appendTextLine(
    panel, 'ai-shadow-action-instruction',
    action.instruction_ru || action.policy || 'Расширенный вариант без инструкции.',
  );
  appendTextLine(
    panel, 'tiny amber',
    'Это альтернативный LLM-вариант, не production-решение quant. Подтверждайте только после фактического изменения у брокера.',
  );
  const status = document.createElement('div');
  status.className = 'tiny dim';
  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const yes = document.createElement('button');
  yes.className = 'btn btn-primary';
  yes.textContent = action.policy === 'TIME_STOP' || action.policy === 'SCALE_OUT_ON_SPIKE'
    ? 'УСЛОВИЕ УСТАНОВЛЕНО' : 'ВЫПОЛНЕНО';
  const no = document.createElement('button');
  no.className = 'btn';
  no.textContent = 'НЕ ВЫПОЛНЕНО';
  actions.append(yes, no);
  panel.append(actions, status);
  container.appendChild(panel);

  let submitting = false;
  let settled = false;
  const submit = async (executed) => {
    if (submitting || settled) return;
    submitting = true;
    yes.disabled = true; no.disabled = true;
    try {
      const result = await post('/api/ai/shadow-action/ack', {
        action_id: action.action_id,
        trade_id: action.trade_id,
        executed,
      });
      settled = true;
      actions.remove();
      status.className = 'tiny green';
      if (!executed) {
        status.textContent = 'Вариант записан как неисполненный.';
      } else if (result.execution_status === 'armed') {
        status.textContent = 'Условие записано как установленное у брокера.';
      } else {
        const stop = finiteNumber(result.position_state?.active_stop_price);
        const take = finiteNumber(result.position_state?.take);
        status.textContent = `Исполнение записано.${stop !== null ? ` Стоп: ${stop}.` : ''}${take !== null ? ` Take: ${take}.` : ''}`;
      }
      await onApplied(result);
    } catch (error) {
      status.className = 'tiny red';
      const message = typeof error?.message === 'string' && error.message
        ? error.message : 'Не удалось сохранить расширенное действие.';
      status.textContent = message;
      yes.disabled = false; no.disabled = false;
    } finally {
      submitting = false;
    }
  };
  yes.addEventListener('click', () => submit(true));
  no.addEventListener('click', () => submit(false));
}

// Pure management-decision UI. The backend remains authoritative.
export function mountManagementDecision(container, decision, post, onApplied = () => {}) {
  container.replaceChildren();
  if (!decision || !decision.manual_execution_required ||
      decision.execution_status !== 'pending_execution') return;
  const title = document.createElement('div');
  title.className = 'ai-execution-title';
  title.textContent = 'ФАКТИЧЕСКОЕ ИСПОЛНЕНИЕ У БРОКЕРА';
  const instruction = document.createElement('div');
  instruction.className = 'ai-execution-instruction';
  instruction.textContent = (decision.authority === 'AI_RISK_OVERLAY_EXTENDED'
    ? 'Расширенное решение после проверки Expected/CVaR: ' : 'Решение ИИ: ') + decision.instruction_ru;
  const hint = document.createElement('div');
  hint.className = 'tiny dim';
  hint.textContent = 'Отметьте результат только после фактического действия у брокера. Повторное подтверждение этого же решения не требуется.';
  const before = finiteNumber(decision.remaining_fraction_before_action);
  const after = finiteNumber(decision.remaining_fraction_after_action);
  const fraction = finiteNumber(decision.incremental_close_fraction);
  if (before !== null && after !== null && fraction !== null && fraction > 0) {
    hint.textContent += ` Из исходной позиции: осталось ${(before * 100).toFixed(1)}%, закрыть ${(before * fraction * 100).toFixed(1)}%, останется ${(after * 100).toFixed(1)}%.`;
  }
  if (decision.repeat_reduction) {
    hint.textContent += ' Новое сокращение уже уменьшенного остатка; предыдущее исполнение учтено.';
  }
  const extended = decision.authority === 'AI_RISK_OVERLAY_EXTENDED';
  const priceLabel = document.createElement('label');
  priceLabel.textContent = extended ? 'Текущая цена у брокера: '
    : 'Цена фактического исполнения (необязательно; иначе оценка по котировке при подтверждении): ';
  const priceInput = document.createElement('input');
  priceInput.type = 'number';
  priceInput.step = 'any';
  priceInput.min = '0';
  priceLabel.appendChild(priceInput);
  const status = document.createElement('div');
  status.className = 'tiny dim';
  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const yes = document.createElement('button');
  yes.className = 'btn btn-primary';
  yes.textContent = decision.policy === 'TIME_STOP' || decision.policy === 'SCALE_OUT_ON_SPIKE'
    ? 'УСЛОВИЕ УСТАНОВЛЕНО' : 'ВЫПОЛНЕНО';
  const no = document.createElement('button');
  no.className = 'btn';
  no.textContent = 'НЕ ВЫПОЛНЕНО';
  actions.append(yes, no);
  container.append(title, instruction, hint);
  container.appendChild(priceLabel);
  container.append(actions, status);
  let submitting = false;
  let settled = false;
  const submit = async (executed) => {
    if (submitting || settled) return;
    const brokerPrice = finiteNumber(priceInput.value);
    if (executed && ((extended && brokerPrice === null) || (brokerPrice !== null && brokerPrice <= 0))) {
      status.textContent = 'Укажите текущую цену у брокера.';
      return;
    }
    submitting = true;
    yes.disabled = true; no.disabled = true;
    try {
      const result = await post('/api/ai/decision/ack', {
        decision_id: decision.decision_id,
        trade_id: decision.trade_id,
        executed,
        ...(executed && brokerPrice !== null ? { execution_price: brokerPrice } : {}),
      });
      settled = true;
      actions.remove();
      const remaining = Number(result.position_state?.remaining_position_fraction);
      status.className = 'tiny green';
      if (executed) {
        status.textContent = result.trade_closed
          ? 'Сделка закрыта. Все фиксации учтены в журнале; повторно закрывать её не нужно.'
          : result.execution_status === 'armed'
          ? 'Условие записано как установленное у брокера.'
          : Number.isFinite(remaining)
          ? 'Исполнение записано. Остаток: ' + (remaining * 100).toFixed(1) + '%.'
          : 'Исполнение записано.';
      } else {
        status.textContent = 'Рекомендация записана как неисполненная до следующего review.';
      }
      await onApplied(result);
    } catch (error) {
      status.className = 'tiny red';
      const rawMessage = typeof error?.message === 'string' ? error.message : '';
      const message = rawMessage && !rawMessage.includes('[object Object]')
        ? rawMessage
        : 'Не удалось сохранить исполнение. Обновите разбор и повторите попытку.';
      status.textContent = message;
      yes.disabled = false; no.disabled = false;
    } finally {
      submitting = false;
    }
  };
  yes.addEventListener('click', () => submit(true));
  no.addEventListener('click', () => submit(false));
}
