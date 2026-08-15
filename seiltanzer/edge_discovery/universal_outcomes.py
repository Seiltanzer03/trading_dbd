"""Strategy-agnostic future market-path outcomes for continuous edge research.

The contract deliberately describes what the market did after T0, not whether a
user trade won.  Barrier distances are normalized by a volatility scale frozen
at T0; future volatility is an outcome and is never used to define the barrier.
"""
from __future__ import annotations

import math
from typing import Any, Iterable


UNIVERSAL_OUTCOME_CONTRACT_VERSION = "g1s-universal-market-outcome-v1"
T0_SCALE_CONTRACT_VERSION = "g1s-local-rv60-sqrt-time-scale-v1"

# (up sigma multiple, down sigma multiple).  These are market geometries, not
# stop/take or RR definitions.
BARRIER_PAIRS: tuple[tuple[float, float], ...] = (
    (0.5, 0.5),
    (1.0, 0.5),
    (0.5, 1.0),
    (1.0, 1.0),
)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def causal_local_sigma_h(realized_vol_60m: Any, horizon_minutes: int) -> float | None:
    """Scale a pre-T0 60m realized-volatility magnitude to horizon H.

    ``realized_vol_60m`` is expected to be the root-sum-square log-return
    magnitude already frozen at T0 by the G1S feature contract.  The square-root
    scaling is intentionally simple and versioned; it is a normalization scale,
    not a forecast claim.
    """
    rv60 = _finite(realized_vol_60m)
    horizon = int(horizon_minutes)
    if rv60 is None or rv60 <= 0.0 or horizon <= 0:
        return None
    return float(rv60 * math.sqrt(horizon / 60.0))


def _bar_rows(bars: Iterable[dict[str, Any]], *, captured_ts: float,
              target_ts: float | None) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for raw in bars:
        start = _finite(raw.get("bar_start_ts"))
        end = _finite(raw.get("bar_end_ts"))
        high = _finite(raw.get("high"))
        low = _finite(raw.get("low"))
        close = _finite(raw.get("close"))
        if None in (start, end, high, low, close):
            continue
        assert start is not None and end is not None and high is not None
        assert low is not None and close is not None
        if end <= captured_ts + 1e-9:
            continue
        if target_ts is not None and end > target_ts + 1e-6:
            continue
        if min(high, low, close) <= 0.0 or high < low:
            continue
        out.append({
            "bar_start_ts": start,
            "bar_end_ts": end,
            "high": high,
            "low": low,
            "close": close,
        })
    out.sort(key=lambda row: (row["bar_start_ts"], row["bar_end_ts"]))
    return out


def _point_rows(points: Iterable[dict[str, Any]], *, captured_ts: float,
                target_ts: float | None) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for raw in points:
        ts = _finite(raw.get("ts"))
        price = _finite(raw.get("price", raw.get("close")))
        if ts is None or price is None or price <= 0.0 or ts <= captured_ts + 1e-9:
            continue
        if target_ts is not None and ts > target_ts + 1e-6:
            continue
        out.append({"ts": ts, "price": price})
    dedup = {row["ts"]: row for row in out}
    return [dedup[key] for key in sorted(dedup)]


def _bar_path_stats(start_price: float, bars: list[dict[str, float]]) -> dict[str, Any]:
    if not bars:
        return {
            "terminal_price": None,
            "mfe_log_return": None,
            "mae_log_return": None,
            "forward_rv_log_return": None,
        }
    highs = [math.log(row["high"] / start_price) for row in bars]
    lows = [math.log(row["low"] / start_price) for row in bars]
    previous = start_price
    squared = 0.0
    for row in bars:
        step = math.log(row["close"] / previous)
        squared += step * step
        previous = row["close"]
    return {
        "terminal_price": float(bars[-1]["close"]),
        "mfe_log_return": float(max(highs)),
        "mae_log_return": float(min(lows)),
        "forward_rv_log_return": float(math.sqrt(max(0.0, squared))),
    }


