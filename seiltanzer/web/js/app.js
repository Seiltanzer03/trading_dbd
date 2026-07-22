// SEILTANZER TERMINAL — оркестратор фронта.
// Поток данных: GET /api/state (первый рендер) -> WS /ws (тики).
// Каждое число получает data-tip «как посчитано» с живыми значениями.

import { $, fmtPct, fmtNum, fmtPrice, fmtR, fmtTs, STATUS_ICON, statusLabel, initTooltips } from './util.js';
import { initLattice } from './lattice.js';
import { initRidge } from './ridge.js';
import { initLevels } from './levels.js';

initTooltips();

const lattice = initLattice($('#lattice-canvas'));
const ridge = initRidge($('#ridge-canvas'));
const levels = initLevels($('#levels-canvas'));

const S = {
  tick: null,
  ridge: null,
  setups: [],
  journal: [],
  chainTs: null,
  wsOk: false,
};

// ------------------------------------------------------------------ clock

setInterval(() => {
  const d = new Date();
  $('#utc-clock').textContent = d.toISOString().slice(11, 19) + ' UTC';
}, 250);

// ------------------------------------------------------------------- boot

async function boot() {
  try {
    const st = await (await fetch('/api/state')).json();
    S.tick = st.tick;
    S.ridge = st.ridge;
    S.setups = st.setups;
    S.journal = st.journal;
    renderAll();
  } catch (e) {
    console.error('state fetch failed', e);
  }
  connectWS();
}

function connectWS() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => { S.wsOk = true; $('#offline-banner').hidden = true; };
  ws.onmessage = (ev) => {
    S.tick = JSON.parse(ev.data);
    onTick();
  };
  ws.onclose = () => {
    S.wsOk = false;
    $('#offline-banner').hidden = false;
    setTimeout(connectWS, 2000);
  };
  ws.onerror = () => ws.close();
}

function onTick() {
  renderHeader();
  renderLattice();
  renderFilters();
  renderLadder();
  renderLevels();
  renderRidgeStats();
  maybeRefreshRidge();
}

function renderAll() {
  onTick();
  renderJournal();
  renderSetupGrid();
  ridge.setData(S.ridge, S.tick?.prob?.p);
}

async function refreshJournalAndSetups() {
  const st = await (await fetch('/api/state')).json();
  S.journal = st.journal;
  S.setups = st.setups;
  S.ridge = st.ridge;
  S.tick = st.tick;
  renderAll();
}

async function maybeRefreshRidge() {
  const ts = S.tick?.feeds?.chain?.ts;
  if (ts && ts !== S.chainTs) {
    S.chainTs = ts;
    try {
      S.ridge = await (await fetch('/api/chain')).json();
      ridge.setData(S.ridge, S.tick?.prob?.p);
    } catch { /* оставляем прежнюю гряду */ }
  }
}

// ----------------------------------------------------------------- header

function feedBadge(el, feed, extraTip) {
  const st = feed?.status || 'no_data';
  el.className = 'feed ' + st;
  const name = el.id.replace('feed-', '').toUpperCase();
  el.textContent = `${STATUS_ICON[st] || '○'} ${name === 'PRICE' ? 'ЦЕНА' : name === 'CHAIN' ? 'ЦЕПОЧКА' : name}`;
  const base = extraTip || '';
  const err = feed?.error ? `\nошибка: ${feed.error}` : '';
  const src = feed?.source ? `\nисточник: ${feed.source}` : '';
  const ts = feed?.ts ? `\nобновлено: ${fmtTs(feed.ts)} UTC` : '';
  el.dataset.tip = `${base}статус: ${statusLabel(st)}${src}${ts}${err}`;
}

