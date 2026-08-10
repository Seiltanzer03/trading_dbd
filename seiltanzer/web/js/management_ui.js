// Pure management-decision UI. The backend remains authoritative.
export function mountManagementDecision(container, decision, post, onApplied = () => {}) {
  container.replaceChildren();
  if (!decision || !decision.manual_execution_required ||
      decision.execution_status !== 'pending_execution') return;
  const title = document.createElement('div');
  title.className = 'ai-execution-title';
  title.textContent = 'ФАКТИЧЕСКОЕ ИСПОЛНЕНИЕ';
  const instruction = document.createElement('div');
  instruction.className = 'ai-execution-instruction';
  instruction.textContent = 'Решение ИИ: ' + decision.instruction_ru;
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
  container.append(title, instruction, actions, status);
  const submit = async (executed) => {
    yes.disabled = true; no.disabled = true;
    try {
      const result = await post('/api/ai/decision/ack', {
        decision_id: decision.decision_id,
        trade_id: decision.trade_id,
        executed,
      });
      const remaining = result.position_state?.remaining_position_fraction;
      status.className = 'tiny green';
      status.textContent = executed
        ? 'Исполнение записано. Остаток: ' + (remaining * 100).toFixed(1) + '%.'
        : 'Рекомендация записана как неисполненная до следующего review.';
      onApplied(result);
    } catch (error) {
      status.className = 'tiny red';
      status.textContent = error.message;
      yes.disabled = false; no.disabled = false;
    }
  };
  yes.addEventListener('click', () => submit(true));
  no.addEventListener('click', () => submit(false));
}
