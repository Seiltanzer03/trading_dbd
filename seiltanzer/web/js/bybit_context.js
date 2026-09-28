// Separate venue context: never modifies S.ridge, the strategy, or management.
let lastPlot = '';
let current = null;

export function selectIVSurface(primary, supplement, mode = 'auto') {
  if (mode !== 'bybit' && primary?.value?.length && primary.status !== 'no_data') return primary;
  if (supplement?.options?.surface?.value?.length && supplement.options.status !== 'no_data') {
    return supplement.options.surface;
  }
  return mode === 'bybit' ? {status: 'no_data', value: null, source: 'Bybit',
    error: supplement?.options?.error || 'Нет доступной цепочки Bybit'} : primary;
}

export function updateBybitContext(supplement) {
  current = supplement?.options;
  const details = document.getElementById('bybit-gamma-context');
  const caption = document.getElementById('bybit-context-status');
  if (!details || !caption) return;
  caption.textContent = current?.gamma?.length && current.status !== 'no_data'
    ? `${current.source} · ${current.gamma.length} точек · НЕ dealer GEX`
    : `Bybit: ${current?.error || 'ожидание данных'} · основные источники сохранены`;
  if (!details.dataset.bound) {
    details.dataset.bound = '1';
    details.addEventListener('toggle', renderGamma);
  }
  if (details.open) renderGamma();
}

function renderGamma() {
  const plot = document.getElementById('bybit-gamma-plot');
  if (!plot || !window.Plotly) return;
  const signature = `${current?.source}:${current?.ts}:${current?.status}:${current?.error}`;
  if (signature === lastPlot) return;
  lastPlot = signature;
  if (current?.status === 'no_data' || !current?.gamma?.length) {
    window.Plotly.purge(plot);
    plot.textContent = 'Нет проверенных OI/gamma — нули не подставляются.';
    return;
  }
  plot.textContent = '';
  // Keep expiries separate. No subtraction of puts to invent dealer direction.
  const keys = [...new Set(current.gamma.map(x => `${x.expiry}:${x.side}`))];
  const traces = keys.map(key => {
    const rows = current.gamma.filter(x => `${x.expiry}:${x.side}` === key);
    const date = new Date(rows[0].expiry * 1000).toISOString().slice(0, 10);
    return {type: 'bar', name: `${date} ${rows[0].side}`,
      x: rows.map(x => x.strike), y: rows.map(x => x.value)};
  });
  window.Plotly.react(plot, traces, {height: 280, barmode: 'group',
    margin: {t: 25, b: 55, l: 75, r: 15},
    xaxis: {title: `${current.base} · native strike`},
    yaxis: {title: 'OI × gamma × S² × 1% (USDT)'},
    legend: {orientation: 'h'}}, {responsive: true, displayModeBar: false});
}