function renderHeader() {
  const t = S.tick;
  if (!t) return;
  $('#demo-badge').hidden = !t.demo;
  const acc = t.account;
  $('#acc-name').textContent = acc.name || 'SEILTANZER';
  $('#hdr-balance').textContent =
    `${fmtNum(acc.balance, 0)} / ${fmtNum(acc.acc_size, 0)} = ${fmtNum(acc.balance_pct, 1)}%`;
  $('#hdr-phase').textContent = acc.phase.toUpperCase();
  $('#hdr-risk').textContent = fmtNum(acc.risk.risk_pct, 2) + '%';
  $('#hdr-risk').dataset.tip =
    `Риск на сделку (глава 2.1 / Excel G):\nбаза по Balance% ${fmtNum(acc.balance_pct, 1)}% -> ${fmtNum(acc.risk.base_risk_pct, 2)}%\n+ фаза ${acc.phase} (${{ '1ph': '+2', '2ph': '+1', funded: '+0' }[acc.phase]}%)\n= ${fmtNum(acc.risk.risk_pct, 2)}%`;
  const rrTip = t.atr?.rr_mult != null
    ? `Целевой RR (Excel J): база ${fmtNum(acc.risk.target_rr, 2)} × ATR-множитель ${fmtNum(t.atr.rr_mult, 1)} (фаза ${t.atr.phase || '—'}) = ${fmtNum(acc.risk.target_rr_adjusted, 2)}`
    : `Целевой RR (Excel J): база ${fmtNum(acc.risk.target_rr, 2)}; ATR-фаза недоступна — множитель не применён`;
  $('#hdr-rr').textContent = t.atr?.rr_mult != null
    ? `${fmtNum(acc.risk.target_rr, 2)}×${fmtNum(t.atr.rr_mult, 1)}=${fmtNum(acc.risk.target_rr_adjusted, 2)}`
    : fmtNum(acc.risk.target_rr, 2);
  $('#hdr-rr').dataset.tip = rrTip;
  $('#hdr-mode').textContent = acc.risk.mode;

  const trade = t.trade;
  if (trade) {
    const su = S.setups.find((s) => s.num === trade.setup);
    $('#hdr-setup').textContent =
      `СЕТАП №${trade.setup} · ${su ? su.name : ''} · ${trade.instrument} · ${trade.direction === 'long' ? 'ЛОНГ' : 'ШОРТ'}`;
    $('#btn-close-trade').hidden = false;
    $('#btn-new-trade').disabled = true;
  } else {
    $('#hdr-setup').textContent = `НЕТ ОТКРЫТОЙ СДЕЛКИ · ИНСТРУМЕНТ ${t.instrument}`;
    $('#btn-close-trade').hidden = true;
    $('#btn-new-trade').disabled = false;
  }

  feedBadge($('#feed-price'), t.feeds.price, 'Фид цены (опрос 3–5 c).\n');
  feedBadge($('#feed-chain'), t.feeds.chain, 'Фид опционной цепочки (опрос 5–10 мин).\n');
  const vols = t.feeds.vols;
  const worst = ['vix', 'gvz', 'dv1x'].map((k) => vols[k]?.status || 'no_data');
  const vixState = vols.vix?.status || 'no_data';
  feedBadge($('#feed-vix'), {
    status: vixState,
    ts: vols.vix?.ts,
    source: `VIX=${fmtNum(vols.vix?.value, 2)} GVZ=${fmtNum(vols.gvz?.value, 2)} DV1X=${vols.dv1x?.value == null ? 'нет' : fmtNum(vols.dv1x?.value, 2)}`,
    error: worst.includes('no_data') ? 'часть индексов недоступна' : null,
  }, 'Индексы волатильности (дневки Yahoo).\n');
}

// ---------------------------------------------------------------- lattice

