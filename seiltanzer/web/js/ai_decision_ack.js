// Manual acknowledgement controls for stateful AI management decisions.
// Loaded as a side effect from util.js so the existing app orchestrator stays intact.

const browser = typeof window !== 'undefined' && typeof document !== 'undefined';
const nativeFetch = browser ? window.fetch.bind(window) : null;

function requestUrl(input) {
  if (typeof input === 'string') return input;
  if (typeof URL !== 'undefined' && input instanceof URL) return input.href;
  return input?.url || '';
}

function requestMethod(input, init) {
  return String(init?.method || input?.method || 'GET').toUpperCase();
}

function isVerdictRequest(input, init) {
  if (!browser) return false;
  try {
    const url = new URL(requestUrl(input), window.location.href);
    return url.pathname === '/api/ai/verdict' && requestMethod(input, init) === 'POST';
  } catch (_) {
    return false;
  }
}

function makeButton(label, primary = false) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = primary ? 'btn btn-primary' : 'btn';
  button.textContent = label;
  button.style.minWidth = '138px';
  return button;
}

function setBusy(buttons, busy) {
  buttons.forEach((button) => { button.disabled = busy; });
}

async function acknowledge(decision, status, statusEl, buttons) {
  setBusy(buttons, true);
  statusEl.textContent = status === 'executed'
    ? 'Сохраняю подтверждение исполнения…'
    : 'Сохраняю отметку «не выполнено»…';
  statusEl.style.color = '';
  try {
    const response = await nativeFetch('/api/ai/decision/ack', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        trade_id: decision.trade_id,
        decision_id: decision.decision_id,
        status,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    statusEl.textContent = body.message || 'Статус решения сохранён.';
    statusEl.style.color = status === 'executed' ? 'var(--green)' : 'var(--ink)';
    buttons.forEach((button) => { button.hidden = true; });
  } catch (error) {
    statusEl.textContent = 'Не удалось сохранить: ' + error.message;
    statusEl.style.color = 'var(--red)';
    setBusy(buttons, false);
  }
}

function attachDecisionControls(payload) {
  if (!browser) return;
  const decision = payload?.management_decision;
  if (!decision || decision.execution_status !== 'pending_execution'
      || !decision.manual_execution_required || !decision.decision_id) return;

  const modal = document.querySelector('#modal');
  const actions = modal?.querySelector('.modal-actions');
  if (!modal || !actions) return;
  modal.querySelector('#ai-decision-ack-panel')?.remove();

  const panel = document.createElement('div');
  panel.id = 'ai-decision-ack-panel';
  panel.style.width = '100%';
  panel.style.borderTop = '1px solid var(--rule)';
  panel.style.paddingTop = '12px';
  panel.style.marginTop = '8px';

  const title = document.createElement('div');
  title.className = 'lbl';
  title.textContent = 'ФАКТИЧЕСКОЕ ИСПОЛНЕНИЕ У БРОКЕРА';

  const hint = document.createElement('div');
  hint.className = 'tiny dim';
  hint.style.margin = '5px 0 10px';
  hint.textContent =
    'Нажмите «Выполнено» только после фактического исполнения указанного действия. ' +
    'Это не отправляет ордер брокеру, а сохраняет состояние для следующего ИИ-разбора.';

  const row = document.createElement('div');
  row.style.display = 'flex';
  row.style.flexWrap = 'wrap';
  row.style.gap = '8px';

  const executed = makeButton('ВЫПОЛНЕНО', true);
  const notExecuted = makeButton('НЕ ВЫПОЛНЕНО');
  row.append(executed, notExecuted);

  const statusEl = document.createElement('div');
  statusEl.className = 'tiny';
  statusEl.style.marginTop = '9px';
  statusEl.textContent = `Ожидается отметка · ${decision.decision_id}`;

  const buttons = [executed, notExecuted];
  executed.addEventListener('click', () =>
    acknowledge(decision, 'executed', statusEl, buttons));
  notExecuted.addEventListener('click', () =>
    acknowledge(decision, 'not_executed', statusEl, buttons));

  panel.append(title, hint, row, statusEl);
  actions.parentNode.insertBefore(panel, actions);
}

if (browser) {
  window.fetch = async function seiltanzerFetch(input, init) {
    const response = await nativeFetch(input, init);
    if (isVerdictRequest(input, init)) {
      response.clone().json()
        .then((payload) => setTimeout(() => attachDecisionControls(payload), 0))
        .catch(() => {});
    }
    return response;
  };
}
