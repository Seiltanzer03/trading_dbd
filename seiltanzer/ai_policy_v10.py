"""Policy manager v10: robust enrichment and compact stateful snapshots."""
from __future__ import annotations

from . import ai_policy_v9 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_ANALYZE = _impl.analyze_policies


def _compact_diagnostics(result: dict) -> None:
    gate = result.get("gate") or {}
    overlay = gate.get("degraded_authority_overlay") or {}
    overlay.pop("candidate_summary", None)
    selected = overlay.get("selected")
    if isinstance(selected, dict):
        support = selected.get("support") or {}
        overlay["selected"] = {
            "policy": selected.get("policy"),
            "qualified": bool(selected.get("qualified")),
            "expected_delta_vs_hold_r": selected.get("expected_delta_vs_hold_r"),
            "expected_sacrifice_vs_hold_r": selected.get("expected_sacrifice_vs_hold_r"),
            "cvar_gain_vs_hold_r": selected.get("cvar_gain_vs_hold_r"),
            "risk_efficient_override": bool(selected.get("risk_efficient_override")),
            "emergency_exit": bool(selected.get("emergency_exit")),
            "local_support": support.get("local_support"),
            "source_support": support.get("source_support"),
        }
    if overlay:
        gate["degraded_authority_overlay"] = overlay
    result["gate"] = gate

    requirements = result.get("decision_requirements") or {}
    # Thresholds are deterministic code constants and need not be duplicated in
    # every stored machine snapshot. The report still explains the active checks.
    requirements.pop("degraded_manual_policies", None)
    if requirements:
        result["decision_requirements"] = requirements


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    # Keep v9 usable even when tests or maintenance replace its lower analysis
    # function with a minimal fixture.
    if "input_audit" not in result:
        _enrich_input_audit(result, tick, ridge)
    if "economic_indifference" not in result:
        result["economic_indifference"] = _economic_indifference(result)
    if "strategy_next_step" not in result:
        result["strategy_next_step"] = _strategy_next_step(result, trade)
    if "management_arbiter" not in result:
        result["management_arbiter"] = _arbiter(result)
    _compact_diagnostics(result)
    result["version"] = "quant-policy-v10-stateful-compact-arbiter"
    return result


for module in (
    _impl,
    _impl._impl,
    _impl._impl._impl,
    _impl._impl._impl._impl,
    _impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl._impl,
    _impl._impl._impl._impl._impl._impl._impl._impl._base,
):
    module.analyze_policies = analyze_policies

globals()["analyze_policies"] = analyze_policies