function renderLattice() {
  const t = S.tick;
  if (!t) return;
  const p = t.prob;
  const active = !!(p && t.mc);
  $('#lattice-empty').style.display = active ? 'none' : 'flex';
  $('#lattice-status').className = 'badge ' + (active ? (t.demo ? 'demo' : 'live') : 'no_data');
  $('#lattice-status').textContent = active
    ? (t.demo ? '◆ DEMO' : '● LIVE') : '○ НЕТ СДЕЛКИ';

  lattice.setData({
    active,
    p: p?.p,
    T: p?.T ?? 2.5,
    hist: t.mc?.hist,
    tradeId: t.trade?.id ?? null,
  });

  if (!active) {
    ['lat-p', 'lat-r', 'lat-ev-hold', 'lat-ev-ladder', 'lat-green', 'lat-conv', 'lat-calib']
      .forEach((id) => { $('#' + id).textContent = '—'; });
    $('#lat-balls').textContent = '0';
    $('#lat-band-fill').style.left = '0%';
    $('#lat-band-fill').style.width = '0%';
    $('#lat-band-tick').style.left = '0%';
    return;
  }

  $('#lat-p').textContent = (p.p * 100).toFixed(1) + '%';
  $('#lat-p').dataset.tip =
    `P(тейк раньше стопа) — модель первого достижения:\n` +
    `dX = μdt + σdW, стоп −1R, тейк +${p.T.toFixed(2)}R\n` +
    `P = (s(x)−s(−1)) / (s(T)−s(−1)), s(x)=exp(−2μx/σ²)\n` +
    `x = r = ${p.r.toFixed(3)} (из фида цены)\n` +
    `μ = ${p.mu.toFixed(4)} — бисекция под винрейт ${(p.winrate * 100).toFixed(1)}% ` +
    `(${p.wins}/${p.n}, источник: ${p.calibration === 'journal' ? 'ваш журнал' : 'встроенная таблица'})\n` +
    `σ = ${p.sigma_ratio.toFixed(3)} — поправка опционной волы (σ_impl/σ_baseline${t.sigma.applied ? '' : ' НЕ применена: ' + (t.sigma.reason || '')})` +
    (p.small_sample ? `\nВЫБОРКА < 30 — смотрите интервал [${(p.p_lo * 100).toFixed(1)}–${(p.p_hi * 100).toFixed(1)}%], не точечное число` : '');

  const lo = p.p_lo * 100, hi = p.p_hi * 100;
  $('#lat-band-fill').style.left = lo + '%';
  $('#lat-band-fill').style.width = Math.max(hi - lo, 0.5) + '%';
  $('#lat-band-tick').style.left = `calc(${p.p * 100}% - 1px)`;
  $('#lat-band-lbl').textContent =
    `[${lo.toFixed(1)}% – ${hi.toFixed(1)}%] интервал 90% (Уилсон, n=${p.n})`;
  $('#lat-band').dataset.tip =
    `Интервал неопределённости: Уилсон 90% по винрейту ${p.wins}/${p.n}\n` +
    `-> винрейт ∈ [${(p.wr_lo * 100).toFixed(1)}%, ${(p.wr_hi * 100).toFixed(1)}%]\n` +
    `-> μ ∈ [lo, hi] -> P ∈ [${lo.toFixed(1)}%, ${hi.toFixed(1)}%].\nПоказывается всегда; при n<30 — единственно честное представление.`;

  $('#lat-r').textContent = fmtR(p.r);
  $('#lat-ev-hold').textContent = fmtR(t.mc.ev_hold);
  $('#lat-ev-hold').dataset.tip =
    `EV удержания до стопа/тейка: среднее терминального R по ${t.mc.n_paths} путям МК\n` +
    `с теми же μ, σ, что и P; ≈ p·T − (1−p) = ${(p.p * p.T - (1 - p.p)).toFixed(3)}`;
  $('#lat-ev-ladder').textContent = fmtR(t.mc.ev_ladder);
  $('#lat-ev-ladder').dataset.tip =
    `EV лестницы фиксации (глава 2.2): 10% позиции на 1.0/1.25/1.5/1.75/2.0/2.2R,\n` +
    `стоп в БУ после 1.5R. По тем же ${t.mc.n_paths} путям МК.\n` +
    `Допущения: рубеж исполняется точно по уровню, БУ — по 0R без проскальзывания.`;

  const st = lattice.stats;
  $('#lat-balls').textContent = String(st.dropped);
  $('#lat-green').textContent = st.greenShare == null ? '—' : fmtPct(st.greenShare);
  $('#lat-conv').textContent = st.convergence == null ? '—'
    : (st.convergence * 100).toFixed(1) + ' пп';
  $('#lat-conv').dataset.tip =
    `|доля зелёных − P(R>0 по МК)| = |${st.greenShare == null ? '—' : (st.greenShare * 100).toFixed(1)}% − ${st.pGreenModel == null ? '—' : (st.pGreenModel * 100).toFixed(1)}%|\n` +
    `Метрика честности доски: корзины сэмплируются из МК-распределения,\nпоэтому расхождение должно убывать с числом шариков (закон больших чисел).`;
  $('#lat-calib').textContent = p.calibration === 'journal'
    ? `ЖУРНАЛ (${p.journal_n})` : `ТАБЛИЦА (${p.n})`;
  $('#lat-calib').dataset.tip =
    `Источник статистики сетапа для калибровки μ.\n` +
    `Встроенная таблица: ${p.calibration === 'builtin' ? `${p.wins}/${p.n}` : '—'}\n` +
    `Журнал по сетапу: ${p.journal_wins}/${p.journal_n} закрытых\n` +
    `Переключение на журнал при ≥20 закрытых сделок по сетапу (приоритет журнала).`;
}

