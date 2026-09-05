"""Фиды рыночных данных: цена, дневки, индексы волатильности, опционные цепочки.

Правила честности:
- каждый фид несёт статус: live / delayed / no_data (+ demo), время и текст ошибки;
- при сбое источника значение НЕ выдумывается: остаётся прежнее со статусом
  delayed (недолго) либо no_data;
- демо-режим синтезирует все потоки и явно помечает их demo.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import random
import statistics
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import numpy as np

from ..config import (ALL_INSTRUMENTS, CRYPTO_INSTRUMENTS, CRYPTO_VOL_INDEX,
                      INSTRUMENTS, SIGMA_INDEX_FOR, VOL_INDEX_TICKERS,
                      Instrument, Settings)
from ..core import options as opt
from .cache import DiskCache, production_chain_snapshot
from .deribit import DeribitFetcher

# yfinance шумит в stderr про делистинги (например ^V1X/VDAX недоступен) —
# это ожидаемо и обрабатывается статусом no_data, поэтому глушим его логгер.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

DELAYED_GRACE = 5.0  # во сколько раз можно превысить период опроса до no_data

# The first eight rows preserve the historical/canonical correlation family.
# Additional rows expose every instrument traded by the terminal to the visual
# topology without silently changing the meaning of the trained core aggregate.
CORRELATION_SERIES = (
    ("NAS", "NQ=F"), ("VXN", "^VXN"),
    ("SP500", "ES=F"), ("VIX", "^VIX"),
    ("GOLD", "GC=F"), ("GVZ", "^GVZ"),
    ("OIL", "CL=F"), ("OVX", "^OVX"),
    ("US30", "YM=F"), ("GER40", "^GDAXI"),
    ("UK100", "^FTSE"), ("JPY100", "^N225"),
    ("XAGUSD", "SI=F"), ("EURUSD", "EURUSD=X"),
    ("USDCAD", "CAD=X"),
)
CORRELATION_CORE_ASSETS = tuple(name for name, _ in CORRELATION_SERIES[:8])


def _status_dict(value=None, status="no_data", ts=None, error=None, source=None):
    return {"value": value, "status": status, "ts": ts, "error": error, "source": source}


def _fetch_swissquote_quote(pair: str, timeout: float = 5.0) -> dict:
    """Прямой OTC bid/ask без ключа; OPENROUTER_PROXY здесь не используется."""
    encoded = "/".join(urllib.parse.quote(x, safe="") for x in pair.split("/"))
    url = ("https://forex-data-feed.swissquote.com/public-quotes/"
           f"bboquotes/instrument/{encoded}")
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Seiltanzer/0.1"})
    # OPENROUTER_PROXY — отдельная переменная и urllib её не читает: данный
    # запрос никогда не идёт через пользовательский AI-прокси.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    rows = [p for venue in payload
            for p in venue.get("spreadProfilePrices", [])
            if math.isfinite(float(p.get("bid", 0)))
            and math.isfinite(float(p.get("ask", 0)))
            and float(p["bid"]) > 0 and float(p["ask"]) >= float(p["bid"])]
    if not rows:
        raise RuntimeError(f"Swissquote не вернул bid/ask для {pair}")
    bids = [float(p["bid"]) for p in rows]
    asks = [float(p["ask"]) for p in rows]
    timestamps = [float(v.get("ts", 0)) / 1000 for v in payload if v.get("ts")]
    bid, ask = statistics.median(bids), statistics.median(asks)
    return {"value": (bid + ask) / 2, "bid": bid, "ask": ask,
            "ts": max(timestamps) if timestamps else time.time()}


def _fetch_tradingview_quote(symbol: str, timeout: float = 5.0) -> dict:
    """Последняя цена конкретного broker CFD из TradingView scanner.

    Это snapshot, а не выдуманная конверсия cash index. Символ включает
    поставщика (например OANDA:NAS100USD или FPMARKETS:GER40).
    """
    if not os.environ.get("TRADINGVIEW_AUTH_TOKEN"):
        raise RuntimeError("TRADINGVIEW_AUTH_TOKEN не задан; используется Yahoo fallback")
    return _fetch_tradingview_ws_quote(symbol, timeout)


def _tv_frame(method: str, params: list) -> str:
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return f"~m~{len(payload.encode())}~m~{payload}"


def _tv_payloads(raw: str | bytes):
    """Разбирает один или несколько ~m~length~m~ кадров TradingView."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    pos = 0
    while True:
        start = raw.find("~m~", pos)
        if start < 0:
            return
        split = raw.find("~m~", start + 3)
        if split < 0:
            return
        try:
            size = int(raw[start + 3:split])
        except ValueError:
            pos = split + 3
            continue
        payload_start = split + 3
        payload = raw[payload_start:payload_start + size]
        if len(payload.encode()) < size:
            return
        yield payload
        pos = payload_start + len(payload)


def _fetch_tradingview_ws_quote(symbol: str, timeout: float = 5.0) -> dict:
    """Одноразовый anonymous WebSocket snapshot конкретного broker symbol."""
    try:
        from websockets.sync.client import connect
    except ImportError as e:  # pragma: no cover — dependency обязательна в prod
        raise RuntimeError("websockets не установлен") from e
    session = "qs_" + "".join(random.choice("abcdefghijklmnopqrstuvwxyz")
                                for _ in range(12))
    url = "wss://data.tradingview.com/socket.io/websocket"
    deadline = time.monotonic() + timeout
    try:
        with connect(url, origin="https://data.tradingview.com",
                     open_timeout=timeout, close_timeout=1) as ws:
            # Сервер сначала выдаёт session_id; команды, посланные раньше этого
            # handshake-сообщения, иногда молча игнорируются.
            ws.recv(timeout=max(0.1, deadline - time.monotonic()))
            ws.send(_tv_frame("set_auth_token", [
                os.environ.get("TRADINGVIEW_AUTH_TOKEN", "unauthorized_user_token")]))
            ws.send(_tv_frame("quote_create_session", [session]))
            ws.send(_tv_frame("quote_set_fields", [session, "lp", "bid", "ask",
                                                     "lp_time", "update_mode",
                                                     "description"]))
            ws.send(_tv_frame("quote_add_symbols", [session, symbol,
                                                      {"flags": ["force_permission"]}]))
            ws.send(_tv_frame("quote_fast_symbols", [session, symbol]))
            while time.monotonic() < deadline:
                raw = ws.recv(timeout=max(0.1, deadline - time.monotonic()))
                for payload in _tv_payloads(raw):
                    if payload.startswith("~h~"):
                        ws.send(f"~m~{len(payload.encode())}~m~{payload}")
                        continue
                    try:
                        message = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if message.get("m") != "qsd":
                        continue
                    item = (message.get("p") or [None, {}])[1] or {}
                    if item.get("n") != symbol:
                        continue
                    data = item.get("v") or {}
                    value = float(data.get("lp"))
                    if not math.isfinite(value) or value <= 0:
                        continue
                    result = {"value": value, "ts": time.time(),
                              "update_mode": data.get("update_mode"),
                              "description": data.get("description"),
                              "transport": "stream"}
                    for key in ("bid", "ask"):
                        raw_value = data.get(key)
                        if (raw_value is not None and math.isfinite(float(raw_value))
                                and float(raw_value) > 0):
                            result[key] = float(raw_value)
                    return result
    except TimeoutError as e:
        raise RuntimeError(f"TradingView WebSocket timeout для {symbol}") from e
    raise RuntimeError(f"TradingView WebSocket не вернул {symbol}")


