"""Read-only, paired replay of conditional management on recorded trade paths.

One preselected review per trade is used for each rule. Observed points are
piecewise linear, not tick fills; this audit never claims precise broker P&L.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math
import sqlite3
from collections import defaultdict
from typing import Any

from .decision_research import _execution_spec
from .execution_simulator import replay_execution_path
from .llm_shadow_working_action import _distance, _geometry, _price_from_current_distance

RULES = (
    "TRAIL_GAMMA_FLIP", "SCALE_OUT_ON_SPIKE", "TIME_STOP",
    "PROTECT_GAIN", "PARTIAL_AT_RUNG", "EXIT_ON_THESIS_BREAK",
)
SCALE_FRACTION = 0.25  # Additional fraction of the remainder at the next rung.
TIME_FRACTION = 0.50  # Half the frozen option horizon from the review.
PROTECT_GAIN_STOP_R = 0.50  # Frozen T0 candidate, after an observed +1R MFE.
MAX_POINT_GAP_SECONDS = 300.0


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _replay_with_effective_stop(path: list[float], spec) -> Any:
    """Honor a positive manual stop even when the strategy BE is armed.

    The shared v1 simulator's BE branch otherwise replaces *any* stop with
    0R. A positive stop subsumes the 0R BE floor, so suppressing that redundant
    BE event preserves the effective barrier without changing production code.
    """
    if spec.stop_r > 0:
        spec = replace(spec, be_after_r=math.inf)
    return replay_execution_path(path, spec)


def _frozen_opposing_edge(manager: dict) -> tuple[float | None, str | None]:
    """Use the T0 combined profile, or the frozen legacy active profile.

    Do not mix two profiles from the same review: their directional votes are
    correlated. The legacy field predates the combined soft weight and allows
    retrospective coverage without reconstructing signals after T0.
    """
    combined = manager.get("combined_edge_soft_weight")
    source = "COMBINED_T0" if isinstance(combined, dict) else "LEGACY_ACTIVE_T0"
    edge = combined if isinstance(combined, dict) else manager.get("active_edge_provisional_weight")
    if not isinstance(edge, dict) or edge.get("available") is not True:
        return None, None
    direction = _finite(edge.get("direction_score"))
    weight = _finite(edge.get(
        "weight_fraction" if source == "LEGACY_ACTIVE_T0" else
        "active_edge_component_weight"))
    exploratory = (
        (_finite(edge.get("exploratory_component_weight")) or 0.0)
        if source == "COMBINED_T0" else 0.0
    )
    if direction is None or direction >= 0 or (weight or 0.0) + exploratory <= 0:
        return None, None
    return direction, source


def replay_rules(snapshot: dict, points: list[dict]) -> dict[str, dict]:
    """Return paired gross R outcomes, or explicit reasons for unavailable rules."""
    spec = _execution_spec(snapshot)
    # The generic replay defaults to -1R. A managed position can already have
    # a tighter active barrier when this review is captured.
    active_stop_r = _finite((snapshot.get("policy_manager") or {}).get("inputs", {}).get("stop_r"))
    if active_stop_r is not None:
        if active_stop_r >= spec.current_r:
            return {name: {"reason": "INVALID_T0_ACTIVE_STOP"} for name in RULES}
        spec = replace(spec, stop_r=active_stop_r)
    captured = float(snapshot["captured_ts"])
    path = sorted(
        (float(row["ts"]), float(row["r"])) for row in points
        if _finite(row.get("ts")) is not None and _finite(row.get("r")) is not None
        and float(row["ts"]) >= captured
    )
    path = sorted({ts: value for ts, value in path}.items())
    if not path or path[0][0] > captured:
        path.insert(0, (captured, spec.current_r))
    if len(path) < 3 or path[-1][0] <= captured:
        return {name: {"reason": "RECORDED_PATH_TOO_SHORT"} for name in RULES}
    if any(b[0] - a[0] > MAX_POINT_GAP_SECONDS
           for a, b in zip(path[:-1], path[1:])):
        return {name: {"reason": "RECORDED_PATH_GAP_OVER_FIVE_MINUTES"}
                for name in RULES}
    if abs(path[0][1] - spec.current_r) > 1e-7:
        return {name: {"reason": "T0_PRICE_MISMATCH"} for name in RULES}
    observed = [value for _, value in path]
    baseline = _replay_with_effective_stop(observed, spec)
    result: dict[str, dict] = {}

    geometry = _geometry(snapshot)
    distance = _distance(snapshot, "distance_to_zero_gamma")
    target_price = _price_from_current_distance(geometry, distance)
    active_price = _finite(geometry.get("active_stop"))
    current_price = _finite(geometry.get("current"))
    if (target_price is None or active_price is None or current_price is None
            or geometry.get("risk") is None or geometry.get("sign") is None
            or not (active_price < target_price < current_price
                    if geometry["sign"] > 0 else current_price < target_price < active_price)):
        result["TRAIL_GAMMA_FLIP"] = {"reason": "T0_GAMMA_STOP_NOT_TIGHTER"}
    else:
        stop_r = float(geometry["sign"]) * (
            target_price - float(geometry["entry"])) / float(geometry["risk"])
        if stop_r <= spec.stop_r:
            result["TRAIL_GAMMA_FLIP"] = {"reason": "T0_GAMMA_STOP_NOT_TIGHTER"}
        else:
            variant = _replay_with_effective_stop(
                observed, replace(spec, stop_r=stop_r))
            result["TRAIL_GAMMA_FLIP"] = {
                "baseline_r": baseline.outcome_r, "variant_r": variant.outcome_r,
                "delta_r": variant.outcome_r - baseline.outcome_r,
                "target_r": stop_r, "anchor": "FROZEN_T0_ZERO_GAMMA",
            }

    next_rung = next((r for r in spec.future_rungs if r > spec.current_r), None)
    event = next((event for event in baseline.events
                  if event.get("type") == "rung" and event.get("r") == next_rung), None)
    if next_rung is None:
        result["SCALE_OUT_ON_SPIKE"] = {"reason": "NEXT_RUNG_UNAVAILABLE_AT_T0"}
    elif event is None:
        result["SCALE_OUT_ON_SPIKE"] = {
            "baseline_r": baseline.outcome_r, "variant_r": baseline.outcome_r,
            "delta_r": 0.0, "target_r": next_rung,
            "fraction_of_remaining": SCALE_FRACTION,
            "anchor": "FROZEN_T0_NEXT_STRATEGY_RUNG",
            "triggered": False,
        }
    else:
        # The baseline strategy already books its normal ladder fraction.
        # Reprice only an additional fraction of the remainder at first touch.
        before = 0.0
        for item in baseline.events:
            if item is event:
                break
            if item.get("type") == "rung":
                before += float(item["fill_fraction"]) * float(item["r"])
        after = before + float(event["fill_fraction"]) * float(next_rung)
        remaining = float(event["remaining_after"])
        if remaining <= 1e-10:
            result["SCALE_OUT_ON_SPIKE"] = {
                "baseline_r": baseline.outcome_r, "variant_r": baseline.outcome_r,
                "delta_r": 0.0, "target_r": next_rung,
                "fraction_of_remaining": SCALE_FRACTION,
                "anchor": "FROZEN_T0_NEXT_STRATEGY_RUNG",
                "triggered": False,
            }
        else:
            continuation_r = (baseline.outcome_r - after) / remaining
            delta = SCALE_FRACTION * remaining * (float(next_rung) - continuation_r)
            result["SCALE_OUT_ON_SPIKE"] = {
                "baseline_r": baseline.outcome_r,
                "variant_r": baseline.outcome_r + delta, "delta_r": delta,
                "target_r": next_rung, "fraction_of_remaining": SCALE_FRACTION,
                "anchor": "FROZEN_T0_NEXT_STRATEGY_RUNG",
                "triggered": True,
            }

    horizon = _finite((snapshot.get("policy_manager") or {}).get("inputs", {}).get("horizon_minutes"))
    deadline = captured + TIME_FRACTION * horizon * 60 if horizon and horizon > 0 else None
    if deadline is None or path[-1][0] < deadline:
        result["TIME_STOP"] = {"reason": "NO_COMPLETE_RECORDED_PATH_TO_DEADLINE"}
    else:
        segment = next(((a, b) for a, b in zip(path[:-1], path[1:])
                        if a[0] <= deadline <= b[0]), None)
        if segment is None:
            result["TIME_STOP"] = {"reason": "DEADLINE_SEGMENT_UNAVAILABLE"}
        else:
            (t0, r0), (t1, r1) = segment
            value = r0 + (r1 - r0) * (deadline - t0) / (t1 - t0)
            truncated = [r for ts, r in path if ts < deadline] + [value]
            variant = _replay_with_effective_stop(truncated, spec)
            result["TIME_STOP"] = {
                "baseline_r": baseline.outcome_r,
                "variant_r": variant.outcome_r,
                "delta_r": variant.outcome_r - baseline.outcome_r,
                "deadline_minutes": TIME_FRACTION * horizon,
                "anchor": "FROZEN_T0_OPTION_HORIZON",
            }
    # A rung touch is the only measured event for the additional 25% sale.
    # Reuse the exact same paired result instead of calling it an independent
    # spike detector or counting these observations twice.
    result["PARTIAL_AT_RUNG"] = {
        **result["SCALE_OUT_ON_SPIKE"],
        **({"anchor": "FROZEN_T0_NEXT_STRATEGY_RUNG"}
           if "delta_r" in result["SCALE_OUT_ON_SPIKE"] else {}),
    }

    if (spec.max_r < 1.0 or spec.current_r <= PROTECT_GAIN_STOP_R
            or spec.stop_r >= PROTECT_GAIN_STOP_R):
        result["PROTECT_GAIN"] = {"reason": "T0_GAIN_STOP_NOT_ELIGIBLE"}
    else:
        variant = _replay_with_effective_stop(
            observed, replace(spec, stop_r=PROTECT_GAIN_STOP_R))
        result["PROTECT_GAIN"] = {
            "baseline_r": baseline.outcome_r, "variant_r": variant.outcome_r,
            "delta_r": variant.outcome_r - baseline.outcome_r,
            "target_r": PROTECT_GAIN_STOP_R,
            "anchor": "FROZEN_T0_MFE_AT_LEAST_1R",
        }

    direction, source = _frozen_opposing_edge(snapshot.get("policy_manager") or {})
    if direction is None:
        result["EXIT_ON_THESIS_BREAK"] = {"reason": "NO_FROZEN_OPPOSING_EDGE_AT_T0"}
    else:
        result["EXIT_ON_THESIS_BREAK"] = {
            "baseline_r": baseline.outcome_r, "variant_r": spec.current_r,
            "delta_r": spec.current_r - baseline.outcome_r,
            "target_r": spec.current_r,
            "direction_score": direction,
            "signal_source": source,
            "anchor": "FROZEN_T0_OPPOSING_DIRECTIONAL_EDGE",
        }
    return result


def audit_database(database: str) -> dict:
    """Read existing resolved reviews; never modify the trading database."""
    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    counts: dict[str, dict[str, int]] = {name: defaultdict(int) for name in RULES}
    deltas: dict[str, list[float]] = {name: [] for name in RULES}
    seen: dict[str, set[int]] = {name: set() for name in RULES}
    by_instrument: dict[str, dict[str, list[float]]] = {
        name: defaultdict(list) for name in RULES}
    by_signal_source: dict[str, list[float]] = defaultdict(list)
    total = 0
    try:
        rows = db.execute(
            "SELECT d.review_id,d.trade_id,d.snapshot_json FROM decision_snapshots d "
            "JOIN decision_replays x ON x.review_id=d.review_id "
            "ORDER BY d.captured_ts,d.review_id"
        )
        for row in rows:
            total += 1
            try:
                snapshot = json.loads(row["snapshot_json"])
                points = [dict(point) for point in db.execute(
                    "SELECT ts,r FROM decision_path_points WHERE review_id=? ORDER BY ts",
                    (row["review_id"],))]
                replay = replay_rules(snapshot, points)
            except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                for name in RULES:
                    counts[name]["INVALID_REVIEW"] += 1
                continue
            for name, value in replay.items():
                if int(row["trade_id"]) in seen[name]:
                    counts[name]["SAME_TRADE_DUPLICATE"] += 1
                    continue
                if "delta_r" not in value:
                    counts[name][value["reason"]] += 1
                    continue
                seen[name].add(int(row["trade_id"]))
                deltas[name].append(float(value["delta_r"]))
                instrument = str((snapshot.get("strategy") or {}).get("instrument") or "UNKNOWN")
                by_instrument[name][instrument].append(float(value["delta_r"]))
                if name == "EXIT_ON_THESIS_BREAK":
                    by_signal_source[str(value["signal_source"])].append(
                        float(value["delta_r"]))
    finally:
        db.close()
    return {
        "contract_version": "historical-conditional-management-audit-v3",
        "historical_resolved_reviews": total,
        "path_assumption": "recorded_points_piecewise_linear_no_slippage",
        "partially_shared_hypotheses": {
            "PARTIAL_AT_RUNG": "same_25_percent_next_rung_proxy_as_SCALE_OUT_ON_SPIKE"
        },
        "no_auto_promotion": True,
        "rules": {name: {
            "independent_trade_n": len(deltas[name]),
            "mean_paired_delta_gross_r": (
                round(sum(deltas[name]) / len(deltas[name]), 6) if deltas[name] else None),
            "positive_trade_n": sum(value > 0 for value in deltas[name]),
            "negative_trade_n": sum(value < 0 for value in deltas[name]),
            "trigger_effect_trade_n": sum(value != 0 for value in deltas[name]),
            "by_instrument": {instrument: {
                "trade_n": len(values),
                "mean_paired_delta_gross_r": round(sum(values) / len(values), 6),
            } for instrument, values in sorted(by_instrument[name].items())},
            **({"by_signal_source": {source: {
                "trade_n": len(values),
                "mean_paired_delta_gross_r": round(sum(values) / len(values), 6),
            } for source, values in sorted(by_signal_source.items())}}
               if name == "EXIT_ON_THESIS_BREAK" else {}),
            "excluded": dict(counts[name]),
        } for name in RULES},
    }
