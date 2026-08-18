"""Resilient selection for thin delayed option-proxy chains.

Thin ETF proxies (notably FXC for USD/CAD) can have an unusable nearest expiry
while a later listed expiry is perfectly serviceable.  The base feed validates
one nearest expiry only.  This refinement keeps all existing option mathematics
and quality checks, but, after the base attempt fails (or falls back to cache),
tries a small bounded set of listed expiries and accepts only a candidate that
passes the same `_compute_chain_metrics` validation.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from .data.feeds import MarketData, _status_dict

OPTION_FEED_RESILIENCE_VERSION = "option-feed-resilience-v1"
MAX_CHAIN_EXPIRY_CANDIDATES = 6
MAX_SURFACE_EXPIRY_CANDIDATES = 8
MAX_SURFACE_ROWS = 3
_INSTALLED = False

_BASE_REFRESH_CHAIN = MarketData.refresh_chain
_BASE_REFRESH_IV_SURFACE = MarketData.refresh_iv_surface


def _mid(bid: Any, ask: Any, last: Any) -> np.ndarray:
    bid = np.asarray(bid, dtype=float)
    ask = np.asarray(ask, dtype=float)
    last = np.asarray(last, dtype=float)
    return np.asarray(np.where((bid > 0) & (ask > 0), (bid + ask) / 2.0, last), dtype=float)


def _candidate_metrics(market: MarketData, ticker: Any, proxy: str, expiry: str,
                       spot: float, term: dict | None) -> dict:
    """Build one candidate using the exact existing option math/validators."""
    exp_dt_local = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
        hour=16, tzinfo=ZoneInfo("America/New_York"))
    exp_ts = exp_dt_local.timestamp()
    t_years = max(exp_ts - time.time(), 3600.0) / (365.0 * 24 * 3600)

    oc = ticker.option_chain(expiry)
    calls, puts = oc.calls, oc.puts
    merged = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
    if len(merged) < 5:
        raise RuntimeError(f"слишком мало общих страйков: {len(merged)}")

    raw = {
        "strikes": merged["strike"].to_numpy(dtype=float),
        "call_mid": _mid(
            merged["bid_c"].fillna(0).to_numpy(),
            merged["ask_c"].fillna(0).to_numpy(),
            merged["lastPrice_c"].fillna(np.nan).to_numpy()),
        "put_mid": _mid(
            merged["bid_p"].fillna(0).to_numpy(),
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
    return market._compute_chain_metrics(
        raw, spot, proxy, demo=False,
        experimental=market.instrument.proxy_experimental, term=term)


def _retry_later_chain_expiries(market: MarketData, base_state: dict) -> None:
    proxy = market.instrument.options_proxy
    if market.demo or proxy is None:
        return

    try:
        import yfinance as yf

        ticker = yf.Ticker(proxy)
        expiries = list(ticker.options or [])
        if not expiries:
            return
        spot = float(ticker.fast_info.last_price)
        if not math.isfinite(spot) or spot <= 0:
            return
        term = market._fetch_term(ticker, expiries, spot)

        rejected: list[str] = []
        for index, expiry in enumerate(expiries[:MAX_CHAIN_EXPIRY_CANDIDATES]):
            try:
                metrics = _candidate_metrics(market, ticker, proxy, expiry, spot, term)
            except Exception as exc:  # candidate-local failure must not abort the scan
                rejected.append(f"{expiry}: {type(exc).__name__}: {str(exc)[:100]}")
                continue

            metrics["expiry_selection"] = {
                "contract_version": OPTION_FEED_RESILIENCE_VERSION,
                "method": "first_valid_listed_expiry",
                "candidate_index": index,
                "candidate_count_checked": index + 1,
                "rejected_before_selected": rejected,
            }
            if market.proxy_price.get("status") != "live":
                market.proxy_price = _status_dict(
                    spot, "delayed", time.time(),
                    source=f"yfinance REST {proxy} (chain snapshot)")
            market.chain = {
                "metrics": metrics,
                **_status_dict(
                    True, "delayed", time.time(),
                    source=f"yfinance {proxy} options {expiry} (validated expiry scan)"),
            }
            market.chain["delay_hint_sec"] = 900
            market.chain["expiry_selection"] = metrics["expiry_selection"]
            market.cache.add_chain_snapshot(proxy, metrics)
            return

        # Preserve a usable cached base state when the live scan also fails.
        if base_state.get("metrics") is not None:
            market.chain = base_state
            market.chain["expiry_scan_error"] = (
                "no valid live expiry; " + " | ".join(rejected[-3:]))[:500]
        elif rejected:
            market.chain = {
                "metrics": None,
                **_status_dict(
                    error=("нет валидной опционной экспирации: "
                           + " | ".join(rejected[-3:]))[:500]),
                "expiry_selection": {
                    "contract_version": OPTION_FEED_RESILIENCE_VERSION,
                    "method": "first_valid_listed_expiry",
                    "candidate_count_checked": min(len(expiries), MAX_CHAIN_EXPIRY_CANDIDATES),
                    "selected": None,
                },
            }
    except Exception:
        # The base feed already published the authoritative error/cache fallback.
        market.chain = base_state


def resilient_refresh_chain(self: MarketData) -> None:
    _BASE_REFRESH_CHAIN(self)
    base_state = dict(self.chain or {})
    source = str(base_state.get("source") or "")
    if self.demo or self.instrument.options_proxy is None:
        return
    if base_state.get("metrics") is not None and source != "кэш цепочки":
        return
    _retry_later_chain_expiries(self, base_state)


def _retry_iv_surface(self: MarketData, base_state: dict) -> None:
    proxy = self.instrument.options_proxy
    if self.demo or proxy is None:
        return
    try:
        import yfinance as yf

        ticker = yf.Ticker(proxy)
        expiries = list(ticker.options or [])
        if not expiries:
            return
        now = dt.datetime.now(dt.timezone.utc)
        spot = float(ticker.fast_info.last_price)
        if not math.isfinite(spot) or spot <= 0:
            return

        surface: list[dict] = []
        rejected: list[str] = []
        for expiry in expiries[:MAX_SURFACE_EXPIRY_CANDIDATES]:
            try:
                exp_dt = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
                    hour=21, tzinfo=dt.timezone.utc)
                days = max((exp_dt - now).total_seconds(), 3600.0) / (24 * 3600)
                calls = ticker.option_chain(expiry).calls
                strikes_raw = calls["strike"].to_numpy(dtype=float)
                ivs_raw = calls["impliedVolatility"].to_numpy(dtype=float)
                ok = (
                    np.isfinite(strikes_raw) & np.isfinite(ivs_raw)
                    & (strikes_raw > 0) & (ivs_raw > 0) & (ivs_raw < 5.0)
                )
                strikes = strikes_raw[ok].tolist()
                ivs = ivs_raw[ok].tolist()
                if len(strikes) < 3:
                    raise RuntimeError(f"валидных IV-точек только {len(strikes)}")
                surface.append({
                    "days": round(days, 2), "expiry": expiry,
                    "strikes": strikes, "ivs": ivs,
                    "spot_at_snapshot": spot,
                })
                if len(surface) >= MAX_SURFACE_ROWS:
                    break
            except Exception as exc:
                rejected.append(f"{expiry}: {type(exc).__name__}: {str(exc)[:100]}")

        if surface:
            self.iv_surface = _status_dict(
                value=surface, status="delayed", ts=time.time(),
                source=f"yfinance {proxy} options (validated expiry scan)")
            self.iv_surface["delay_hint_sec"] = 900
            self.iv_surface["expiry_selection"] = {
                "contract_version": OPTION_FEED_RESILIENCE_VERSION,
                "candidate_count_checked": min(len(expiries), MAX_SURFACE_EXPIRY_CANDIDATES),
                "usable_expiries": [row["expiry"] for row in surface],
                "rejected": rejected,
            }
        else:
            self.iv_surface = base_state
    except Exception:
        self.iv_surface = base_state


def resilient_refresh_iv_surface(self: MarketData) -> None:
    _BASE_REFRESH_IV_SURFACE(self)
    base_state = dict(self.iv_surface or {})
    if self.demo or self.instrument.options_proxy is None:
        return
    if base_state.get("value"):
        return
    _retry_iv_surface(self, base_state)


def install_option_feed_resilience() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    MarketData.refresh_chain = resilient_refresh_chain
    MarketData.refresh_iv_surface = resilient_refresh_iv_surface
    _INSTALLED = True
