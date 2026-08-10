"""Immutable decision records and realized-path counterfactual replay."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

from .execution_simulator import (
    SIMULATOR_VERSION,
    ExecutionSpec,
    replay_execution_path,
)


DECISION_SCHEMA_VERSION = "decision-snapshot-f2-v1"
REPLAY_VERSION = "counterfactual-replay-f1-exposure-v1"
POLICY_FRACTIONS = {
    "HOLD": 0.0, "CLOSE_10": 0.10, "CLOSE_25": 0.25,
    "CLOSE_50": 0.50, "EXIT": 1.0,
}


def _at(value: Any, *path: str, default=None):
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def validate_no_future_timestamps(snapshot: dict, captured_ts: float,
                                  tolerance_sec: float = 1.0) -> None:
    """Reject numeric source timestamps newer than the decision capture."""
    violations: list[str] = []

    def visit(value: Any, path: tuple[str, ...]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if (key == "ts" or key.endswith("_ts") or key.endswith("_at")):
                    timestamp = _finite(child)
                    # Epoch-like values only. Durations and sample indices can
                    # also be named "ts" in third-party payloads.
                    if (timestamp is not None and timestamp > 1_000_000_000
                            and timestamp > captured_ts + tolerance_sec):
                        violations.append(".".join(child_path))
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*path, str(index)))

    visit(snapshot, ())
    if violations:
        raise ValueError(
            "decision snapshot contains post-capture timestamps: "
            + ", ".join(violations[:8]))


def canonical_snapshot(snapshot: dict) -> dict:
    captured = _finite(snapshot.get("captured_ts"))
    trade_id = snapshot.get("trade_id")
    if captured is None or trade_id is None:
        raise ValueError("decision snapshot requires captured_ts and trade_id")
    validate_no_future_timestamps(snapshot, captured)
    manager = snapshot.get("policy_manager") or {}
    decision = manager.get("management_decision") or {}
    production = (
        decision.get("policy")
        or _at(manager, "recommendation", "policy")
        or "HOLD"
    )
    shadow = (
        _at(manager, "shadow_policy_contract", "new_candidate_policy")
        or _at(manager, "derived_scenario_ensemble", "candidate_policy")
    )
    payload = json.dumps(
        snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    review_id = f"review-{int(trade_id)}-{int(captured * 1_000_000)}-{digest[:10]}"
    return {
        "schema_version": DECISION_SCHEMA_VERSION,
        "review_id": review_id, "trade_id": int(trade_id),
        "captured_ts": captured, "snapshot_json": payload,
        "snapshot_sha256": digest,
        "production_policy": str(production),
        "shadow_candidate": str(shadow) if shadow else None,
        "policy_version": manager.get("version"),
        "simulator_version": (
            _at(manager, "scenario_geometry", "execution_contract", "simulator_version")
            or SIMULATOR_VERSION),
        "calibration_version": (
            _at(manager, "calibration_contract", "version") or "unpromoted-v1"),
        "scenario_version": (
            _at(manager, "derived_scenario_ensemble", "version") or "production-base"),
        "feature_contract_version": "phase-f-feature-contract-v1",
        "simulation_seed": _finite(_at(manager, "policies", "HOLD", "monte_carlo", "seed")),
        "initial_price": _finite(_at(snapshot, "observation", "exact_levels", "current")),
        "initial_r": _finite(_at(snapshot, "observation", "position", "r")),
    }


def _execution_spec(snapshot: dict) -> ExecutionSpec:
    manager = snapshot.get("policy_manager") or {}
    inputs = manager.get("inputs") or {}
    current = _finite(inputs.get("r0"))
    max_r = _finite(inputs.get("max_r"))
    take = _finite(inputs.get("T"))
    if current is None or max_r is None or take is None:
        raise ValueError("snapshot lacks authoritative execution inputs")
    return ExecutionSpec.from_values(
        current_r=current, max_r=max_r, take_r=take,
        rungs=inputs.get("rungs") or (),
        rung_fraction_original=float(inputs.get("rung_fraction") or 0.10),
        be_after_r=float(inputs.get("be_after") or 1.5),
    )


def counterfactual_replay(snapshot: dict, path_points: Iterable[dict]) -> dict:
    """Replay all policies over one observed post-review R path."""
    spec = _execution_spec(snapshot)
    captured = float(snapshot["captured_ts"])
    points = sorted(
        ({"ts": float(row["ts"]), "r": float(row["r"]),
          "price": _finite(row.get("price"))}
         for row in path_points
         if _finite(row.get("ts")) is not None and _finite(row.get("r")) is not None
         and float(row["ts"]) >= captured - 1e-9),
        key=lambda row: row["ts"],
    )
    if not points or abs(points[0]["r"] - spec.current_r) > 1e-7:
        points.insert(0, {"ts": captured, "r": spec.current_r,
                          "price": _finite(_at(snapshot, "observation", "exact_levels", "current"))})
    # Deduplicate identical timestamps deterministically, keeping the last write.
    points = list({row["ts"]: row for row in points}.values())
    points.sort(key=lambda row: row["ts"])
    baseline = replay_execution_path([row["r"] for row in points], spec)

    def event_timestamp(event: dict) -> float:
        step = int(event.get("step") or 0)
        if step <= 0 or len(points) == 1:
            return points[0]["ts"]
        step = min(step, len(points) - 1)
        left, right = points[step - 1]["ts"], points[step]["ts"]
        fraction = min(max(float(event.get("segment_fraction") or 0.0), 0.0), 1.0)
        return left + (right - left) * fraction

    timeline = [
        {**event, "ts": event_timestamp(event)} for event in baseline.events
    ]

    def risk_time(close_fraction: float) -> tuple[float, float]:
        if close_fraction >= 1.0 - 1e-12:
            return 0.0, 0.0
        exposure = initial_remaining * (1.0 - close_fraction)
        previous_ts = points[0]["ts"]
        integral_seconds = 0.0
        exit_ts = points[-1]["ts"]
        for event in timeline:
            ts = max(previous_ts, float(event["ts"]))
            integral_seconds += exposure * (ts - previous_ts)
            if event.get("type") == "rung":
                exposure = initial_remaining * (1.0 - close_fraction) * max(
                    0.0, float(event.get("remaining_after") or 0.0))
            elif event.get("type") in ("take", "stop", "breakeven", "horizon"):
                exposure = 0.0
                exit_ts = ts
                previous_ts = ts
                break
            previous_ts = ts
        return max(0.0, exit_ts - points[0]["ts"]) / 60.0, integral_seconds / 60.0

    manager = snapshot.get("policy_manager") or {}
    position = snapshot.get("position_state") or {}
    initial_remaining = max(0.0, min(1.0, float(
        position.get("remaining_position_fraction", 1.0))))
    already_realized = float(position.get("realized_r_weighted") or 0.0)
    costs = manager.get("execution_cost_model") or {}
    immediate = max(0.0, _finite(costs.get("immediate_full_close_r")) or 0.0)
    deferred = max(0.0, _finite(costs.get("deferred_full_close_r")) or 0.0)
    outcomes = {}
    for name, fraction in POLICY_FRACTIONS.items():
        if fraction >= 1.0:
            future_gross = spec.current_r
            future_net = spec.current_r - immediate
        else:
            future_gross = (fraction * spec.current_r
                            + (1.0 - fraction) * baseline.outcome_r)
            future_net = (fraction * (spec.current_r - immediate)
                          + (1.0 - fraction) * (baseline.outcome_r - deferred))
        gross = already_realized + initial_remaining * future_gross
        net = already_realized + initial_remaining * future_net
        outcomes[name] = {
            "initial_remaining_fraction": initial_remaining,
            "already_realized_r": round(already_realized, 6),
            "future_r_on_remaining": round(future_net, 6),
            "close_fraction": fraction,
            "gross_realized_r": round(gross, 6),
            "net_realized_r": round(net, 6),
            "execution_cost_r": round(gross - net, 6),
            "calendar_time_under_risk_minutes": round(risk_time(fraction)[0], 6),
            "exposure_weighted_risk_minutes": round(risk_time(fraction)[1], 6),
        }
    best = max(outcomes, key=lambda name: outcomes[name]["net_realized_r"])
    best_value = outcomes[best]["net_realized_r"]
    production = (
        _at(manager, "management_decision", "policy")
        or _at(manager, "recommendation", "policy") or "HOLD")
    shadow = (
        _at(manager, "shadow_policy_contract", "new_candidate_policy")
        or _at(manager, "derived_scenario_ensemble", "candidate_policy"))
    for row in outcomes.values():
        row["regret_r"] = round(best_value - row["net_realized_r"], 6)
    path_rs = [row["r"] for row in points]
    duration = max(0.0, points[-1]["ts"] - points[0]["ts"])
    return {
        "version": REPLAY_VERSION,
        "simulator_version": SIMULATOR_VERSION,
        "path_source": (
            "synthetic_demo_points" if snapshot.get("demo")
            else "observed_post_review_points"),
        "path_resolution_warning": (
            "barrier ordering between observations uses the published "
            "piecewise-linear barrier-fill/no-slippage contract"),
        "point_count": len(points), "start_ts": points[0]["ts"],
        "end_ts": points[-1]["ts"],
        "time_under_risk_minutes": round(duration / 60.0, 4),
        "mfe_r": round(max(path_rs) - spec.current_r, 6),
        "mae_r": round(min(path_rs) - spec.current_r, 6),
        "execution_path": {**baseline.as_dict(), "events": timeline},
        "execution_cost_contract": {
            "version": costs.get("version") or "unversioned",
            "immediate_full_close_r": immediate,
            "deferred_full_close_r": deferred,
            "rung_costs": "not_modelled",
            "price_impact": "assumed_zero",
        },
        "policies": outcomes, "best_realized_policy": best,
        "production_policy": production,
        "shadow_policy": shadow,
        "production_realized_r": outcomes.get(production, {}).get("net_realized_r"),
        "shadow_realized_r": outcomes.get(shadow, {}).get("net_realized_r"),
        "production_regret_r": outcomes.get(production, {}).get("regret_r"),
        "shadow_regret_r": outcomes.get(shadow, {}).get("regret_r"),
        "causal_claim": False,
    }
