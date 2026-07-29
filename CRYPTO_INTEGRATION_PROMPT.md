# ПРОМТ: Интеграция криптовалют в Seiltanzer Terminal

> Для AI-агента (Claude, Cursor, Windsurf и т.д.) который будет реализовывать интеграцию.
> Скопируй этот промт целиком и вставь в чат с агентом.

---

## ЗАДАЧА

Добавить поддержку **криптовалютных инструментов** (BTC, ETH, SOL) в Seiltanzer Terminal —
веб-дашборд поддержки торговых решений. Крипто-инструменты должны получать:
1. **Живую цену** — через Binance WebSocket (бесплатно, без ключа, 24/7).
2. **Полную опционную цепочку** — через Deribit REST API (бесплатно, без ключа).
3. **Индекс волатильности DVOL** — через Deribit (аналог VIX для крипты).
4. **Все существующие аналитики** терминала: implied move, BL-плотность, GEX, скью,
   term-structure, sigma-ratio, 3D-конус, гамма-пиннинг, вердикт.

**ВАЖНО:** Существующая функциональность для фондовых инструментов (NAS100, SP500 и т.д.)
НЕ должна сломаться. Все текущие тесты (`pytest tests/`) должны проходить.

---

## ТЕКУЩАЯ АРХИТЕКТУРА (АКТУАЛЬНАЯ НА ИЮЛЬ 2026)

### Структура проекта

```
seiltanzer/
├── __init__.py              # __version__ = "0.1.0"
├── __main__.py              # CLI: --demo, --stream, --check, --host, --port, --data-dir
├── app.py                   # FastAPI: REST API (/api/state, /api/trade, ...), WS /ws, poll_loop
├── engine.py                # Движок: tick_payload, verdict, trade_payloads, cone, levels, ridge
├── config.py                # SETUPS (1-16), INSTRUMENTS, VOL_INDEX_TICKERS, Settings
├── journal.py               # SQLite trades.db: CRUD сделок, account, setup_stats, edge_track
├── check.py                 # --check: проверка живых фидов Yahoo
├── core/
│   ├── options.py           # implied_move, bl_density, gex_profile, risk_reversal_skew,
│   │                        #   term_structure, gamma_pin, synth_chain, market_r_distribution
│   ├── prob.py              # first_passage_prob, calibrate_mu, prob_band, wilson_interval,
│   │                        #   simulate_remainder, forward_distribution, cone_surface
│   └── risk.py              # risk_matrix_row, atr_ratio, classify_atr_phase, efficiency
├── data/
│   ├── cache.py             # DiskCache: SQLite kv-store + chain_snapshots history
│   ├── feeds.py             # MarketData: refresh_price/daily/vols/chain, DemoMarket
│   └── stream.py            # StreamHub: Yahoo WS wss://streamer.finance.yahoo.com protobuf
└── web/
    ├── index.html           # Фронтенд: header, state, lattice, ridge, cone3D, levels, journal
    ├── css/terminal.css
    ├── vendor/plotly-gl3d.min.js
    └── js/
        ├── app.js           # WS-клиент, render-цикл, модальные окна (53KB)
        ├── lattice.js       # Доска вероятности (Canvas 2D, шары Гальтона)
        ├── ridge.js         # Strike Landscape (Canvas 2D, гряда плотностей)
        ├── cone.js          # 3D-конус (Plotly WebGL)
        ├── levels.js        # Карта уровней (Canvas 2D, частицы)
        ├── anim.js          # Анимации (60fps lerp)
        └── util.js          # DOM-хелперы, форматтеры, tooltip
```

### Как устроены инструменты сейчас

**`config.py` — `INSTRUMENTS` dict:**
```python
@dataclass(frozen=True)
class Instrument:
    code: str               # "NAS100", "SP500", ...
    yahoo: str              # тикер Yahoo Finance для цены ("^NDX", "^GSPC")
    options_proxy: str|None # тикер ETF для опционной цепочки ("QQQ", "SPY") или None
    demo_price: float       # стартовая цена в демо
    demo_vol: float         # годовая вола в демо
    proxy_experimental: bool = False  # тонкий прокси — помечается в UI

INSTRUMENTS: dict[str, Instrument] = {i.code: i for i in [
    Instrument("NAS100", "^NDX",     "QQQ", 21500.0, 0.22),
    Instrument("SP500",  "^GSPC",    "SPY", 6100.0,  0.17),
    # ... ещё 8 инструментов
]}
```

