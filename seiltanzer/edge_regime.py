"""Bounded causal regime context; working thresholds are not predictive edge.

Only explicitly verified direct source bars and already published observations
are eligible. A missing authority contract yields UNKNOWN, not a proxy guess.
"""
from __future__ import annotations

import math
from contextlib import nullcontext
from typing import Any

from .canonical_market_context import canonical_instrument_code

CONTRACT_VERSION = "edge-regime-working-v1"
AUTHORITY_CONTRACT = "completed-direct-minute-authority-v1"
THRESHOLDS = {
    "window_minutes": 60, "min_completed_minutes": 61,
    "max_price_age_sec": 180., "event_window_sec": 1800.,
    "trend_efficiency_min": .65, "trend_abs_log_return_min": .0002,
    "range_efficiency_max": .35, "stress_rv_ratio_min": 2.5,
    "stress_rv_min": .00001, "stress_last_return_multiple": 4.,
    "stress_last_abs_log_return_min": .0002,
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _events(snapshot: dict, cutoff: float, instrument: str) -> list[dict]:
    """Observed release presence only: no calendar guess or surprise forecast."""
    root = _dict(snapshot.get("macro_context_v1") or snapshot.get("macro_t0_context"))
    rows = [(str(key), _dict(row), True) for key, row in
            _dict(_dict(root.get("numeric_macro")).get("releases")).items()]
    rows += [(key, _dict(root.get(key)), True) for key in ("fomc", "fomc_deterministic")]
    supplied = _dict(snapshot.get("edge_family_sources")).get("event", [])
    supplied = [supplied] if isinstance(supplied, dict) else supplied
    if isinstance(supplied, list):
        rows += [(str(row.get("event_type") or "release"), row, False)
                 for row in supplied[:32] if isinstance(row, dict)]
    accepted = {}
    for family, row, official_context in rows[:64]:
        published = _number(row.get("published_at"))
        received = _number(row.get("available_at", row.get("received_ts")))
        source_id = str(row.get("release_id") or row.get("document_id") or row.get("source_id") or "")
        verified = row.get("official_source_verified") is True if official_context else row.get("source_verified") is True
        valid = row.get("status") == "VALID" or row.get("available") is True
        related = official_context or row.get("global_context") is True or canonical_instrument_code(row.get("instrument")) == instrument
        if (not verified or not valid or not related or not source_id
                or published is None or received is None
                or not 0 < published <= received <= cutoff
                or cutoff - published > THRESHOLDS["event_window_sec"]):
            continue
        accepted[source_id] = {"source_id": source_id, "family": family,
                               "published_at": published, "available_at": received,
                               "age_sec": cutoff - published}
    return sorted(accepted.values(), key=lambda row: row["published_at"], reverse=True)[:8]


def classify_edge_regime(raw_bars: Any, source_authority: Any, *,
                         instrument: str, captured_ts: Any,
                         snapshot: dict | None = None) -> dict:
    """Classify one frozen T0 from at most 4096 completed direct minute bars."""
    code = canonical_instrument_code(instrument)
    cutoff = _number(captured_ts)
    authority = _dict(source_authority)
    output = {"contract_version": CONTRACT_VERSION, "instrument": code,
              "captured_ts": cutoff, "regime": "UNKNOWN", "price_regime": "UNKNOWN",
              "available": False, "status": "UNKNOWN", "reason": "SOURCE_AUTHORITY_UNAVAILABLE",
              "quality": 0., "feature_values": {}, "causal_clocks": {},
              "source_authority": {}, "observed_events": [],
              "thresholds": dict(THRESHOLDS), "threshold_status": "WORKING_UNCALIBRATED",
              "management_applicability_only": True, "independent_evidence_vote": False,
              "automatic_execution": False, "hard_risk_modified": False,
              "training_proof": False, "profit_proof": False, "dynamic_weight_authority": False}

    def reject(reason: str) -> dict:
        output["reason"] = reason
        return output

    if not code or cutoff is None or cutoff <= 0:
        return reject("INSTRUMENT_OR_T0_UNAVAILABLE")
    observed = _number(authority.get("observed_ts"))
    received = _number(authority.get("available_at", authority.get("received_ts")))
    if (authority.get("contract_version") != AUTHORITY_CONTRACT
            or authority.get("source_verified") is not True
            or not str(authority.get("source_id") or "")
            or authority.get("direct_source") is not True
            or authority.get("derived") is not False
            or authority.get("proxy") is not False
            or _number(authority.get("interval_sec")) != 60):
        return reject("SOURCE_AUTHORITY_UNAVAILABLE_OR_NOT_DIRECT")
    if canonical_instrument_code(authority.get("source_instrument")) != code:
        return reject("SOURCE_INSTRUMENT_MISMATCH")
    if observed is None or received is None or not 0 < observed <= received <= cutoff:
        return reject("SOURCE_CLOCK_MISSING_OR_AFTER_T0")
    source_quality = _number(authority.get("quality", 1.))
    if source_quality is None or not 0 < source_quality <= 1:
        return reject("SOURCE_QUALITY_INVALID")
    output["source_authority"] = {key: authority[key] for key in (
        "contract_version", "source_id", "source_instrument", "direct_source",
        "source_verified", "derived", "proxy", "interval_sec")}
    if not isinstance(raw_bars, (list, tuple)):
        return reject("COMPLETED_DIRECT_BARS_UNAVAILABLE")
    rows = {}
    for row in raw_bars[-4096:]:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        start = _number(row[0])
        if start is None or start + 60 > cutoff or start < cutoff - 4 * 3600:
            continue
        if start != int(start) or int(start) % 60:
            return reject("MINUTE_BAR_CLOCK_INVALID")
        values = [_number(value) for value in row[1:5]]
        if any(value is None or value <= 0 for value in values):
            return reject("DIRECT_BAR_PRICE_INVALID")
        opened, high, low, close = values
        if low > min(opened, close) or high < max(opened, close) or low > high:
            return reject("DIRECT_BAR_OHLC_INCONSISTENT")
        if start in rows:
            return reject("DIRECT_BAR_CLOCK_DUPLICATE")
        rows[start] = close
    ordered = sorted(rows.items())[-THRESHOLDS["min_completed_minutes"]:]
    if len(ordered) < THRESHOLDS["min_completed_minutes"]:
        return reject("INSUFFICIENT_COMPLETED_DIRECT_MINUTES")
    if any(b[0] - a[0] != 60 for a, b in zip(ordered, ordered[1:])):
        return reject("COMPLETED_DIRECT_MINUTE_CONTINUITY_MISSING")
    latest = ordered[-1][0] + 60
    if observed < latest:
        return reject("BAR_END_AFTER_SOURCE_OBSERVATION")
    if cutoff - latest > THRESHOLDS["max_price_age_sec"]:
        return reject("COMPLETED_DIRECT_PRICE_STALE")
    returns = [math.log(b[1] / a[1]) for a, b in zip(ordered, ordered[1:])]
    total = sum(returns)
    absolute = sum(abs(value) for value in returns)
    efficiency = abs(total) / absolute if absolute else 0.
    rv15 = math.sqrt(sum(value * value for value in returns[-15:]) / 15)
    baseline = math.sqrt(sum(value * value for value in returns[:-15]) / 45)
    ratio = rv15 / baseline if baseline else None
    stress = (rv15 >= THRESHOLDS["stress_rv_min"]
              and (baseline == 0 or ratio >= THRESHOLDS["stress_rv_ratio_min"]))
    stress = stress or (abs(returns[-1]) >= THRESHOLDS["stress_last_abs_log_return_min"]
                       and abs(returns[-1]) >= THRESHOLDS["stress_last_return_multiple"] * baseline)
    if stress:
        price_regime = "STRESS"
    elif efficiency >= THRESHOLDS["trend_efficiency_min"] and abs(total) >= THRESHOLDS["trend_abs_log_return_min"]:
        price_regime = "TREND"
    elif efficiency <= THRESHOLDS["range_efficiency_max"]:
        price_regime = "RANGE"
    else:
        price_regime = "UNKNOWN"
    events = _events(snapshot or {}, cutoff, code)
    regime = "EVENT" if events else price_regime
    output.update(regime=regime, price_regime=price_regime,
                  available=regime != "UNKNOWN", status="WORKING" if regime != "UNKNOWN" else "UNKNOWN",
                  reason="OBSERVED_PUBLISHED_EVENT" if events else ("WORKING_PRICE_THRESHOLDS" if regime != "UNKNOWN" else "WORKING_THRESHOLDS_INDETERMINATE"),
                  quality=source_quality * max(0., 1 - (cutoff - latest) / 240) if regime != "UNKNOWN" else 0.,
                  feature_values={"log_return_60m": total, "trend_efficiency_60m": efficiency,
                                  "rv15_rms_log_return": rv15, "prior45_rms_log_return": baseline,
                                  "rv15_over_prior45": ratio, "last_abs_log_return": abs(returns[-1]),
                                  "constant_price_window": absolute == 0.},
                  causal_clocks={"first_bar_end_ts": ordered[0][0] + 60,
                                 "latest_bar_end_ts": latest, "source_observed_ts": observed,
                                 "source_available_at": received, "age_sec": cutoff - latest,
                                 "completed_bar_count": len(ordered), "future_bars_used": False},
                  observed_events=events)
    return output


def build_edge_regime_context(engine: Any, snapshot: dict) -> dict:
    """Freeze the actual current instrument feed, with no I/O or fitting."""
    feed = getattr(engine, "market", None)
    code = canonical_instrument_code(_dict(snapshot.get("strategy")).get("instrument") or snapshot.get("instrument"))
    with getattr(feed, "_intraday_lock", None) or nullcontext():
        authority = dict(_dict(getattr(feed, "intraday_source_authority", None)))
        raw = getattr(feed, "intraday_ohlcv", None)
        raw = [tuple(row) if isinstance(row, (list, tuple)) else row
               for row in raw[-4096:]] if isinstance(raw, (list, tuple)) else None
        if (canonical_instrument_code(getattr(feed, "instrument_code", None)) != code
                or getattr(feed, "demo", False) or getattr(feed, "intraday_is_offset", False)):
            authority = {}
    return classify_edge_regime(raw, authority,
                                instrument=code, captured_ts=snapshot.get("captured_ts"), snapshot=snapshot)


def refine_regime_with_events(snapshot: dict) -> dict:
    """Refresh observed-event context while retaining the exact frozen price T0."""
    regime = _dict(snapshot.get("edge_regime"))
    cutoff = _number(snapshot.get("captured_ts"))
    code = canonical_instrument_code(_dict(snapshot.get("strategy")).get("instrument") or snapshot.get("instrument"))
    if (regime.get("contract_version") != CONTRACT_VERSION or cutoff is None
            or regime.get("captured_ts") != cutoff or regime.get("instrument") != code
            or not regime.get("source_authority") or not regime.get("causal_clocks")):
        return regime
    events = _events(snapshot, cutoff, code)
    base = str(regime.get("price_regime") or "UNKNOWN")
    label = "EVENT" if events else base
    refined = {**regime, "regime": label, "observed_events": events,
               "available": label != "UNKNOWN", "status": "WORKING" if label != "UNKNOWN" else "UNKNOWN",
               "reason": "OBSERVED_PUBLISHED_EVENT" if events else ("WORKING_PRICE_THRESHOLDS" if label != "UNKNOWN" else "WORKING_THRESHOLDS_INDETERMINATE")}
    # An uncertain price classification does not acquire forecast quality merely
    # because a release exists; this remains observable applicability context.
    snapshot["edge_regime"] = refined
    snapshot["market_regime"] = label
    return refined
