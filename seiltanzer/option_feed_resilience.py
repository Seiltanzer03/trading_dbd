"""Resilience for thin experimental option proxies.

The canonical adaptive-chain layer already scans multiple listed expiries and
keeps the raw quoted-mid quality gate strict.  Thin currency/country ETF options
can nevertheless publish a usable implied-volatility smile while their bid/ask
or last-trade mids are too sparse/stale for a Breeden-Litzenberger second
 derivative.  In that case only, this refinement reconstructs smooth theoretical
call/put prices from the *reported* IV smile, then runs the exact same existing
implied-move / BL-density / skew / GEX validators.

This is deliberately a lower-evidence fallback for `proxy_experimental`
instruments.  It never invents an option chain, never turns missing IV into data,
and records explicit provenance in the metrics.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from .core import options as opt
from .data.adaptive_chain import ChainCandidate, MAX_EXPIRIES_TO_SCAN, select_candidate
from .data.feeds import MarketData, _status_dict

OPTION_FEED_RESILIENCE_VERSION = "option-feed-resilience-v2-iv-smile"
MAX_SURFACE_EXPIRY_CANDIDATES = 8
MAX_SURFACE_ROWS = 3
MIN_IV_POINTS = 7
_INSTALLED = False

# Importing MarketData has already executed seiltanzer.data.__init__, therefore
# this base method is the canonical adaptive-chain implementation, not the old
# nearest-expiry legacy method in feeds.py.
_BASE_REFRESH_CHAIN = MarketData.refresh_chain
_BASE_REFRESH_IV_SURFACE = MarketData.refresh_iv_surface


def _expiry_contract(expiry: str) -> tuple[float, float]:
    exp_dt_local = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
        hour=16, tzinfo=ZoneInfo("America/New_York"))
    exp_ts = exp_dt_local.timestamp()
    t_years = max(exp_ts - time.time(), 3600.0) / (365.0 * 24 * 3600)
    return exp_ts, t_years


def _enrich_proxy_contract(market: MarketData, metrics: dict) -> dict:
    """Make inverse/direct mapping and selected expiry explicit downstream."""
    metrics["proxy_transform"] = market.instrument.proxy_transform
    expiry = metrics.get("expiry")
    if (not market.demo and isinstance(expiry, str)
            and len(expiry) == 10 and expiry[4:5] == "-" and expiry[7:8] == "-"):
        try:
            exp_ts, t_years = _expiry_contract(expiry)
            metrics["expiry_ts_utc"] = exp_ts
            metrics["t_years"] = t_years
            metrics["expiry_date"] = expiry
            metrics["expiry_timezone"] = "America/New_York"
            metrics["expiry_time_assumption"] = "assumed_market_close_16:00_ET"
            metrics["expiry_time_quality"] = "assumed_market_close"
        except (ValueError, TypeError):
            pass
    return metrics


def _smooth_reported_iv(strikes: np.ndarray, iv: np.ndarray, spot: float) -> np.ndarray:
    """Robust quadratic smile fit in log-moneyness using only finite quoted IV."""
    x = np.log(strikes / spot)
    valid = (
        np.isfinite(x) & np.isfinite(iv)
        & (strikes > 0) & (iv > 0.005) & (iv < 3.0)
    )
    if int(valid.sum()) < MIN_IV_POINTS:
        raise ValueError(f"валидных IV-точек только {int(valid.sum())}")

    xv, yv = x[valid], iv[valid]
    q1, q3 = np.percentile(yv, [25.0, 75.0])
    iqr = max(float(q3 - q1), 0.005)
    robust = (yv >= max(0.005, q1 - 2.5 * iqr)) & (yv <= min(3.0, q3 + 2.5 * iqr))
    if int(robust.sum()) < MIN_IV_POINTS:
        robust = np.ones_like(yv, dtype=bool)
    xv, yv = xv[robust], yv[robust]

    degree = 2 if len(xv) >= 5 else 1
    # ATM points carry more information for the near-horizon straddle while the
    # wings remain present to preserve smile curvature.
    weights = 1.0 / (1.0 + (np.abs(xv) / 0.12) ** 2)
    coeff = np.polyfit(xv, yv, degree, w=np.sqrt(weights))
    fitted = np.polyval(coeff, x)
    lo = max(0.005, float(np.percentile(yv, 5.0)) * 0.65)
    hi = min(3.0, float(np.percentile(yv, 95.0)) * 1.35)
    fitted = np.clip(fitted, lo, hi)
    if not np.all(np.isfinite(fitted)):
        raise ValueError("IV-smile fit содержит нечисловые значения")
    return fitted.astype(float)


def _iv_smile_candidate(market: MarketData, ticker: Any, proxy: str,
                        expiry: str, spot: float, ordinal: int) -> ChainCandidate:
    exp_ts, t_years = _expiry_contract(expiry)
    chain = ticker.option_chain(expiry)
    merged = chain.calls.merge(chain.puts, on="strike", suffixes=("_c", "_p"))
    if len(merged) < MIN_IV_POINTS:
        raise RuntimeError(f"слишком мало общих страйков: {len(merged)}")

    strikes_all = merged["strike"].to_numpy(dtype=float)
    call_iv_all = merged["impliedVolatility_c"].to_numpy(dtype=float)
    put_iv_all = merged["impliedVolatility_p"].to_numpy(dtype=float)
    valid = (
        np.isfinite(strikes_all) & (strikes_all > 0)
        & np.isfinite(call_iv_all) & (call_iv_all > 0.005) & (call_iv_all < 3.0)
        & np.isfinite(put_iv_all) & (put_iv_all > 0.005) & (put_iv_all < 3.0)
    )
    if int(valid.sum()) < MIN_IV_POINTS:
        raise RuntimeError(f"общих call/put IV-точек только {int(valid.sum())}")

    strikes = strikes_all[valid]
    order = np.argsort(strikes)
    strikes = strikes[order]
    call_iv = call_iv_all[valid][order]
    put_iv = put_iv_all[valid][order]
    if not (float(strikes[0]) < spot < float(strikes[-1])):
        raise RuntimeError("IV-smile не охватывает текущий proxy spot")

    call_fit = _smooth_reported_iv(strikes, call_iv, spot)
    put_fit = _smooth_reported_iv(strikes, put_iv, spot)
    call_mid = np.asarray([
        opt.bs_call(spot, float(k), t_years, float(iv))
        for k, iv in zip(strikes, call_fit)
    ], dtype=float)
    put_mid = np.asarray([
        opt.bs_put(spot, float(k), t_years, float(iv))
        for k, iv in zip(strikes, put_fit)
    ], dtype=float)

    call_oi = merged["openInterest_c"].fillna(0).to_numpy(dtype=float)[valid][order]
    put_oi = merged["openInterest_p"].fillna(0).to_numpy(dtype=float)[valid][order]
    raw = {
        "strikes": strikes,
        "call_mid": call_mid,
        "put_mid": put_mid,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "call_iv": call_fit,
        "put_iv": put_fit,
        "t_years": t_years,
        "spot": spot,
        "expiry": expiry,
        "expiry_ts_utc": exp_ts,
    }
    metrics = market._compute_chain_metrics(
        raw, spot, proxy, demo=False,
        experimental=market.instrument.proxy_experimental, term=None)
    metrics = _enrich_proxy_contract(market, metrics)
    density_strikes = np.asarray((metrics.get("density") or {}).get("strikes") or [], dtype=float)
    if len(density_strikes) < 5:
        raise RuntimeError("IV-smile fallback не дал валидной density-сетки")
    support_low = float(np.min(density_strikes)) / spot
    support_high = float(np.max(density_strikes)) / spot
    metrics["density_input"] = {
        "contract_version": OPTION_FEED_RESILIENCE_VERSION,
        "mode": "reported_iv_smile_bs_reconstruction",
        "raw_mid_quality": "rejected_by_canonical_adaptive_chain",
        "reported_iv_points": int(len(strikes)),
        "quality_tier": "experimental_proxy_iv_rescue",
        "mathematics": "same_existing_implied_move_bl_skew_gex_after_iv_smile_reconstruction",
    }
    return ChainCandidate(
        expiry=expiry,
        metrics=metrics,
        support_low_ratio=support_low,
        support_high_ratio=support_high,
        ordinal=ordinal,
    )


def _rescue_chain_from_reported_iv(market: MarketData, base_state: dict) -> None:
    proxy = market.instrument.options_proxy
    if market.demo or proxy is None or not market.instrument.proxy_experimental:
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

        candidates: list[ChainCandidate] = []
        rejected: list[str] = []
        for ordinal, expiry in enumerate(expiries[:MAX_EXPIRIES_TO_SCAN]):
            try:
                candidate = _iv_smile_candidate(market, ticker, proxy, expiry, spot, ordinal)
                candidates.append(candidate)
                if candidate.covers():
                    break
            except Exception as exc:
                rejected.append(f"{expiry}: {type(exc).__name__}: {str(exc)[:110]}")

        if not candidates:
            if base_state.get("metrics") is not None:
                market.chain = base_state
            elif rejected:
                market.chain = {
                    "metrics": None,
                    **_status_dict(
                        error=("raw-mid и IV-smile кандидаты невалидны: "
                               + " | ".join(rejected[-3:]))[:500]),
                    "density_input": {
                        "contract_version": OPTION_FEED_RESILIENCE_VERSION,
                        "mode": "unavailable",
                        "quality_tier": "no_valid_reported_iv_smile",
                    },
                }
            return

        selected = select_candidate(candidates)
        selected.metrics["term"] = market._fetch_term(ticker, expiries, spot)
        selection = {
            "contract_version": OPTION_FEED_RESILIENCE_VERSION,
            "mode": "adaptive_proxy_support_iv_smile_rescue",
            "support_moneyness": [
                round(selected.support_low_ratio, 6),
                round(selected.support_high_ratio, 6),
            ],
            "covered": selected.covers(),
            "selected_ordinal": selected.ordinal,
            "scanned": min(len(expiries), MAX_EXPIRIES_TO_SCAN),
            "valid_candidates": len(candidates),
            "scan_errors": rejected[:3],
        }
        selected.metrics["expiry_selection"] = selection
        if market.proxy_price.get("status") != "live":
            market.proxy_price = _status_dict(
                spot, "delayed", time.time(),
                source=f"yfinance REST {proxy} (chain snapshot)")
        market.chain = {
            "metrics": selected.metrics,
            **_status_dict(
                True, "delayed", time.time(),
                source=f"yfinance {proxy} options {selected.expiry} · IV-smile rescue"),
            "expiry_selection": selection,
        }
        market.chain["delay_hint_sec"] = 900
        market.cache.add_chain_snapshot(proxy, selected.metrics)
    except Exception:
        market.chain = base_state


def resilient_refresh_chain(self: MarketData) -> None:
    _BASE_REFRESH_CHAIN(self)
    base_state = dict(self.chain or {})
    metrics = base_state.get("metrics")
    source = str(base_state.get("source") or "")
    if isinstance(metrics, dict):
        _enrich_proxy_contract(self, metrics)
        self.chain["metrics"] = metrics
    if self.demo or self.instrument.options_proxy is None:
        return
    if isinstance(metrics, dict) and source != "кэш цепочки":
        return
    _rescue_chain_from_reported_iv(self, base_state)


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
                    & (strikes_raw > 0) & (ivs_raw > 0.005) & (ivs_raw < 3.0)
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
                source=f"yfinance {proxy} options (validated IV expiry scan)")
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