// ---------------------------------------------------------------- filters

function renderFilters() {
  const t = S.tick;
  if (!t) return;
  const box = $('#filter-chips');
  box.innerHTML = '';
  for (const c of t.filters) {
    const div = document.createElement('div');
    div.className = 'chip ' + c.state;
    const icon = { pass: '●', block: '✕', manual: '◑', na: '·', no_data: '○' }[c.state] || '·';
    const txt = { pass: 'PASS', block: 'BLOCK', manual: 'MANUAL', na: '—', no_data: 'NO DATA' }[c.state];
    div.innerHTML = `<span>${icon} ${c.label}</span>` +
      `<span class="chip-val">${c.value == null ? '' : fmtNum(c.value, 2)}</span>` +
      `<span>${txt}</span>`;
    const feedNote = c.status_feed ? `\nфид: ${statusLabel(c.status_feed)}` : '';
    div.dataset.tip = ({
      vix: `Фильтр VIX>20 — сетапы 5, 6, 11 (режим страха).\nтекущее значение: ${c.value == null ? 'нет данных' : fmtNum(c.value, 2)}${feedNote}`,
      gvz: `Фильтр GVZ<18 — сетап 11 (вола золота).\nтекущее значение: ${c.value == null ? 'нет данных' : fmtNum(c.value, 2)}${feedNote}`,
      dv1x: `Фильтр DV1X<19 — сетап 7 (GER40). Тикер ^V1X в Yahoo обычно недоступен —\nтогда статус MANUAL: проверь значение вручную, не пропускай молча.${feedNote}`,
      atr: `ATR-фаза (глава 2.9): ratio = ATR(5)/ATR(20) на дневках = ${c.value == null ? 'нет данных' : fmtNum(c.value, 3)}\n${c.detail || ''}\nШок (>1.5) — лучше не входить; фильтр корректирует целевой RR, не отменяет сетап.`,
      tech: `Индикатор «Теханализ» TradingView (1D NAS100, All/60m/240m/1D/1W/1M) должен быть > −30\nдля индексных СВИНГ-сетапов (глава 2.7). Проверяется только вручную.`,
    })[c.key] || c.detail || '';
    if (c.required && c.state !== 'na') div.style.fontWeight = '600';
    box.appendChild(div);
  }
}

function renderLadder() {
  const t = S.tick;
  const box = $('#ladder-row');
  box.innerHTML = '';
  const lad = t?.ladder;
  const rungs = lad?.rungs || [1.0, 1.25, 1.5, 1.75, 2.0, 2.2];
  rungs.forEach((r, i) => {
    const div = document.createElement('div');
    const crossed = lad?.crossed?.[i];
    div.className = 'rung' + (crossed ? ' crossed' : '') + (r === (lad?.be_after ?? 1.5) ? ' be' : '');
    div.innerHTML = `<div class="r-mark">${crossed ? '✓' : '·'}</div>` +
      `<div class="r-lbl">${r.toFixed(2)}R</div>`;
    div.dataset.tip = `Рубеж ${r.toFixed(2)}R: закрыть 10% позиции (глава 2.2).\n` +
      (lad ? `Пройден, если максимум r за сделку (${fmtR(lad.max_r)}) ≥ ${r.toFixed(2)}.\n` : '') +
      (r === (lad?.be_after ?? 1.5) ? 'После этого рубежа стоп переносится в безубыток.' : '');
    box.appendChild(div);
  });
  let note = $('.ladder-note');
  if (!note) {
    note = document.createElement('div');
    note.className = 'ladder-note';
    $('#ladder-row').after(note);
  }
  if (lad) {
    note.textContent = `max r = ${fmtR(lad.max_r)} · БУ ${lad.be_armed ? 'АКТИВЕН' : 'после 1.5R'} · EV лестницы ${fmtR(t.mc?.ev_ladder)} vs холд ${fmtR(t.mc?.ev_hold)}`;
  } else {
    note.textContent = 'нет открытой сделки';
  }
}

// ----------------------------------------------------------------- levels