Текущий поток данных:
1. **Цена**: `yfinance.Ticker(yahoo).fast_info.last_price` (REST, 4с) ИЛИ Yahoo WS `wss://streamer.finance.yahoo.com` (protobuf yaticker) — через `StreamHub` в `data/stream.py`.
2. **Опционная цепочка**: `yfinance.Ticker(options_proxy).option_chain(expiry)` → `strikes, call/put mid, OI, IV` → `_compute_chain_metrics()`.
3. **Индексы волы**: `yfinance.Ticker("^VIX")` и т.д. (REST, 60с).
4. **Дневки**: `yfinance.Ticker(yahoo).history(period="4mo", interval="1d")` (REST, 30мин).

### Как устроена опционная математика

Файл `core/options.py` — **полностью source-agnostic**: функции принимают numpy-массивы
`strikes, call_mid, put_mid, call_oi, put_oi, call_iv, put_iv, t_years, spot` и не зависят
от того, откуда пришли данные (Yahoo, Deribit, синтетика). Это критично — **математику менять не нужно**.

Ключевые функции:
- `implied_move(strikes, call_mids, put_mids, spot, t_years)` → `ImpliedMove`
- `bl_density(strikes, call_mids, t_years)` → `RNDensity`
- `gex_profile(strikes, call_oi, put_oi, call_iv, put_iv, spot, t_years)` → `GexProfile`
- `risk_reversal_skew(strikes, call_iv, put_iv, spot)` → skew dict
- `term_structure([(days, atm_iv), ...])` → term dict
- `gamma_pin(...)` → gamma dict

### Ключевое допущение: proxy_scale

Фондовые инструменты используют ETF-прокси для опционов (^NDX → QQQ). Цена прокси ≠ цене
инструмента, поэтому engine.py считает `scale = price_instrument / spot_proxy` и пересчитывает
страйки: `strikes_instr = strikes_proxy * scale`.

**Для крипты прокси не нужен** — Deribit торгует опционы прямо на BTC/ETH/SOL. Значит
`options_proxy` может быть равен `code` (или новое поле), а `scale = 1.0`.

### Логика сессии и часов

`engine.py: _seconds_to_session_end()` считает время до 21:00 UTC (конец US-сессии).
**Для крипты** рынок 24/7 — нужна адаптация (или вернуть None/∞ для крипто-инструментов).

---

## БЕСПЛАТНЫЕ API ДЛЯ КРИПТЫ

### 1. Deribit — ОСНОВНОЙ источник опционных данных

**Общая информация:**
- Крупнейшая крипто-опционная биржа (~90% объёма).
- **Полностью бесплатный публичный API**, без ключа, без регистрации.
- Поддерживает: **BTC, ETH, SOL** (с конца 2024).
- Rate limit: ~20 req/s для неаутентифицированных запросов.

**REST API (основной для опционных цепочек):**

```
Base URL: https://www.deribit.com/api/v2/

# Все опционные инструменты для валюты
GET /public/get_instruments?currency=BTC&kind=option&expired=false
→ [{instrument_name, strike, expiration_timestamp, option_type, ...}, ...]

# Тикер конкретного опциона (bid, ask, mark_price, mark_iv, greeks, OI)
GET /public/ticker?instrument_name=BTC-28JUL26-100000-C
→ {mark_price, mark_iv, bid_price, ask_price, open_interest,
   greeks: {delta, gamma, vega, theta, rho}, underlying_price, ...}

# Book summary по валюте (все опционы разом)
GET /public/get_book_summary_by_currency?currency=BTC&kind=option
→ [{instrument_name, mark_price, mark_iv, open_interest,
    bid_price, ask_price, underlying_price, ...}, ...]
  ☝️ Это самый полезный эндпоинт — одним запросом ВСЕ опционы с IV и OI

# Индекс DVOL (аналог VIX)
GET /public/get_volatility_index_data?currency=BTC&resolution=1&start_timestamp=...&end_timestamp=...
→ {data: [[ts, open, high, low, close], ...]}

# Или текущий DVOL через тикер:
GET /public/ticker?instrument_name=BTC_DVOL
→ {last_price: 52.3, ...}  (это BTC DVOL в % годовой волы)

# Текущая цена индекса (спот)
GET /public/get_index_price?index_name=btc_usd
→ {index_price: 67500.0}
```

