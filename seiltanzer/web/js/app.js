// SEILTANZER TERMINAL — оркестратор фронта.
// Поток данных: GET /api/state (первый рендер) -> WS /ws (тики).
// Каждое число получает data-tip «как посчитано» с живыми значениями.

import { $, fmtPct, fmtNum, fmtPrice, fmtR, fmtTs, STATUS_ICON, statusLabel, initTooltips } from './util.js';
import { tweenNumber } from './anim.js';
import { initLattice } from './lattice.js';
import { initRidge } from './ridge.js';
import { initLevels } from './levels.js';
import { initCone } from './cone.js';
import { initFan } from './fan.js';

initTooltips();

const lattice = initLattice($('#lattice-canvas'));
const ridge = initRidge($('#ridge-canvas'));
const levels = initLevels($('#levels-canvas'));
const cone = initCone('#cone-plot');
const fan = initFan($('#cone-fan'));

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
    S.edge_track = st.edge_track;
    renderAll();
  } catch (e) {
    console.error('state fetch failed', e);
  }
  connectWS();
}

function setWsDot(ok) {
  const el = $('#ws-status');
  if (!el) return;
  el.className = 'feed ' + (ok ? 'live' : 'no_data');
  el.textContent = ok ? '● ONLINE' : '○ OFFLINE';
}

