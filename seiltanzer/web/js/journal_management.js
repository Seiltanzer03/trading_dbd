import { fmtPrice, fmtR, fmtTs } from './util.js';

const names = {
  TRADE_OPEN: 'Открытие', AI_CLOSE_10: 'ИИ: закрытие 10%', AI_CLOSE_25: 'ИИ: закрытие 25%',
  AI_CLOSE_50: 'ИИ: закрытие 50%', AI_EXIT: 'ИИ: полный выход', TAKE_EXIT: 'Выход по тейку',
  STOP_EXIT: 'Выход по стопу', BE_EXIT: 'Выход по БУ', MANUAL_EXIT: 'Закрытие остатка',
  MANUAL_REDUCTION: 'Ручная фиксация', LADDER_REDUCTION: 'Фиксация по лестнице',
  BE_ARM: 'Стратегия: БУ', AI_MOVE_TO_BE: 'ИИ: БУ', AI_TIGHTEN_STOP: 'ИИ: подтянуть стоп',
  AI_ADJUST_TAKE: 'ИИ: изменить тейк', AI_SCALE_OUT_ARM: 'Фиксация на импульсе: условие',
  AI_TIME_STOP_ARM: 'Выход по времени: условие', AI_SCALE_OUT_FILL: 'Фиксация на импульсе: исполнено',
  AI_TIME_STOP_FILL: 'Выход по времени: исполнено', AI_CONDITIONAL_CANCEL: 'Условие отменено',
};

export function totalAfterRemainingExit(trade, position, price) {
  if (price === null || price === '' || price === undefined || !Number.isFinite(Number(price)) || Number(price) <= 0 ||
      position?.realized_r_weighted == null || position?.remaining_position_fraction == null) return null;
  const risk = Math.abs(trade.entry - trade.stop);
  if (risk <= 0) return null;
  const sign = trade.direction === 'long' ? 1 : -1;
  return Number(position.realized_r_weighted) + Number(position.remaining_position_fraction)
    * sign * (Number(price) - trade.entry) / risk;
}

export function mountJournalManagement(container, payload) {
  container.replaceChildren();
  const { trade, summary, events } = payload;
  const line = document.createElement('p');
  const result = trade.result_r == null ? 'не определён' : fmtR(trade.result_r);
  line.textContent = `Результат всей сделки: ${result}. ` +
    (trade.result_basis === 'manual_total_override' ? 'Общий результат указан вручную.' :
      trade.result_basis === 'ledger_weighted' ? 'Сумма результатов закрытых долей исходного объёма.' : 'Старый ручной результат.') +
    (trade.result_status === 'ESTIMATED' ? ' Часть цен исполнения оценочная.' : '') +
    (trade.result_status === 'UNAVAILABLE' ? ' Не хватает цен или объёмов исполнений.' : '');
  container.appendChild(line);
  const costs = document.createElement('p');
  costs.className = 'tiny dim';
  costs.textContent = 'Расчёт по ценам исполнений не включает неподтверждённые комиссии и свопы. ' +
    'Для сверки с брокером можно исправить общий результат сделки в журнале.';
  container.appendChild(costs);
  if (!events.length) {
    const empty = document.createElement('p');
    empty.textContent = 'История исполнений этой сделки не записана; восстановить её из конечной цены нельзя.';
    container.appendChild(empty);
    return;
  }
  const remaining = document.createElement('p');
  remaining.textContent = `Остаток исходной позиции: ${(summary.remaining_position_fraction * 100).toFixed(1)}%. ` +
    `Исполнений закрытия: ${summary.fill_count}.`;
  container.appendChild(remaining);
  const wrapper = document.createElement('div');
  wrapper.style.overflowX = 'auto';
  const table = document.createElement('table');
  const header = table.insertRow();
  ['Время', 'Действие', 'Закрыто от исходного объёма', 'Цена', 'Вклад в итог, R', 'Стоп / тейк', 'Источник цены'].forEach(text => {
    const cell = document.createElement('th'); cell.textContent = text; header.appendChild(cell);
  });
  for (const event of events) {
    const row = table.insertRow();
    const fraction = Number(event.fraction_closed);
    const contribution = fraction > 0 && event.execution_r != null ? fraction * event.execution_r : null;
    const source = event.metadata?.execution_price_source;
    const values = [fmtTs(event.timestamp), names[event.event_type] || event.event_type,
      fraction > 0 ? (fraction * 100).toFixed(1) + '%' : '—',
      event.execution_price == null ? '—' : fmtPrice(event.execution_price),
      contribution == null ? '—' : fmtR(contribution),
      `${fmtPrice(event.active_stop)} / ${fmtPrice(event.take)}`,
      fraction <= 0 ? '—' : source === 'user_supplied_broker_fill' ? 'Фактическая' :
      event.execution_r == null ? 'Не указана' : 'Оценка / не уточнена'];
    values.forEach(value => { const cell = row.insertCell(); cell.textContent = value; });
  }
  wrapper.appendChild(table);
  container.appendChild(wrapper);
}