**Deribit WebSocket (для стриминга — опционально):**
```
wss://www.deribit.com/ws/api/v2

# Подписка на тикер опциона:
{"jsonrpc": "2.0", "method": "public/subscribe",
 "params": {"channels": ["ticker.BTC-28JUL26-100000-C.raw"]}}

# Подписка на DVOL:
{"jsonrpc": "2.0", "method": "public/subscribe",
 "params": {"channels": ["deribit_volatility_index.btc_usd"]}}
```

**Данные Deribit включают:**
- ✅ Strikes, bid/ask/mark prices
- ✅ mark_iv (implied volatility) для каждого страйка
- ✅ Greeks (delta, gamma, vega, theta) для каждого страйка
- ✅ Open Interest
- ✅ Underlying price (index price)
- ✅ DVOL (аналог VIX) для BTC и ETH
- ✅ Множество экспираций (дневные, недельные, месячные, квартальные)

### 2. Binance WebSocket — ОСНОВНОЙ источник спот-цены

```
wss://stream.binance.com:9443/ws/btcusdt@trade    # тики BTC
wss://stream.binance.com:9443/ws/ethusdt@trade     # тики ETH
wss://stream.binance.com:9443/ws/solusdt@trade     # тики SOL

# Формат сообщения:
{"e":"trade","s":"BTCUSDT","p":"67500.12","T":1719300000000,...}
```
- Бесплатно, без ключа.
- Реальное время (sub-second latency).
- Для дневных баров: `GET https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=120`

### 3. OKX — РЕЗЕРВНЫЙ источник опционных данных

Если Deribit недоступен (блокировка по IP), OKX — полноценная замена.

```
# Все опционы с Greeks и IV одним запросом:
GET https://www.okx.com/api/v5/public/opt-summary?uly=BTC-USD
→ [{delta, gamma, vega, theta, markVol (IV), realVol, openInterest, markPx, ...}, ...]

# Список инструментов:
GET https://www.okx.com/api/v5/public/instruments?instType=OPTION&uly=BTC-USD

# WebSocket (Greeks + IV push):
wss://ws.okx.com:8443/ws/v5/public
{"op": "subscribe", "args": [{"channel": "opt-summary", "uly": "BTC-USD"}]}
```
- Бесплатно, без ключа. 20 req/2s. BTC, ETH, SOL.

### 4. Bybit — дополнительный резерв

```
# Тикеры всех опционов с Greeks:
GET https://api.bybit.com/v5/market/tickers?category=option&baseCoin=BTC
→ [{markPrice, markIv, delta, gamma, vega, theta, openInterest, ...}, ...]

# WebSocket:
wss://stream.bybit.com/v5/public/option
Topic: tickers.BTC-25JUL26-60000-C
```
- Бесплатно, без ключа. 600 req/5s. BTC, ETH, SOL, XRP, DOGE.

### 5. Индексы волатильности

| Индекс | API | Аналог |
|--------|-----|--------|
| **BTC DVOL** | Deribit `GET /public/ticker?instrument_name=BTC_DVOL` | VIX для BTC |
| **ETH DVOL** | Deribit `GET /public/ticker?instrument_name=ETH_DVOL` | VIX для ETH |
| **SOL DVOL** | Deribit `GET /public/ticker?instrument_name=SOL_DVOL` | VIX для SOL |

---

## ПЛАН ИНТЕГРАЦИИ (ЧТО МЕНЯТЬ)

