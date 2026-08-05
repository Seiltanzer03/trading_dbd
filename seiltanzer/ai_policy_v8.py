"""Policy manager v8: observed-live degraded authority with compact diagnostics."""
from __future__ import annotations

from . import ai_policy_v7 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_SELECT = _impl._BASE_SELECT
_BASE_ANALYZE = _impl.analyze_policies


def _observed_active_evidence(evidence: dict) -> dict:
    declared = list(evidence.get("adverse_confirmation_families") or [])
    supportive = list(evidence.get("supportive_confirmation_families") or [])
    mixed = list(evidence.get("mixed_confirmation_families") or [])
    items = [
        item for item in (evidence.get("adverse_confirmations") or [])
        if isinstance(item, dict) and not item.get("context_only")
    ]
    observed_families = []
    observed_metrics = []
    for item in items:
        family = item.get("family") or _confirmation_family(item.get("metric"))
        if family not in observed_families:
            observed_families.append(family)
        observed_metrics.append({
            "metric": item.get("metric"),
            "family": family,
        })

    # A family label without an underlying metric row is not enough to authorize
    # an active order. This preserves the old safety contract for incomplete
    # snapshots while allowing real production evidence to pass.
    adverse = [family for family in declared if family in observed_families]
    live = [family for family in adverse if family in _LIVE_AUTHORITY_FAMILIES]
    non_option = [family for family in adverse if family != "option_distribution"]
    return {
        "adverse_families": adverse,
        "declared_adverse_families": declared,
        "supportive_families": supportive,
        "mixed_families": mixed,
        "observed_metrics": observed_metrics,
        "observed_adverse_item_count": len(items),
        "total_adverse_count": len(adverse),
        "live_adverse_families": live,
        "live_adverse_count": len(live),
        "non_option_adverse_count": len(non_option),
        "option_only": bool(adverse and not non_option),
        "incomplete_family_labels": sorted(set(declared) - set(observed_families)),
    }


def _failure_codes(row: dict) -> list[str]:
    checks = row.get("checks") or {}
    return [name for name, passed in checks.items() if not passed]


def _compact_overlay(raw_choice: str, base_result: dict,
                     stability: dict, metrics: dict[str, dict], evidence: dict,
                     inputs: PolicyInputs, selection_rule: dict) -> dict:
    authority = base_result.get("authority_stability") or {}
    mode = _authority_mode(inputs, evidence)
    evidence_summary = _observed_active_evidence(evidence)
    rows = [
        _candidate_row(
            policy, metrics, stability, authority, raw_choice, selection_rule,
            evidence_summary,
        )
        for policy in ("CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
        if policy in metrics
    ]
    qualified = [row for row in rows if row["qualified"]]
    selected = max(qualified, key=lambda row: row["utility"]) if qualified else None
    compact_rows = {
        row["policy"]: {
            "qualified": row["qualified"],
            "expected_delta_r": row["expected_delta_vs_hold_r"],
            "cvar_gain_r": row["cvar_gain_vs_hold_r"],
            "local_support": row["support"]["local_support"],
            "source_support": row["support"]["source_support"],
            "failed": _failure_codes(row),
        }
        for row in rows
    }
    compact_evidence = {
        "adverse_families": evidence_summary["adverse_families"],
        "declared_adverse_families": evidence_summary["declared_adverse_families"],
        "live_adverse_families": evidence_summary["live_adverse_families"],
        "total_adverse_count": evidence_summary["total_adverse_count"],
        "live_adverse_count": evidence_summary["live_adverse_count"],
        "observed_adverse_item_count": evidence_summary["observed_adverse_item_count"],
        "option_only": evidence_summary["option_only"],
        "incomplete_family_labels": evidence_summary["incomplete_family_labels"],
    }
    overlay = {
        "authority": mode,
        "evidence": compact_evidence,
        "candidate_summary": compact_rows,
        "selected": selected,
        "active_recommendation_available": selected is not None,
        "rule": (
            "manual active decision requires observed live metrics plus material "
            "net tail-risk improvement; family labels alone are insufficient"
        ),
    }
    base_result["degraded_authority_overlay"] = overlay
    base_result["authority_mode"] = mode["mode"]
    if selected is None:
        return base_result

    policy = selected["policy"]
    reasons = [
        f"{policy} разрешён в режиме пониженного авторитета для ручного исполнения",
        (
            f"Expected против HOLD {selected['expected_delta_vs_hold_r']:+.3f}R; "
            f"улучшение CVaR10 {selected['cvar_gain_vs_hold_r']:+.3f}R"
        ),
        (
            f"наблюдаемых живых семей {compact_evidence['live_adverse_count']}; "
            f"всего наблюдаемых независимых семей "
            f"{compact_evidence['total_adverse_count']}"
        ),
        "автоматическое исполнение отключено; решение предназначено для ручного исполнения",
    ]
    if selected["emergency_exit"]:
        reasons.append("HOLD существенно нарушает hard CVaR floor")
    elif selected["risk_efficient_override"]:
        reasons.append(
            "risk-overlay сильнее стандартной лестницы: малая потеря Expected "
            "обменивается на существенное улучшение CVaR"
        )
    base_result.update({
        "policy": policy,
        "provisional_policy": policy,
        "execution_policy": None,
        "execution_required": True,
        "manual_execution_required": True,
        "automatic_execution_allowed": False,
        "working_action_confirmed": True,
        "status": "confirmed_degraded_manual",
        "source_stability_share": selected["support"]["source_support"],
        "confirmation_families": compact_evidence["adverse_families"],
        "confirmation_count": compact_evidence["total_adverse_count"],
        "reasons": reasons,
    })
    return base_result


def select_final_policy(raw_choice: str, stability: dict,
                        metrics: dict[str, dict], evidence: dict,
                        inputs: PolicyInputs, selection_rule: dict) -> dict:
    result = _BASE_SELECT(
        raw_choice, stability, metrics, evidence, inputs, selection_rule)
    return _compact_overlay(
        raw_choice, result, stability, metrics, evidence, inputs, selection_rule
    )


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    result["version"] = "quant-policy-v8-observed-live-degraded-authority"
    return result


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._base,
):
    module.select_final_policy = select_final_policy
    module.analyze_policies = analyze_policies

globals()["select_final_policy"] = select_final_policy
globals()["analyze_policies"] = analyze_policies
