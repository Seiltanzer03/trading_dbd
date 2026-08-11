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
      const message = (typeof error?.message === 'string' && error.message !== '[object Object]')
        ? error.message
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