### Фаза 1: config.py — добавить крипто-инструменты

```python
# Новый флаг: источник данных
@dataclass(frozen=True)
class Instrument:
    code: str
    yahoo: str                    # тикер Yahoo (для фондовых)
    options_proxy: str | None     # ETF-прокси (для фондовых)
    demo_price: float
    demo_vol: float
    proxy_experimental: bool = False
    # === НОВЫЕ ПОЛЯ ===
    asset_class: str = "equity"   # "equity" | "crypto"
    deribit_currency: str | None = None  # "BTC" | "ETH" | "SOL"
    binance_symbol: str | None = None    # "BTCUSDT" | "ETHUSDT" | "SOLUSDT"

# Добавить в INSTRUMENTS:
    Instrument("BTCUSD", "BTC-USD", None, 67000.0, 0.55,
               asset_class="crypto", deribit_currency="BTC", binance_symbol="BTCUSDT"),
    Instrument("ETHUSD", "ETH-USD", None, 3500.0,  0.65,
               asset_class="crypto", deribit_currency="ETH", binance_symbol="ETHUSDT"),
    Instrument("SOLUSD", "SOL-USD", None, 180.0,   0.85,
               asset_class="crypto", deribit_currency="SOL", binance_symbol="SOLUSDT"),

# Добавить в VOL_INDEX_TICKERS:
    "btc_dvol": None,   # будет fetched через Deribit, не Yahoo
    "eth_dvol": None,

# Добавить маппинг DVOL:
CRYPTO_VOL_INDEX = {"BTCUSD": "btc_dvol", "ETHUSD": "eth_dvol"}
```

### Фаза 2: data/feeds.py — двойной фид для цены и цепочки

**Новый файл `data/deribit.py`** — адаптер Deribit REST API:

```python
"""Адаптер Deribit REST API для крипто-опционных цепочек."""

import datetime as dt, time, math
import numpy as np
from typing import Optional

DERIBIT_BASE = "https://www.deribit.com/api/v2"

class DeribitFetcher:
    """Получает опционную цепочку, DVOL и индексную цену с Deribit."""

    def __init__(self, session=None):
        self._session = session  # aiohttp или requests session

    def fetch_chain(self, currency: str) -> dict:
        """Полная цепочка ближайшей экспирации → dict совместимый с options.py.

        Возвращает: {strikes, call_mid, put_mid, call_oi, put_oi,
                     call_iv, put_iv, t_years, spot, expiry}
        """
        # 1. Получить все опционы: GET /public/get_book_summary_by_currency
        # 2. Получить spot: GET /public/get_index_price?index_name={currency}_usd
        # 3. Сгруппировать по экспирации, выбрать ближайшую (>= 1 день)
        # 4. Разделить на calls/puts, объединить по страйку
        # 5. Собрать numpy-массивы: strikes, call_mid, put_mid, ...
        # 6. Вернуть dict в том же формате, что и yfinance-путь в feeds.py
        ...

    def fetch_dvol(self, currency: str) -> float | None:
        """Текущий DVOL (годовая implied vol в %)."""
        # GET /public/ticker?instrument_name={currency}_DVOL
        ...

    def fetch_index_price(self, currency: str) -> float | None:
        """Текущая индексная цена (спот)."""
        # GET /public/get_index_price?index_name={currency.lower()}_usd
        ...

    def fetch_term(self, currency: str, spot: float) -> list[tuple[int, float]]:
        """ATM IV по нескольким экспирациям для term-structure."""
        # Из book_summary: для каждой экспирации найти ATM-страйк, взять mark_iv
        ...
```

**Модификация `data/feeds.py` — `MarketData`:**