function renderLevels() {
  const t = S.tick;
  const has = !!t?.levels;
  $('#levels-empty').style.display = has ? 'none' : 'flex';
  $('#levels-status').className = 'badge ' + (has ? (t.demo ? 'demo' : 'live') : 'no_data');
  $('#levels-status').textContent = has ? (t.demo ? '◆ DEMO' : '● LIVE') : '○ НЕТ СДЕЛКИ';
  $('#btn-zones').disabled = !has;
  if (has) levels.setData(t.levels);
}

// ------------------------------------------------------------ ridge stats

function renderRidgeStats() {
  const t = S.tick;
  if (!t) return;
  const os = t.options_summary;
  const chainSt = t.feeds.chain?.status || 'no_data';
  const rAvail = S.ridge?.available && os;
  $('#ridge-empty').style.display = rAvail ? 'none' : 'flex';
  $('#ridge-empty').textContent = '○ ' + (S.ridge?.reason || `ОПЦИОННЫЕ ДАННЫЕ НЕДОСТУПНЫ ДЛЯ ${t.instrument}`).toUpperCase();
  $('#ridge-status').className = 'badge ' + (rAvail ? chainSt : 'no_data');
  $('#ridge-status').textContent = rAvail
    ? `${STATUS_ICON[chainSt]} ${statusLabel(chainSt)}` : '○ НЕТ ДАННЫХ';

  if (!os) {
    ['rg-proxy', 'rg-expiry', 'rg-move', 'rg-sigma', 'rg-base', 'rg-ratio',
     'rg-p-take', 'rg-p-stop', 'rg-p-model'].forEach((id) => { $('#' + id).textContent = '—'; });
    return;
  }
  $('#rg-proxy').textContent = os.proxy + (os.demo ? ' ◆' : '');
  $('#rg-expiry').textContent = os.expiry;
  $('#rg-move').textContent = `${fmtPct(os.implied_move_frac)} / ${fmtPrice(os.implied_move_abs_instr)}`;
  $('#rg-move').dataset.tip =
    `Implied move до экспирации ${os.expiry}:\nATM straddle ${os.proxy} / спот = ${fmtPct(os.implied_move_frac)}\n` +
    `в пунктах инструмента: × scale ${fmtNum(os.scale, 4)} = ${fmtPrice(os.implied_move_abs_instr)}\n` +
    `(ожидаемое |движение|, E|ΔS/S|)`;
  $('#rg-sigma').textContent = fmtPct(t.sigma.sigma_implied, 1);
  $('#rg-sigma').dataset.tip =
    `σ_implied годовая = implied_move × √(π/2t) = ${fmtPct(t.sigma.sigma_implied, 2)}\n(из E|Z| = σ√(2/π) для нормального Z)`;
  $('#rg-base').textContent = fmtPct(t.sigma.sigma_baseline, 1);
  $('#rg-ratio').textContent = t.sigma.applied ? '×' + fmtNum(t.sigma.ratio, 3) : 'НЕ ПРИМЕНЕНА';
  $('#rg-ratio').dataset.tip = t.sigma.applied
    ? `σ процесса умножена на σ_impl/σ_baseline = ${fmtPct(t.sigma.sigma_implied, 1)}/${fmtPct(t.sigma.sigma_baseline, 1)} = ${fmtNum(t.sigma.ratio, 3)}\n(п.4 ТЗ: сжатый рынок «остужает» далёкий тейк на горизонте, разогнанный — наоборот)`
    : `Поправка не применена: ${t.sigma.reason || 'нет данных'} — модель работает с σ=1 (без опционной поправки), это явно честнее, чем выдумывать.`;

  const rn = S.ridge?.rn_probs;
  $('#rg-p-take').textContent = rn ? fmtPct(rn.p_beyond_take) : '—';
  $('#rg-p-take').dataset.tip = rn
    ? `P(цена за тейком на экспирации ${rn.expiry}) по risk-neutral плотности:\n∫ q(K)dK за уровнем тейка; q ≈ e^{rT}·d²C/dK² (Бриден–Литценбергер),\nсглаживание локальной квадратичной регрессией, отрицательные значения обрезаны.\nСтрайки прокси → шкала инструмента пропорцией (приближение).${rn.demo ? '\n◆ DEMO-цепочка' : ''}`
    : 'нужны открытая сделка и цепочка';
  $('#rg-p-stop').textContent = rn ? fmtPct(rn.p_beyond_stop) : '—';
  $('#rg-p-stop').dataset.tip = rn
    ? `P(цена за стопом на экспирации) — аналогично P(за тейк), хвост с другой стороны.${rn.demo ? '\n◆ DEMO-цепочка' : ''}`
    : 'нужны открытая сделка и цепочка';
  $('#rg-p-model').textContent = S.tick?.prob ? fmtPct(S.tick.prob.p) : '—';
}