def _point_path_stats(start_price: float, points: list[dict[str, float]]) -> dict[str, Any]:
    if not points:
        return {
            "terminal_price": None,
            "mfe_log_return": None,
            "mae_log_return": None,
            "forward_rv_log_return": None,
        }
    returns = [math.log(row["price"] / start_price) for row in points]
    # Irregular point spacing does not have the same meaning as completed-bar RV.
    return {
        "terminal_price": float(points[-1]["price"]),
        "mfe_log_return": float(max(returns)),
        "mae_log_return": float(min(returns)),
        "forward_rv_log_return": None,
    }


def _barrier_id(up_sigma: float, down_sigma: float) -> str:
    def token(value: float) -> str:
        text = (f"{value:.3f}").rstrip("0").rstrip(".")
        return text.replace(".", "p")
    return f"up_{token(up_sigma)}s_down_{token(down_sigma)}s"


def _bar_first_touch(
    bars: list[dict[str, float]], *, start_price: float, sigma_h: float,
    up_sigma: float, down_sigma: float, captured_ts: float, path_complete: bool,
) -> dict[str, Any]:
    upper = float(up_sigma * sigma_h)
    lower = float(-down_sigma * sigma_h)
    for row in bars:
        high_ret = math.log(row["high"] / start_price)
        low_ret = math.log(row["low"] / start_price)
        upper_hit = high_ret >= upper
        lower_hit = low_ret <= lower
        event_ts = float(row["bar_end_ts"])
        if upper_hit and lower_hit:
            return {
                "label": "AMBIGUOUS_SAME_BAR",
                "event_ts": event_ts,
                "calendar_minutes_to_event": (event_ts-captured_ts)/60.0,
                "clean_label": False,
                "ambiguous_same_bar": True,
                "up_threshold_log_return": upper,
                "down_threshold_log_return": lower,
                "timestamp_semantics": "BAR_END_UPPER_BOUND_ON_INTRABAR_TOUCH_TIME",
            }
        if upper_hit:
            return {
                "label": "UP_FIRST",
                "event_ts": event_ts,
                "calendar_minutes_to_event": (event_ts-captured_ts)/60.0,
                "clean_label": bool(path_complete),
                "ambiguous_same_bar": False,
                "up_threshold_log_return": upper,
                "down_threshold_log_return": lower,
                "timestamp_semantics": "BAR_END_UPPER_BOUND_ON_INTRABAR_TOUCH_TIME",
            }
        if lower_hit:
            return {
                "label": "DOWN_FIRST",
                "event_ts": event_ts,
                "calendar_minutes_to_event": (event_ts-captured_ts)/60.0,
                "clean_label": bool(path_complete),
                "ambiguous_same_bar": False,
                "up_threshold_log_return": upper,
                "down_threshold_log_return": lower,
                "timestamp_semantics": "BAR_END_UPPER_BOUND_ON_INTRABAR_TOUCH_TIME",
            }
    return {
        "label": "NO_TOUCH" if path_complete else "CENSORED",
        "event_ts": None,
        "calendar_minutes_to_event": None,
        "clean_label": bool(path_complete),
        "ambiguous_same_bar": False,
        "up_threshold_log_return": upper,
        "down_threshold_log_return": lower,
        "timestamp_semantics": "NO_EVENT_TIMESTAMP",
    }


def _point_first_touch(
    points: list[dict[str, float]], *, start_price: float, sigma_h: float,
    up_sigma: float, down_sigma: float, captured_ts: float, path_complete: bool,
) -> dict[str, Any]:
    upper = float(up_sigma * sigma_h)
    lower = float(-down_sigma * sigma_h)
    for row in points:
        value = math.log(row["price"] / start_price)
        if value >= upper or value <= lower:
            event_ts = float(row["ts"])
            return {
                "label": "UP_FIRST" if value >= upper else "DOWN_FIRST",
                "event_ts": event_ts,
                "calendar_minutes_to_event": (event_ts-captured_ts)/60.0,
                # Point sampling can miss an earlier excursion between samples.
                "clean_label": False,
                "ambiguous_same_bar": False,
                "up_threshold_log_return": upper,
                "down_threshold_log_return": lower,
                "timestamp_semantics": "OBSERVED_POINT_NOT_EXACT_FIRST_PASSAGE",
            }
    return {
        "label": "NO_TOUCH" if path_complete else "CENSORED",
        "event_ts": None,
        "calendar_minutes_to_event": None,
        "clean_label": False,
        "ambiguous_same_bar": False,
        "up_threshold_log_return": upper,
        "down_threshold_log_return": lower,
        "timestamp_semantics": "POINT_PATH_CANNOT_PROVE_CLEAN_NO_TOUCH",
    }


