"""Stable public facade for the quantitative AI policy manager v3."""
from __future__ import annotations

from . import ai_policy_v3 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__"}
})

_BASE_SELECT_FINAL_POLICY = _impl._ORIGINAL_SELECT_FINAL_POLICY


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: _impl.PolicyInputs, selection_rule: dict) -> dict:
    """Apply source authority without hiding a more specific prior conflict."""
    result = _BASE_SELECT_FINAL_POLICY(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    floor = float(selection_rule.get("cvar_floor_r", -0.60))
    source_stability = authority_stability(inputs, floor)
    result["authority_stability"] = source_stability

    reliability = _impl._impl._at(
        evidence, "data_quality", "reliability", default={}) or {}
    level = reliability.get("level") or "не определена"
    known_reliability = level in {"высокая", "средняя", "низкая"}
    families = list(evidence.get("adverse_confirmation_families") or [])
    selected = result.get("policy") or raw_choice
    source_share = float(
        (source_stability.get("winner_shares") or {}).get(selected, 0.0))
    result["confirmation_families"] = families
    result["confirmation_count"] = len(families)
    result["source_stability_share"] = source_share
    result["data_reliability"] = level

    reasons = list(result.get("reasons") or [])
    base_status = result.get("status") or "conflict"
    status = base_status
    executable_statuses = {"confirmed", "downgraded_within_feasible_set"}
    executable = base_status in executable_statuses

    source_thresholds = {
        "HOLD": 0.00,
        "CLOSE_10": 0.45,
        "CLOSE_25": 0.50,
        "CLOSE_50": 0.625,
        "EXIT": 0.75,
    }
    required_share = source_thresholds.get(selected, 1.0)

    # Preserve specific selector diagnostics such as stability fallback. The
    # authority layer only replaces an otherwise executable/generic decision.
    if executable and source_share < required_share:
        executable = False
        status = "manual_source_conflict"
        reasons.append(
            f"устойчивость к отключению ненадёжных входов {source_share:.0%} "
            f"ниже {required_share:.0%}")

    if known_reliability and level == "низкая":
        executable = False
        if base_status not in {"conflict_stability_fallback", "manual_conflict"}:
            status = "manual_data_conflict"
        reasons.append("надёжность расчёта низкая: действие не подтверждено")
    elif (known_reliability and selected == "EXIT"
          and not reliability.get("full_exit_authority", False)):
        executable = False
        if base_status not in {"conflict_stability_fallback", "manual_conflict"}:
            status = "manual_data_conflict"
        reasons.append(
            "EXIT запрещён без высокой надёжности, live-цепочки и непрокси IV")

    if status not in executable_statuses:
        executable = False
    result["status"] = status
    result["reasons"] = list(dict.fromkeys(reasons))
    result["automatic_execution_allowed"] = executable
    result["execution_policy"] = selected if executable else None
    result["provisional_policy"] = selected
    return result


# ai_policy_v3 delegates analysis to ai_policy_v2, so patch all three module
# namespaces before build_snapshot imports this public facade.
_impl.select_final_policy = select_final_policy
_impl._impl.select_final_policy = select_final_policy
_impl._impl._base.select_final_policy = select_final_policy
globals()["select_final_policy"] = select_final_policy