class DemoMarket:
    """Синтетический рынок: GBM-цены, OU-индексы волы, BS-цепочки."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.prices = {c: i.demo_price for c, i in ALL_INSTRUMENTS.items()}
        self.vols = {"vix": 17.5, "gvz": 16.0, "dv1x": 16.5, "evz": 8.0, "vxn": 22.0,
                     "btc_dvol": 52.0, "eth_dvol": 62.0, "sol_dvol": 78.0}
        self._last = time.time()

    def step(self) -> None:
        now = time.time()
        dt_sec = max(now - self._last, 1e-3)
        self._last = now
        dt_y = dt_sec / (365.0 * 24 * 3600)
        for code, inst in ALL_INSTRUMENTS.items():
            sigma = inst.demo_vol
            z = self.rng.gauss(0, 1)
            self.prices[code] *= math.exp(-0.5 * sigma * sigma * dt_y
                                          + sigma * math.sqrt(dt_y) * z * 8.0)
            # *8: демо-время ускорено, чтобы движение было видно на панелях
        for k, anchor, floor in (("vix", 17.5, 9.0), ("gvz", 16.0, 9.0),
                                 ("dv1x", 16.5, 9.0), ("evz", 8.0, 4.0),
                                 ("vxn", 22.0, 12.0), ("btc_dvol", 52.0, 20.0),
                                 ("eth_dvol", 62.0, 25.0), ("sol_dvol", 78.0, 30.0)):
            v = self.vols.get(k, anchor)
            self.vols[k] = max(floor, v + 0.05 * (anchor - v) + self.rng.gauss(0, 0.15))

    def daily_bars(self, code: str, days: int = 60) -> dict:
        inst = ALL_INSTRUMENTS[code]
        rng = random.Random(hash(code) & 0xFFFF)
        closes, highs, lows = [], [], []
        p = self.prices[code] * 0.97
        for _ in range(days):
            drift = rng.gauss(0, inst.demo_vol / math.sqrt(inst.annual_days))
            o = p
            p = p * math.exp(drift)
            hi = max(o, p) * (1 + abs(rng.gauss(0, 0.004)))
            lo = min(o, p) * (1 - abs(rng.gauss(0, 0.004)))
            closes.append(p); highs.append(hi); lows.append(lo)
        # приводим хвост к текущей демо-цене
        scale = self.prices[code] / closes[-1]
        return {"highs": [h * scale for h in highs],
                "lows": [l * scale for l in lows],
                "closes": [c * scale for c in closes]}

    def chain(self, code: str) -> dict | None:
        inst = ALL_INSTRUMENTS[code]
        if inst.options_proxy is None and inst.asset_class != "crypto":
            return None
        spot = self.prices[code]
        base_iv = inst.demo_vol
        iv = base_iv + 0.04 * math.sin(time.time() / 300.0)
        iv_skew = 0.7 * math.sin(time.time() / 240.0)   # меандрирует бычий/медвежий
        return opt.synth_chain(spot, iv, t_years=2.0 / 365.0, n_strikes=41,
                               width=0.10 if inst.asset_class == "crypto" else 0.06,
                               oi_skew=0.5, iv_skew=iv_skew,
                               seed=int(time.time()) // 30)


class MarketData:
    """Все фиды для активного инструмента + индексы волатильности."""

    def __init__(self, settings: Settings, cache: DiskCache):
        self.settings = settings
        self.cache = cache
        self.demo = settings.demo
        self.demo_market = DemoMarket(seed=7) if self.demo else None
        self.stream = None          # StreamHub | None (живой WS-стрим цены)
        self.instrument_code: str = "NAS100"

        self.price = _status_dict()
        self.proxy_price = _status_dict()
        self._price_prev_val: float | None = None   # для детекта «нет тиков» (закрыт рынок)
        self._price_change_ts: float | None = None
        self._last_price_rest_attempt = 0.0
        self._last_broker_rest_attempt = 0.0
        self._last_proxy_rest_attempt = 0.0
        # Yahoo WebSocket нередко отдаёт proxy, но молчит по cash index или
        # активному фьючерсу. Тогда сохраняем синхронную пару instrument↔driver
        # и переносим только последующую ДОХОДНОСТЬ driver.
        # Это не новая «котировка фьючерса», а явно помеченный live mapping.
        self._price_anchor_raw: float | None = None
        self._price_anchor_proxy: float | None = None
        self._price_anchor_driver: str | None = None
        self._price_anchor_ts: float | None = None
        self.daily = {"bars": None, **_status_dict()}
        self.intraday: list[tuple[float, float, float]] = []  # (ts, price, volume)
        self.intraday_ohlcv: list[tuple[float, float, float, float, float, float]] = []
        self.intraday_is_offset: bool = False
        self.vols = {k: _status_dict() for k in VOL_INDEX_TICKERS}
        self.chain = {"metrics": None, **_status_dict()}
        self._chain_error_detail: str | None = None
        self.iv_surface = _status_dict()
        self.correlation = _status_dict()

        if self.demo:
            self._seed_demo_snapshots()

    # -------------------------------------------------------------- helpers

    @property
    def instrument(self) -> Instrument:
        return ALL_INSTRUMENTS[self.instrument_code]

    def _has_direct_price_scale(self) -> bool:
        """Цена сейчас действительно в шкале spot/broker, а не Yahoo fallback."""
        source = str(self.price.get("source") or "")
        return (bool(self.instrument.swissquote_pair and source.startswith("Swissquote OTC"))
                or bool(self.instrument.tradingview_symbol
                        and source.startswith("TradingView snapshot")))

    def set_instrument(self, code: str) -> None:
        if code not in ALL_INSTRUMENTS:
            raise ValueError(f"неизвестный инструмент: {code}")
        if code != self.instrument_code:
            self.instrument_code = code
            self.price = _status_dict()
            self.proxy_price = _status_dict()
            self._price_prev_val = None
            self._price_change_ts = None
            self._last_price_rest_attempt = 0.0
            self._last_broker_rest_attempt = 0.0
            self._last_proxy_rest_attempt = 0.0
            self._price_anchor_raw = None
            self._price_anchor_proxy = None
            self._price_anchor_driver = None
            self._price_anchor_ts = None
            self.intraday = []
            self.daily = {"bars": None, **_status_dict()}
            self.chain = {"metrics": None, **_status_dict()}
            self.iv_surface = _status_dict()

    def _mark_fail(self, d: dict, poll_sec: float, err: str) -> None:
        d["error"] = err[:200]
        if d["ts"] is not None and time.time() - d["ts"] < poll_sec * DELAYED_GRACE:
            d["status"] = "delayed"
        else:
            d["status"] = "no_data"
            d["value"] = None

    # порог «холостого хода» цены: столько секунд без изменения котировки трактуем
    # как отсутствие тиков (рынок закрыт/неторговое время). Кэш-индексы вне сессии
    # и фьючерсы в перерыв возвращают одну и ту же цену — это честнее пометить, чем
    # рисовать зелёный LIVE у замершего числа.
    PRICE_IDLE_SEC = 120.0
    PRICE_ANCHOR_REFRESH_SEC = 60.0
    PRICE_ANCHOR_MAX_AGE_SEC = 6 * 3600.0

    def _annotate_freshness(self) -> None:
        """Проставляет idle_secs/fresh: цена live, но не двигается → рынок стоит."""
        d = self.price
        v = d.get("value")
        if v is None or d.get("status") != "live":
            return
        now = time.time()
        if self._price_prev_val is None or abs(v - self._price_prev_val) > 1e-12:
            self._price_prev_val = v
            self._price_change_ts = now
        idle = now - (self._price_change_ts or now)
        d["idle_secs"] = round(idle, 1)
        d["fresh"] = idle <= self.PRICE_IDLE_SEC

    def _fresh_proxy_stream(self) -> float | None:
        proxy = self.instrument.options_proxy
        if self.stream is None or proxy is None:
            return None
        return self.stream.fresh(proxy, max_age=8.0)

    def _price_driver_tickers(self) -> tuple[str, ...]:
        """Тиковые proxy для цены; option-proxy — совместимый default."""
        if self.instrument.live_price_drivers:
            return self.instrument.live_price_drivers
        proxy = self.instrument.options_proxy
        return (proxy,) if proxy else ()

    def _fresh_price_driver(self, preferred: str | None = None
                            ) -> tuple[str, float] | None:
        if self.stream is None:
            return None
        tickers = (preferred,) if preferred else self._price_driver_tickers()
        for ticker in tickers:
            if not ticker:
                continue
            value = self.stream.fresh(ticker, max_age=8.0)
            if value is not None:
                return ticker, float(value)
        return None

    def _set_price_anchor(self, raw: float, proxy: float | None,
                          ts: float | None = None,
                          driver_ticker: str | None = None) -> None:
        if proxy is None or not math.isfinite(proxy) or proxy <= 0:
            return
        if not math.isfinite(raw) or raw <= 0:
            return
        if driver_ticker is None:
            drivers = self._price_driver_tickers()
            driver_ticker = drivers[0] if drivers else None
        if driver_ticker is None:
            return
        self._price_anchor_raw = float(raw)
        self._price_anchor_proxy = float(proxy)
        self._price_anchor_driver = driver_ticker
        self._price_anchor_ts = float(ts if ts is not None else time.time())

    def _mapped_proxy_tick(self, now: float | None = None) -> dict | None:
        """Живое изменение инструмента из stream-доходности ETF-прокси.

        Уровень инструмента берётся только из его собственной REST/stream
        котировки. Proxy переносит изменение между переякориваниями; направление
        `inverse` меняет знак доходности. Старый якорь не используется бесконечно.
        """
        now = time.time() if now is None else now
        proxy = self._price_anchor_driver
        live_driver = self._fresh_price_driver(preferred=proxy)
        sp = live_driver[1] if live_driver else None
        if (proxy is None or sp is None or self._price_anchor_raw is None
                or self._price_anchor_proxy is None or self._price_anchor_ts is None):
            return None
        age = now - self._price_anchor_ts
        if age > self.PRICE_ANCHOR_MAX_AGE_SEC:
            return None
        ratio = sp / self._price_anchor_proxy
        if not math.isfinite(ratio) or ratio <= 0:
            return None
        if self.instrument.proxy_transform == "inverse":
            value = self._price_anchor_raw / ratio
        else:
            value = self._price_anchor_raw * ratio
        if not math.isfinite(value) or value <= 0:
            return None
        return {
            "value": float(value),
            "status": "live",
            "ts": now,
            "error": None,
            "source": (
                f"stream {proxy} return → {self.instrument.yahoo} "
                f"({self.instrument.proxy_transform} derived)"
            ),
            "derived": True,
            "driver_ticker": proxy,
            "driver_experimental": proxy != self.instrument.options_proxy,
            "anchor_ticker": self.instrument.yahoo,
            "anchor_age_sec": round(max(age, 0.0), 1),
            "anchor_value": self._price_anchor_raw,
            "driver_value": float(sp),
        }

    # ---------------------------------------------------------------- price

    def refresh_proxy_price(self) -> None:
        """Цена опционного прокси для синхронного moneyness-преобразования.

        Цепочка может обновляться раз в несколько минут, но QQQ/GLD/другой ETF
        доступен в том же бесплатном тиковом стриме. Поэтому форма опционов
        остаётся снимком, а её положение относительно текущей цены оживает
        между снимками без выдумывания новых опционных котировок.
        """
        if self.instrument.asset_class == "crypto":
            p = self.price.get("value")
            st = self.price.get("status") or "live"
            self.proxy_price = _status_dict(p, st, time.time(), source="direct scale (crypto native)")
            return
        proxy = self.instrument.options_proxy
        if proxy is None:
            self.proxy_price = _status_dict(error="у инструмента нет опционного прокси")
            return
        if self.demo:
            # В демо цепочка строится сразу в шкале базового инструмента.
            self.proxy_price = _status_dict(
                self.demo_market.prices[self.instrument_code], "demo", time.time(),
                source=f"demo proxy {proxy}")
            return
        if self.stream is not None:
            sp = self.stream.fresh(proxy, max_age=8.0)
            if sp is not None:
                self.proxy_price = _status_dict(
                    sp, "live", time.time(), source=f"stream {proxy}")
                return
            if self.proxy_price.get("status") == "live":
                self.proxy_price["status"] = "delayed"
                self.proxy_price["error"] = "stream tick stale; REST fallback"
        # Не долбим Yahoo отдельным REST-запросом чаще заданного периода, в том
        # числе после ошибки или при пропавшем stream.
        now = time.time()
        if now - self._last_proxy_rest_attempt < self.settings.proxy_poll_sec:
            return
        self._last_proxy_rest_attempt = now
        try:
            import yfinance as yf
            t = yf.Ticker(proxy)
            p = None
            try:
                p = float(t.fast_info.last_price)
            except Exception:
                pass
            if p is None or not math.isfinite(p) or p <= 0:
                hist = t.history(period="1d", interval="1m")
                if len(hist) == 0:
                    raise RuntimeError("Yahoo вернул пустую историю прокси")
                p = float(hist["Close"].iloc[-1])
            self.proxy_price = _status_dict(
                p, "delayed", time.time(),
                source=f"yfinance REST {proxy} (indicative)")
        except Exception as e:  # noqa: BLE001
            self._mark_fail(self.proxy_price, self.settings.proxy_poll_sec, str(e))

    def refresh_price(self) -> None:
        if self.demo:
            self.demo_market.step()
            p = self.demo_market.prices[self.instrument_code]
            now = time.time()
            self.price = _status_dict(p, "demo", now, source="demo GBM")
            self.refresh_proxy_price()
            self.intraday.append((now, p, abs(random.gauss(1000, 300))))
            cutoff = now - 8 * 3600
            self.intraday = [x for x in self.intraday if x[0] > cutoff]
            return
        if self.instrument.asset_class == "crypto":
            now = time.time()
            binance_sym = self.instrument.binance_symbol or "BTCUSDT"
            # 1. Проверяем тиковый Binance WebSocket стрим
            if self.stream is not None:
                sp = self.stream.fresh(binance_sym, max_age=8.0)
                if sp is not None:
                    self.price = _status_dict(
                        sp, "live", now, source=f"Binance WS {binance_sym}")
                    self.price.update({
                        "derived": False,
                        "instrument_type": "crypto_spot",
                    })
                    self._annotate_freshness()
                    self.refresh_proxy_price()
                    self.intraday.append((now, sp, 0.0))
                    self.intraday = [x for x in self.intraday if x[0] > now - 8 * 3600]
                    return

            # 2. REST fallback: Deribit Index или Binance REST
            if now - self._last_price_rest_attempt < self.settings.price_poll_sec:
                return
            self._last_price_rest_attempt = now
            try:
                curr = self.instrument.deribit_currency or "BTC"
                fetcher = getattr(self, "_deribit_fetcher", None)
                if fetcher is None:
                    fetcher = DeribitFetcher()
                    self._deribit_fetcher = fetcher
                p = fetcher.fetch_index_price(curr)
                if p is None or p <= 0:
                    import httpx
                    client = httpx.Client(timeout=4.0)
                    r = client.get(f"https://api.binance.com/api/v3/ticker/price?symbol={binance_sym}")
                    if r.status_code == 200:
                        p = float(r.json().get("price", 0.0))
                if p is None or p <= 0:
                    raise RuntimeError("не удалось получить крипто-котировку с Deribit/Binance")
                self.price = _status_dict(
                    p, "live", now, source=f"Binance/Deribit REST {binance_sym}")
                self.price.update({
                    "derived": False,
                    "instrument_type": "crypto_spot",
                })
                self._annotate_freshness()
                self.refresh_proxy_price()
                self.intraday.append((now, p, 0.0))
                self.intraday = [x for x in self.intraday if x[0] > now - 8 * 3600]
                return
            except Exception as e:
                self._mark_fail(self.price, self.settings.price_poll_sec, str(e))
                return
        broker_error = None
        broker_symbol = self.instrument.tradingview_symbol
        if broker_symbol:
            now = time.time()
            if now - self._last_broker_rest_attempt < self.settings.price_poll_sec:
                return
            self._last_broker_rest_attempt = now
            try:
                quote = _fetch_tradingview_quote(broker_symbol)
                mode = str(quote.get("update_mode") or "").lower()
                status = ("delayed" if "delayed" in mode or "endofday" in mode
                          else "live")
                transport = quote.get("transport") or "snapshot"
                self.price = _status_dict(
                    quote["value"], status, quote["ts"],
                    source=f"TradingView {transport} {broker_symbol}")
                self.price.update({
                    "derived": False, "instrument_type": "broker_cfd",
                    "update_mode": quote.get("update_mode"),
                    "description": quote.get("description"),
                })
                for key in ("bid", "ask"):
                    if key in quote:
                        self.price[key] = quote[key]
                if "bid" in quote and "ask" in quote:
                    self.price["spread"] = quote["ask"] - quote["bid"]
                self._annotate_freshness()
                self.intraday.append((now, quote["value"], 0.0))
                self.intraday = [x for x in self.intraday if x[0] > now - 8 * 3600]
                return
            except Exception as e:  # Yahoo cash остаётся честным fallback
                broker_error = str(e)[:200]
        # Для OTC/spot инструментов нельзя показывать Yahoo futures: у XAU их
        # basis меняется с экспирацией и сейчас достигает десятков долларов.
        pair = self.instrument.swissquote_pair
        if pair:
            now = time.time()
            if now - self._last_price_rest_attempt < self.settings.price_poll_sec:
                return
            self._last_price_rest_attempt = now
            try:
                quote = _fetch_swissquote_quote(pair)
                age = max(0.0, now - float(quote["ts"]))
                status = "live" if age <= 30 else "delayed"
                self.price = _status_dict(
                    quote["value"], status, quote["ts"],
                    source=f"Swissquote OTC {pair} bid/ask")
                self.price.update({"bid": quote["bid"], "ask": quote["ask"],
                                   "spread": quote["ask"] - quote["bid"],
                                   "derived": False, "instrument_type": "spot_otc"})
                self._annotate_freshness()
                self.intraday.append((now, quote["value"], 0.0))
                self.intraday = [x for x in self.intraday if x[0] > now - 8 * 3600]
            except Exception as e:  # неверный futures fallback хуже, чем no_data
                self._mark_fail(self.price, self.settings.price_poll_sec, str(e))
            return
        # живой WebSocket-стрим цены (если включён и есть свежий тик) — приоритет
        if self.stream is not None:
            sp = self.stream.fresh(self.instrument.yahoo, max_age=8.0)
            if sp is not None:
                now = time.time()
                self.price = _status_dict(sp, "live", now,
                                          error=broker_error,
                                          source=(f"stream {self.instrument.yahoo}"
                                                  + (" (broker fallback)"
                                                     if broker_error else "")))
                self.price["derived"] = False
                self._annotate_freshness()
                self.intraday.append((now, sp, 0.0))
                self.intraday = [x for x in self.intraday if x[0] > now - 8 * 3600]
                return
        now = time.time()
        if now - self._last_price_rest_attempt < self.settings.price_poll_sec:
            return
        self._last_price_rest_attempt = now
        try:
            import yfinance as yf
            t = yf.Ticker(self.instrument.yahoo)
            p = None
            try:
                p = float(t.fast_info.last_price)
            except Exception:
                pass
            if p is None or not math.isfinite(p) or p <= 0:
                hist = t.history(period="1d", interval="1m")
                if len(hist) == 0:
                    raise RuntimeError("Yahoo вернул пустую историю")
                p = float(hist["Close"].iloc[-1])
            anchor_now = time.time()
            self.price = _status_dict(
                p, "delayed", anchor_now,
                error=broker_error,
                source=(f"yfinance REST {self.instrument.yahoo} (indicative)"
                        + ("; broker feed fallback" if broker_error else "")))
            self.price["derived"] = False
            self._annotate_freshness()
        except Exception as e:  # noqa: BLE001 — фид обязан пережить любой сбой источника
            self._mark_fail(self.price, self.settings.price_poll_sec, str(e))

    def refresh_intraday(self) -> None:
        """1m-бары дня для VWAP (объём нужен; у кэш-индексов его нет — честно None)."""
        if self.demo:
            return
        if self.instrument.asset_class == "crypto":
            try:
                import httpx
                binance_sym = self.instrument.binance_symbol or "BTCUSDT"
                client = httpx.Client(timeout=6.0)
                resp = client.get(
                    f"https://api.binance.com/api/v3/klines?symbol={binance_sym}&interval=1m&limit=120"
                )
                if resp.status_code == 200:
                    raw_bars = resp.json()
                    self.intraday_is_offset = False
                    self.intraday_ohlcv = [
                        (float(b[0]) / 1000.0, float(b[1]), float(b[2]), float(b[3]), float(b[4]), float(b[5]))
                        for b in raw_bars
                    ]
                    self.intraday = [
                        (float(b[0]) / 1000.0, float(b[4]), float(b[5]))
                        for b in raw_bars
                    ]
            except Exception:
                pass
            return
        try:
            import yfinance as yf
            hist = yf.Ticker(self.instrument.yahoo).history(period="1d", interval="1m")
            if len(hist):
                offset = 0.0
                if self.instrument.swissquote_pair or self.instrument.tradingview_symbol:
                    if self.price.get("value") is None:
                        return  # не публикуем чужую шкалу под именем broker/spot
                    # Фьючерсные бары служат формой/объёмом, но вся шкала
                    # переносится в текущий spot одним внутридневным basis.
                    if self._has_direct_price_scale():
                        offset = (float(self.price["value"])
                                  - float(hist["Close"].iloc[-1]))
                self.intraday_is_offset = (offset != 0.0)
                self.intraday_ohlcv = [
                    (ts.timestamp(), float(r["Open"]) + offset, float(r["High"]) + offset, float(r["Low"]) + offset, float(r["Close"]) + offset, float(r["Volume"]))
                    for ts, r in hist.iterrows()]
                self.intraday = [
                    (ts.timestamp(), float(r["Close"]) + offset, float(r["Volume"]))
                    for ts, r in hist.iterrows()]
        except Exception:
            pass  # VWAP просто останется в no_data

    def vwap(self) -> float | None:
        """VWAP дня: sum(p*v)/sum(v); None, если объёмов нет (например, ^NDX)."""
        if not self.intraday:
            return None
        v = sum(x[2] for x in self.intraday)
        if v <= 0:
            return None
        return sum(x[1] * x[2] for x in self.intraday) / v

    def day_range(self) -> tuple[float, float] | None:
        if not self.intraday:
            return None
        ps = [x[1] for x in self.intraday]
        return min(ps), max(ps)

    # ---------------------------------------------------------------- daily

    def refresh_daily(self) -> None:
        if self.demo:
            self.daily = {"bars": self.demo_market.daily_bars(self.instrument_code),
                          **_status_dict(True, "demo", time.time(), source="demo GBM")}
            return
        if self.instrument.asset_class == "crypto":
            try:
                import httpx
                binance_sym = self.instrument.binance_symbol or "BTCUSDT"
                client = httpx.Client(timeout=8.0)
                resp = client.get(
                    f"https://api.binance.com/api/v3/klines?symbol={binance_sym}&interval=1d&limit=120"
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Binance klines status: {resp.status_code}")
                raw_bars = resp.json()
                if len(raw_bars) < 25:
                    raise RuntimeError(f"мало дневных баров Binance: {len(raw_bars)}")
                bars = {
                    "highs": [float(b[2]) for b in raw_bars],
                    "lows": [float(b[3]) for b in raw_bars],
                    "closes": [float(b[4]) for b in raw_bars],
                }
                self.daily = {
                    "bars": bars,
                    **_status_dict(True, "live", time.time(),
                                   source=f"Binance REST {binance_sym} 1d klines")
                }
                self.cache.put(f"daily:{self.instrument_code}", bars)
                return
            except Exception as e:
                cached = self.cache.get(f"daily:{self.instrument_code}", max_age=3 * 24 * 3600)
                if cached:
                    bars, ts = cached
                    self.daily = {"bars": bars, **_status_dict(True, "delayed", ts,
                                                               error=str(e)[:200],
                                                               source="кэш дневок")}
                else:
                    self.daily = {"bars": None, **_status_dict(error=str(e)[:200])}
                return
        try:
            import yfinance as yf
            hist = yf.Ticker(self.instrument.yahoo).history(period="4mo", interval="1d")
            if len(hist) < 25:
                raise RuntimeError(f"мало дневных баров: {len(hist)}")
            bars = {"highs": hist["High"].tolist(),
                    "lows": hist["Low"].tolist(),
                    "closes": hist["Close"].tolist()}
            if self.instrument.swissquote_pair or self.instrument.tradingview_symbol:
                if self.price.get("value") is None:
                    raise RuntimeError("нет прямого spot-якоря для дневной шкалы")
                if self._has_direct_price_scale():
                    offset = float(self.price["value"]) - float(bars["closes"][-1])
                    bars = {key: [float(x) + offset for x in values]
                            for key, values in bars.items()}
            self.daily = {"bars": bars,
                          **_status_dict(True, "live", time.time(),
                                         source=(f"yfinance {self.instrument.yahoo} 1d "
                                                 "→ current quote basis"))}
            self.cache.put(f"daily:{self.instrument.yahoo}", bars)
        except Exception as e:  # noqa: BLE001
            cached = self.cache.get(f"daily:{self.instrument.yahoo}",
                                    max_age=3 * 24 * 3600)
            if cached:
                bars, ts = cached
                self.daily = {"bars": bars, **_status_dict(True, "delayed", ts,
                                                           error=str(e)[:200],
                                                           source="кэш дневок")}
            else:
                self.daily = {"bars": None, **_status_dict(error=str(e)[:200])}

    # ----------------------------------------------------------- vol indices

    def refresh_vols(self) -> None:
        if self.demo:
            for k in self.vols:
                self.vols[k] = _status_dict(round(self.demo_market.vols[k], 2),
                                            "demo", time.time(), source="demo OU")
            return
        import yfinance as yf
        for key, ticker in VOL_INDEX_TICKERS.items():
            if ticker is None:
                # Крипто DVOL индекс с Deribit (btc_dvol, eth_dvol, sol_dvol)
                curr = key.split("_")[0].upper()
                try:
                    fetcher = getattr(self, "_deribit_fetcher", None)
                    if fetcher is None:
                        fetcher = DeribitFetcher()
                        self._deribit_fetcher = fetcher
                    dvol_val = fetcher.fetch_dvol(curr)
                    if dvol_val is not None and dvol_val > 0:
                        self.vols[key] = _status_dict(round(float(dvol_val), 2), "live",
                                                      time.time(),
                                                      source=f"Deribit {curr} DVOL")
                        self.vols[key]["timeframe"] = "1m"
                        self.vols[key]["delay_hint_sec"] = 60
                    else:
                        raise RuntimeError(f"нет данных DVOL для {curr}")
                except Exception as e:
                    self._mark_fail(self.vols[key], self.settings.vol_poll_sec, str(e))
                continue
            try:
                # 15m — бесплатный динамический контекст. Это не тиковый опцион и
                # не точная 4H-последовательность стратегии; источник/таймфрейм
                # явно возвращаются в payload.
                hist = yf.Ticker(ticker).history(period="5d", interval="15m")
                if len(hist) == 0:
                    raise RuntimeError("пусто")
                self.vols[key] = _status_dict(float(hist["Close"].iloc[-1]), "delayed",
                                              time.time(),
                                              source=f"yfinance {ticker} 15m")
                self.vols[key]["timeframe"] = "15m"
                self.vols[key]["delay_hint_sec"] = 900
            except Exception as e:  # noqa: BLE001
                self._mark_fail(self.vols[key], self.settings.vol_poll_sec, str(e))

    # ----------------------------------------------------------------- chain

    def refresh_iv_surface(self) -> None:
        """Сетка IV для нескольких экспираций -> поверхность (3D)."""
        if self.instrument.asset_class == "crypto":
            curr = self.instrument.deribit_currency or "BTC"
            if self.demo:
                days_arr = [0.05, 0.1, 0.25, 0.5, 1.0]
                surface = []
                now_ts = time.time()
                for i, d in enumerate(days_arr):
                    iv_base = self.instrument.demo_vol + 0.06 * math.sin(now_ts / 60.0) + (i * 0.005)
                    iv_skew = 0.8 * math.sin(now_ts / 45.0)
                    spot = self.demo_market.prices[self.instrument_code]
                    chain = opt.synth_chain(spot, iv_base, max(0.001, d / 365.0), n_strikes=41, width=0.10, r=0.05, iv_skew=iv_skew, seed=int(now_ts)//10 + i)
                    surface.append({
                        "days": d, "expiry": f"{d*24:.1f}h",
                        "strikes": chain["strikes"].tolist(), "ivs": chain["call_iv"].tolist(),
                        "spot_at_snapshot": spot,
                    })
                self.iv_surface = _status_dict(value=surface, status="demo", ts=now_ts, source="synthetic crypto micro-3D")
                return

            try:
                fetcher = getattr(self, "_deribit_fetcher", None)
                if fetcher is None:
                    fetcher = DeribitFetcher()
                    self._deribit_fetcher = fetcher
                matrix = fetcher.fetch_full_options_matrix(curr)
                spot = matrix.get("spot") or 0.0
                now_ts = matrix.get("ts", time.time())
                surface = []
                for exp_name, opts in sorted(matrix.get("expiries", {}).items(), key=lambda x: len(x[1]), reverse=True)[:5]:
                    calls = [o for o in opts if o.get("type") == "C" and (o.get("iv") or 0) > 0]
                    if len(calls) < 3:
                        continue
                    calls.sort(key=lambda o: o["strike"])
                    try:
                        exp_dt = dt.datetime.strptime(exp_name, "%d%b%y").replace(
                            hour=8, minute=0, second=0, tzinfo=dt.timezone.utc
                        )
                        days = max(0.1, (exp_dt.timestamp() - now_ts) / 86400.0)
                    except Exception:
                        days = 1.0
                    surface.append({
                        "days": round(days, 2),
                        "expiry": exp_name,
                        "strikes": [c["strike"] for c in calls],
                        "ivs": [c["iv"] / 100.0 for c in calls],
                        "spot_at_snapshot": spot,
                    })
                surface.sort(key=lambda s: s["days"])
                self.iv_surface = _status_dict(
                    value=surface, status="live", ts=now_ts,
                    source=f"Deribit {curr} 3D surface")
            except Exception as e:
                self.iv_surface = _status_dict(status="no_data", error=str(e)[:200])
            return

        proxy = self.instrument.options_proxy
        if proxy is None:
            self.iv_surface = _status_dict(status="no_data", error="нет опционов")
            return
        
        if self.demo:
            # Для демо генерируем микро-поверхность (внутридневную)
            days_arr = [0.05, 0.1, 0.25, 0.5, 1.0]
            surface = []
            now_ts = time.time()
            for i, d in enumerate(days_arr):
                # Быстрое "дыхание" волы для интрадей (каждую минуту меняется)
                iv_base = 0.16 + 0.06 * math.sin(now_ts / 60.0) + (i * 0.005)
                # Быстро меняющийся скью
                iv_skew = 0.8 * math.sin(now_ts / 45.0)
                spot = self.demo_market.prices[self.instrument_code]
                chain = opt.synth_chain(spot, iv_base, max(0.001, d/365.0), n_strikes=41, width=0.05, r=0.05, iv_skew=iv_skew, seed=int(now_ts)//10 + i)
                strikes = chain["strikes"].tolist()
                ivs = chain["call_iv"].tolist() # Используем call_iv для поверхности
                surface.append({
                    "days": d, "expiry": f"{d*24:.1f}h",
                    "strikes": strikes, "ivs": ivs,
                    "spot_at_snapshot": spot,
                })
            
            self.iv_surface = _status_dict(
                value=surface, status="demo", ts=now_ts,
                source="synthetic micro-3D")
            return

        try:
            import yfinance as yf
            t = yf.Ticker(proxy)
            expiries = t.options
            if not expiries:
                raise RuntimeError("нет экспираций")

            # Берем до 3 ближайших экспираций (Micro-surface фокус)
            surface = []
            now = dt.datetime.now(dt.timezone.utc)
            spot_snapshot = float(t.fast_info.last_price)
            if not math.isfinite(spot_snapshot) or spot_snapshot <= 0:
                raise RuntimeError("нет валидного spot для IV surface")
            for expiry in expiries[:3]:
                exp_dt = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(hour=21, tzinfo=dt.timezone.utc)
                days = max((exp_dt - now).total_seconds(), 3600.0) / (24 * 3600)

                chain = t.option_chain(expiry)
                calls = chain.calls
                strikes_raw = calls["strike"].to_numpy(dtype=float)
                ivs_raw = calls["impliedVolatility"].to_numpy(dtype=float)
                ok = (
                    np.isfinite(strikes_raw) & np.isfinite(ivs_raw)
                    & (strikes_raw > 0) & (ivs_raw > 0) & (ivs_raw < 5.0)
                )
                strikes = strikes_raw[ok].tolist()
                ivs = ivs_raw[ok].tolist()
                if len(strikes) < 3:
                    continue
                surface.append({"days": round(days, 2), "expiry": expiry,
                                "strikes": strikes, "ivs": ivs,
                                "spot_at_snapshot": spot_snapshot})
            if not surface:
                raise RuntimeError("нет валидных IV-точек")

            self.iv_surface = _status_dict(value=surface, status="delayed",
                                           ts=time.time(),
                                           source=f"yfinance {proxy} options")
            self.iv_surface["delay_hint_sec"] = 900
        except Exception as e:
            self._mark_fail(self.iv_surface, self.settings.chain_poll_sec * 3, f"IV surface ошибка: {e}")

    def refresh_correlation(self) -> None:
        """Rolling cross-asset regime: 5m correlation versus 3-month baseline."""
        names = [name for name, _ in CORRELATION_SERIES]
        tickers = [ticker for _, ticker in CORRELATION_SERIES]
        now_ts = time.time()

        if self.demo:
            n = len(tickers)
            baseline = np.eye(n)
            short = np.eye(n)
            t = now_ts / 180.0
            for i in range(n):
                for j in range(i + 1, n):
                    base = (-0.55 if (i, j) in {(0, 1), (2, 3), (4, 5), (6, 7)}
                            else 0.68 if (i, j) == (0, 2) else
                            0.22 * math.sin(i * 1.7 + j))
                    live = max(-0.98, min(0.98, base + 0.24 * math.sin(t + i * 0.9 + j * 1.3)))
                    baseline[i, j] = baseline[j, i] = base
                    short[i, j] = short[j, i] = live
            delta = short - baseline
            self.correlation = _status_dict(
                value={"assets": names, "matrix": short.tolist(),
                       "matrix_short": short.tolist(),
                       "matrix_baseline": baseline.tolist(),
                       "matrix_delta": delta.tolist(),
                       "core_assets": list(CORRELATION_CORE_ASSETS),
                       "observations_short": [96] * n,
                       "short_window": "5m × 96", "baseline_window": "3mo × 1d",
                       "asof": now_ts},
                status="demo",
                ts=now_ts,
                source="synthetic rolling regime"
            )
            return

        try:
            import yfinance as yf

            def closes_from(data):
                if data is None or len(data) == 0:
                    return None
                cols = data.columns
                if getattr(cols, "nlevels", 1) > 1:
                    if "Close" in cols.get_level_values(0):
                        frame = data["Close"]
                    elif "Close" in cols.get_level_values(1):
                        frame = data.xs("Close", axis=1, level=1)
                    else:
                        return None
                elif "Close" in cols:
                    frame = data[["Close"]].rename(columns={"Close": tickers[0]})
                else:
                    return None
                return frame.reindex(columns=tickers)

            def pair_matrix(returns, min_obs: int):
                n = len(tickers)
                mat = [[None] * n for _ in range(n)]
                counts = [0] * n
                if returns is None or len(returns) == 0:
                    return mat, counts
                counts = [int(returns[t].notna().sum()) if t in returns else 0 for t in tickers]
                for i, t1 in enumerate(tickers):
                    mat[i][i] = 1.0 if counts[i] >= min_obs else None
                    for j in range(i + 1, n):
                        pair = returns[[t1, tickers[j]]].dropna()
                        val = (
                            float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))
                            if len(pair) >= min_obs else None
                        )
                        if val is not None and not math.isfinite(val):
                            val = None
                        mat[i][j] = mat[j][i] = val
                return mat, counts

            intraday_data = yf.download(
                tickers, period="5d", interval="5m", progress=False,
                auto_adjust=False, threads=False)
            daily_data = yf.download(
                tickers, period="3mo", interval="1d", progress=False,
                auto_adjust=False, threads=False)
            intraday = closes_from(intraday_data)
            daily = closes_from(daily_data)
            intraday_ret = (
                intraday.ffill(limit=2).pct_change(fill_method=None).tail(96)
                if intraday is not None else None)
            daily_ret = (
                daily.pct_change(fill_method=None).tail(60)
                if daily is not None else None)
            short, observations = pair_matrix(intraday_ret, 12)
            baseline, _ = pair_matrix(daily_ret, 20)
            matrix, delta = [], []
            valid_dynamic = 0
            for i in range(len(tickers)):
                row, drow = [], []
                for j in range(len(tickers)):
                    sv, bv = short[i][j], baseline[i][j]
                    if sv is not None and bv is not None:
                        valid_dynamic += int(i < j)
                        row.append(sv)
                        drow.append(sv - bv)
                    else:
                        row.append(bv if bv is not None else (
                            1.0 if i == j and observations[i] >= 12 else None))
                        drow.append(None)
                matrix.append(row)
                delta.append(drow)
            if valid_dynamic == 0:
                raise RuntimeError("нет пар с достаточным числом 5m наблюдений")

            self.correlation = _status_dict(
                value={"assets": names, "matrix": matrix,
                       "matrix_short": short,
                       "matrix_baseline": baseline,
                       "matrix_delta": delta,
                       "core_assets": list(CORRELATION_CORE_ASSETS),
                       "observations_short": observations,
                       "short_window": "5m × 96",
                       "baseline_window": "3mo × 1d",
                       "dynamic_pairs": valid_dynamic,
                       "asof": now_ts},
                status="delayed",
                ts=now_ts,
                source="yfinance rolling 5m vs daily baseline"
            )
        except Exception as e:
            self._mark_fail(self.correlation, 300.0, f"Correlation error: {e}")

    def refresh_chain(self) -> None:
        """Цепочка ближайшей экспирации -> implied move, BL-плотность, GEX.

        Снапшот уходит в кэш — история снапшотов питает Strike Landscape.
        """
        if self.instrument.asset_class == "crypto":
            curr = self.instrument.deribit_currency or "BTC"
            if self.demo:
                raw = self.demo_market.chain(self.instrument_code)
                spot = self.demo_market.prices[self.instrument_code]
                term = self._demo_term()
                try:
                    metrics = self._compute_chain_metrics(
                        raw, spot, curr, demo=True,
                        experimental=False, term=term)
                    self.chain = {"metrics": metrics,
                                  **_status_dict(True, "demo", time.time(),
                                                 source="synthetic BS chain")}
                    self.cache.add_chain_snapshot(curr, metrics)
                except ValueError as e:
                    self.chain = {"metrics": None, **_status_dict(error=str(e))}
                return

            try:
                fetcher = getattr(self, "_deribit_fetcher", None)
                if fetcher is None:
                    fetcher = DeribitFetcher()
                    self._deribit_fetcher = fetcher

                raw = fetcher.fetch_chain(curr)
                if raw is None:
                    raise RuntimeError(f"Deribit API не вернул цепочку для {curr}")
                spot = raw["spot"]
                term_points = fetcher.fetch_term_structure(curr, spot)
                term = opt.term_structure(term_points) if len(term_points) >= 2 else None
                metrics = self._compute_chain_metrics(
                    raw, spot, curr, demo=False,
                    experimental=False, term=term)
                self.chain = {"metrics": metrics,
                              **_status_dict(True, "live", time.time(),
                                             source=f"Deribit {curr} options {raw.get('expiry')}")}
                self.chain["delay_hint_sec"] = 60
                self.cache.add_chain_snapshot(curr, metrics)
            except Exception as e:
                snaps = [
                    snap for snap in self.cache.chain_snapshots(curr, limit=60)
                    if production_chain_snapshot(snap)
                ]
                if snaps and time.time() - snaps[-1]["ts"] < 24 * 3600:
                    self.chain = {"metrics": snaps[-1],
                                  **_status_dict(True, "delayed", snaps[-1]["ts"],
                                                 error=str(e)[:200], source="кэш цепочки"),
                                  "cache_fallback": {
                                      "used": True,
                                      "snapshot_provenance": "explicit_real_demo_false",
                                  }}
                else:
                    self.chain = {
                        "metrics": None,
                        **_status_dict(error=str(e)[:200]),
                        "cache_fallback": {
                            "used": False,
                            "reason": "no_explicitly_real_snapshot",
                            "demo_or_unverified_rejected": True,
                        },
                    }
            return
        proxy = self.instrument.options_proxy
        if proxy is None:
            self.chain = {"metrics": None,
                          **_status_dict(status="no_data",
                                         error=f"опционных данных для "
                                               f"{self.instrument_code} нет")}
            return
        if self.demo:
            raw = self.demo_market.chain(self.instrument_code)
            spot = self.demo_market.prices[self.instrument_code]
            term = self._demo_term()
            try:
                metrics = self._compute_chain_metrics(
                    raw, spot, proxy, demo=True,
                    experimental=self.instrument.proxy_experimental, term=term)
                self.chain = {"metrics": metrics,
                              **_status_dict(True, "demo", time.time(),
                                             source="synthetic BS chain")}
                self.cache.add_chain_snapshot(proxy, metrics)
            except ValueError as e:
                self.chain = {"metrics": None, **_status_dict(error=str(e))}
            return
        try:
            import yfinance as yf
            t = yf.Ticker(proxy)
            expiries = t.options
            if not expiries:
                raise RuntimeError("нет экспираций")
            spot = float(t.fast_info.last_price)
            if not math.isfinite(spot) or spot <= 0:
                raise RuntimeError("нет валидной цены option-proxy")
            # Не затираем свежий stream-тик более слабой REST-котировкой.
            if self.proxy_price.get("status") != "live":
                self.proxy_price = _status_dict(
                    spot, "delayed", time.time(),
                    source=f"yfinance REST {proxy} (chain snapshot)")
            expiry = expiries[0]
            exp_dt_local = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
                hour=16, tzinfo=ZoneInfo("America/New_York"))
            exp_ts = exp_dt_local.timestamp()
            t_years = max(exp_ts - time.time(), 3600.0) / (365.0 * 24 * 3600)
            oc = t.option_chain(expiry)
            calls, puts = oc.calls, oc.puts
            merged = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
            if len(merged) < 5:
                raise RuntimeError(f"слишком мало страйков: {len(merged)}")

            def mid(bid, ask, last):
                m = np.where((bid > 0) & (ask > 0), (bid + ask) / 2.0, last)
                return np.asarray(m, dtype=float)

            raw = {
                "strikes": merged["strike"].to_numpy(dtype=float),
                "call_mid": mid(merged["bid_c"].fillna(0).to_numpy(),
                                merged["ask_c"].fillna(0).to_numpy(),
                                merged["lastPrice_c"].fillna(np.nan).to_numpy()),
                "put_mid": mid(merged["bid_p"].fillna(0).to_numpy(),
                               merged["ask_p"].fillna(0).to_numpy(),
                               merged["lastPrice_p"].fillna(np.nan).to_numpy()),
                "call_oi": merged["openInterest_c"].fillna(0).to_numpy(dtype=float),
                "put_oi": merged["openInterest_p"].fillna(0).to_numpy(dtype=float),
                "call_iv": merged["impliedVolatility_c"].to_numpy(dtype=float),
                "put_iv": merged["impliedVolatility_p"].to_numpy(dtype=float),
                "t_years": t_years,
                "spot": spot,
                "expiry": expiry,
                "expiry_ts_utc": exp_ts,
            }
            term = self._fetch_term(t, expiries, spot)
            metrics = self._compute_chain_metrics(
                raw, spot, proxy, demo=False,
                experimental=self.instrument.proxy_experimental, term=term)
            self.chain = {"metrics": metrics,
                          **_status_dict(True, "delayed", time.time(),
                                         source=f"yfinance {proxy} options {expiry}")}
            self.chain["delay_hint_sec"] = 900
            self.cache.add_chain_snapshot(proxy, metrics)
        except Exception as e:  # noqa: BLE001
            # протухший кэш допустим для контекста, но статус честный
            snaps = [
                snap for snap in self.cache.chain_snapshots(proxy, limit=60)
                if production_chain_snapshot(snap)
            ]
            if snaps and time.time() - snaps[-1]["ts"] < 24 * 3600:
                self.chain = {"metrics": snaps[-1],
                              **_status_dict(True, "delayed", snaps[-1]["ts"],
                                             error=str(e)[:200], source="кэш цепочки"),
                              "cache_fallback": {
                                  "used": True,
                                  "snapshot_provenance": "explicit_real_demo_false",
                              }}
            else:
                self.chain = {
                    "metrics": None,
                    **_status_dict(error=str(e)[:200]),
                    "cache_fallback": {
                        "used": False,
                        "reason": "no_explicitly_real_snapshot",
                        "demo_or_unverified_rejected": True,
                    },
                }

    def _compute_chain_metrics(self, raw: dict, spot: float, proxy: str,
                               demo: bool, experimental: bool = False,
                               term: dict | None = None) -> dict:
        im = opt.implied_move(raw["strikes"], raw["call_mid"], raw["put_mid"],
                              spot, raw["t_years"])
        if not (1e-5 < im.move_frac < 0.50 and im.sigma_annual < 5.0):
            raise ValueError(
                "ATM straddle дал неправдоподобный implied move; "
                "цепочка отклонена как повреждённая")
        density = opt.bl_density(raw["strikes"], raw["call_mid"], raw["t_years"])
        gex = opt.gex_profile(raw["strikes"], raw["call_oi"], raw["put_oi"],
                              raw["call_iv"], raw["put_iv"], spot, raw["t_years"])
        skew = opt.risk_reversal_skew(raw["strikes"], raw["call_iv"],
                                      raw["put_iv"], spot)
        return {
            "proxy": proxy,
            "demo": demo,
            "experimental": experimental,
            "spot": spot,
            "proxy_spot": spot,
            "expiry": raw.get("expiry", "demo+2d"),
            "expiry_ts_utc": raw.get("expiry_ts_utc", time.time() + 2 * 86400),
            "expiry_date": raw.get("expiry", "demo+2d"),
            "expiry_timezone": "America/New_York" if not demo else "UTC",
            "expiry_time_assumption": "assumed_market_close_16:00_ET" if not demo else "demo_time",
            "expiry_time_quality": "assumed_market_close" if not demo else "demo_time",
            "expiry_contract_version": "option-expiry-contract-f32-v1",
            "t_years": raw["t_years"],
            "skew": skew,
            "term": term,
            "implied_move": {
                "atm_strike": im.atm_strike,
                "straddle": im.straddle,
                "move_frac": im.move_frac,
                "move_abs": im.move_abs,
                "sigma_annual": im.sigma_annual,
            },
            "density": {
                "strikes": [round(float(x), 4) for x in density.strikes],
                "q": [float(x) for x in density.density],
            },
            "oi_profile": {
                "strikes": [float(x) for x in raw["strikes"]],
                "call_oi": [float(x) for x in np.nan_to_num(raw["call_oi"])],
                "put_oi": [float(x) for x in np.nan_to_num(raw["put_oi"])],
            },
            "gex": {
                "strikes": [float(x) for x in gex.strikes],
                "net": [float(x) for x in gex.net_gex],
                "zero_flip": gex.zero_flip,
                "top": gex.top_levels,
            },
        }

    def _demo_term(self, phase: float = 0.0) -> dict | None:
        """Синтетическая term-structure: контанго/бэквордация меандрируют во времени."""
        base = 0.16
        slope = 0.08 * math.sin(time.time() / 300.0 + phase)
        pts = [(2, base), (9, base * (1 + slope * 0.5)), (30, base * (1 + slope))]
        return opt.term_structure(pts)

    def _fetch_term(self, ticker, expiries, spot: float) -> dict | None:
        """ATM-IV ближайших ~3 экспираций -> delayed term-structure."""
        pts = []
        for exp in list(expiries)[:3]:
            try:
                calls = ticker.option_chain(exp).calls
                idx = (calls["strike"] - spot).abs().idxmin()
                iv = float(calls.loc[idx, "impliedVolatility"])
                days = (dt.datetime.strptime(exp, "%Y-%m-%d")
                        - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)).days
                if iv > 0:
                    pts.append((max(days, 1), iv))
            except Exception:  # noqa: BLE001
                continue
        return opt.term_structure(pts) if len(pts) >= 2 else None

    # ---------------------------------------------------------- demo seeding

    def _seed_demo_snapshots(self) -> None:
        """8 «исторических» снапшотов, чтобы гряда была видна сразу после старта."""
        for code, inst in INSTRUMENTS.items():
            if inst.options_proxy is None:
                continue
            if self.cache.chain_snapshots(inst.options_proxy, limit=1):
                continue
            base = inst.demo_price
            now = time.time()
            for i in range(8):
                wobble = 1.0 + 0.004 * math.sin(i * 1.3) + 0.002 * (i - 4)
                spot = base * wobble
                raw = opt.synth_chain(spot, 0.16 + 0.01 * math.cos(i), 2.0 / 365.0,
                                      n_strikes=41, width=0.06, oi_skew=0.5,
                                      iv_skew=0.5 * math.sin(i * 0.8), seed=i)
                m = self._compute_chain_metrics(
                    raw, spot, inst.options_proxy, demo=True,
                    experimental=inst.proxy_experimental, term=self._demo_term(i))
                self.cache.add_chain_snapshot(inst.options_proxy, m,
                                              ts=now - (8 - i) * 600.0)

    # ------------------------------------------------------------- derived

    def atr_ratio(self) -> float | None:
        bars = self.daily.get("bars")
        if not bars:
            return None
        try:
            from ..core.risk import atr_ratio
            return atr_ratio(bars["highs"], bars["lows"], bars["closes"])
        except ValueError:
            return None

    def baseline_vol(self) -> float | None:
        bars = self.daily.get("bars")
        if not bars:
            return None
        try:
            return opt.realized_vol(bars["closes"], trading_days=20)
        except ValueError:
            return None

    def sigma_ratio(self) -> dict:
        """Опционная поправка: sigma_implied / sigma_baseline (п.4 ядра).

        Источник sigma_implied по приоритету:
          1) полная опционная цепочка (implied move) — "chain";
          2) профильный индекс волы (например ^EVZ для EURUSD) — "vol_index";
        иначе поправка не применяется (честно указывается причина).

        Возвращает {ratio, sigma_implied, sigma_baseline, applied, source, reason}.
        """
        out = {"ratio": 1.0, "sigma_implied": None, "sigma_baseline": None,
               "applied": False, "source": None, "reason": None}
        base = self.baseline_vol()
        m = self.chain.get("metrics")
        si, source = None, None
        if m is not None:
            si, source = m["implied_move"]["sigma_annual"], "chain"
        else:
            key = SIGMA_INDEX_FOR.get(self.instrument_code)
            feed = self.vols.get(key) if key else None
            if feed and feed.get("value"):
                si, source = feed["value"] / 100.0, "vol_index"
        if si is None:
            out["reason"] = (f"нет опционной цепочки/индекса волы для "
                             f"{self.instrument_code}")
            return out
        if base is None or base <= 0:
            out["reason"] = "нет дневной истории для базовой волы"
            return out
        out.update(sigma_implied=si, sigma_baseline=base,
                   ratio=min(max(si / base, 0.25), 4.0), applied=True,
                   source=source)
        return out