function connectWS() {
  // на https-странице (Codespaces и т.п.) браузер блокирует ws:// как mixed content
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws`;
  console.log('[seiltanzer] WS connecting →', url);
  const ws = new WebSocket(url);
  ws.onopen = () => {
    S.wsOk = true; $('#offline-banner').hidden = true; setWsDot(true);
    console.log('[seiltanzer] WS connected ✓ — живые тики пошли');
  };
  ws.onmessage = (ev) => {
    S.tick = JSON.parse(ev.data);
    onTick();
  };
  ws.onclose = (e) => {
    S.wsOk = false; $('#offline-banner').hidden = false; setWsDot(false);
    console.warn('[seiltanzer] WS closed', e.code, e.reason || '', '— переподключение через 2с');
    setTimeout(connectWS, 2000);
  };
  ws.onerror = (e) => { console.error('[seiltanzer] WS error', e); ws.close(); };
}

function onTick() {
  renderHeader();
  renderVerdict();
  renderState();
  renderLattice();
  renderFilters();
  renderLadder();
  renderLevels();
  renderRidgeStats();
  renderCone();
  maybeRefreshRidge();
  // живое обновление гряды каждый тик: луч цены двигается всегда (даже без сделки)
  ridge.updateLive({
    price: S.tick?.feeds?.price?.value,
    modelHist: S.tick?.mc?.hist,
    trade: S.tick?.trade || null,
    modelProb: S.tick?.prob?.p,
  });
  // живой луч цены в конусе + точка цены в веере (r) двигаются каждый тик
  cone.updateLive({ r: S.tick?.prob?.r });
  fan.updateLive({ r: S.tick?.prob?.r });
}

function renderAll() {
  onTick();
  renderJournal();
  renderSetupGrid();
  renderEdgeTrack();
  ridge.setData(S.ridge, S.tick?.prob?.p);
}

async function refreshJournalAndSetups() {
  const st = await (await fetch('/api/state')).json();
  S.journal = st.journal;
  S.setups = st.setups;
  S.ridge = st.ridge;
  S.tick = st.tick;
  S.edge_track = st.edge_track;
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

function fmtIdle(secs) {
  if (secs == null) return '—';
  if (secs < 90) return `${Math.round(secs)} с`;
  const m = secs / 60;
  if (m < 90) return `${Math.round(m)} мин`;
  return `${(m / 60).toFixed(1)} ч`;
}

function feedBadge(el, feed, extraTip) {
  const st = feed?.status || 'no_data';
  // «стоит» = live, но котировка не двигается дольше порога → нет тиков (рынок
  // закрыт/неторговое время). Показываем это отдельно, а не зелёным LIVE.
  const stale = feed?.fresh === false;
  el.className = 'feed ' + (stale ? 'delayed' : st);
  const name = el.id.replace('feed-', '').toUpperCase();
  const label = name === 'PRICE' ? 'ЦЕНА' : name === 'CHAIN' ? 'ЦЕПОЧКА' : name;
  el.textContent = `${stale ? '⏸' : (STATUS_ICON[st] || '○')} ${label}${stale ? ' СТОИТ' : ''}`;
  const base = extraTip || '';
  const err = feed?.error ? `\nошибка: ${feed.error}` : '';
  const src = feed?.source ? `\nисточник: ${feed.source}` : '';
  const ts = feed?.ts ? `\nобновлено: ${fmtTs(feed.ts)} UTC` : '';
  const idle = stale ? `\n⏸ нет тиков ${fmtIdle(feed.idle_secs)} — рынок закрыт/неторговое время` : '';
  el.dataset.tip = `${base}статус: ${statusLabel(st)}${src}${ts}${idle}${err}`;
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

// живая котировка на латтике: тик, цвет вверх/вниз, вспышка, % от входа
function handleLivePrice(t) {
  const price = t.feeds?.price?.value;
  const streaming = (t.feeds?.price?.source || '').startsWith('stream');
  const stale = t.feeds?.price?.fresh === false;
  const idle = t.feeds?.price?.idle_secs;
  $('#lat-price-instr').textContent = t.instrument
    + (streaming ? ' ⚡' : '') + (stale ? ' · ⏸ ЗАКРЫТ' : '');
  $('#lat-price-instr').title = stale
    ? `нет свежих тиков ${fmtIdle(idle)} — рынок закрыт или неторговое время; цена = последняя котировка`
    : (streaming ? 'живой WebSocket-стрим цены' : '');
  if (price == null) { $('#lat-price').textContent = '—'; $('#lat-price-chg').textContent = ''; return; }
  const el = $('#lat-price');
  tweenNumber(el, price, (v) => fmtPrice(v), 14);
  const prev = S._lastPrice;
  if (prev != null && price !== prev) {
    const up = price > prev;
    el.className = 'live-price ' + (up ? 'up' : 'down');
    const row = $('#lat-price-row');
    row.classList.remove('tickflash'); void row.offsetWidth; row.classList.add('tickflash');
  }
  // изменение от входа (если в сделке)
  const chg = $('#lat-price-chg');
  const tr = t.trade;
  if (tr) {
    const pct = tr.direction === 'long' ? (price - tr.entry) / tr.entry * 100
                                        : (tr.entry - price) / tr.entry * 100;
    chg.textContent = `${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct).toFixed(2)}% от входа`;
    chg.className = 'live-chg ' + (pct >= 0 ? 'up' : 'down');
  } else if (stale) {
    chg.textContent = `⏸ нет тиков ${fmtIdle(idle)}`;
    chg.className = 'live-chg';
  } else { chg.textContent = ''; }
  S._lastPrice = price;
}

// ---------------------------------------------------------------- verdict

function renderVerdict() {
  const v = S.tick?.verdict;
  const strip = $('#verdict-strip');
  if (!v) { strip.hidden = true; return; }
  strip.hidden = false;
  const lbl = $('#v-label');
  lbl.textContent = v.label;
  lbl.className = 'verdict-badge ' + v.tone;
  const eEl = $('#v-edge');
  if (v.edge == null) { eEl.textContent = '—'; eEl.className = 'verdict-edge'; }
  else {
    tweenNumber(eEl, v.edge * 100, (x) => (x >= 0 ? '+' : '') + x.toFixed(1) + '%');
    eEl.className = 'verdict-edge ' + (v.edge >= 0 ? 'good' : 'bad');
  }
  const mkt = S.tick.market;
  $('#v-pmm').textContent = mkt
    ? `P модели ${fmtPct(mkt.p_model)} · P рынка ${fmtPct(mkt.hit_ratio)}`
    : 'опционов для инструмента нет — край недоступен';
  $('#v-action').textContent = v.action;
  const fx = $('#v-factors');
  fx.innerHTML = '';
  for (const f of v.factors) {
    const d = document.createElement('div');
    d.className = 'vfactor ' + f.tone;
    d.innerHTML = `<span class="vk">${f.k}</span><span>${f.v}</span>`;
    fx.appendChild(d);
  }
}

// ------------------------------------------------------ state / prospects

// адаптивный формат длительности (годы -> минуты/часы/дни)
function fmtDur(years) {
  if (years == null || !isFinite(years)) return '—';
  const min = years * 365 * 24 * 60;
  if (min < 1) return '<1 мин';
  if (min < 90) return `${Math.round(min)} мин`;
  const h = min / 60;
  if (h < 48) return `${h.toFixed(1)} ч`;
  return `${(h / 24).toFixed(1)} дн`;
}

function renderState() {
  const s = S.tick?.state;
  const card = $('#panel-state');
  if (!s) { card.hidden = true; return; }
  card.hidden = false;

  // позиция r
  tweenNumber($('#st-r'), s.r, (v) => fmtR(v), 12);
  $('#st-r').className = 'state-val ' + (s.r >= 0 ? 'green' : 'red');
  $('#st-r-sub').textContent = s.be_armed ? 'стоп в БУ' : `цель ${s.T.toFixed(2)}R`;

  // до тейка / стопа (R + ATR)
  $('#st-take').textContent = fmtR(s.to_take_r);
  $('#st-take-atr').textContent = s.to_take_atr != null ? `${s.to_take_atr.toFixed(1)} ATR` : 'ATR н/д';
  $('#st-stop').textContent = fmtR(-s.to_stop_r);
  $('#st-stop-atr').textContent = s.to_stop_atr != null ? `${s.to_stop_atr.toFixed(1)} ATR` : 'ATR н/д';

  // P с полосой + примерное время до развязки (из волы, адаптивно)
  tweenNumber($('#st-p'), s.p * 100, (v) => v.toFixed(1) + '%', 10);
  $('#st-p-band').textContent = `[${(s.p_lo * 100).toFixed(0)}–${(s.p_hi * 100).toFixed(0)}%]`
    + (s.small_sample ? ' · n<30' : '')
    + (s.median_years != null ? ` · развязка ≈ ${fmtDur(s.median_years)}` : '');

  // край + сдвиг от входа
  if (s.edge == null) {
    $('#st-edge').textContent = '—'; $('#st-edge').className = 'state-val dim';
    $('#st-edge-shift').textContent = 'нет опционов';
  } else {
    $('#st-edge').textContent = (s.edge >= 0 ? '+' : '') + (s.edge * 100).toFixed(0) + '%';
    $('#st-edge').className = 'state-val ' + (s.edge >= 0 ? 'green' : 'red');
    if (s.edge_shift == null) {
      $('#st-edge-shift').textContent = 'вход: фиксируется';
    } else {
      const arrow = s.edge_shift > 0.005 ? '↑' : s.edge_shift < -0.005 ? '↓' : '→';
      $('#st-edge-shift').textContent =
        `вход ${(s.edge_at_open * 100).toFixed(0)}% ${arrow}`;
    }
  }

  // действие
  const h = $('#st-headline');
  h.textContent = s.headline || '';
  h.className = 'state-headline ' + (s.tone || '');
}

// ---------------------------------------------------------------- cone

function renderCone() {
  const t = S.tick;
  const c = t?.cone;
  const active = !!(c && c.available);
  $('#cone-empty').style.display = active ? 'none' : 'flex';
  $('#cone-status').className = 'badge ' + (active ? (t.demo ? 'demo' : 'live') : 'no_data');
  $('#cone-status').textContent = active ? (t.demo ? '◆ DEMO' : '● LIVE') : '○ НЕТ СДЕЛКИ';
  cone.setData(active ? c : null, {
    direction: t?.trade?.direction || 'long',
    headlineP: t?.prob?.p,
  });
  fan.setData(active ? c : null);
}

// ---------------------------------------------------------------- lattice

function renderLattice() {
  const t = S.tick;
  if (!t) return;
  handleLivePrice(t);          // котировка тикает всегда, даже без сделки
  const p = t.prob;
  const active = !!(p && t.mc);
  $('#lattice-empty').style.display = active ? 'none' : 'flex';
  $('#lattice-status').className = 'badge ' + (active ? (t.demo ? 'demo' : 'live') : 'no_data');
  $('#lattice-status').textContent = active
    ? (t.demo ? '◆ DEMO' : '● LIVE') : '○ НЕТ СДЕЛКИ';

  const mkt = t.market;
  lattice.setData({
    active,
    p: p?.p,
    T: p?.T ?? 2.5,
    r: p?.r ?? 0,
    marketProbs: mkt?.probs,
    modelProbs: t.mc?.hist?.probs,
    edges: mkt?.edges || t.mc?.hist?.edges,
    hit: mkt?.hit_ratio,
    edge: mkt?.edge,
    tradeId: t.trade?.id ?? null,
    regime: p?.vol_regime,
  });

  if (!active) {
    ['lat-p', 'lat-mhit', 'lat-edge', 'lat-r', 'lat-ev-hold', 'lat-ev-ladder',
     'lat-be', 'lat-green', 'lat-conv', 'lat-calib', 'lat-read']
      .forEach((id) => { $('#' + id).textContent = '—'; });
    $('#lat-balls').textContent = '0';
    $('#lat-band-fill').style.left = '0%';
    $('#lat-band-fill').style.width = '0%';
    $('#lat-band-tick').style.left = '0%';
    return;
  }

  // рынок vs модель
  if (mkt) {
    $('#lat-mhit').textContent = fmtPct(mkt.hit_ratio);
    $('#lat-mhit').dataset.tip =
      `Рыночный «hit» = P(тейк раньше стопа) по risk-neutral диффузии (вола опционов/реализ. + снос скью, БЕЗ винрейта)${mkt.median_years != null ? ', медиана развязки ≈ ' + fmtDur(mkt.median_years) : ''}.\n` +
      `P дойти к горизонту: тейк ${fmtPct(mkt.p_take)}, стоп ${fmtPct(mkt.p_stop)}.\nСравнивается с P модели (${fmtPct(mkt.p_model)}); расхождение = КРАЙ.`;
    const ed = mkt.edge;
    $('#lat-edge').textContent = ed == null ? '—' : (ed >= 0 ? '+' : '') + fmtPct(ed);
    $('#lat-edge').className = 'val ' + (ed == null ? '' : ed >= 0 ? 'green' : 'red');
    $('#lat-edge').dataset.tip =
      `Край = P модели − hit рынка = ${fmtPct(mkt.p_model)} − ${fmtPct(mkt.hit_ratio)} = ${ed == null ? '—' : (ed >= 0 ? '+' : '') + fmtPct(ed)}.\n` +
      `Положительный → ваша статистика даёт лучшие шансы, чем закладывает рынок опционов (потенциальный край). Отрицательный → рынок оценивает сетап выше вас — осторожно.`;
  } else {
    $('#lat-mhit').textContent = '—';
    $('#lat-mhit').dataset.tip = `Опционной цепочки для ${t.instrument} нет — рыночного распределения нет, доска показывает вашу модель честно.`;
    $('#lat-edge').textContent = '—';
    $('#lat-edge').className = 'val';
  }

  tweenNumber($('#lat-p'), p.p * 100, (v) => v.toFixed(1) + '%');
  $('#lat-p').dataset.tip =
    `P(тейк раньше стопа) — модель первого достижения:\n` +
    `dX = μdt + σdW, стоп −1R, тейк +${p.T.toFixed(2)}R\n` +
    `P = (s(x)−s(−1)) / (s(T)−s(−1)), s(x)=exp(−2μx/σ²)\n` +
    `x = r = ${p.r.toFixed(3)} (из фида цены)\n` +
    `μ = ${p.mu.toFixed(4)} — бисекция под винрейт ${(p.winrate * 100).toFixed(1)}% ` +
    `(${p.wins}/${p.n}, источник: ${p.calibration === 'journal' ? 'ваш журнал' : 'встроенная таблица'})\n` +
    `σ = ${p.sigma_ratio.toFixed(3)} — поправка опционной волы (σ_impl/σ_baseline${t.sigma.applied ? '' : ' НЕ применена: ' + (t.sigma.reason || '')})` +
    (p.small_sample ? `\nВЫБОРКА < 30 — смотрите интервал [${(p.p_lo * 100).toFixed(1)}–${(p.p_hi * 100).toFixed(1)}%], не точечное число` : '');

  // среднее P за сделку (визуальный ориентир — стабильно ли преимущество)
  if (t.trade?.id !== S._pTradeId) { S._pTradeId = t.trade?.id; S._pSum = 0; S._pN = 0; }
  S._pSum += p.p; S._pN += 1;
  $('#lat-p-avg').textContent = `· ср ${((S._pSum / S._pN) * 100).toFixed(1)}%`;

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

  // порог безубытка по винрейту + запас
  if (p.p_breakeven != null) {
    const marg = (p.p - p.p_breakeven) * 100;
    $('#lat-be').textContent = fmtPct(p.p_breakeven) +
      ` (${marg >= 0 ? '+' : ''}${marg.toFixed(0)}пп)`;
    $('#lat-be').className = 'val ' + (marg >= 0 ? 'green' : 'red');
    $('#lat-be').dataset.tip =
      `Порог EV=0 при RR 1:${p.T.toFixed(2)} = 1/(1+${p.T.toFixed(2)}) = ${fmtPct(p.p_breakeven)}.\n` +
      `Ваша P(тейк) ${fmtPct(p.p)} ${marg >= 0 ? 'ВЫШЕ' : 'НИЖЕ'} порога на ${Math.abs(marg).toFixed(0)}пп -> ` +
      `удержание до цели математически ${marg >= 0 ? 'в плюс' : 'в минус'} (без учёта лестницы фиксации).`;
  } else { $('#lat-be').textContent = '—'; $('#lat-be').className = 'val'; }

  // практический вывод доски одной строкой
  const readEl = $('#lat-read');
  const overBE = p.p - (p.p_breakeven ?? 1 / (1 + p.T));
  const parts = [];
  parts.push(overBE >= 0
    ? `P выше порога EV=0 на ${(overBE * 100).toFixed(0)}пп`
    : `P НИЖЕ порога EV=0 на ${(Math.abs(overBE) * 100).toFixed(0)}пп`);
  if (mkt && mkt.edge != null) {
    parts.push(mkt.edge >= 0.03 ? `рынок недооценивает сетап (+${(mkt.edge * 100).toFixed(0)}%)`
      : mkt.edge <= -0.03 ? `рынок оценивает выше вас (${(mkt.edge * 100).toFixed(0)}%)`
      : 'вы на уровне рынка');
  } else parts.push('рынка опционов нет — только модель');
  if (p.small_sample) parts.push(`выборка n=${p.n}<30 — доверяй интервалу, не точке`);
  readEl.textContent = parts.join(' · ');
  readEl.className = 'lat-read ' + (overBE >= 0 ? 'good' : 'bad');

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

  // σ-поправка показывается из t.sigma всегда (в т.ч. когда источник — индекс
  // волы и полной цепочки нет); строки цепочки — только при наличии os.
  const srcLabel = { chain: 'цепочка', vol_index: 'индекс волы' }[t.sigma.source] || '';
  $('#rg-sigma').textContent = t.sigma.applied ? fmtPct(t.sigma.sigma_implied, 1) : '—';
  $('#rg-sigma').dataset.tip = t.sigma.applied
    ? `σ_implied годовая = ${fmtPct(t.sigma.sigma_implied, 2)}\nисточник: ${srcLabel}` +
      (t.sigma.source === 'chain' ? '\n(из ATM straddle: implied_move × √(π/2t))'
                                  : '\n(значение индекса волы ÷ 100)')
    : 'нет источника implied-волы';
  $('#rg-base').textContent = t.sigma.applied ? fmtPct(t.sigma.sigma_baseline, 1) : '—';
  $('#rg-ratio').textContent = t.sigma.applied
    ? '×' + fmtNum(t.sigma.ratio, 3) + (srcLabel ? ` (${srcLabel})` : '') : 'НЕ ПРИМЕНЕНА';
  $('#rg-ratio').dataset.tip = t.sigma.applied
    ? `σ процесса умножена на σ_impl/σ_baseline = ${fmtPct(t.sigma.sigma_implied, 1)}/${fmtPct(t.sigma.sigma_baseline, 1)} = ${fmtNum(t.sigma.ratio, 3)}\nисточник σ_implied: ${srcLabel}\n(сжатый рынок «остужает» далёкий тейк, разогнанный — наоборот)`
    : `Поправка не применена: ${t.sigma.reason || 'нет данных'} — модель работает без опционной поправки (честнее, чем выдумывать).`;

  renderOiWalls();

  if (!os) {
    ['rg-proxy', 'rg-expiry', 'rg-move', 'rg-skew', 'rg-term', 'rg-p-take', 'rg-p-stop']
      .forEach((id) => { $('#' + id).textContent = '—'; });
    $('#rg-proxy').textContent = t.sigma.source === 'vol_index'
      ? 'ИНДЕКС ВОЛЫ' : '—';
    $('#rg-p-model').textContent = S.tick?.prob ? fmtPct(S.tick.prob.p) : '—';
    return;
  }
  $('#rg-proxy').textContent = os.proxy + (os.demo ? ' ◆' : '') + (os.experimental ? ' ⚠' : '');
  $('#rg-proxy').dataset.tip = os.experimental
    ? `⚠ ЭКСПЕРИМЕНТАЛЬНЫЙ ПРОКСИ ${os.proxy}: US-ETF на страну/валюту, трекинг неточный и опционы тонкие — плотность/скью/GEX/гамма для ${t.instrument} НИЗКОЙ НАДЁЖНОСТИ, используйте как грубый контекст.`
    : `Опционная цепочка ETF-прокси ${os.proxy}. Страйки пересчитаны в шкалу инструмента пропорцией цена/спот_прокси (приближение).`;
  $('#rg-expiry').textContent = os.expiry;
  // скью (risk-reversal)
  const sk = os.skew;
  if (sk) {
    $('#rg-skew').textContent = `${(sk.rr * 100 >= 0 ? '+' : '')}${(sk.rr * 100).toFixed(1)}пп · ${sk.tilt}`;
    $('#rg-skew').className = 'val ' + (sk.tilt === 'бычий' ? 'green' : sk.tilt === 'медвежий' ? 'red' : '');
    $('#rg-skew').dataset.tip =
      `Risk-reversal = IV(OTM call) − IV(OTM put) = ${fmtPct(sk.call_iv_otm, 1)} − ${fmtPct(sk.put_iv_otm, 1)} = ${(sk.rr * 100).toFixed(1)}пп.\n` +
      `Уклон: ${sk.tilt}. Отрицательный = рынок платит за защиту от падения; положительный = спрос на рост.\n` +
      `КОГДА СМОТРЕТЬ: перед входом. Уклон против вашего направления → сетап слабее (учтено в вердикте). Сильный уклон (>3пп) = рынок явно позиционирован в одну сторону.`;
  } else { $('#rg-skew').textContent = '—'; $('#rg-skew').className = 'val'; }
  // term-structure
  const tm = os.term;
  if (tm) {
    $('#rg-term').textContent = `${tm.shape} (${(tm.slope * 100 >= 0 ? '+' : '')}${(tm.slope * 100).toFixed(1)}%)`;
    $('#rg-term').dataset.tip =
      `Наклон ATM-волы: ${(tm.slope * 100).toFixed(1)}% -> ${tm.shape}.\n` +
      (tm.shape === 'бэквордация' ? 'Ближняя вола выше — near-term стресс/событие, движение ждут скоро.'
       : tm.shape === 'контанго' ? 'Дальняя вола выше — спокойно сейчас, далёкие по времени цели ок.'
       : 'Плоская — без выраженного ожидания.') +
      `\nКОГДА СМОТРЕТЬ: при выборе горизонта сделки. Бэквордация → жди быстрого движения (можно брать ближе тейк/быстрее фиксировать). Контанго → время работает, далёкие цели по RR реалистичнее.`;
  } else { $('#rg-term').textContent = '—'; }
  $('#rg-move').textContent = `${fmtPct(os.implied_move_frac)} / ${fmtPrice(os.implied_move_abs_instr)}`;
  $('#rg-move').dataset.tip =
    `Implied move до экспирации ${os.expiry}:\nATM straddle ${os.proxy} / спот = ${fmtPct(os.implied_move_frac)}\n` +
    `в пунктах инструмента: × scale ${fmtNum(os.scale, 4)} = ${fmtPrice(os.implied_move_abs_instr)}\n` +
    `(ожидаемое |движение|, E|ΔS/S|)`;

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

// стены open interest: где стоит крупнейший опционный интерес (сопротивление/поддержка)
function renderOiWalls() {
  const ow = S.ridge?.oi_walls;
  const call = $('#rg-call-wall'), put = $('#rg-put-wall'), read = $('#rg-wall-read');
  if (!ow) {
    call.textContent = '—'; put.textContent = '—'; read.textContent = '—';
    return;
  }
  const pctStr = (x) => x == null ? '' : ` (${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%)`;
  call.textContent = fmtPrice(ow.call_wall) + pctStr(ow.call_wall_pct);
  put.textContent = fmtPrice(ow.put_wall) + pctStr(ow.put_wall_pct);
  // тейк/стоп относительно стен (нужна сделка)
  const tr = S.ridge?.trade || S.tick?.trade;
  if (!tr) { read.textContent = 'нет сделки'; read.className = 'val dim'; return; }
  const long = tr.direction === 'long';
  const takeBeyondCall = long ? tr.take > ow.call_wall : tr.take < ow.put_wall;
  const wallOnPath = long ? ow.call_wall : ow.put_wall;   // барьер по ходу к тейку
  if (takeBeyondCall) {
    read.textContent = `тейк ЗА стеной ${long ? 'коллов' : 'путов'} — труднее`;
    read.className = 'val red';
  } else if (long ? (wallOnPath > tr.entry && wallOnPath < tr.take)
                  : (wallOnPath < tr.entry && wallOnPath > tr.take)) {
    read.textContent = `стена на пути к тейку — фиксируй у ${fmtPrice(wallOnPath)}`;
    read.className = 'val';
  } else {
    read.textContent = 'простор до тейка — крупных стен по пути нет';
    read.className = 'val green';
  }
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
      `<td class="notes">${(t.notes || '').slice(0, 90)}</td>` +
      `<td class="jrow-actions"><button class="jbtn j-edit" data-id="${t.id}" title="Редактировать">✎</button>` +
      `<button class="jbtn j-del" data-id="${t.id}" title="Удалить">✕</button></td>`;
    tbody.appendChild(tr);
  }
  tbody.querySelectorAll('.j-edit').forEach((b) =>
    b.addEventListener('click', () => editTradeModal(Number(b.dataset.id))));
  tbody.querySelectorAll('.j-del').forEach((b) =>
    b.addEventListener('click', () => deleteTradeModal(Number(b.dataset.id))));
}

function editTradeModal(id) {
  const t = S.journal.find((x) => x.id === id);
  if (!t) return;
  const opts = S.setups.map((su) =>
    `<option value="${su.num}" ${su.num === t.setup ? 'selected' : ''}>№${su.num} · ${su.name}</option>`).join('');
  openModal(`
    <h3>РЕДАКТИРОВАТЬ СДЕЛКУ №${t.id}</h3>
    <div class="form-grid">
      <label>Сетап</label><select id="e-setup">${opts}</select>
      <label>Направление</label>
      <select id="e-dir"><option value="long" ${t.direction === 'long' ? 'selected' : ''}>ЛОНГ</option><option value="short" ${t.direction === 'short' ? 'selected' : ''}>ШОРТ</option></select>
      <label>Вход</label><input id="e-entry" type="number" step="any" value="${t.entry}">
      <label>Стоп</label><input id="e-stop" type="number" step="any" value="${t.stop}">
      <label>Тейк</label><input id="e-take" type="number" step="any" value="${t.take}">
      <label>Результат, R</label><input id="e-res" type="number" step="any" value="${t.result_r ?? ''}"${t.status === 'open' ? ' disabled' : ''}>
      <span class="form-hint">${t.status === 'open' ? 'открытая сделка — результат задаётся при закрытии' : 'закрытая — можно исправить результат'}</span>
      <label>Заметки</label><textarea id="e-notes">${t.notes || ''}</textarea>
    </div>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-save">СОХРАНИТЬ</button>
    </div>`);
  $('#f-cancel').onclick = closeModal;
  $('#f-save').onclick = async () => {
    try {
      const body = { trade_id: id, setup: Number($('#e-setup').value),
        direction: $('#e-dir').value, entry: Number($('#e-entry').value),
        stop: Number($('#e-stop').value), take: Number($('#e-take').value),
        notes: $('#e-notes').value };
      if (t.status === 'closed' && $('#e-res').value !== '') body.result_r = Number($('#e-res').value);
      await apiPost('/api/trade/edit', body);
      closeModal();
      await refreshJournalAndSetups();
    } catch (e) { $('#f-err').textContent = e.message; }
  };
}

function deleteTradeModal(id) {
  const t = S.journal.find((x) => x.id === id);
  if (!t) return;
  openModal(`
    <h3>УДАЛИТЬ СДЕЛКУ №${t.id}?</h3>
    <p style="font-size:12px;line-height:1.5;">Сделка №${t.id} · ${t.instrument} · ${t.direction === 'long' ? 'ЛОНГ' : 'ШОРТ'} · вход ${fmtPrice(t.entry)}. Удаление необратимо и повлияет на статистику сетапа.</p>
    <div class="form-error" id="f-err"></div>
    <div class="form-actions">
      <button class="btn" id="f-cancel">ОТМЕНА</button>
      <button class="btn btn-primary" id="f-del" style="border-color:var(--red);background:var(--red);color:#fff;">УДАЛИТЬ</button>
    </div>`);
  $('#f-cancel').onclick = closeModal;
  $('#f-del').onclick = async () => {
    try { await apiPost('/api/trade/delete', { trade_id: id }); closeModal(); await refreshJournalAndSetups(); }
    catch (e) { $('#f-err').textContent = e.message; }
  };
}

function renderEdgeTrack() {
  const et = S.edge_track;
  const el = $('#edge-track');
  if (!el) return;
  if (!et || et.n === 0) {
    el.textContent = 'ещё нет закрытых сделок с зафиксированным краем — накопится по мере торговли';
    el.className = 'edge-track dim';
    return;
  }
  const pos = et.pos_wr == null ? '—' : fmtPct(et.pos_wr);
  const neg = et.neg_wr == null ? '—' : fmtPct(et.neg_wr);
  const better = et.pos_wr != null && et.neg_wr != null && et.pos_wr > et.neg_wr;
  el.innerHTML =
    `<b>+КРАЙ:</b> ${pos} винрейт (${et.pos_n} сд.) &nbsp;·&nbsp; ` +
    `<b>−/0 КРАЙ:</b> ${neg} винрейт (${et.neg_n} сд.) &nbsp;·&nbsp; ` +
    `<span class="${better ? 'green' : 'dim'}">${better ? 'край предсказателен ✓' : 'пока без явного преимущества'}</span>`;
  el.className = 'edge-track';
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
