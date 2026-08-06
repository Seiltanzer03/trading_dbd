// Stateful Probability Lattice revaluation panel.
// Uses the server-side tracker, never the decorative falling-ball animation.

const PANEL_ID = 'lattice-revaluation-panel';
let reconnectTimer = null;

function pct(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function pp(value, digits = 1) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value) * 100;
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)} пп`;
}

function r(value, digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}R`;
}

function signed(value, suffix = '', digits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`;
}

function ensureStyles() {
  if (document.getElementById('lattice-revaluation-style')) return;
  const style = document.createElement('style');
  style.id = 'lattice-revaluation-style';
  style.textContent = `
    #${PANEL_ID} { border-top: 1px solid rgba(255,255,255,.08); padding: 14px 18px 18px; }
    .lr-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:10px; }
    .lr-title { font: 700 12px/1.2 'IBM Plex Mono', monospace; letter-spacing:.08em; color:#bfc6cc; }
    .lr-badge { font:700 10px/1 'IBM Plex Mono',monospace; padding:5px 7px; border:1px solid #555; border-radius:3px; color:#bbb; white-space:nowrap; }
    .lr-badge.good { color:#4fd68b; border-color:rgba(79,214,139,.45); }
    .lr-badge.bad { color:#ff7373; border-color:rgba(255,115,115,.45); }
    .lr-badge.warn { color:#f2bd58; border-color:rgba(242,189,88,.45); }
    .lr-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px; }
    .lr-card { min-width:0; padding:10px 11px; border:1px solid rgba(255,255,255,.08); border-radius:5px; background:rgba(255,255,255,.018); }
    .lr-card.current { border-color:rgba(104,181,255,.32); background:rgba(104,181,255,.035); }
    .lr-card.score.good { border-color:rgba(79,214,139,.35); }
    .lr-card.score.bad { border-color:rgba(255,115,115,.35); }
    .lr-kicker { color:#777f87; font:700 9px/1.2 'IBM Plex Mono',monospace; letter-spacing:.08em; margin-bottom:7px; }
    .lr-main { color:#e2e6e9; font:700 16px/1.2 'IBM Plex Mono',monospace; white-space:nowrap; }
    .lr-sub { margin-top:5px; color:#8b939b; font:10px/1.45 'IBM Plex Mono',monospace; }
    .lr-good { color:#4fd68b; } .lr-bad { color:#ff7373; } .lr-warn { color:#f2bd58; }
    .lr-flow { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; margin-top:9px; }
    .lr-flow-cell { padding:8px 9px; border-radius:4px; border:1px solid rgba(255,255,255,.07); background:rgba(0,0,0,.12); }
    .lr-flow-name { color:#727b83; font:700 9px/1.2 'IBM Plex Mono',monospace; }
    .lr-flow-value { margin-top:4px; color:#d9dde0; font:700 12px/1.2 'IBM Plex Mono',monospace; }
    .lr-note { margin-top:8px; color:#69727a; font:9px/1.45 'IBM Plex Mono',monospace; }
    @media (max-width: 900px) { .lr-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media (max-width: 560px) { .lr-grid,.lr-flow { grid-template-columns:1fr; } #${PANEL_ID}{padding:12px;} }
  `;
  document.head.appendChild(style);
}

function ensurePanel() {
  ensureStyles();
  let panel = document.getElementById(PANEL_ID);
  if (panel) return panel;
  const host = document.getElementById('panel-lattice');
  if (!host) return null;
  panel = document.createElement('div');
  panel.id = PANEL_ID;
  panel.innerHTML = `
    <div class="lr-head">
      <div class="lr-title">ПЕРЕОЦЕНКА РАСПРЕДЕЛЕНИЯ · ВХОД → СРЕДНЕЕ → СЕЙЧАС</div>
      <div class="lr-badge" data-lr="source">НЕТ ДАННЫХ</div>
    </div>
    <div class="lr-grid" data-lr="cards"></div>
    <div class="lr-flow" data-lr="flow"></div>
    <div class="lr-note" data-lr="note">Считается на сервере из той же терминальной RND, а не из анимации шариков.</div>`;
  host.appendChild(panel);
  return panel;
}

function card(label, data, className = '') {
  const pTake = pct(data?.p_take);
  const ev = r(data?.barrier_ev_r);
  const center = r(data?.q50_r);
  const width = r(data?.width_r);
  return `<div class="lr-card ${className}">
    <div class="lr-kicker">${label}</div>
    <div class="lr-main">P тейка ${pTake}</div>
    <div class="lr-sub">barrier EV ${ev}<br>P50 ${center} · ширина P10–P90 ${width}</div>
  </div>`;
}

function scoreCard(rev) {
  const score = rev?.score || {};
  const de = rev?.change_from_entry || {};
  const direction = score.direction || 'neutral';
  const tone = direction === 'improving' ? 'good' : direction === 'deteriorating' ? 'bad' : '';
  const label = direction === 'improving' ? 'УЛУЧШЕНИЕ' : direction === 'deteriorating' ? 'УХУДШЕНИЕ' : 'БЕЗ ЯВНОГО СДВИГА';
  return `<div class="lr-card score ${tone}">
    <div class="lr-kicker">ВЗВЕШЕННАЯ ПЕРЕОЦЕНКА</div>
    <div class="lr-main ${tone ? `lr-${tone}` : ''}">${label} · ${signed(score.weighted, '', 2)}</div>
    <div class="lr-sub">к входу: P тейка ${pp(de.p_take)} · EV ${r(de.barrier_ev_r)}<br>центр ${r(de.q50_r)} · доверие ${(Number(score.confidence_weight || 0) * 100).toFixed(0)}%</div>
  </div>`;
}

function renderFlow(rev) {
  const current = rev.current?.buckets || {};
  const delta = rev.change_from_entry?.buckets || {};
  const rows = [
    ['СТОП-ХВОСТ ≤−1R', 'stop_tail'],
    ['КРАСНАЯ ЗОНА −1…0R', 'red_zone'],
    ['ЗЕЛЁНАЯ ЗОНА 0…T', 'green_zone'],
    ['ТЕЙК-ХВОСТ ≥T', 'take_tail'],
  ];
  return rows.map(([label, key]) => {
    const d = Number(delta[key]);
    const tone = Number.isFinite(d) ? (d > 0.002 ? (key.includes('stop') || key === 'red_zone' ? 'bad' : 'good') : d < -0.002 ? (key.includes('stop') || key === 'red_zone' ? 'good' : 'bad') : '') : '';
    return `<div class="lr-flow-cell">
      <div class="lr-flow-name">${label}</div>
      <div class="lr-flow-value">${pct(current[key])} <span class="${tone ? `lr-${tone}` : ''}">(${pp(delta[key])})</span></div>
    </div>`;
  }).join('');
}

function render(tick) {
  const panel = ensurePanel();
  if (!panel) return;
  const rev = tick?.lattice_revaluation;
  const sourceEl = panel.querySelector('[data-lr="source"]');
  const cardsEl = panel.querySelector('[data-lr="cards"]');
  const flowEl = panel.querySelector('[data-lr="flow"]');
  const noteEl = panel.querySelector('[data-lr="note"]');

  if (!rev?.available) {
    sourceEl.textContent = 'НЕТ ДАННЫХ';
    sourceEl.className = 'lr-badge';
    cardsEl.innerHTML = '<div class="lr-card"><div class="lr-kicker">ОЖИДАНИЕ</div><div class="lr-sub">Нет активной сделки или терминальной RND.</div></div>';
    flowEl.innerHTML = '';
    return;
  }

  const source = rev.source_quality || rev.current?.source || {};
  const score = rev.score || {};
  sourceEl.textContent = `${source.label || source.mode || 'SOURCE'} · ×${Number(source.weight || 0).toFixed(2)}`;
  sourceEl.className = `lr-badge ${score.direction === 'improving' ? 'good' : score.direction === 'deteriorating' ? 'bad' : 'warn'}`;
  sourceEl.title = source.reason || '';

  cardsEl.innerHTML = [
    card('НА ВХОДЕ', rev.entry),
    card(`СРЕДНЕЕ · ${rev.sample_count || 0} СНИМКОВ`, rev.average),
    card('СЕЙЧАС', rev.current, 'current'),
    scoreCard(rev),
  ].join('');
  flowEl.innerHTML = renderFlow(rev);

  const momentum = rev.momentum || {};
  noteEl.textContent = `Переток массы в скобках — изменение к входу. Темп P тейка ${signed(momentum.p_take_pp_per_min, ' пп/мин', 2)} · шум ${signed(momentum.p_take_noise_pp, ' пп', 2)} · согласованность ${pct(momentum.direction_consistency)}. Производные метрики входят в ИИ одной семьёй option_distribution и не дают несколько независимых голосов.`;
}

async function initialState() {
  try {
    const response = await fetch('/api/state', { cache: 'no-store' });
    if (!response.ok) return;
    const body = await response.json();
    render(body.tick);
  } catch (_) { /* existing terminal offline banner remains authoritative */ }
}

function connect() {
  clearTimeout(reconnectTimer);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${protocol}//${location.host}/ws`);
  ws.onmessage = (event) => {
    try { render(JSON.parse(event.data)); } catch (_) { /* ignore malformed tick */ }
  };
  ws.onclose = () => { reconnectTimer = setTimeout(connect, 2200); };
  ws.onerror = () => ws.close();
}

ensurePanel();
initialState();
connect();