// ---------------------------------------------------------------- journal

function renderJournal() {
  const tbody = $('#journal-table tbody');
  tbody.innerHTML = '';
  $('#journal-count').textContent = `(${S.journal.length})`;
  for (const t of S.journal) {
    const tr = document.createElement('tr');
    const res = t.result_r;
    tr.innerHTML =
      `<td>${t.id}</td><td>${fmtTs(t.opened_at)}</td><td>№${t.setup}</td>` +
      `<td>${t.instrument}</td><td>${t.direction === 'long' ? 'ЛОНГ' : 'ШОРТ'}</td>` +
      `<td>${fmtPrice(t.entry)}</td><td>${fmtPrice(t.stop)}</td><td>${fmtPrice(t.take)}</td>` +
      `<td class="${res > 0 ? 'green' : res < 0 ? 'red' : ''}">${res == null ? '—' : fmtR(res)}</td>` +
      `<td>${t.status === 'open' ? '● ОТКРЫТА' : 'закрыта'}</td>` +
      `<td class="notes">${(t.notes || '').slice(0, 120)}</td>`;
    tbody.appendChild(tr);
  }
}

function renderSetupGrid() {
  const grid = $('#setup-grid');
  grid.innerHTML = '';
  for (const s of S.setups) {
    const div = document.createElement('div');
    div.className = 'setup-cell' + (s.calibration === 'journal' ? ' journal-cal' : '');
    const eff = s.efficiency == null ? '—' : s.efficiency.toFixed(2);
    div.innerHTML =
      `<span class="name">№${s.num} ${s.name}</span>` +
      `<span class="nums">${(s.winrate * 100).toFixed(0)}% · ${s.wins}/${s.n} · 2α/(α+β)=${eff}</span>`;
    div.dataset.tip =
      `Сетап №${s.num} — ${s.name} (${s.instrument}, целевой RR ${s.rr})\n` +
      `Калибровка: ${s.calibration === 'journal' ? 'ЖУРНАЛ' : 'встроенная таблица'}\n` +
      `встроенная статистика: ${s.builtin_wins}/${s.builtin_n}\n` +
      `журнал: ${s.journal_wins}/${s.journal_n} закрытых (переключение при ≥20)\n` +
      `2α/(α+β) по журналу: ${eff} (${s.efficiency == null ? 'нет закрытых сделок' : s.efficiency > 1 ? 'прибыльный' : s.efficiency > 0.4 ? 'мониторить' : 'пересмотреть'})`;
    grid.appendChild(div);
  }
}

$('#journal-toggle').addEventListener('click', (e) => {
  if (e.target.closest('a, button')) return;
  const body = $('#journal-body');
  body.hidden = !body.hidden;
  $('#journal-arrow').textContent = body.hidden ? '▸' : '▾';
});

// ------------------------------------------------------------------ modal

function openModal(html) {
  $('#modal').innerHTML = html;
  $('#modal-back').hidden = false;
}
function closeModal() { $('#modal-back').hidden = true; }
$('#modal-back').addEventListener('click', (e) => {
  if (e.target === $('#modal-back')) closeModal();
});

async function apiPost(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || resp.statusText);
  }
  return resp.json();
}

// --------------------------------------------------------------- new trade

