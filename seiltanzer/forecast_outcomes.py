"""Horizon-aligned first-passage labels for prospective Q forecasts."""
from __future__ import annotations

import math
from typing import Iterable

from .execution_simulator import ExecutionSpec, replay_execution_path


FORECAST_OUTCOME_VERSION = "forecast-outcome-f1-v1"


def _finite(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def resolve_forecast_outcome(forecast: dict, observed_path: Iterable[dict],
                             horizon_minutes: float | None = None) -> dict:
    """Resolve only the market path inside ``[prediction_ts, T0 + H]``.

    A disappearing path is censored, never labelled NO_TOUCH. Actual trade P&L,
    lifetime MFE and notes are deliberately absent from this interface.
    """
    forecast_id = str(forecast.get("forecast_id") or forecast.get("id") or "")
    prediction_ts = _finite(forecast.get("prediction_ts"))
    if prediction_ts is None:
        prediction_ts = _finite(forecast.get("ts"))
    horizon = _finite(horizon_minutes)
    if horizon is None:
        horizon = _finite(forecast.get("horizon_minutes"))
    current_r = _finite(forecast.get("r"))
    take_r = _finite(forecast.get("take_r"))
    max_r = _finite(forecast.get("max_r"))
    be_after = _finite(forecast.get("be_after_r"))
    base = {
        "forecast_id": forecast_id,
        "prediction_ts": prediction_ts,
        "horizon_end_ts": (
            prediction_ts + horizon * 60.0
            if prediction_ts is not None and horizon is not None else None),
        "resolved": False,
        "event": "censored",
        "event_ts": None,
        "path_complete": False,
        "resolution_version": FORECAST_OUTCOME_VERSION,
        "path_semantics": "market_path_forecast_resolution_independent_of_position_closure",
    }
    if None in (prediction_ts, horizon, current_r, take_r, max_r, be_after) or horizon <= 0:
        return {**base, "reason": "forecast_contract_incomplete"}
    horizon_end = prediction_ts + horizon * 60.0
    rows = []
    for row in observed_path:
        ts, r = _finite(row.get("ts")), _finite(row.get("r"))
        if ts is None or r is None or ts < prediction_ts - 1e-9:
            continue
        rows.append({"ts": ts, "r": r})
    rows.sort(key=lambda row: row["ts"])
    rows = list({row["ts"]: row for row in rows}.values())
    rows.sort(key=lambda row: row["ts"])
    if not rows or rows[0]["ts"] > prediction_ts + 1e-9:
        rows.insert(0, {"ts": prediction_ts, "r": current_r})
    else:
        rows[0] = {"ts": prediction_ts, "r": current_r}
    # Retain no information beyond H. The first point after H is used only to
    # linearly interpolate the boundary value at H, then discarded.
    clipped = [row for row in rows if row["ts"] <= horizon_end + 1e-9]
    after = next((row for row in rows if row["ts"] > horizon_end + 1e-9), None)
    if after is not None and clipped:
        previous = clipped[-1]
        span = after["ts"] - previous["ts"]
        fraction = (horizon_end - previous["ts"]) / max(span, 1e-12)
        clipped.append({
            "ts": horizon_end,
            "r": previous["r"] + (after["r"] - previous["r"]) * fraction,
        })
    rows = clipped

    spec = ExecutionSpec.from_values(
        current_r=current_r, max_r=max_r, take_r=take_r,
        rungs=(), rung_fraction_original=0.0, be_after_r=be_after,
    )
    result = replay_execution_path([row["r"] for row in rows], spec)
    if result.exit_reason in ("take", "stop", "breakeven"):
        left = rows[max(0, result.exit_step - 1)]["ts"]
        right = rows[result.exit_step]["ts"]
        event_ts = left + (right - left) * result.exit_fraction
        event = "take" if result.exit_reason == "take" else "stop_or_be"
        return {
            **base, "resolved": True, "event": event,
            "event_ts": event_ts, "path_complete": True,
            "realized_r_at_resolution": result.exit_r,
            "execution_reason": result.exit_reason,
        }

    path_complete = bool(rows and rows[-1]["ts"] >= horizon_end - 1e-6)
    if path_complete:
        return {
            **base, "resolved": True, "event": "no_touch",
            "event_ts": horizon_end, "path_complete": True,
            "realized_r_at_resolution": rows[-1]["r"],
            "execution_reason": "horizon",
        }
    return {
        **base, "reason": "observed_market_path_ends_before_forecast_horizon",
        "last_observed_ts": rows[-1]["ts"] if rows else None,
    }