def resolve_universal_market_outcome(
    *, start_price: Any, captured_ts: Any, target_ts: Any,
    horizon_minutes: int, t0_realized_vol_60m: Any,
    bars: Iterable[dict[str, Any]] = (), points: Iterable[dict[str, Any]] = (),
    path_complete: bool,
) -> dict[str, Any]:
    """Resolve a versioned market-path label with no user strategy semantics."""
    start = _finite(start_price)
    captured = _finite(captured_ts)
    target = _finite(target_ts)
    sigma_h = causal_local_sigma_h(t0_realized_vol_60m, horizon_minutes)
    base = {
        "contract_version": UNIVERSAL_OUTCOME_CONTRACT_VERSION,
        "scale_contract_version": T0_SCALE_CONTRACT_VERSION,
        "strategy_agnostic": True,
        "horizon_minutes": int(horizon_minutes),
        "captured_ts": captured,
        "target_ts": target,
        "t0_realized_vol_60m": _finite(t0_realized_vol_60m),
        "t0_local_sigma_h": sigma_h,
        "normalization_uses_future_data": False,
        "production_authority": False,
    }
    if start is None or start <= 0.0 or captured is None or target is None or target <= captured:
        return {**base, "available": False, "reason": "INVALID_T0_OR_HORIZON"}
    if sigma_h is None or sigma_h <= 0.0:
        return {**base, "available": False, "reason": "T0_VOLATILITY_SCALE_UNAVAILABLE"}

    clean_bars = _bar_rows(bars, captured_ts=captured, target_ts=target)
    clean_points = _point_rows(points, captured_ts=captured, target_ts=target)
    if clean_bars:
        source = "RECORDED_OHLC_BARS"
        stats = _bar_path_stats(start, clean_bars)
        resolver = _bar_first_touch
        path = clean_bars
        path_count = len(clean_bars)
    elif clean_points:
        source = "RECORDED_POINTS"
        stats = _point_path_stats(start, clean_points)
        resolver = _point_first_touch
        path = clean_points
        path_count = len(clean_points)
    else:
        return {**base, "available": False, "reason": "NO_FUTURE_PATH"}

    terminal_price = stats["terminal_price"]
    assert terminal_price is not None
    terminal_log_return = float(math.log(float(terminal_price) / start))
    direction = "UP" if terminal_log_return > 0.0 else (
        "DOWN" if terminal_log_return < 0.0 else "FLAT"
    )
    barriers: dict[str, Any] = {}
    for up_sigma, down_sigma in BARRIER_PAIRS:
        barriers[_barrier_id(up_sigma, down_sigma)] = resolver(
            path,
            start_price=start,
            sigma_h=sigma_h,
            up_sigma=up_sigma,
            down_sigma=down_sigma,
            captured_ts=captured,
            path_complete=bool(path_complete),
        )

    mfe = stats["mfe_log_return"]
    mae = stats["mae_log_return"]
    return {
        **base,
        "available": True,
        "path_source": source,
        "path_complete": bool(path_complete),
        "path_count": path_count,
        "terminal_log_return": terminal_log_return,
        "direction_label": direction,
        "mfe_log_return": mfe,
        "mae_log_return": mae,
        "mfe_sigma": (float(mfe / sigma_h) if mfe is not None else None),
        "mae_sigma": (float(mae / sigma_h) if mae is not None else None),
        "forward_rv_log_return": stats["forward_rv_log_return"],
        "barriers": barriers,
        "barrier_pairs": [
            {"up_sigma": up, "down_sigma": down, "id": _barrier_id(up, down)}
            for up, down in BARRIER_PAIRS
        ],
        "contains_user_entry": False,
        "contains_user_stop": False,
        "contains_user_take": False,
        "contains_user_rr": False,
    }
