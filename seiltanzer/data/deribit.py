"""Адаптер Deribit REST API для крипто-опционных цепочек, Greeks, DVOL и Term Structure.

Поддерживает BTC, ETH, SOL без необходимости регистрации или API-ключей.
Публичный API: https://www.deribit.com/api/v2/
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any

import httpx
import numpy as np

logger = logging.getLogger(__name__)

DERIBIT_BASE = "https://www.deribit.com/api/v2"

_fetcher_instance: DeribitFetcher | None = None
_fetcher_lock = threading.Lock()


def get_deribit_fetcher() -> DeribitFetcher:
    global _fetcher_instance
    with _fetcher_lock:
        if _fetcher_instance is None:
            _fetcher_instance = DeribitFetcher()
        return _fetcher_instance


class DeribitFetcher:

    """Потокобезопасный адаптер Deribit REST API с кэшированием."""

    def __init__(self, timeout: float = 12.0):
        self._timeout = timeout
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        # Кэш: {key: (timestamp, data)}
        self._cache: dict[str, tuple[float, Any]] = {}

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=DERIBIT_BASE,
                timeout=self._timeout,
                headers={"User-Agent": "Seiltanzer/0.1.0"},
            )
        return self._client

    def _cached_get(self, endpoint: str, ttl_sec: float) -> dict | None:
        now = time.time()
        with self._lock:
            if endpoint in self._cache:
                cached_ts, cached_val = self._cache[endpoint]
                if now - cached_ts < ttl_sec:
                    return cached_val

        client = self._get_client()
        try:
            resp = client.get(endpoint)
            if resp.status_code == 200:
                data = resp.json().get("result")
                with self._lock:
                    self._cache[endpoint] = (now, data)
                return data
            logger.warning("Deribit API %s вернул статус %s: %s", endpoint, resp.status_code, resp.text[:120])
        except Exception as exc:
            logger.warning("Ошибка запроса Deribit %s: %s", endpoint, exc)
        return None

    def fetch_index_price(self, currency: str) -> float | None:
        """Текущая индексная (спот) цена валюты в USD."""
        curr = currency.lower()
        res = self._cached_get(f"/public/get_index_price?index_name={curr}_usd", ttl_sec=4.0)
        if isinstance(res, dict):
            price = res.get("index_price") or res.get("estimated_delivery_price")
            if price is not None:
                return float(price)
        return None

    def fetch_dvol(self, currency: str) -> float | None:
        """Текущее значение индекса волатильности DVOL (годовая IV в %)."""
        curr = currency.upper()
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - 3600 * 1000
        res = self._cached_get(
            f"/public/get_volatility_index_data?currency={curr}&resolution=60&start_timestamp={start_ms}&end_timestamp={now_ms}",
            ttl_sec=30.0,
        )
        if isinstance(res, dict):
            data = res.get("data")
            if isinstance(data, list) and len(data) > 0:
                # [timestamp, open, high, low, close]
                return float(data[-1][-1])

        # Fallback: для инструментов без отдельного индекса DVOL (например, SOL)
        # берем среднюю ATM IV ближайшей экспирации
        chain_res = self.fetch_chain(currency)
        if chain_res and "atm_iv" in chain_res:
            return float(chain_res["atm_iv"] * 100.0)
        return None

    def fetch_raw_options(self, currency: str) -> list[dict]:
        """Получить сырой стакан всех активных опционов валюты."""
        curr = currency.upper()
        if curr == "SOL":
            res = self._cached_get(
                "/public/get_book_summary_by_currency?currency=USDC&kind=option",
                ttl_sec=10.0,
            )
            if isinstance(res, list):
                return [it for it in res if it.get("base_currency") == "SOL"]
            return []
        res = self._cached_get(
            f"/public/get_book_summary_by_currency?currency={curr}&kind=option",
            ttl_sec=10.0,
        )
        if isinstance(res, list):
            return res
        return []

    def fetch_chain(self, currency: str) -> dict | None:
        """Собрать опционную цепочку ближайшей экспирации для options.py.

        Возвращает dict:
            strikes: np.ndarray
            call_mid: np.ndarray
            put_mid: np.ndarray
            call_oi: np.ndarray
            put_oi: np.ndarray
            call_iv: np.ndarray
            put_iv: np.ndarray
            t_years: float
            spot: float
            expiry: str
            atm_iv: float
        """
        spot = self.fetch_index_price(currency)
        if not spot or spot <= 0:
            return None

        items = self.fetch_raw_options(currency)
        if not items:
            return None

        now = time.time()
        by_expiry: dict[str, dict[str, Any]] = {}

        for it in items:
            name = it.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) != 4:
                continue
            curr, exp_str, strike_str, opt_type = parts
            try:
                strike = float(strike_str)
            except ValueError:
                continue

            # Парсинг даты: DDMMMYY в 08:00:00 UTC (стандарт расчетов Deribit)
            try:
                exp_date = dt.datetime.strptime(exp_str, "%d%b%y").replace(
                    hour=8, minute=0, second=0, tzinfo=dt.timezone.utc
                )
                exp_ts = exp_date.timestamp()
            except Exception:
                continue

            t_sec = exp_ts - now
            if t_sec < 3600:  # Пропускаем экспирированные или менее часа
                continue

            if exp_str not in by_expiry:
                by_expiry[exp_str] = {"ts": exp_ts, "t_sec": t_sec, "items": []}
            by_expiry[exp_str]["items"].append((strike, opt_type, it))

        if not by_expiry:
            return None

        sorted_expiries = sorted(by_expiry.items(), key=lambda x: x[1]["ts"])
        # Выбираем фронт-месяц: экспирация >= 1 дня, или ближайшая доступная
        target_exp = next((e for e in sorted_expiries if e[1]["t_sec"] >= 86400), sorted_expiries[0])
        exp_name, exp_data = target_exp
        t_years = max(exp_data["t_sec"] / (365.0 * 86400.0), 1e-4)

        calls: dict[float, dict[str, float]] = {}
        puts: dict[float, dict[str, float]] = {}

        for strike, opt_type, it in exp_data["items"]:
            iv = (it.get("mark_iv") or 0.0) / 100.0
            mp_crypto = float(it.get("mark_price") or 0.0)
            bid_crypto = float(it.get("bid_price") or 0.0)
            ask_crypto = float(it.get("ask_price") or 0.0)
            oi = float(it.get("open_interest") or 0.0)

            # Конвертация цены опциона в USD:
            # Для USDC-settled опционов (например SOL) котировка уже в USD/USDC.
            # Для coin-margined (BTC, ETH) котировка в базовой крипте и умножается на spot.
            is_usdc = (it.get("quote_currency") == "USDC")
            mult = 1.0 if is_usdc else spot

            if bid_crypto > 0 and ask_crypto > 0:
                mid_usd = 0.5 * (bid_crypto + ask_crypto) * mult
            elif mp_crypto > 0:
                mid_usd = mp_crypto * mult
            elif bid_crypto > 0:
                mid_usd = bid_crypto * mult
            else:
                mid_usd = 0.0

            if opt_type == "C":
                calls[strike] = {"mid": mid_usd, "oi": oi, "iv": iv}
            else:
                puts[strike] = {"mid": mid_usd, "oi": oi, "iv": iv}

        all_strikes = sorted(set(calls.keys()) | set(puts.keys()))
        if len(all_strikes) < 3:
            return None

        strikes_arr = np.array(all_strikes, dtype=float)
        call_mids = np.array([calls.get(k, {}).get("mid", 0.0) for k in all_strikes], dtype=float)
        put_mids = np.array([puts.get(k, {}).get("mid", 0.0) for k in all_strikes], dtype=float)
        call_ois = np.array([calls.get(k, {}).get("oi", 0.0) for k in all_strikes], dtype=float)
        put_ois = np.array([puts.get(k, {}).get("oi", 0.0) for k in all_strikes], dtype=float)
        call_ivs = np.array([calls.get(k, {}).get("iv", 0.0) for k in all_strikes], dtype=float)
        put_ivs = np.array([puts.get(k, {}).get("iv", 0.0) for k in all_strikes], dtype=float)

        # ATM IV
        atm_idx = int(np.argmin(np.abs(strikes_arr - spot)))
        atm_iv_c = call_ivs[atm_idx]
        atm_iv_p = put_ivs[atm_idx]
        atm_iv = float(0.5 * (atm_iv_c + atm_iv_p) if (atm_iv_c > 0 and atm_iv_p > 0) else (atm_iv_c or atm_iv_p or 0.5))

        return {
            "strikes": strikes_arr,
            "call_mid": call_mids,
            "put_mid": put_mids,
            "call_oi": call_ois,
            "put_oi": put_ois,
            "call_iv": call_ivs,
            "put_iv": put_ivs,
            "t_years": t_years,
            "spot": spot,
            "expiry": exp_name,
            "atm_iv": atm_iv,
        }

    def fetch_term_structure(self, currency: str, spot: float | None = None) -> list[tuple[int, float]]:
        """Кривая Term Structure (дней до экспирации, ATM IV)."""
        if spot is None:
            spot = self.fetch_index_price(currency)
        if not spot or spot <= 0:
            return []

        items = self.fetch_raw_options(currency)
        if not items:
            return []

        now = time.time()
        by_expiry: dict[str, list[dict]] = {}
        expiry_days: dict[str, int] = {}

        for it in items:
            name = it.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) != 4:
                continue
            exp_str = parts[1]
            try:
                exp_date = dt.datetime.strptime(exp_str, "%d%b%y").replace(
                    hour=8, minute=0, second=0, tzinfo=dt.timezone.utc
                )
                exp_ts = exp_date.timestamp()
            except Exception:
                continue

            days = max(1, int(round((exp_ts - now) / 86400.0)))
            if (exp_ts - now) < 3600:
                continue

            if exp_str not in by_expiry:
                by_expiry[exp_str] = []
                expiry_days[exp_str] = days
            by_expiry[exp_str].append(it)

        points: list[tuple[int, float]] = []
        for exp_str, opts in by_expiry.items():
            # Находим инструмент ближе всего к spot
            best_diff = float("inf")
            best_iv = None
            for it in opts:
                try:
                    k = float(it["instrument_name"].split("-")[2])
                    diff = abs(k - spot)
                    iv = (it.get("mark_iv") or 0.0) / 100.0
                    if diff < best_diff and iv > 0:
                        best_diff = diff
                        best_iv = iv
                except (ValueError, KeyError):
                    continue
            if best_iv is not None and best_iv > 0:
                points.append((expiry_days[exp_str], float(best_iv)))

        points.sort(key=lambda x: x[0])
        return points

    def fetch_full_options_matrix(self, currency: str) -> dict[str, Any]:
        """Полная матрица опционов для детального просмотра в UI `/crypto`."""
        curr = currency.upper()
        spot = self.fetch_index_price(curr) or 0.0
        items = self.fetch_raw_options(curr)
        now = time.time()

        by_expiry: dict[str, dict[str, Any]] = {}
        expiry_ts_map: dict[str, float] = {}

        for it in items:
            name = it.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) != 4:
                continue
            inst_curr, exp_str, strike_str, opt_type = parts
            try:
                strike = float(strike_str)
            except ValueError:
                continue

            try:
                exp_date = dt.datetime.strptime(exp_str, "%d%b%y").replace(
                    hour=8, minute=0, second=0, tzinfo=dt.timezone.utc
                )
                exp_ts = exp_date.timestamp()
            except Exception:
                continue

            if (exp_ts - now) < 3600:
                continue

            if exp_str not in by_expiry:
                expiry_ts_map[exp_str] = exp_ts
                days = max(1, int(round((exp_ts - now) / 86400.0)))
                by_expiry[exp_str] = {
                    "expiry": exp_str,
                    "days": days,
                    "ts": exp_ts,
                    "calls": {},
                    "puts": {},
                }

            is_usdc = (it.get("quote_currency") == "USDC")
            mult = 1.0 if is_usdc else (spot if spot > 0 else 0.0)

            mp = float(it.get("mark_price") or 0.0)
            bid = float(it.get("bid_price") or 0.0)
            ask = float(it.get("ask_price") or 0.0)
            iv = float(it.get("mark_iv") or 0.0)
            oi = float(it.get("open_interest") or 0.0)
            delta = float(it.get("delta") or 0.0)
            gamma = float(it.get("gamma") or 0.0)

            opt_info = {
                "instrument_name": name,
                "strike": strike,
                "type": opt_type,
                "mark_usd": round(mp * mult, 2),
                "bid_usd": round(bid * mult, 2) if bid > 0 else None,
                "ask_usd": round(ask * mult, 2) if ask > 0 else None,
                "iv": round(iv, 1),
                "oi": round(oi, 2),
                "delta": round(delta, 3),
                "gamma": round(gamma, 5),
            }

            if opt_type == "C":
                by_expiry[exp_str]["calls"][strike] = opt_info
            else:
                by_expiry[exp_str]["puts"][strike] = opt_info

        # Сортируем экспирации по дате
        sorted_exp_keys = sorted(by_expiry.keys(), key=lambda e: expiry_ts_map[e])

        formatted_expiries: dict[str, dict[str, Any]] = {}
        for exp_key in sorted_exp_keys:
            exp_info = by_expiry[exp_key]
            all_strikes = sorted(set(exp_info["calls"].keys()) | set(exp_info["puts"].keys()))
            rows = []
            for k in all_strikes:
                rows.append({
                    "strike": k,
                    "call": exp_info["calls"].get(k),
                    "put": exp_info["puts"].get(k),
                })
            formatted_expiries[exp_key] = {
                "expiry": exp_key,
                "days": exp_info["days"],
                "ts": exp_info["ts"],
                "strikes_count": len(rows),
                "rows": rows,
            }

        return {
            "currency": curr,
            "spot": spot,
            "ts": now,
            "dvol": self.fetch_dvol(curr),
            "term_structure": self.fetch_term_structure(curr),
            "expiries_list": sorted_exp_keys,
            "matrix": formatted_expiries,
        }

