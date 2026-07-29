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
import { initVrp, updateVrp } from './vrp.js';
import { initGex, updateGex, updateLiveGex } from './gex.js';
import { initIVSurface } from './iv_surface.js';
import { initVp, updateVp } from './vp.js';
import { initCorrelation, updateCorrelation } from './correlation.js';

initTooltips();

const lattice = initLattice($('#lattice-canvas'));
const ridge = initRidge($('#ridge-canvas'));
const levels = initLevels($('#levels-canvas'));
const cone = initCone('#cone-plot');
const fan = initFan($('#cone-fan'));
const ivSurface = initIVSurface('#iv-surface-plot');
initVrp();
initGex();
initVp();
initCorrelation();

const S = {
  tick: null,
  ridge: null,
  setups: [],
  journal: [],
  validation: null,
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
    S.validation = st.validation;
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
    proxyPrice: S.tick?.feeds?.proxy_price?.value,
    modelHist: S.tick?.mc?.hist,
    trade: S.tick?.trade || null,
    modelProb: S.tick?.prob?.model_p,
    optionProb: S.tick?.prob?.source === 'options_barrier_mc' ? S.tick.prob.p : null,
    rnProbs: S.tick?.market?.available ? {
      p_beyond_take: S.tick.market.terminal_p_take,
      p_beyond_stop: S.tick.market.terminal_p_stop,
    } : null,
  });
  // живой луч цены в конусе + точка цены в веере (r) двигаются каждый тик
  cone.updateLive({ r: S.tick?.prob?.r });
  fan.updateLive({ r: S.tick?.prob?.r });
  updateVrp(S.tick?.vrp);
  updateLiveGex({
    price: S.tick?.feeds?.price?.value,
    proxyPrice: S.tick?.feeds?.proxy_price?.value,
    trade: S.tick?.trade || null,
  });
  ivSurface.render(S.tick?.state, S.tick?.iv_surface);
  updateVp(S.tick?.levels?.volume_profile);
  updateCorrelation(S.tick?.correlation?.value);
}

function renderAll() {
  onTick();
  renderJournal();
  renderSetupGrid();
  renderEdgeTrack();
  ridge.setData(S.ridge, S.tick?.prob?.model_p);
  updateGex(S.ridge);
}

async function refreshJournalAndSetups() {
  const st = await (await fetch('/api/state')).json();
  S.journal = st.journal;
  S.setups = st.setups;
  S.ridge = st.ridge;
  S.tick = st.tick;
  S.edge_track = st.edge_track;
  S.validation = st.validation;
  renderAll();
}

