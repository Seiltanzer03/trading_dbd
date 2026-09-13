"""Turn a guarded LLM shadow choice into an exact manual action variant.

The bridge never sends an order.  Dynamic choices are exposed as actionable
only when their price/time parameters can be derived from authoritative
snapshot geometry and validated not to widen the stop or add position risk.
"""
from __future__ import annotations

import math
from typing import Any


CONTRACT_VERSION = "llm-shadow-manual-action-v1"
MIN_CONFIDENCE = 0.65
BASE_FRACTIONS = {
    "HOLD": 0.0,
    "CLOSE_10": 0.10,
    "CLOSE_25": 0.25,
    "CLOSE_50": 0.50,
    "EXIT": 1.0,
}


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _at(value: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _first_number(snapshot: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> float | None:
    for path in paths:
        value = _number(_at(snapshot, *path))
        if value is not None:
            return value
    return None


def _geometry(snapshot: dict[str, Any]) -> dict[str, Any]:
    direction = str(_at(snapshot, "strategy", "direction") or "").lower()
    is_long = direction in {"long", "buy", "лонг"}
    is_short = direction in {"short", "sell", "шорт"}
    entry = _first_number(snapshot, (
        ("trade_geometry", "entry"), ("observation", "exact_levels", "entry"),
    ))
    stop = _first_number(snapshot, (
        ("trade_geometry", "original_stop"), ("observation", "exact_levels", "stop"),
    ))
    active_stop = _first_number(snapshot, (
        ("trade_geometry", "active_risk_barrier"),
        ("position_state", "active_stop_price"),
    ))
    current = _first_number(snapshot, (
        ("trade_geometry", "current"), ("observation", "exact_levels", "current"),
        ("observation", "price"),
    ))
    take = _first_number(snapshot, (
        ("trade_geometry", "final_take"), ("observation", "exact_levels", "take"),
    ))
    valid_direction = is_long or is_short
    risk = abs(entry - stop) if entry is not None and stop is not None else None
    return {
        "direction": "long" if is_long else "short" if is_short else None,
        "sign": 1.0 if is_long else -1.0 if is_short else None,
        "entry": entry, "original_stop": stop,
        "active_stop": active_stop if active_stop is not None else stop,
        "current": current, "take": take,
        "risk": risk if valid_direction and risk and risk > 0 else None,
    }


def _price_at_r(geometry: dict[str, Any], r_value: float | None) -> float | None:
    if r_value is None or geometry.get("risk") is None or geometry.get("entry") is None:
        return None
    return float(geometry["entry"]) + float(geometry["sign"]) * r_value * float(geometry["risk"])


def _is_tighter_stop(geometry: dict[str, Any], price: float | None) -> bool:
    current = geometry.get("current")
    active = geometry.get("active_stop")
    if price is None or current is None or active is None:
        return False
    if geometry.get("direction") == "long":
        return float(active) < price < float(current)
    if geometry.get("direction") == "short":
        return float(current) < price < float(active)
    return False


def _distance(snapshot: dict[str, Any], name: str) -> float | None:
    return _first_number(snapshot, (
        ("policy_manager", "option_derivative_state", "gex_geometry", name),
        ("policy_manager", "evidence", "option_derivative_state", "gex_geometry", name),
        ("policy_manager", "option_derivative_state", "metrics", name, "value"),
    ))


def _price_from_current_distance(
    geometry: dict[str, Any], distance_r: float | None,
) -> float | None:
    if distance_r is None or geometry.get("current") is None or geometry.get("risk") is None:
        return None
    return (
        float(geometry["current"])
        + float(geometry["sign"]) * distance_r * float(geometry["risk"])
    )


def _next_rung(snapshot: dict[str, Any], geometry: dict[str, Any]) -> tuple[float | None, float | None]:
    rung_r = _first_number(snapshot, (
        ("policy_manager", "strategy_next_step", "next_rung_r"),
        ("policy_manager", "scenario_geometry", "next_rung_r"),
        ("policy_manager", "recommendation", "next_rung_r"),
    ))
    rung_price = _first_number(snapshot, (
        ("policy_manager", "strategy_next_step", "next_rung_price"),
    ))
    return rung_r, rung_price if rung_price is not None else _price_at_r(geometry, rung_r)


def _unavailable(policy: str | None, reason: str, confidence: float | None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "NOT_ACTIONABLE",
        "policy": policy,
        "confidence": confidence,
        "minimum_confidence": MIN_CONFIDENCE,
        "reason": reason,
        "manual_confirmation_required": True,
        "automatic_execution_allowed": False,
        "may_widen_stop": False,
        "may_increase_position": False,
    }


def build_working_action(snapshot: dict[str, Any], shadow: dict[str, Any]) -> dict[str, Any]:
    policy = str(shadow.get("policy") or "") or None
    confidence = _number(shadow.get("confidence"))
    if shadow.get("status") != "ok" or shadow.get("blocked_by_hard_guard"):
        return _unavailable(policy, "LLM_SHADOW_DID_NOT_PASS_HARD_GUARD", confidence)
    if confidence is None or confidence < MIN_CONFIDENCE:
        return _unavailable(policy, "LLM_CONFIDENCE_BELOW_MANUAL_ACTION_THRESHOLD", confidence)
    if policy in BASE_FRACTIONS:
        fraction = BASE_FRACTIONS[policy]
        instruction = (
            "УДЕРЖИВАТЬ ТЕКУЩУЮ ПОЗИЦИЮ БЕЗ НОВОГО ОРДЕРА"
            if policy == "HOLD" else
            f"ВРУЧНУЮ ЗАКРЫТЬ {fraction * 100:.0f}% ТЕКУЩЕГО ОСТАТКА"
        )
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "READY_FOR_MANUAL_CONFIRMATION",
            "policy": policy,
            "confidence": confidence,
            "instruction_ru": instruction,
            "close_fraction": fraction,
            "manual_confirmation_required": True,
            "automatic_execution_allowed": False,
            "hard_guard_passed": True,
            "may_widen_stop": False,
            "may_increase_position": False,
        }

    geometry = _geometry(snapshot)
    if geometry.get("risk") is None or geometry.get("current") is None:
        return _unavailable(policy, "AUTHORITATIVE_TRADE_GEOMETRY_UNAVAILABLE", confidence)

    params: dict[str, Any] = {}
    instruction = ""
    if policy == "MOVE_TO_BE":
        target = geometry.get("entry")
        if not _is_tighter_stop(geometry, target):
            return _unavailable(policy, "BREAK_EVEN_IS_NOT_A_VALID_TIGHTER_STOP", confidence)
        params = {"stop_price": target, "anchor": "ENTRY_PRICE"}
        instruction = f"ВРУЧНУЮ ПЕРЕНЕСТИ СТОП В БУ: {target:g}"
    elif policy in {"TRAIL_GAMMA_FLIP", "TIGHTEN_STOP"}:
        zero = _price_from_current_distance(
            geometry, _distance(snapshot, "distance_to_zero_gamma"))
        anchors = [("ZERO_GAMMA", zero), ("ENTRY_PRICE", geometry.get("entry"))]
        valid = [(name, value) for name, value in anchors if _is_tighter_stop(geometry, value)]
        if policy == "TRAIL_GAMMA_FLIP":
            valid = [item for item in valid if item[0] == "ZERO_GAMMA"]
        if not valid:
            return _unavailable(policy, "NO_AUTHORITATIVE_NON_WIDENING_STOP_ANCHOR", confidence)
        name, target = min(valid, key=lambda item: abs(float(geometry["current"]) - float(item[1])))
        params = {"stop_price": target, "anchor": name}
        instruction = f"ВРУЧНУЮ ПОДТЯНУТЬ СТОП К {name}: {target:g}"
    elif policy in {"REDUCE_TAKE", "SCALE_OUT_ON_SPIKE"}:
        rung_r, target = _next_rung(snapshot, geometry)
        current = float(geometry["current"])
        take = geometry.get("take")
        valid = target is not None and take is not None and (
            current < target <= float(take)
            if geometry["direction"] == "long" else float(take) <= target < current
        )
        if not valid:
            return _unavailable(policy, "NEXT_STRATEGY_RUNG_PRICE_UNAVAILABLE", confidence)
        if policy == "REDUCE_TAKE":
            params = {"take_price": target, "target_r": rung_r}
            instruction = f"ВРУЧНУЮ ПОДТЯНУТЬ TAKE К СЛЕДУЮЩЕЙ СТУПЕНИ: {target:g}"
        else:
            params = {"trigger_price": target, "trigger_r": rung_r, "close_fraction": 0.10}
            instruction = f"ВРУЧНУЮ ЗАКРЫТЬ 10% НА СЛЕДУЮЩЕЙ СТУПЕНИ: {target:g}"
    elif policy == "EXTEND_TAKE":
        distance_name = (
            "distance_to_call_wall_r" if geometry["direction"] == "long"
            else "distance_to_put_wall_r"
        )
        target = _price_from_current_distance(geometry, _distance(snapshot, distance_name))
        take = geometry.get("take")
        farther = target is not None and take is not None and (
            target > float(take) if geometry["direction"] == "long" else target < float(take)
        )
        if not farther:
            return _unavailable(policy, "FARTHER_AUTHORITATIVE_OPTION_WALL_UNAVAILABLE", confidence)
        params = {"take_price": target, "anchor": distance_name}
        instruction = f"ВРУЧНУЮ ПЕРЕНЕСТИ TAKE К ОПЦИОННОЙ СТЕНЕ: {target:g}"
    elif policy == "TIME_STOP":
        minutes = _first_number(snapshot, (
            ("policy_manager", "scenario_geometry", "full_horizon_minutes"),
            ("policy_manager", "inputs", "horizon_minutes"),
        ))
        captured = _number(snapshot.get("captured_ts"))
        if minutes is None or minutes <= 0 or captured is None:
            return _unavailable(policy, "MODEL_TIME_HORIZON_UNAVAILABLE", confidence)
        params = {"deadline_ts": captured + minutes * 60.0, "horizon_minutes": minutes}
        instruction = f"ВРУЧНУЮ ЗАКРЫТЬ ПО TIME-STOP ЧЕРЕЗ {minutes:g} МИНУТ, ЕСЛИ СДЕЛКА ЕЩЁ ОТКРЫТА"
    else:
        return _unavailable(policy, "UNSUPPORTED_DYNAMIC_POLICY", confidence)

    return {
        "contract_version": CONTRACT_VERSION,
        "status": "READY_FOR_MANUAL_CONFIRMATION",
        "policy": policy,
        "confidence": confidence,
        "instruction_ru": instruction,
        "parameters": params,
        "manual_confirmation_required": True,
        "automatic_execution_allowed": False,
        "hard_guard_passed": True,
        "may_widen_stop": False,
        "may_increase_position": False,
    }