$('#btn-new-trade').addEventListener('click', () => {
  const opts = S.setups.map((s) =>
    `<option value="${s.num}">№${s.num} · ${s.name} · ${s.instrument} · WR ${(s.winrate * 100).toFixed(0)}% · RR ${s.rr}</option>`).join('');
  openModal(`
    <h3>НОВАЯ СДЕЛКА</h3>
    <div class="form-grid">
      <label>Сетап</label><select id="f-setup">${opts}</select>
      <label>Направление</label>
      <select id="f-dir"><option value="long">ЛОНГ</option><option value="short">ШОРТ</option></select>
      <label>Вход</label><input id="f-entry" type="number" step="any">
      <label>Стоп</label><input id="f-stop" type="number" step="any">
      <label>Тейк</label><input id="f-take" type="number" step="any">
      <span class="form-hint" id="f-rr-hint">тейк можно оставить пустым — рассчитаю из целевого RR сетапа (правило 2.8)</span>
      <label>Заметки</label><textarea id="f-notes"></textarea>
    </div>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-open">ОТКРЫТЬ</button>
    </div>`);
  $('#f-cancel').onclick = closeModal;
  // Автоподстановка входа честна только если инструмент выбранного сетапа
  // совпадает с активным (для которого сейчас идёт фид цены). Иначе — пусто.
  const prefill = () => {
    const su = S.setups.find((s) => s.num === Number($('#f-setup').value));
    const price = S.tick?.feeds?.price?.value;
    const sameInstr = su && su.instrument === S.tick?.instrument;
    if (sameInstr && price) {
      $('#f-entry').value = price.toPrecision(8);
      $('#f-rr-hint').textContent =
        `вход подставлен из фида ${su.instrument} (${price.toPrecision(8)}); тейк можно оставить пустым — рассчитаю из RR (правило 2.8)`;
    } else {
      $('#f-entry').value = '';
      $('#f-rr-hint').textContent = su
        ? `инструмент сетапа — ${su.instrument}; нет живого фида для него, введите вход вручную. Тейк можно оставить пустым (рассчитаю из RR).`
        : 'тейк можно оставить пустым — рассчитаю из целевого RR сетапа (правило 2.8)';
    }
  };
  $('#f-setup').onchange = prefill;
  prefill();
  $('#f-open').onclick = async () => {
    try {
      const setup = Number($('#f-setup').value);
      const su = S.setups.find((s) => s.num === setup);
      const dir = $('#f-dir').value;
      const entry = Number($('#f-entry').value);
      const stop = Number($('#f-stop').value);
      let take = $('#f-take').value ? Number($('#f-take').value) : null;
      if (take == null && su && entry && stop) {
        const rr = S.tick?.account?.risk?.target_rr_adjusted || su.rr;
        take = dir === 'long' ? entry + rr * (entry - stop) : entry - rr * (stop - entry);
      }
      await apiPost('/api/trade', {
        setup, direction: dir, entry, stop, take,
        notes: $('#f-notes').value, zones: [],
      });
      closeModal();
      lattice.reset();
      await refreshJournalAndSetups();
    } catch (e) {
      $('#f-err').textContent = e.message;
    }
  };
});

// -------------------------------------------------------------- close trade

$('#btn-close-trade').addEventListener('click', () => {
  const t = S.tick?.trade;
  if (!t) return;
  const rNow = S.tick?.prob?.r;
  openModal(`
    <h3>ЗАКРЫТЬ СДЕЛКУ №${t.id} (СЕТАП №${t.setup})</h3>
    <div class="form-grid">
      <label>Результат, R</label>
      <input id="f-result" type="number" step="any" value="${rNow != null ? rNow.toFixed(2) : ''}">
      <span class="form-hint">текущий r = ${rNow != null ? rNow.toFixed(2) : '—'}; впишите фактический результат (с учётом частичных фиксаций)</span>
      <label>Заметки</label><textarea id="f-notes">${t.notes || ''}</textarea>
    </div>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-close">ЗАКРЫТЬ</button>
    </div>`);
  $('#f-cancel').onclick = closeModal;
  $('#f-close').onclick = async () => {
    try {
      await apiPost('/api/trade/close', {
        trade_id: t.id,
        result_r: Number($('#f-result').value),
        notes: $('#f-notes').value,
      });
      closeModal();
      await refreshJournalAndSetups();
    } catch (e) {
      $('#f-err').textContent = e.message;
    }
  };
});

// -------------------------------------------------------------- zones edit

