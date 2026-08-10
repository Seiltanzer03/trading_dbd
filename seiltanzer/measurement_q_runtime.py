"""Phase F.3.2a forecast/capture integrity closure.

Keeps historical rows immutable while tightening only newly captured runtime data.
"""
from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any

import numpy as np

from .config import INSTRUMENTS
from . import option_q_adapter as _q
from . import passive_learning as _pl

MEASUREMENT_RUNTIME_VERSION = "measurement-runtime-f32a-v1"
_ALLOWED_ORIGINS = {"background_collector", "manual", "test", "replay"}
_KNOWN_FUTURE_REFERENCE_TS_KEYS = {
    "expiry_ts_utc", "source_expiry_ts_utc", "contract_expiry_ts"
}

_ENGINE = _pl.PassiveLearningEngine
_ORIGINAL_ADAPT = _q.adapt_option_q_forecast
_ORIGINAL_FORECAST = _ENGINE._forecast
_ORIGINAL_CAPTURE = _ENGINE.capture_observation
_ORIGINAL_COLLECT = _ENGINE._collect_instrument


def finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def valid_terminal_cdf(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    support, cdf = value.get("support"), value.get("cdf")
    if not isinstance(support, list) or not isinstance(cdf, list):
        return False
    if len(support) < 5 or len(support) != len(cdf):
        return False
    try:
        x = np.asarray(support, dtype=float)
        f = np.asarray(cdf, dtype=float)
    except (TypeError, ValueError):
        return False
    return bool(
        np.all(np.isfinite(x))
        and np.all(np.isfinite(f))
        and np.all(np.diff(x) > 0)
        and np.all(np.diff(f) >= -1e-10)
        and np.min(f) >= -1e-8
        and np.max(f) <= 1.0 + 1e-8
        and abs(float(f[0])) <= 1e-6
        and abs(float(f[-1]) - 1.0) <= 1e-6
    )


def _unavailable(option_metrics: dict | None, horizon_kind: str, method: str) -> dict:
    res = _ORIGINAL_ADAPT(
        None, 0, None, "NAS100", instrument_spot=1.0,
        horizon_kind=horizon_kind,
    )
    res["horizon_alignment_method"] = method
    res["proxy_symbol"] = option_metrics.get("proxy") if isinstance(option_metrics, dict) else None
    return res


def adapt_option_q_forecast_f32a(
    option_metrics: dict | None,
    horizon_minutes: int,
    sigma_h: float | None,
    instrument: str,
    instrument_spot: float | None,
    horizon_kind: str = "fixed_trading_time",
) -> dict:
    """Wire immutable config transform into producer data and validate frozen Q CDF."""
    metrics = deepcopy(option_metrics) if isinstance(option_metrics, dict) else option_metrics
    transform_source = "unavailable"
    if isinstance(metrics, dict):
        explicit = metrics.get("proxy_transform")
        if explicit is None and instrument in INSTRUMENTS:
            explicit = INSTRUMENTS[instrument].proxy_transform
            metrics["proxy_transform"] = explicit
            transform_source = "instrument_config"
        elif explicit is not None:
            transform_source = "producer"
        transform = str(explicit or "direct").lower()
        if transform not in {"direct", "inverse"}:
            bad = _unavailable(metrics, horizon_kind, "invalid_proxy_transform")
            bad["measurement_runtime_contract"] = MEASUREMENT_RUNTIME_VERSION
            return bad
        metrics["proxy_transform"] = transform

    res = _ORIGINAL_ADAPT(
        metrics, horizon_minutes, sigma_h, instrument,
        instrument_spot=instrument_spot, horizon_kind=horizon_kind,
    )
    res["proxy_transform_source"] = transform_source
    res["measurement_runtime_contract"] = MEASUREMENT_RUNTIME_VERSION
    if res.get("q_terminal_distribution_available") and not valid_terminal_cdf(
        res.get("terminal_q_cdf")
    ):
        bad = _unavailable(
            metrics if isinstance(metrics, dict) else None,
            horizon_kind, "invalid_terminal_q_cdf",
        )
        bad["proxy_transform_source"] = transform_source
        bad["measurement_runtime_contract"] = MEASUREMENT_RUNTIME_VERSION
        return bad
    return res


def walk_timestamps_f32a(value: Any, path: str = ""):
    """No-lookahead guard excluding only known-at-T0 future contract references."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key not in _KNOWN_FUTURE_REFERENCE_TS_KEYS:
                if key.endswith("_ts") or key in {"ts", "timestamp", "as_of"}:
                    number = finite(child)
                    if number is not None:
                        yield child_path, number
            yield from walk_timestamps_f32a(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_timestamps_f32a(child, f"{path}[{index}]")


def advance_trading_time_f32a(instrument: str, start: float, trading_minutes: int) -> float:
    """Advance by exactly requested open-session seconds, including partial minutes."""
    required = float(trading_minutes) * 60.0
    cursor, accumulated = float(start), 0.0
    deadline = cursor + 10 * 86400.0
    while accumulated + 1e-9 < required and cursor < deadline:
        minute_end = math.floor(cursor / 60.0) * 60.0 + 60.0
        segment_end = min(minute_end, deadline)
        if _pl._session_state(instrument, (cursor + segment_end) / 2.0)["is_open"]:
            available = segment_end - cursor
            take = min(available, required - accumulated)
            cursor += take
            accumulated += take
        else:
            cursor = segment_end
    if accumulated + 1e-6 < required:
        raise ValueError("unable to advance trading horizon")
    return cursor


def forecast_f32a(
    price: float,
    vol_info: dict | None,
    horizon_minutes: int,
    option_metrics: dict | None,
    instrument: str,
    horizon_kind: str = "fixed_trading_time",
) -> dict:
    result = dict(_ORIGINAL_FORECAST(
        price, vol_info, horizon_minutes, option_metrics, instrument,
        horizon_kind=horizon_kind,
    ))
    result["measurement_runtime_contract"] = MEASUREMENT_RUNTIME_VERSION
    if horizon_kind != "option_native_expiry":
        result.pop("terminal_q_cdf", None)
        result.pop("source_expiry_ts_utc", None)
        return result

    q_res = adapt_option_q_forecast_f32a(
        option_metrics, horizon_minutes, finite(result.get("sigma_h_return")),
        instrument, instrument_spot=price, horizon_kind=horizon_kind,
    )
    if q_res.get("q_terminal_distribution_available") and valid_terminal_cdf(
        q_res.get("terminal_q_cdf")
    ):
        for key in (
            "terminal_q_cdf", "source_expiry_ts_utc", "q_source_instrument",
            "q_target_instrument", "q_source_spot", "q_target_spot",
            "proxy_transform_source",
        ):
            result[key] = deepcopy(q_res.get(key))
        result["proxy_transform"] = q_res.get("proxy_transform")
    else:
        result.pop("terminal_q_cdf", None)
        result["q_terminal_distribution_available"] = False
        result["probability_measure"] = "unavailable"
        result["horizon_alignment_method"] = q_res.get(
            "horizon_alignment_method", "invalid_terminal_q_cdf"
        )
    return result


def capture_observation_f32a(
    self: _ENGINE,
    *,
    instrument: str,
    captured_ts: float,
    market_price: float,
    features: dict,
    forecast: dict,
    provenance: dict,
    trigger_reason: str = "cadence",
    evidence_eligible: bool = True,
    observation_origin: str | None = None,
) -> list[str]:
    internal_background = bool(getattr(self, "_f32a_background_capture", False))
    if trigger_reason == "test":
        origin = "test"
    elif observation_origin is None:
        origin = "background_collector" if internal_background else "manual"
    else:
        origin = str(observation_origin)
    if origin not in _ALLOWED_ORIGINS:
        raise ValueError("invalid observation_origin")
    if origin == "background_collector" and not internal_background:
        origin = "manual"

    effective_ts = float(captured_ts)
    frozen_features = deepcopy(features)
    frozen_provenance = deepcopy(provenance)
    if internal_background:
        # T0 is the instant the complete feature+forecast snapshot is frozen.
        effective_ts = time.time()
        source_ts = finite((frozen_features.get("price_state") or {}).get("ts"))
        if source_ts is None:
            source_ts = finite(frozen_features.get("source_observation_ts"))
        if source_ts is not None:
            frozen_provenance.setdefault("price", {})["age_sec"] = max(
                0.0, effective_ts - source_ts
            )

    # ACT/365 is recomputed from immutable source expiry rather than stale cached t_years.
    for key in ("option_derivatives",):
        wrapper = frozen_features.get(key)
        data = wrapper.get("data") if isinstance(wrapper, dict) else None
        if isinstance(data, dict):
            expiry = finite(data.get("expiry_ts_utc"))
            if expiry is not None and expiry > effective_ts:
                data["t_years"] = (expiry - effective_ts) / (365.0 * 86400.0)
                data["calendar_ttm_source"] = "expiry_ts_utc_minus_capture"
    distribution = frozen_features.get("option_distribution")
    if isinstance(distribution, dict):
        expiry = finite(distribution.get("expiry_ts_utc"))
        if expiry is not None and expiry > effective_ts:
            distribution["t_years"] = (expiry - effective_ts) / (365.0 * 86400.0)
            distribution["calendar_ttm_source"] = "expiry_ts_utc_minus_capture"

    frozen_features["measurement_runtime_contract"] = MEASUREMENT_RUNTIME_VERSION
    stored_eligible = bool(evidence_eligible)
    if origin == "background_collector":
        price_meta = frozen_provenance.get("price") or {}
        quality = finite(price_meta.get("quality")) or 0.0
        age = finite(price_meta.get("age_sec"))
        direct_fresh = (
            str(price_meta.get("kind") or "") == "direct"
            and quality >= 0.90
            and age is not None and age <= 60.0
        )
        stored_eligible = stored_eligible and direct_fresh and not self.settings.demo
        if not direct_fresh:
            self._contract_error_counters["non_direct_or_stale_t0_price_n"] += 1

    return _ORIGINAL_CAPTURE(
        self, instrument=instrument, captured_ts=effective_ts,
        market_price=market_price, features=frozen_features, forecast=forecast,
        provenance=frozen_provenance, trigger_reason=trigger_reason,
        evidence_eligible=stored_eligible, observation_origin=origin,
    )


def collect_instrument_f32a(self: _ENGINE, instrument: str, now: float) -> list[str]:
    self._f32a_background_capture = True
    try:
        created = _ORIGINAL_COLLECT(self, instrument, now)
    finally:
        self._f32a_background_capture = False
    if created:
        self._last_skip_reason = None
    return created


def install_q_runtime() -> None:
    if getattr(_ENGINE, "_measurement_q_runtime", None) == MEASUREMENT_RUNTIME_VERSION:
        return
    _pl._walk_timestamps = walk_timestamps_f32a
    _pl._advance_trading_time = advance_trading_time_f32a
    _q.adapt_option_q_forecast = adapt_option_q_forecast_f32a
    _pl.adapt_option_q_forecast = adapt_option_q_forecast_f32a
    _ENGINE._forecast = staticmethod(forecast_f32a)
    _ENGINE.capture_observation = capture_observation_f32a
    _ENGINE._collect_instrument = collect_instrument_f32a
    _ENGINE._measurement_q_runtime = MEASUREMENT_RUNTIME_VERSION