async function maybeRefreshRidge() {
  const ts = S.tick?.feeds?.chain?.ts;
  if (ts && ts !== S.chainTs) {
    S.chainTs = ts;
    try {
      S.ridge = await (await fetch('/api/chain')).json();
      ridge.setData(S.ridge, S.tick?.prob?.model_p);
      updateGex(S.ridge);
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
  const derived = feed?.derived ? ' · PROXY MAP' : '';
  el.textContent = `${stale ? '⏸' : (STATUS_ICON[st] || '○')} ${label}${derived}${stale ? ' СТОИТ' : ''}`;
  const base = extraTip || '';
  const err = feed?.error ? `\nошибка: ${feed.error}` : '';
  const src = feed?.source ? `\nисточник: ${feed.source}` : '';
  const ts = feed?.ts ? `\nобновлено: ${fmtTs(feed.ts)} UTC` : '';
  const idle = stale ? `\n⏸ нет тиков ${fmtIdle(feed.idle_secs)} — рынок закрыт/неторговое время` : '';
  const mapping = feed?.derived
    ? `\nderived live: доходность ${feed.driver_ticker || 'proxy'} перенесена от контрольного якоря ${feed.anchor_ticker || ''}; возраст якоря ${fmtIdle(feed.anchor_age_sec)}`
    : '';
  el.dataset.tip = `${base}статус: ${statusLabel(st)}${src}${mapping}${ts}${idle}${err}`;
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

  const px = t.feeds.price;
  const basisTip = px?.basis_offset
    ? `ряд: ${px.label || px.ticker}\nсырой тик ${fmtPrice(px.raw_value)} + basis ${px.basis_offset >= 0 ? '+' : ''}${fmtPrice(px.basis_offset)} = ${fmtPrice(px.value)}\n`
    : `ряд: ${px?.label || px?.ticker || '—'}\n`;
  feedBadge($('#feed-price'), px, basisTip);
  feedBadge($('#feed-chain'), t.feeds.chain, 'Фид опционной цепочки (опрос 5–10 мин).\n');
  const vols = t.feeds.vols;
  const worst = ['vix', 'gvz', 'dv1x'].map((k) => vols[k]?.status || 'no_data');
  const vixState = vols.vix?.status || 'no_data';
  feedBadge($('#feed-vix'), {
    status: vixState,
    ts: vols.vix?.ts,
    source: `VIX=${fmtNum(vols.vix?.value, 2)} GVZ=${fmtNum(vols.gvz?.value, 2)} DV1X=${vols.dv1x?.value == null ? 'нет' : fmtNum(vols.dv1x?.value, 2)}`,
    error: worst.includes('no_data') ? 'часть индексов недоступна' : null,
  }, 'Индексы волатильности: бесплатный 15m delayed-контекст, не тиковый фильтр.\n');
}

// живая котировка на латтике: тик, цвет вверх/вниз, вспышка, % от входа
function handleLivePrice(t) {
  const price = t.feeds?.price?.value;
  const streaming = (t.feeds?.price?.source || '').startsWith('stream');
  const derived = !!t.feeds?.price?.derived;
  const stale = t.feeds?.price?.fresh === false;
  const idle = t.feeds?.price?.idle_secs;
  $('#lat-price-instr').textContent = t.instrument
    + (streaming ? ' ⚡' : '') + (derived ? ' · PROXY MAP' : '')
    + (stale ? ' · ⏸ ЗАКРЫТ' : '');
  $('#lat-price-instr').title = stale
    ? `нет свежих тиков ${fmtIdle(idle)} — рынок закрыт или неторговое время; цена = последняя котировка`
    : (derived
      ? `derived live: уровень якорится к ${t.feeds.price.anchor_ticker}, движение приходит из ${t.feeds.price.driver_ticker}`
      : (streaming ? 'живой WebSocket-стрим цены' : ''));
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
  $('#v-pmm').textContent = mkt?.available
    ? `P options ${fmtPct(mkt.hit_ratio)} · EV=0 ${fmtPct(mkt.p_breakeven)}`
    : `option edge недоступен${mkt?.anchor_reason ? ` · ${mkt.anchor_reason}` : ''}`;
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

  // Только option-anchored P. При отсутствии якоря не показываем surrogate.
  if (s.p != null && s.p_lo != null && s.p_hi != null) {
    tweenNumber($('#st-p'), s.p * 100, (v) => v.toFixed(1) + '%', 10);
    $('#st-p-band').textContent = `[${(s.p_lo * 100).toFixed(0)}–${(s.p_hi * 100).toFixed(0)}%]`
      + (s.median_years != null ? ` · развязка ≈ ${fmtDur(s.median_years)}` : '');
  } else {
    $('#st-p').textContent = '—';
    $('#st-p-band').textContent = 'нет option anchor · сценарий без P';
  }

  // край + сдвиг от входа
  if (s.edge == null) {
    $('#st-edge').textContent = '—'; $('#st-edge').className = 'state-val dim';
    $('#st-edge-shift').textContent = 'option edge недоступен';
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
  const anchored = !!c?.option_anchored;
  const liveMapping = t?.feeds?.proxy_price?.status === 'live';
  $('#cone-status').className = 'badge ' + (active
    ? (t.demo ? 'demo' : anchored && liveMapping ? 'live' : 'delayed')
    : 'no_data');
  $('#cone-status').textContent = active
    ? (t.demo ? '◆ DEMO'
      : anchored && liveMapping ? '● OPTIONS + LIVE MAPPING'
      : anchored ? '◐ OPTIONS + INDICATIVE MAPPING'
      : '◐ SCENARIO · БЕЗ P / EDGE')
    : '○ НЕТ СДЕЛКИ';
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
  const optionAnchored = p?.source === 'options_barrier_mc';
  const liveMapping = t?.feeds?.proxy_price?.status === 'live';
  $('#lattice-empty').style.display = active ? 'none' : 'flex';
  $('#lattice-status').className = 'badge ' + (active
    ? (t.demo ? 'demo' : optionAnchored && liveMapping ? 'live' : 'delayed')
    : 'no_data');
  $('#lattice-status').textContent = active
    ? (t.demo ? '◆ DEMO'
      : optionAnchored && liveMapping ? '● OPTIONS + LIVE MAPPING'
      : optionAnchored ? '◐ OPTIONS + INDICATIVE MAPPING'
      : '◐ SCENARIO · БЕЗ P / EDGE')
    : '○ НЕТ СДЕЛКИ';

  const mkt = t.market;
  lattice.setData({
    active,
    T: p?.T ?? 2.5,
    r: p?.r ?? 0,
    distributionProbs: mkt?.scenario_probs,
    edges: mkt?.scenario_edges,
    optionAnchored: !!mkt?.available,
    hit: mkt?.hit_ratio,
    edge: mkt?.edge,
    pStop: mkt?.p_stop_horizon,
    pTake: mkt?.p_take_horizon,
    unresolved: mkt?.p_unresolved_horizon,
    q10: mkt?.scenario_p10_r,
    q50: mkt?.scenario_median_r,
    q90: mkt?.scenario_p90_r,
    mode: mkt?.scenario_mode_r,
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

  // Опционная вероятность и её запас над безубыточностью.
  if (mkt?.available) {
    $('#lat-mhit').textContent = fmtPct(mkt.hit_ratio);
    $('#lat-mhit').dataset.tip =
      `P(тейк раньше стопа) по finite-horizon barrier MC.\n` +
      `Ширина: implied move; хвост и forward: BL-плотность; асимметрия: skew; время: term structure.${mkt.median_years != null ? '\nМедиана развязки ≈ ' + fmtDur(mkt.median_years) : ''}\n` +
      `К горизонту уже поглощено: тейк ${fmtPct(mkt.p_take_horizon)}, стоп ${fmtPct(mkt.p_stop_horizon)}, не разрешено ${fmtPct(mkt.p_unresolved_horizon)}. Неразрешённая масса якорится BL tail-ratio ${fmtPct(mkt.terminal_hit)}.`;
    const ed = mkt.edge;
    $('#lat-edge').textContent = ed == null ? '—' : (ed >= 0 ? '+' : '') + fmtPct(ed);
    $('#lat-edge').className = 'val ' + (ed == null ? '' : ed >= 0 ? 'green' : 'red');
    $('#lat-edge').dataset.tip =
      `Опционный запас = P_options − P(EV=0) = ${fmtPct(mkt.hit_ratio)} − ${fmtPct(mkt.p_breakeven)} = ${ed == null ? '—' : (ed >= 0 ? '+' : '') + fmtPct(ed)}.\n` +
      `Это честная проверка асимметрии текущих барьеров по данным опционов, а не вероятность из одной пропорции стоп/тейк.`;
  } else {
    $('#lat-mhit').textContent = '—';
    $('#lat-mhit').dataset.tip = `${mkt?.anchor_reason || `Нет валидной цепочки/преобразования для ${t.instrument}`}; опционный edge выключен. Тёмная контрольная модель остаётся только справочной.`;
    $('#lat-edge').textContent = '—';
    $('#lat-edge').className = 'val';
  }

  if (p.available && p.p != null) {
    tweenNumber($('#lat-p'), p.p * 100, (v) => v.toFixed(1) + '%');
    $('#lat-p').dataset.tip =
      `ОПЦИОННАЯ P(тейк раньше стопа), не пропорция расстояний.\nТекущий r=${p.r.toFixed(3)}; moneyness использует ${t.feeds?.proxy_price?.status === 'live' ? 'живой stream-тик' : 'последнюю indicative/snapshot-котировку'} ${t.options_summary?.proxy || 'proxy'}.\nПолный ход σ=${p.sigma_R.toFixed(3)}R из implied move; BL-плотность задаёт terminal tail/forward, skew — асимметрию, term structure — раскрытие по времени.\nВинрейт сетапа в эту P не подставляется.`;
    if (t.trade?.id !== S._pTradeId) {
      S._pTradeId = t.trade?.id; S._pSum = 0; S._pN = 0;
    }
    S._pSum += p.p; S._pN += 1;
    $('#lat-p-avg').textContent = `· ср ${((S._pSum / S._pN) * 100).toFixed(1)}%`;
    const lo = p.p_lo * 100, hi = p.p_hi * 100;
    $('#lat-band-fill').style.left = lo + '%';
    $('#lat-band-fill').style.width = Math.max(hi - lo, 0.5) + '%';
    $('#lat-band-tick').style.left = `calc(${p.p * 100}% - 1px)`;
    $('#lat-band-lbl').textContent =
      `[${lo.toFixed(1)}% – ${hi.toFixed(1)}%] сценарная полоса proxy/snapshot`;
    $('#lat-band').dataset.tip =
      `Не статистический confidence interval. Полоса расширяется из-за возраста цепочки и качества proxy; нужна, чтобы не принимать одну бесплатную delayed-оценку за точное число.`;
  } else {
    $('#lat-p').textContent = '—';
    $('#lat-p').dataset.tip =
      `Нет валидного опционного якоря: P выключена. Доска показывает только условную сценарную плотность живых путей; расстояние стоп/тейк и таблица сетапа не подставляются как вероятность.`;
    $('#lat-p-avg').textContent = '';
    $('#lat-band-fill').style.left = '0%';
    $('#lat-band-fill').style.width = '0%';
    $('#lat-band-tick').style.left = '0%';
    $('#lat-band-lbl').textContent = 'P выключена · сценарная плотность без edge';
  }

  $('#lat-r').textContent = fmtR(p.r);
  $('#lat-ev-hold').textContent = p.available ? fmtR(t.mc.ev_hold) : '—';
  $('#lat-ev-hold').dataset.tip =
    t.mc.ev_hold_source === 'options_probability'
      ? `Опционный EV удержания = P_options·T − (1−P_options) = ${(p.p * p.T - (1 - p.p)).toFixed(3)}R. Комиссии, проскальзывание и physical drift не включены.`
      : `Без option anchor EV не показывается: историческая таблица не заменяет рыночную вероятность.`;
  $('#lat-ev-ladder').textContent = p.available ? fmtR(t.mc.ev_ladder) : '—';
  $('#lat-ev-ladder').dataset.tip =
    `Исследовательский path-control лестницы по исторической модели: 10% на 1.0/1.25/1.5/1.75/2.0/2.2R, БУ после 1.5R.\nНе участвует в опционном edge; сохранён для контроля исполнения плана.`;

  // порог безубытка по винрейту + запас
  if (p.available && p.p != null && p.p_breakeven != null) {
    const marg = (p.p - p.p_breakeven) * 100;
    $('#lat-be').textContent = fmtPct(p.p_breakeven) +
      ` (${marg >= 0 ? '+' : ''}${marg.toFixed(0)}пп)`;
    $('#lat-be').className = 'val ' + (marg >= 0 ? 'green' : 'red');
    $('#lat-be').dataset.tip =
      `Порог EV=0 при RR 1:${p.T.toFixed(2)} = 1/(1+${p.T.toFixed(2)}) = ${fmtPct(p.p_breakeven)}.\n` +
      `Ваша P(тейк) ${fmtPct(p.p)} ${marg >= 0 ? 'ВЫШЕ' : 'НИЖЕ'} порога на ${Math.abs(marg).toFixed(0)}пп -> ` +
      `risk-neutral сценарный EV ${marg >= 0 ? 'положительный' : 'отрицательный'} ` +
      `(не доказанный physical edge; без комиссий и лестницы).`;
  } else { $('#lat-be').textContent = '—'; $('#lat-be').className = 'val'; }

  // практический вывод доски одной строкой
  const readEl = $('#lat-read');
  const parts = [];
  const overBE = p.available && p.p != null
    ? p.p - (p.p_breakeven ?? 1 / (1 + p.T)) : null;
  if (overBE != null) {
    parts.push(overBE >= 0
      ? `P выше порога EV=0 на ${(overBE * 100).toFixed(0)}пп`
      : `P НИЖЕ порога EV=0 на ${(Math.abs(overBE) * 100).toFixed(0)}пп`);
  } else {
    parts.push('P не рассчитана: нет устойчивого option anchor');
  }
  if (mkt?.available && mkt.edge != null) {
    parts.push(mkt.edge >= 0.03 ? `опционная асимметрия положительная (+${(mkt.edge * 100).toFixed(0)}пп)`
      : mkt.edge <= -0.03 ? `опционная асимметрия отрицательная (${(mkt.edge * 100).toFixed(0)}пп)`
      : 'опционная асимметрия около нуля');
  } else parts.push('нет option anchor — edge выключен');
  if (mkt?.scenario_p10_r != null) {
    parts.push(`живая масса P10/P50/P90: ${fmtR(mkt.scenario_p10_r)} / ${fmtR(mkt.scenario_median_r)} / ${fmtR(mkt.scenario_p90_r)}`);
  }
  readEl.textContent = parts.join(' · ');
  readEl.className = 'lat-read ' + (overBE == null ? '' : overBE >= 0 ? 'good' : 'bad');

  const st = lattice.stats;
  $('#lat-balls').textContent = String(st.dropped);
  $('#lat-green').textContent = st.greenShare == null ? '—' : fmtPct(st.greenShare);
  $('#lat-conv').textContent = st.convergence == null ? '—'
    : (st.convergence * 100).toFixed(1) + ' пп';
  $('#lat-conv').dataset.tip =
    `|доля зелёных − P(R>0 по МК)| = |${st.greenShare == null ? '—' : (st.greenShare * 100).toFixed(1)}% − ${st.pGreenModel == null ? '—' : (st.pGreenModel * 100).toFixed(1)}%|\n` +
    `Метрика честности доски: корзины сэмплируются из МК-распределения,\nпоэтому расхождение должно убывать с числом шариков (закон больших чисел).`;
  $('#lat-calib').textContent =
    `t=${((mkt?.scenario_slice_time_frac ?? 0) * 100).toFixed(0)}% · alive ${fmtPct(mkt?.scenario_slice_alive)}`;
  $('#lat-calib').dataset.tip =
    `Доска показывает условное распределение путей, ещё не поглощённых барьерами, на информативном временном срезе.\n` +
    `Поглощённые массы стопа/тейка вынесены отдельно и больше не раздувают крайние колонки.`;
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
  const px = t?.feeds?.price;
  const fresh = px?.fresh !== false;
  const live = px?.status === 'live' && fresh;
  const tone = !has ? 'no_data' : t.demo ? 'demo' : live ? 'live' : 'delayed';
  $('#levels-status').className = 'badge ' + tone;
  $('#levels-status').textContent = !has ? '○ НЕТ СДЕЛКИ'
    : t.demo ? '◆ DEMO'
    : live ? (px?.derived ? '● LIVE · PROXY MAP' : '● LIVE')
    : px?.fresh === false ? '⏸ НЕТ ТИКОВ' : '◐ INDICATIVE';
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
    $('#rg-p-model').textContent = S.tick?.prob?.source === 'options_barrier_mc'
      ? fmtPct(S.tick.prob.p) : '—';
    return;
  }
  $('#rg-proxy').textContent = os.proxy + (os.demo ? ' ◆' : '') + (os.experimental ? ' ⚠' : '');
  $('#rg-proxy').dataset.tip = os.experimental
    ? `⚠ ЭКСПЕРИМЕНТАЛЬНЫЙ ПРОКСИ ${os.proxy}: трекинг/ликвидность ограничены. Преобразование ${os.proxy_transform}; сценарная полоса расширена, GEX может быть выключен.`
    : `Delayed-цепочка ${os.proxy}; страйки переносятся по текущей moneyness (${os.proxy_transform}). Proxy mapping сейчас: ${os.spot_proxy_status === 'live' ? 'live stream' : os.spot_proxy_is_snapshot_fallback ? 'snapshot fallback' : 'REST indicative'}.`;
  $('#rg-expiry').textContent = os.expiry;
  // скью (risk-reversal)
  const sk = os.skew;
  if (sk) {
    $('#rg-skew').textContent = `${(sk.rr * 100 >= 0 ? '+' : '')}${(sk.rr * 100).toFixed(1)}пп · ${sk.tilt}`;
    $('#rg-skew').className = 'val ' + (sk.tilt === 'бычий' ? 'green' : sk.tilt === 'медвежий' ? 'red' : '');
    $('#rg-skew').dataset.tip =
      `Risk-reversal = IV(OTM call) − IV(OTM put) = ${fmtPct(sk.call_iv_otm, 1)} − ${fmtPct(sk.put_iv_otm, 1)} = ${(sk.rr * 100).toFixed(1)}пп.\n` +
      `Уклон: ${sk.tilt}. Отрицательный = рынок платит за защиту от падения; положительный = спрос на рост.\n` +
      `Это контекст формы хвоста; отдельно verdict не усиливает, чтобы не считать один и тот же опционный эффект дважды.`;
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
    `в пунктах текущего инструмента: цена × move% = ${fmtPrice(os.implied_move_abs_instr)}\n` +
    `proxy snapshot ${fmtPrice(os.spot_proxy_snapshot)} → ${os.spot_proxy_status === 'live' ? 'live' : os.spot_proxy_is_snapshot_fallback ? 'snapshot fallback' : 'indicative'} ${fmtPrice(os.spot_proxy_current)}; transform=${os.proxy_transform}`;

  const rn = t.market?.available ? {
    p_beyond_take: t.market.terminal_p_take,
    p_beyond_stop: t.market.terminal_p_stop,
    expiry: os.expiry,
    demo: t.market.demo,
  } : null;
  $('#rg-p-take').textContent = rn ? fmtPct(rn.p_beyond_take) : '—';
  $('#rg-p-take').dataset.tip = rn
    ? `P(цена за тейком на экспирации ${rn.expiry}) по risk-neutral плотности:\n∫ q(K)dK за уровнем тейка; q ≈ e^{rT}·d²C/dK² (Бриден–Литценбергер),\nсглаживание локальной квадратичной регрессией, отрицательные значения обрезаны.\nСтрайки прокси → шкала инструмента пропорцией (приближение).${rn.demo ? '\n◆ DEMO-цепочка' : ''}`
    : 'нужны открытая сделка и цепочка';
  $('#rg-p-stop').textContent = rn ? fmtPct(rn.p_beyond_stop) : '—';
  $('#rg-p-stop').dataset.tip = rn
    ? `P(цена за стопом на экспирации) — аналогично P(за тейк), хвост с другой стороны.${rn.demo ? '\n◆ DEMO-цепочка' : ''}`
    : 'нужны открытая сделка и цепочка';
  $('#rg-p-model').textContent = S.tick?.prob?.source === 'options_barrier_mc'
    ? fmtPct(S.tick.prob.p) : '—';
}

// Концентрации open interest: контекст страйков, не наблюдаемая «стена» дилеров.
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
  // тейк/стоп относительно концентраций OI (нужна сделка)
  const tr = S.ridge?.trade || S.tick?.trade;
  if (!tr) { read.textContent = 'нет сделки'; read.className = 'val dim'; return; }
  const long = tr.direction === 'long';
  const takeBeyondCall = long ? tr.take > ow.call_wall : tr.take < ow.put_wall;
  const wallOnPath = long ? ow.call_wall : ow.put_wall;   // барьер по ходу к тейку
  if (takeBeyondCall) {
    read.textContent = `тейк за max OI ${long ? 'коллов' : 'путов'} · контекст`;
    read.className = 'val';
  } else if (long ? (wallOnPath > tr.entry && wallOnPath < tr.take)
                  : (wallOnPath < tr.entry && wallOnPath > tr.take)) {
    read.textContent = `max OI на пути: ${fmtPrice(wallOnPath)} · наблюдай реакцию`;
    read.className = 'val';
  } else {
    read.textContent = 'max OI не лежит между входом и тейком';
    read.className = 'val';
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
  const vr = S.validation;
  const el = $('#edge-track');
  if (!el) return;
  if (!et || et.n === 0) {
    el.textContent = vr?.message
      || 'ещё нет закрытых сделок с зафиксированным option edge';
    el.className = 'edge-track dim';
    return;
  }
  const pos = et.pos_wr == null ? '—' : fmtPct(et.pos_wr);
  const neg = et.neg_wr == null ? '—' : fmtPct(et.neg_wr);
  const enough = (vr?.n || 0) >= 30;
  const better = enough && et.pos_wr != null && et.neg_wr != null && et.pos_wr > et.neg_wr;
  const calibration = vr?.n
    ? `<br><b>OPTION CALIBRATION:</b> Brier ${vr.brier.toFixed(3)} · log loss ${vr.log_loss.toFixed(3)} · barrier outcomes ${vr.n}` +
      (vr.censored_n ? ` · censored ${vr.censored_n}` : '')
    : `<br><span class="dim">${vr?.message || 'barrier-калибровка ещё не накоплена'}</span>`;
  el.innerHTML =
    `<b>+OPTION EDGE:</b> ${pos} положительных исходов (${et.pos_n} сд.) &nbsp;·&nbsp; ` +
    `<b>−/0 EDGE:</b> ${neg} (${et.neg_n} сд.) &nbsp;·&nbsp; ` +
    `<span class="${better ? 'green' : 'dim'}">${better ? 'есть out-of-sample подтверждение' : 'выборка пока не подтверждает преимущество'}</span>` +
    calibration;
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
      <label>Цена у брокера сейчас</label><input id="f-reference" type="number" step="any">
      <span class="form-hint">Необязательно. Для XAU/XAG/CFD укажите текущую котировку брокера: терминал зафиксирует basis к бесплатному фьючерсу и дальше будет двигать её живыми тиками.</span>
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
      $('#f-reference').value = price.toPrecision(8);
      $('#f-rr-hint').textContent =
        `вход подставлен из фида ${su.instrument} (${price.toPrecision(8)}); тейк можно оставить пустым — рассчитаю из RR (правило 2.8)`;
    } else {
      $('#f-entry').value = '';
      $('#f-reference').value = '';
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
        reference_price: $('#f-reference').value ? Number($('#f-reference').value) : null,
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
