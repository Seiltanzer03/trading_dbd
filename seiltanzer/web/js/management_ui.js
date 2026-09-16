import { mountG1SEvidencePanel } from './g1s_evidence.js';
import './trade_delete_ui_guard.js';

// Mount once from an already-loaded main-dashboard module. The evidence panel is
// independent from management execution and reads research-only bounded APIs.
mountG1SEvidencePanel();

const EDGE_STATUS_RU = {
  EARLY_ADVANTAGE: 'раннее преимущество',
  EARLY_DISADVANTAGE: 'гипотеза хуже базовой (не обратный сигнал)',
  EARLY_MIXED: 'смешанный результат',
  EARLY_UNDECIDED: 'результат пока не определён',
};

const EDGE_RELATION_RU = {
  SUPPORTS_POSITION: 'за текущую позицию',
  OPPOSES_POSITION: 'против текущей позиции',
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

// Human-readable explanation of the edge layer already used by the backend.
// It is informational: execution remains bound to management_decision below.
export function mountEdgeManagement(container, payload) {
  container.replaceChildren();
  if (!payload || !payload.available) return;

  const panel = document.createElement('section');
  panel.className = 'ai-edge-management';
  appendTextLine(panel, 'ai-execution-title', 'ПЕРЕВЕСЫ В РЕШЕНИИ');

  const weight = finiteNumber(payload.weights?.combined) || 0;
  appendTextLine(
    panel,
    'ai-execution-instruction',
    `Сейчас: ${payload.action_now || 'HOLD'} · ${payload.direction_ru || 'нет чистого направления'} · вес ${(weight * 100).toFixed(1)}%`,
  );

  const counterfactual = payload.counterfactual || {};
  if (counterfactual.raw_policy_without_edge && counterfactual.raw_policy_with_edge) {
    const changed = counterfactual.raw_policy_changed ? 'изменил первичный выбор' : 'не изменил первичный выбор';
    appendTextLine(
      panel,
      counterfactual.raw_policy_changed ? 'tiny amber' : 'tiny dim',
      `Без перевеса: ${counterfactual.raw_policy_without_edge} → с перевесом: ${counterfactual.raw_policy_with_edge} (${changed}).`,
    );
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
  instruction.textContent = 'Решение ИИ: ' + decision.instruction_ru;
  const hint = document.createElement('div');
  hint.className = 'tiny dim';
  hint.textContent = 'Отметьте результат только после фактического действия у брокера. Повторное подтверждение этого же решения не требуется.';
  const status = document.createElement('div');
  status.className = 'tiny dim';
  const actions = document.createElement('div');
  actions.className = 'form-actions';
  const yes = document.createElement('button');
  yes.className = 'btn btn-primary';
  yes.textContent = 'ВЫПОЛНЕНО';
  const no = document.createElement('button');
  no.className = 'btn';
  no.textContent = 'НЕ ВЫПОЛНЕНО';
  actions.append(yes, no);
  container.append(title, instruction, hint, actions, status);
  let submitting = false;
  let settled = false;
  const submit = async (executed) => {
    if (submitting || settled) return;
    submitting = true;
    yes.disabled = true; no.disabled = true;
    try {
      const result = await post('/api/ai/decision/ack', {
        decision_id: decision.decision_id,
        trade_id: decision.trade_id,
        executed,
      });
      settled = true;
      actions.remove();
      const remaining = Number(result.position_state?.remaining_position_fraction);
      status.className = 'tiny green';
      if (executed) {
        status.textContent = Number.isFinite(remaining)
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
