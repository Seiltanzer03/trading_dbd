"""Фиды рыночных данных: цена, дневки, индексы волатильности, опционные цепочки.

Правила честности:
- каждый фид несёт статус: live / delayed / no_data (+ demo), время и текст ошибки;
- при сбое источника значение НЕ выдумывается: остаётся прежнее со статусом
  delayed (недолго) либо no_data;
- демо-режим синтезирует все потоки и явно помечает их demo.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import random
import time

import numpy as np

from ..config import (INSTRUMENTS, SIGMA_INDEX_FOR, VOL_INDEX_TICKERS,
                      Instrument, Settings)
from ..core import options as opt
from .cache import DiskCache

# yfinance шумит в stderr про делистинги (например ^V1X/VDAX недоступен) —
# это ожидаемо и обрабатывается статусом no_data, поэтому глушим его логгер.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

DELAYED_GRACE = 5.0  # во сколько раз можно превысить период опроса до no_data


def _status_dict(value=None, status="no_data", ts=None, error=None, source=None):
    return {"value": value, "status": status, "ts": ts, "error": error, "source": source}


class DemoMarket:
    """Синтетический рынок: GBM-цены, OU-индексы волы, BS-цепочки."""

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.prices = {c: i.demo_price for c, i in INSTRUMENTS.items()}
        self.vols = {"vix": 17.5, "gvz": 16.0, "dv1x": 16.5, "evz": 8.0, "vxn": 22.0}
        self._last = time.time()

    def step(self) -> None:
        now = time.time()
        dt_sec = max(now - self._last, 1e-3)
        self._last = now
        dt_y = dt_sec / (365.0 * 24 * 3600)
        for code, inst in INSTRUMENTS.items():
            sigma = inst.demo_vol
            z = self.rng.gauss(0, 1)
            self.prices[code] *= math.exp(-0.5 * sigma * sigma * dt_y
                                          + sigma * math.sqrt(dt_y) * z * 8.0)
            # *8: демо-время ускорено, чтобы движение было видно на панелях
        for k, anchor, floor in (("vix", 17.5, 9.0), ("gvz", 16.0, 9.0),
                                 ("dv1x", 16.5, 9.0), ("evz", 8.0, 4.0),
                                 ("vxn", 22.0, 12.0)):
            v = self.vols[k]
            self.vols[k] = max(floor, v + 0.05 * (anchor - v) + self.rng.gauss(0, 0.15))

    def daily_bars(self, code: str, days: int = 60) -> dict:
        inst = INSTRUMENTS[code]
        rng = random.Random(hash(code) & 0xFFFF)
        closes, highs, lows = [], [], []
        p = self.prices[code] * 0.97
        for _ in range(days):
            drift = rng.gauss(0, inst.demo_vol / math.sqrt(252))
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
        inst = INSTRUMENTS[code]
        if inst.options_proxy is None:
            return None
        spot = self.prices[code]
        iv = 0.16 + 0.04 * math.sin(time.time() / 300.0)
        iv_skew = 0.7 * math.sin(time.time() / 240.0)   # меандрирует бычий/медвежий
        return opt.synth_chain(spot, iv, t_years=2.0 / 365.0, n_strikes=41,
                               width=0.06, oi_skew=0.5, iv_skew=iv_skew,
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
        self._last_proxy_rest_attempt = 0.0
        # Yahoo WebSocket нередко отдаёт proxy, но молчит по cash index или
        # активному фьючерсу. Тогда сохраняем синхронную пару instrument↔driver
        # и переносим только последующую ДОХОДНОСТЬ driver.
        # Это не новая «котировка фьючерса», а явно помеченный live mapping.
        self._price_anchor_raw: float | None = None
        self._price_anchor_proxy: float | None = None
        self._price_anchor_driver: str | None = None
        self._price_anchor_ts: float | None = None
        self.intraday: list[tuple[float, float, float]] = []  # (ts, price, volume)
        self.daily = {"bars": None, **_status_dict()}
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
        return INSTRUMENTS[self.instrument_code]

    def set_instrument(self, code: str) -> None:
        if code not in INSTRUMENTS:
            raise ValueError(f"неизвестный инструмент: {code}")
        if code != self.instrument_code:
            self.instrument_code = code
            self.price = _status_dict()
            self.proxy_price = _status_dict()
            self._price_prev_val = None
            self._price_change_ts = None
            self._last_price_rest_attempt = 0.0
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
        # живой WebSocket-стрим цены (если включён и есть свежий тик) — приоритет
        if self.stream is not None:
            sp = self.stream.fresh(self.instrument.yahoo, max_age=8.0)
            if sp is not None:
                now = time.time()
                self.price = _status_dict(sp, "live", now,
                                          source=f"stream {self.instrument.yahoo}")
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
                source=f"yfinance REST {self.instrument.yahoo} (indicative)")
            self.price["derived"] = False
            self._annotate_freshness()
        except Exception as e:  # noqa: BLE001 — фид обязан пережить любой сбой источника
            self._mark_fail(self.price, self.settings.price_poll_sec, str(e))

    def refresh_intraday(self) -> None:
        """1m-бары дня для VWAP (объём нужен; у кэш-индексов его нет — честно None)."""
        if self.demo:
            return
        try:
            import yfinance as yf
            hist = yf.Ticker(self.instrument.yahoo).history(period="1d", interval="1m")
            if len(hist):
                self.intraday = [(ts.timestamp(), float(r["Close"]), float(r["Volume"]))
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
        try:
            import yfinance as yf
            hist = yf.Ticker(self.instrument.yahoo).history(period="4mo", interval="1d")
            if len(hist) < 25:
                raise RuntimeError(f"мало дневных баров: {len(hist)}")
            bars = {"highs": hist["High"].tolist(),
                    "lows": hist["Low"].tolist(),
                    "closes": hist["Close"].tolist()}
            self.daily = {"bars": bars,
                          **_status_dict(True, "live", time.time(),
                                         source=f"yfinance {self.instrument.yahoo} 1d")}
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
        tickers = ["NQ=F", "^VXN", "ES=F", "^VIX", "GC=F", "^GVZ", "CL=F", "^OVX"]
        names = ["NAS", "VXN", "SP500", "VIX", "GOLD", "GVZ", "OIL", "OVX"]
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
                    mat[i][i] = 1.0
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
                        row.append(bv if bv is not None else (1.0 if i == j else 0.0))
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
            exp_dt = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
                hour=21, tzinfo=dt.timezone.utc)
            t_years = max((exp_dt - dt.datetime.now(dt.timezone.utc)).total_seconds(),
                          3600.0) / (365.0 * 24 * 3600)
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
            snaps = self.cache.chain_snapshots(proxy, limit=1)
            if snaps and time.time() - snaps[-1]["ts"] < 24 * 3600:
                self.chain = {"metrics": snaps[-1],
                              **_status_dict(True, "delayed", snaps[-1]["ts"],
                                             error=str(e)[:200], source="кэш цепочки")}
            else:
                self.chain = {"metrics": None, **_status_dict(error=str(e)[:200])}

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
            "expiry": raw.get("expiry", "demo+2d"),
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