```python
# В refresh_price():
if self.instrument.asset_class == "crypto":
    # 1. Проверить Binance WS стрим (StreamHub)
    # 2. Если нет — REST: Deribit get_index_price или Binance REST
    ...
else:
    # существующий код yfinance
    ...

# В refresh_chain():
if self.instrument.asset_class == "crypto":
    # Deribit: fetch_chain(currency) → готовый dict
    # _compute_chain_metrics() — БЕЗ ИЗМЕНЕНИЙ (формат одинаковый)
    # proxy_scale = 1.0 (нет прокси)
    ...
else:
    # существующий код yfinance
    ...

# В refresh_vols():
if self.instrument.asset_class == "crypto":
    # Deribit: fetch_dvol(currency) → значение DVOL
    # Записать в self.vols["btc_dvol"] или self.vols["eth_dvol"]
    ...
else:
    # существующий код yfinance
    ...

# В refresh_daily():
if self.instrument.asset_class == "crypto":
    # Binance REST: GET /api/v3/klines?symbol=BTCUSDT&interval=1d&limit=120
    # Собрать bars = {highs, lows, closes}
    ...
else:
    # существующий код yfinance
    ...
```

### Фаза 3: data/stream.py — добавить Binance WS

```python
# Расширить StreamHub: два WebSocket-соединения параллельно
# 1. Yahoo WS (для фондовых тикеров) — существующий код
# 2. Binance WS (для крипто-тикеров) — новое соединение

BINANCE_WS = "wss://stream.binance.com:9443/ws"

# В _run() добавить логику:
# - Разделить self.tickers на yahoo_tickers и binance_symbols
# - Запустить два asyncio.Task: _run_yahoo() и _run_binance()

async def _run_binance(self):
    symbols = [s.lower() for s in self.binance_symbols]
    streams = "/".join(f"{s}@trade" for s in symbols)
    url = f"{BINANCE_WS}/{streams}"
    async with websockets.connect(url, ping_interval=20) as ws:
        async for raw in ws:
            data = json.loads(raw)
            symbol = data.get("s", "").upper()  # "BTCUSDT"
            price = float(data.get("p", 0))
            if symbol and price > 0:
                self.latest[symbol] = (price, time.time())
```

### Фаза 4: engine.py — адаптация

1. **`_proxy_scale()`**: для crypto вернуть `1.0` (нет прокси).
2. **`_seconds_to_session_end()`**: для crypto вернуть `None` или большое значение (24/7 рынок).
3. **`_options_summary()`**: `session_band_abs` — для crypto считать по implied_move без привязки к сессии.
4. **`_filters_payload()`**: для crypto-сетапов фильтры VIX/GVZ не нужны, использовать DVOL.
5. **DemoMarket**: добавить крипто-параметры (высокая вола 0.55-0.85, цены BTC/ETH/SOL).

### Фаза 5: Математические адаптации в core/options.py

**Менять сами функции НЕ НУЖНО** — они source-agnostic. Но учти:

1. **Торговый год**: для крипты 365 дней, не 252. Это влияет на `realized_vol()`:
   ```python
   # Сейчас: annualize=252
   # Для крипты: annualize=365
   # Решение: параметризовать в Instrument или передавать из engine
   ```

2. **Высокая вола**: крипто-волатильность 50-100% годовых vs 15-25% фондовые.
   Убедись, что `prob_band`, `simulate_remainder`, `cone_surface` не ломаются при sigma > 1.0.

3. **Размер контракта Deribit**: опционы котируются в BTC (не USD). Нужен пересчёт:
   ```
   mark_price (BTC) × index_price (USD) = цена в USD
   ```

### Фаза 6: Фронтенд (web/)

1. **Переключатель инструмента** в app.js: добавить BTCUSD, ETHUSD, SOLUSD в dropdown.
2. **Индикатор рынка 24/7**: для крипты убрать "часы до закрытия US-сессии".
3. **Фильтры**: для крипто-сетапов показывать DVOL вместо VIX/GVZ.
4. **Форматирование цен**: BTC ~ 67000 (целые), ETH ~ 3500 (1 знак), SOL ~ 180 (2 знака).
5. **Бейдж «CRYPTO»** рядом с «DEMO» в шапке — чтобы было ясно, что это крипто-инструмент.

### Фаза 7: Сетапы (если нужны — опционально)

Пока крипто-сетапов в стратегии нет. Можно:
- Добавить placeholder-сетапы (Setup 17-19) с n=0 (без встроенной статистики).
- Или позволить трейдеру создавать custom-сетапы через журнал.
- Вся математика prob_band/MC будет работать с журнальной статистикой после 20+ сделок.

