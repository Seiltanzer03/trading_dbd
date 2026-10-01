"""Match generic causal 2bp path heads to actual trade-action geometry.

These are bounded preferences, not expected returns or independent evidence.
Excursion heads do not reveal barrier order; early touch never gives direction.
All usable heads share the existing mathematical edge and price_path budgets.
"""
from __future__ import annotations

import math

CONTRACT_VERSION = "mathematical-action-preferences-v1"
BARRIER_LOG_RETURN = .0002
# Ten percent of the trained barrier, i.e. 0.2bp absolute log-return error.
BARRIER_LOG_TOLERANCE = .00002
TARGETS = {
    "downside_excursion": "FUTURE_LOW_REACHES_T0_CLOSE_EXP_MINUS_2BP",
    "upside_excursion": "FUTURE_HIGH_REACHES_T0_CLOSE_EXP_PLUS_2BP",
    "upper_before_lower": "UPPER_2BP_FIRST_GIVEN_RESOLVED_FIRST_TOUCH_NO_SAME_BAR_TIES",
    "early_first_touch": "FIRST_2BP_TOUCH_COMPLETED_BAR_END_WITHIN_HALF_HORIZON",
}
CLOSE = {"HOLD": 0., "CLOSE_10": .1, "CLOSE_25": .25, "CLOSE_50": .5, "EXIT": 1.}
STOPS = {"MOVE_TO_BE", "TIGHTEN_STOP", "TRAIL_GAMMA_FLIP"}
TAKES = {"EXTEND_TAKE", "REDUCE_TAKE"}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _num(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _distance(price, current, sign):
    price = _num(price)
    return sign * math.log(price/current) if price is not None and price > 0 else None


def _matches(distance, target):
    return distance is not None and abs(distance-target) <= BARRIER_LOG_TOLERANCE + 1e-12


def _goal(geometry, inputs, current, sign):
    """The nearest still-active rung takes precedence over the final take."""
    levels = []
    for name in ("next_rung_price", "final_take"):
        value = _num(geometry.get(name))
        if value is not None and value > 0 and sign*(value-current) > 0:
            levels.append(value)
    entry, original = _num(geometry.get("entry")), _num(geometry.get("original_stop"))
    if entry is not None and original is not None and entry != original:
        for rung in inputs.get("rungs") or []:
            rung = _num(rung)
            max_r = _num(inputs.get("max_r"))
            if rung is None or (max_r is not None and rung <= max_r+1e-8):
                continue
            value = entry + sign*abs(entry-original)*rung
            if value > 0 and sign*(value-current) > 0:
                levels.append(value)
    return min(levels, key=lambda value: sign*(value-current)) if levels else None


def mathematical_action_preferences(snapshot, candidates, profile):
    """Return per-candidate path preferences and explicit mapping/rejection audit.

    Caller combines these scores inside its single mathematical component. A
    generic event stays diagnostic when geometry or forecast horizon differs.
    Action eligibility and every hard-risk gate remain owned by the caller.
    """
    snapshot, profile = _dict(snapshot), _dict(profile)
    output = {"contract_version": CONTRACT_VERSION, "scores": {}, "available": False,
              "quality": 0., "applicability": {}, "reason": "DIAGNOSTIC_ONLY_NO_MATCHED_TRADE_TARGET",
              "evidence_family_ids": ["price_path"], "independent_evidence_vote": False,
              "score_semantics": "bounded_working_action_affinity_not_return",
              "barrier_log_return": BARRIER_LOG_RETURN,
              "barrier_log_tolerance": BARRIER_LOG_TOLERANCE}
    captured = _num(snapshot.get("captured_ts"))
    if captured is None:
        output["reason"] = "SNAPSHOT_TIMESTAMP_UNAVAILABLE"
        return output
    for name in ("captured_ts", "latest_bar_end_ts", "training_cutoff"):
        observed = _num(profile.get(name))
        if name in profile and (observed is None or observed > captured):
            output["reason"] = "PROFILE_CLOCK_INVALID_OR_FUTURE"
            return output
        if name in ("captured_ts", "latest_bar_end_ts") and observed is not None and captured-observed > 900:
            output["reason"] = "PROFILE_PRICE_STALE"
            return output
    instrument = _dict(snapshot.get("strategy")).get("instrument") or snapshot.get("instrument")
    if instrument and profile.get("instrument") and instrument != profile["instrument"]:
        output["reason"] = "PROFILE_INSTRUMENT_MISMATCH"
        return output
    geometry = _dict(snapshot.get("trade_geometry"))
    inputs = _dict(_dict(snapshot.get("policy_manager")).get("inputs"))
    current = _num(geometry.get("current"))
    direction = str(_dict(snapshot.get("strategy")).get("direction") or geometry.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        sign = 1.
    elif direction in {"short", "sell"}:
        sign = -1.
    else:
        entry, original = _num(geometry.get("entry")), _num(geometry.get("original_stop"))
        sign = 1. if entry is not None and original is not None and original < entry else (
            -1. if entry is not None and original is not None and original > entry else None)
    if current is None or current <= 0 or sign is None:
        output["reason"] = "TRADE_PRICE_OR_ORIENTATION_UNAVAILABLE"
        return output
    active_stop = geometry.get("active_risk_barrier")
    goal = _goal(geometry, inputs, current, sign)
    contributions, qualities = {}, []
    heads = _dict(profile.get("path_predictions"))
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        identity, policy = candidate.get("candidate_id"), candidate.get("policy")
        if not identity:
            continue
        params = _dict(candidate.get("parameters"))
        horizon = _num(candidate.get("horizon_minutes", _dict(candidate.get("quant_evaluation")).get(
            "horizon_minutes", inputs.get("horizon_minutes"))))
        stop = params.get("stop_price") if policy in STOPS else active_stop
        target = params.get("take_price") if policy in TAKES else (
            params.get("trigger_price") if policy == "SCALE_OUT_ON_SPIKE" else goal)
        # A closer still-active strategy rung can resolve the goal first.
        # Replacing the final take does not remove those existing rungs.
        nearest = _goal({**geometry, "final_take": target}, inputs, current, sign) if policy in TAKES else goal
        intervening_goal = (policy in TAKES or policy == "SCALE_OUT_ON_SPIKE") and (
            _num(target) is not None and nearest is not None
            and sign*(float(target)-nearest) > 1e-10)
        stop_distance, goal_distance = _distance(stop, current, sign), _distance(target, current, sign)
        mapping = {"status": "diagnostic_only", "matched_heads": [], "rejections": {},
                   "stop_log_distance": stop_distance, "goal_log_distance": goal_distance,
                   "horizon_minutes": horizon}
        output["applicability"][identity] = mapping
        if intervening_goal:
            mapping["reason"] = "CLOSER_ACTIVE_STRATEGY_GOAL_PRECEDES_CANDIDATE_TARGET"
            continue
        if not (_matches(stop_distance, -BARRIER_LOG_RETURN) and _matches(goal_distance, BARRIER_LOG_RETURN)):
            mapping["reason"] = "TRADE_BARRIERS_DO_NOT_MATCH_GENERIC_2BP"
            continue
        for name, row in heads.items():
            row = _dict(row)
            if name not in TARGETS:
                continue
            p, baseline = _num(row.get("probability")), _num(row.get("baseline_probability"))
            quality, head_horizon = _num(row.get("quality_multiplier")), _num(row.get("horizon_minutes"))
            age = _num(row.get("training_age_days"))
            clock_invalid = any(
                key in row and (_num(row[key]) is None or _num(row[key]) > captured)
                for key in ("captured_ts", "observed_ts", "training_cutoff"))
            if row.get("target_semantics") != TARGETS[name] or p is None or baseline is None or not 0 <= p <= 1 or not 0 <= baseline <= 1:
                mapping["rejections"][name] = "HEAD_PROBABILITY_OR_CONTRACT_INVALID"
                continue
            if (quality is None or not 0 < quality <= 1 or clock_invalid
                    or ("training_age_days" in row and (age is None or age < 0))):
                mapping["rejections"][name] = "HEAD_QUALITY_INVALID_OR_FUTURE"
                continue
            if horizon is None or horizon <= 0 or head_horizon is None or abs(horizon-head_horizon) > 1e-8:
                mapping["rejections"][name] = "HEAD_ACTION_HORIZON_MISMATCH"
                continue
            delta = p-baseline
            favourable_name = "upside_excursion" if sign > 0 else "downside_excursion"
            adverse_name = "downside_excursion" if sign > 0 else "upside_excursion"
            preference = None
            if name == "early_first_touch":
                deadline = _num(params.get("deadline_ts"))
                if policy == "TIME_STOP" and deadline is not None and abs(deadline-captured-head_horizon*30.) <= 1.:
                    preference = -delta
                else:
                    mapping["rejections"][name] = "EARLY_TOUCH_REQUIRES_EXACT_HALF_HORIZON_TIME_STOP"
            elif policy == "TIME_STOP":
                mapping["rejections"][name] = "DIRECTION_OR_EXCURSION_NOT_TIME_STOP_EVIDENCE"
            else:
                # Order is conditional on resolved, non-tied first touches.
                # Excursion preferences are marginal; they imply no ordering.
                favourable = delta if name == favourable_name else -delta if name == adverse_name else sign*delta
                if policy in CLOSE:
                    preference = favourable*(1.-2.*CLOSE[policy])
                elif policy in STOPS or policy == "REDUCE_TAKE":
                    preference = -favourable
                elif policy == "EXTEND_TAKE":
                    preference = favourable
                elif policy == "SCALE_OUT_ON_SPIKE" and name == favourable_name:
                    fraction = _num(params.get("close_fraction"))
                    if fraction is not None and 0 < fraction <= 1:
                        preference = delta*fraction
            if preference is None:
                continue
            contributions.setdefault(identity, []).append((max(-1., min(1., preference)), quality))
            qualities.append(quality)
            mapping["matched_heads"].append(name)
        if mapping["matched_heads"]:
            mapping.update(status="matched", reason="EXACT_GENERIC_BARRIER_AND_HORIZON_MATCH")
        else:
            mapping["reason"] = "NO_VALID_ACTION_SPECIFIC_PATH_HEAD"
    # Average correlated transformations, never sum them as independent votes.
    for identity, rows in contributions.items():
        mass = sum(quality for _, quality in rows)
        output["scores"][identity] = sum(score*quality for score, quality in rows)/mass
    if output["scores"]:
        output.update(available=True, quality=max(qualities), reason="MATCHED_CAUSAL_PATH_ACTION_PREFERENCES")
    return output