$('#btn-zones').addEventListener('click', () => {
  const t = S.tick?.trade;
  if (!t) return;
  const zones = t.zones || [];
  const zoneRow = (z = {}) => `
    <div class="zone-row">
      <input type="number" step="any" placeholder="низ" class="z-low" value="${z.low ?? ''}">
      <input type="number" step="any" placeholder="верх" class="z-high" value="${z.high ?? ''}">
      <select class="z-tf">${['15m', '1H', '2H', '4H', '8H', '12H', '1D', '1W']
        .map((tf) => `<option ${z.tf === tf ? 'selected' : ''}>${tf}</option>`).join('')}</select>
    </div>`;
  openModal(`
    <h3>FVG-ЗОНЫ СДЕЛКИ №${t.id}</h3>
    <div id="zones-box">${zones.map(zoneRow).join('') || zoneRow()}</div>
    <button class="btn btn-small" id="f-add-zone">+ ЗОНА</button>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-save">СОХРАНИТЬ</button>
    </div>`);
  $('#f-add-zone').onclick = () => {
    $('#zones-box').insertAdjacentHTML('beforeend', zoneRow());
  };
  $('#f-cancel').onclick = closeModal;
  $('#f-save').onclick = async () => {
    try {
      const rows = [...document.querySelectorAll('.zone-row')];
      const zs = rows.map((r) => ({
        low: Number(r.querySelector('.z-low').value),
        high: Number(r.querySelector('.z-high').value),
        tf: r.querySelector('.z-tf').value,
      })).filter((z) => isFinite(z.low) && isFinite(z.high) && z.low && z.high);
      await apiPost('/api/trade/zones', { trade_id: t.id, zones: zs });
      closeModal();
      await refreshJournalAndSetups();
    } catch (e) {
      $('#f-err').textContent = e.message;
    }
  };
});

// ----------------------------------------------------------- account modal

$('#hdr-balance').addEventListener('click', () => {
  const acc = S.tick?.account;
  if (!acc) return;
  openModal(`
    <h3>АККАУНТ</h3>
    <div class="form-grid">
      <label>Название</label><input id="f-name" value="${acc.name || ''}">
      <label>Фаза</label>
      <select id="f-phase">
        ${['1ph', '2ph', 'funded'].map((p) => `<option ${acc.phase === p ? 'selected' : ''}>${p}</option>`).join('')}
      </select>
      <label>Начальный капитал</label><input id="f-size" type="number" step="any" value="${acc.acc_size}">
      <label>Текущий баланс</label><input id="f-bal" type="number" step="any" value="${acc.balance}">
    </div>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-save">СОХРАНИТЬ</button>
    </div>`);
  $('#f-cancel').onclick = closeModal;
  $('#f-save').onclick = async () => {
    try {
      await apiPost('/api/account', {
        name: $('#f-name').value,
        phase: $('#f-phase').value,
        acc_size: Number($('#f-size').value),
        balance: Number($('#f-bal').value),
      });
      closeModal();
    } catch (e) {
      $('#f-err').textContent = e.message;
    }
  };
});

// -------------------------------------------------------------- backfill

$('#btn-add-hist').addEventListener('click', () => {
  const opts = S.setups.map((s) =>
    `<option value="${s.num}">№${s.num} · ${s.name}</option>`).join('');
  openModal(`
    <h3>ДОБАВИТЬ ЗАКРЫТУЮ СДЕЛКУ (ИСТОРИЯ)</h3>
    <div class="form-grid">
      <label>Сетап</label><select id="f-setup">${opts}</select>
      <label>Направление</label>
      <select id="f-dir"><option value="long">ЛОНГ</option><option value="short">ШОРТ</option></select>
      <label>Вход</label><input id="f-entry" type="number" step="any" value="100">
      <label>Стоп</label><input id="f-stop" type="number" step="any" value="99">
      <label>Тейк</label><input id="f-take" type="number" step="any" value="102.5">
      <label>Результат, R</label><input id="f-result" type="number" step="any">
      <label>Заметки</label><textarea id="f-notes"></textarea>
    </div>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-save">ДОБАВИТЬ</button>
    </div>`);
  $('#f-cancel').onclick = closeModal;
  $('#f-save').onclick = async () => {
    try {
      await apiPost('/api/journal', {
        setup: Number($('#f-setup').value),
        direction: $('#f-dir').value,
        entry: Number($('#f-entry').value),
        stop: Number($('#f-stop').value),
        take: Number($('#f-take').value),
        result_r: Number($('#f-result').value),
        notes: $('#f-notes').value,
      });
      closeModal();
      await refreshJournalAndSetups();
    } catch (e) {
      $('#f-err').textContent = e.message;
    }
  };
});

$('#btn-lattice-reset').addEventListener('click', () => lattice.reset());

boot();
