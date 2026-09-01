"""Adaptive option-proxy chain selection and conservative tail fallback.

The legacy feed always selected the first expiry and treated finite
Breeden–Litzenberger support as the complete market support. Free ETF chains
frequently truncate that support, which disabled the option model for QQQ/EWU/
EWG/FXC even when the chain itself was available.

The patch scans several expiries and enables a conservative parametric tail only
for real cross-scale proxy mappings. Direct core calculations and demo chains
retain the original strict contract.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from ..core import options as opt
from .cache import production_chain_snapshot


REQUIRED_MONEYNESS = (0.78, 1.22)
MAX_EXPIRIES_TO_SCAN = 10
_MIN_VARIANCE_RATIO = 1e-8
_PROXY_SCALE_LOG_THRESHOLD = 0.05
_INSTALLED = False
_ORIGINAL_REFRESH_CHAIN = None
_ORIGINAL_INSTRUMENT_PROPERTY = None
_ORIGINAL_MARKET_R_DISTRIBUTION = opt.market_r_distribution
_ORIGINAL_MAP_PROXY_DENSITY = opt.map_proxy_density


@dataclass(frozen=True)
class ChainCandidate:
    expiry: str
    metrics: dict
    support_low_ratio: float
    support_high_ratio: float
    ordinal: int

    @property
    def balanced_width(self) -> float:
        return min(1.0 - self.support_low_ratio,
                   self.support_high_ratio - 1.0)

    def covers(self, required: tuple[float, float] = REQUIRED_MONEYNESS) -> bool:
        lo, hi = required
        return self.support_low_ratio <= lo and self.support_high_ratio >= hi


def select_candidate(candidates: list[ChainCandidate],
                     required: tuple[float, float] = REQUIRED_MONEYNESS
                     ) -> ChainCandidate:
    """Use the shortest covering expiry, otherwise the broadest balanced one."""
    if not candidates:
        raise ValueError("нет валидных опционных экспираций")
    for candidate in sorted(candidates, key=lambda item: item.ordinal):
        if candidate.covers(required):
            return candidate
    return max(
        candidates,
        key=lambda item: (
            item.balanced_width,
            item.support_high_ratio - item.support_low_ratio,
            -item.ordinal,
        ),
    )


def _mid(bid, ask, last) -> np.ndarray:
    return np.asarray(
        np.where((bid > 0) & (ask > 0), (bid + ask) / 2.0, last),
        dtype=float,
    )


def _raw_chain(ticker: Any, expiry: str, spot: float) -> dict:
    exp_dt = dt.datetime.strptime(expiry, "%Y-%m-%d").replace(
        hour=21, tzinfo=dt.timezone.utc)
    t_years = max(
        (exp_dt - dt.datetime.now(dt.timezone.utc)).total_seconds(),
        3600.0,
    ) / (365.0 * 24 * 3600)
    chain = ticker.option_chain(expiry)
    merged = chain.calls.merge(chain.puts, on="strike", suffixes=("_c", "_p"))
    if len(merged) < 5:
        raise RuntimeError(f"слишком мало общих страйков: {len(merged)}")
    return {
        "strikes": merged["strike"].to_numpy(dtype=float),
        "call_mid": _mid(
            merged["bid_c"].fillna(0).to_numpy(),
            merged["ask_c"].fillna(0).to_numpy(),
            merged["lastPrice_c"].fillna(np.nan).to_numpy(),
        ),
        "put_mid": _mid(
            merged["bid_p"].fillna(0).to_numpy(),
            merged["ask_p"].fillna(0).to_numpy(),
            merged["lastPrice_p"].fillna(np.nan).to_numpy(),
        ),
        "call_oi": merged["openInterest_c"].fillna(0).to_numpy(dtype=float),
        "put_oi": merged["openInterest_p"].fillna(0).to_numpy(dtype=float),
        "call_iv": merged["impliedVolatility_c"].to_numpy(dtype=float),
        "put_iv": merged["impliedVolatility_p"].to_numpy(dtype=float),
        "t_years": t_years,
        "spot": spot,
        "expiry": expiry,
    }


def _support_ratios(metrics: dict, spot: float) -> tuple[float, float]:
    strikes = ((metrics.get("density") or {}).get("strikes") or [])
    if len(strikes) < 3 or not math.isfinite(spot) or spot <= 0:
        raise ValueError("нет валидной support-сетки")
    low, high = float(min(strikes)) / spot, float(max(strikes)) / spot
    if not (0 < low < high and math.isfinite(low) and math.isfinite(high)):
        raise ValueError("некорректные границы support-сетки")
    return low, high


def _cache_fallback(self, proxy: str, error: Exception, status_dict) -> None:
    snapshots = [
        snapshot for snapshot in self.cache.chain_snapshots(proxy, limit=60)
        if production_chain_snapshot(snapshot)
    ]
    if snapshots and time.time() - snapshots[-1]["ts"] < 24 * 3600:
        self.chain = {
            "metrics": snapshots[-1],
            **status_dict(
                True,
                "delayed",
                snapshots[-1]["ts"],
                error=str(error)[:200],
                source="кэш цепочки",
            ),
            "cache_fallback": {
                "used": True,
                "snapshot_provenance": "explicit_real_demo_false",
            },
        }
    else:
        self.chain = {
            "metrics": None,
            **status_dict(error=str(error)[:200]),
            "cache_fallback": {
                "used": False,
                "reason": "no_explicitly_real_snapshot",
                "demo_or_unverified_rejected": True,
            },
        }


def _adaptive_refresh_chain(self, status_dict) -> None:
    proxy = self.instrument.options_proxy
    if self.demo or proxy is None:
        return _ORIGINAL_REFRESH_CHAIN(self)
    try:
        import yfinance as yf

        ticker = yf.Ticker(proxy)
        expiries = list(ticker.options or [])
        if not expiries:
            raise RuntimeError(f"{proxy}: источник не вернул экспирации")
        spot = float(ticker.fast_info.last_price)
        if not math.isfinite(spot) or spot <= 0:
            raise RuntimeError(f"{proxy}: нет валидной цены proxy")
        if self.proxy_price.get("status") != "live":
            self.proxy_price = status_dict(
                spot,
                "delayed",
                time.time(),
                source=f"yfinance REST {proxy} (chain snapshot)",
            )

        candidates: list[ChainCandidate] = []
        scan_errors: list[str] = []
        for ordinal, expiry in enumerate(expiries[:MAX_EXPIRIES_TO_SCAN]):
            try:
                raw = _raw_chain(ticker, expiry, spot)
                metrics = self._compute_chain_metrics(
                    raw,
                    spot,
                    proxy,
                    demo=False,
                    experimental=self.instrument.proxy_experimental,
                    term=None,
                )
                low, high = _support_ratios(metrics, spot)
                candidate = ChainCandidate(
                    expiry=expiry,
                    metrics=metrics,
                    support_low_ratio=low,
                    support_high_ratio=high,
                    ordinal=ordinal,
                )
                candidates.append(candidate)
                if candidate.covers():
                    break
            except Exception as exc:
                scan_errors.append(f"{expiry}: {str(exc)[:100]}")

        selected = select_candidate(candidates)
        selected.metrics["term"] = self._fetch_term(ticker, expiries, spot)
        selected.metrics["expiry_selection"] = {
            "mode": "adaptive_proxy_support",
            "required_moneyness": list(REQUIRED_MONEYNESS),
            "support_moneyness": [
                round(selected.support_low_ratio, 6),
                round(selected.support_high_ratio, 6),
            ],
            "covered": selected.covers(),
            "selected_ordinal": selected.ordinal,
            "scanned": min(len(expiries), MAX_EXPIRIES_TO_SCAN),
            "valid_candidates": len(candidates),
            "scan_errors": scan_errors[:3],
        }
        self.chain = {
            "metrics": selected.metrics,
            **status_dict(
                True,
                "delayed",
                time.time(),
                source=f"yfinance {proxy} options {selected.expiry} · adaptive",
            ),
        }
        self.chain["delay_hint_sec"] = 900
        self.cache.add_chain_snapshot(proxy, selected.metrics)
    except Exception as exc:
        _cache_fallback(self, proxy, exc, status_dict)


def _trapz(y, x) -> float:
    fn = getattr(np, "trapezoid", None)
    return float((fn if fn is not None else np.trapz)(y, x))


def _normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def _normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _log_lower_tail(z: float) -> float:
    if z < -8.0:
        return -0.5 * z * z - math.log(-z) - 0.5 * math.log(2.0 * math.pi)
    return math.log(max(_normal_cdf(z), 1e-300))


def _log_upper_tail(z: float) -> float:
    return _log_lower_tail(-z)


def _ratio_from_logs(log_a: float, log_b: float) -> float:
    delta = log_b - log_a
    if delta > 700:
        return 0.0
    if delta < -700:
        return 1.0
    return 1.0 / (1.0 + math.exp(delta))


def map_proxy_density(density: opt.RNDensity, proxy_spot: float,
                      instrument_spot: float,
                      transform: str = "direct") -> opt.RNDensity:
    """Mark only genuine cross-scale proxy mappings for tail extrapolation."""
    mapped = _ORIGINAL_MAP_PROXY_DENSITY(
        density, proxy_spot, instrument_spot, transform
    )
    ratio = float(instrument_spot) / float(proxy_spot)
    if ratio > 0 and abs(math.log(ratio)) > _PROXY_SCALE_LOG_THRESHOLD:
        setattr(mapped, "_allow_proxy_tail_extrapolation", True)
    return mapped


def _parametric_tail_distribution(density: opt.RNDensity, scale: float,
                                  entry: float, stop: float, take: float,
                                  direction: str, T: float,
                                  n_bins: int) -> dict:
    strikes = np.asarray(density.strikes, dtype=float) * float(scale)
    q = np.asarray(density.density, dtype=float) / max(float(scale), 1e-12)
    ok = np.isfinite(strikes) & np.isfinite(q) & (strikes > 0) & (q >= 0)
    strikes, q = strikes[ok], q[ok]
    if len(strikes) < 5:
        raise ValueError("слишком мало точек для tail extrapolation")
    area = _trapz(q, strikes)
    if not math.isfinite(area) or area <= 0:
        raise ValueError("нулевая площадь density")
    q = q / area
    mean = _trapz(strikes * q, strikes)
    second = _trapz(strikes * strikes * q, strikes)
    variance = max(second - mean * mean, mean * mean * _MIN_VARIANCE_RATIO)
    sigma2 = math.log1p(variance / max(mean * mean, 1e-18))
    sigma = math.sqrt(max(sigma2, _MIN_VARIANCE_RATIO))
    mu = math.log(mean) - 0.5 * sigma2

    def z(level: float) -> float:
        return (math.log(max(float(level), 1e-12)) - mu) / sigma

    z_take, z_stop = z(take), z(stop)
    if direction == "long":
        p_take, p_stop = _normal_sf(z_take), _normal_cdf(z_stop)
        log_take, log_stop = _log_upper_tail(z_take), _log_lower_tail(z_stop)
    else:
        p_take, p_stop = _normal_cdf(z_take), _normal_sf(z_stop)
        log_take, log_stop = _log_lower_tail(z_take), _log_upper_tail(z_stop)
    hit_ratio = _ratio_from_logs(log_take, log_stop)

    risk = abs(entry - stop)
    edges = np.linspace(-1.0, T, n_bins + 1)

    def price_of_r(rv: float) -> float:
        return entry + rv * risk if direction == "long" else entry - rv * risk

    probs = np.zeros(n_bins, dtype=float)
    for index in range(n_bins):
        a, b = price_of_r(edges[index]), price_of_r(edges[index + 1])
        lo, hi = min(a, b), max(a, b)
        probs[index] = max(_normal_cdf(z(hi)) - _normal_cdf(z(lo)), 0.0)
    probs[0] += p_stop
    probs[-1] += p_take
    total = float(probs.sum())
    if total <= 0:
        raise ValueError("tail extrapolation дала нулевое распределение")
    probs /= total
    mean_r = ((mean - entry) / risk if direction == "long"
              else (entry - mean) / risk)
    return {
        "edges": edges.tolist(),
        "probs": probs.tolist(),
        "p_take": float(p_take),
        "p_stop": float(p_stop),
        "hit_ratio": float(hit_ratio),
        "mean_r": float(mean_r),
        "barriers_supported": True,
        "tail_anchor_supported": True,
        "tail_mass": float(p_take + p_stop),
        "support_low": float(min(strikes[0], stop, take)),
        "support_high": float(max(strikes[-1], stop, take)),
        "observed_support_low": float(strikes[0]),
        "observed_support_high": float(strikes[-1]),
        "tail_extrapolated": True,
        "tail_method": "moment_matched_lognormal",
    }


def market_r_distribution(density: opt.RNDensity, scale: float, entry: float,
                          stop: float, take: float, direction: str,
                          T: float, n_bins: int = 11) -> dict:
    empirical = _ORIGINAL_MARKET_R_DISTRIBUTION(
        density, scale, entry, stop, take, direction, T, n_bins
    )
    allowed = bool(getattr(density, "_allow_proxy_tail_extrapolation", False))
    if empirical.get("hit_ratio") is not None or not allowed:
        empirical["tail_extrapolated"] = False
        empirical["tail_method"] = "observed_bl_support"
        return empirical
    fallback = _parametric_tail_distribution(
        density, scale, entry, stop, take, direction, T, n_bins
    )
    fallback["observed_barriers_supported"] = empirical.get("barriers_supported")
    fallback["observed_tail_anchor_supported"] = empirical.get("tail_anchor_supported")
    fallback["observed_tail_mass"] = empirical.get("tail_mass")
    return fallback


def install(feeds_module) -> None:
    global _INSTALLED, _ORIGINAL_REFRESH_CHAIN, _ORIGINAL_INSTRUMENT_PROPERTY
    if _INSTALLED:
        return
    _ORIGINAL_REFRESH_CHAIN = feeds_module.MarketData.refresh_chain
    _ORIGINAL_INSTRUMENT_PROPERTY = feeds_module.MarketData.instrument

    def instrument(self):
        base = _ORIGINAL_INSTRUMENT_PROPERTY.fget(self)
        if (self.instrument_code == "JPY100" and not self.demo
                and base.options_proxy is None):
            return replace(base, options_proxy="EWJ", proxy_experimental=True)
        return base

    def refresh_chain(self):
        return _adaptive_refresh_chain(self, feeds_module._status_dict)

    feeds_module.MarketData.instrument = property(instrument)
    feeds_module.MarketData.refresh_chain = refresh_chain
    opt.map_proxy_density = map_proxy_density
    opt.market_r_distribution = market_r_distribution
    _INSTALLED = True
