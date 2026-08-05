"""Stable public facade for the quantitative AI policy manager v4."""
from __future__ import annotations

from . import ai_policy_v4 as _impl

globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_SELECT_FINAL_POLICY = _impl._BASE_SELECT


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: _impl.PolicyInputs, selection_rule: dict) -> dict:
    """Apply v4 authority without hiding a more specific base conflict."""
    result = _BASE_SELECT_FINAL_POLICY(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    floor = float(selection_rule.get("cvar_floor_r", -1.0))
    source_stability = _impl.authority_stability(inputs, floor)
    selected = result.get("policy") or raw_choice
    source_share = float(
        (source_stability.get("winner_shares") or {}).get(selected, 0.0))

    reliability = _impl._at(
        evidence, "data_quality", "reliability", default={}) or {}
    level = reliability.get("level") or "не определена"
    known_reliability = level in {"высокая", "средняя", "низкая"}
    families = list(evidence.get("adverse_confirmation_families") or [])
    reasons = list(result.get("reasons") or [])
    base_status = result.get("status") or "conflict"
    status = base_status
    executable_statuses = {"confirmed", "downgraded_within_feasible_set"}
    executable = base_status in executable_statuses

    threshold = {
        "HOLD": 0.0,
        "CLOSE_10": 0.45,
        "CLOSE_25": 0.50,
        "CLOSE_50": 0.625,
        "EXIT": 0.75,
    }.get(selected, 1.0)
    if executable and source_share < threshold:
        executable = False
        status = "manual_source_conflict"
        reasons.append(
            f"устойчивость к источнику данных {source_share:.0%} ниже {threshold:.0%}")

    if known_reliability and level == "низкая":
        executable = False
        if base_status not in {"conflict_stability_fallback", "manual_conflict"}:
            status = "manual_data_conflict"
        reasons.append("надёжность расчёта низкая")
    elif (known_reliability and selected == "EXIT"
          and not reliability.get("full_exit_authority", False)):
        executable = False
        if base_status not in {"conflict_stability_fallback", "manual_conflict"}:
            status = "manual_data_conflict"
        reasons.append(
            "EXIT требует высокой надёжности, live/direct цепочки и непрокси IV")

    if status not in executable_statuses:
        executable = False
    result.update({
        "status": status,
        "reasons": list(dict.fromkeys(reasons)),
        "authority_stability": source_stability,
        "confirmation_families": families,
        "confirmation_count": len(families),
        "mixed_confirmation_families": (
            evidence.get("mixed_confirmation_families") or []),
        "source_stability_share": source_share,
        "data_reliability": level,
        "automatic_execution_allowed": executable,
        "execution_policy": selected if executable else None,
        "provisional_policy": selected,
    })
    return result


# Delegated v4/v3/v2 analysis resolves this name in the lower modules.
for _module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._base,
):
    _module.select_final_policy = select_final_policy

globals()["select_final_policy"] = select_final_policy