---

## ПРИОРИТЕТ РЕАЛИЗАЦИИ

1. **`config.py`** — добавить 3 крипто-инструмента + DVOL тикеры
2. **`data/deribit.py`** — новый файл, адаптер Deribit REST
3. **`data/feeds.py`** — ветвление equity/crypto в refresh_*
4. **`data/stream.py`** — добавить Binance WS параллельно Yahoo WS
5. **`engine.py`** — адаптация session, scale, demo
6. **`web/js/app.js`** — инструмент-свитчер + UI-адаптация
7. **Тесты** — добавить тесты для Deribit-адаптера (mock HTTP)

---

## ОГРАНИЧЕНИЯ И НЮАНСЫ

1. **Deribit API — только с VPS/не из РФ IP**: Deribit блокирует доступ из некоторых юрисдикций.
   Сервер `94.241.171.182` (Timeweb, СПб) может потребовать проверки — запусти
   `curl -s https://www.deribit.com/api/v2/public/get_index_price?index_name=btc_usd`
   с сервера и убедись, что ответ приходит.

2. **Размер контракта**: Deribit опционы BTC = 1 BTC, ETH = 1 ETH. Цены опционов
   приходят в базовой валюте (BTC), нужен пересчёт в USD для GEX и OI-walls.

3. **Экспирации Deribit**: формат `"BTC-28JUL26-100000-C"`. Парсить из instrument_name:
   `{currency}-{expiry_date}-{strike}-{C|P}`.

4. **Greeks**: Deribit возвращает `greeks.gamma` в стандартном виде — можно использовать
   напрямую в `gex_profile` вместо пересчёта через `bs_gamma`.

5. **Память на VPS**: ~750 MB RAM + 1G swap. Крипто-цепочки BTC имеют 200+ страйков —
   numpy-массивы займут ~100KB, не проблема.

6. **Зависимости**: `requests` или `aiohttp` для Deribit REST. `websockets` уже есть.
   Добавить в `pyproject.toml`.

---

## КОНТРОЛЬНЫЕ ТОЧКИ (ПРОВЕРКА)

После реализации убедись:

- [ ] `python -m seiltanzer --demo` запускается, BTCUSD/ETHUSD/SOLUSD видны в переключателе
- [ ] В демо-режиме все крипто-инструменты показывают синтетические данные
- [ ] `python -m seiltanzer --stream` подключается к Binance WS для крипты
- [ ] `python -m seiltanzer --check` тестирует Deribit API для BTC/ETH/SOL
- [ ] API `GET /api/state` возвращает данные для BTCUSD с chain.metrics
- [ ] BL-плотность, GEX, implied_move, skew, term — все рассчитываются для крипты
- [ ] 3D-конус отображается для крипто-сделки
- [ ] Strike Landscape показывает гряду для крипто-цепочки
- [ ] Переключение между NAS100 и BTCUSD не ломает фиды
- [ ] `pytest tests/` — все существующие тесты проходят
- [ ] На VPS `systemctl restart seiltanzer` — сервис поднимается с крипто-поддержкой

---

## СПРАВКА: ТЕКУЩИЕ ЗАВИСИМОСТИ (`pyproject.toml`)

```toml
dependencies = [
    "fastapi >= 0.110",
    "uvicorn[standard] >= 0.29",
    "numpy >= 1.26",
    "pandas >= 2.1",
    "yfinance >= 0.2.40",
    "websockets >= 12",
]
```

Добавить: `"requests >= 2.31"` (или `"aiohttp >= 3.9"` если async).

---

## ЗАПРЕЩЕНО

- НЕ ломать существующие фондовые инструменты (NAS100, SP500, XAU и т.д.)
- НЕ менять формат выходных данных `tick_payload()` / `ridge_payload()`
- НЕ менять математику в `core/options.py` (она source-agnostic)
- НЕ удалять существующие тесты
- НЕ использовать платные API или API требующие API-ключ
- НЕ хардкодить IP-адреса или credentials
