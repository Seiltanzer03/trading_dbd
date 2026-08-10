"""Policy manager v14: compare net CVaR with a net hard-risk floor.

All policy outcome distributions already include immediate/deferred execution
costs. Selection must therefore compare them with a net floor. The public risk
constraint keeps its historical gross ``cvar_floor_r`` contract and exposes the
net selection floor separately, so older strategy consumers are not broken.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import ai_policy_v13 as _impl


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_RISK_CONSTRAINT = _impl.risk_constraint
_BASE_ANALYZE = _impl.analyze_policies


def _number(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out and abs(out) != float("inf") else float(default)
    except (TypeError, ValueError):
        return float(default)


def _deferred_cost(costs: dict | None) -> float:
    return max(0.0, _number((costs or {}).get("deferred_full_close_r"), 0.0))


def risk_constraint(inputs: PolicyInputs, tick: dict, trade: dict) -> dict:
    """Expose gross strategy floor plus the compact net optimizer floor."""
    spec = dict(_BASE_RISK_CONSTRAINT(inputs, tick, trade))
    costs = execution_cost_model(tick, trade)
    gross_floor = _number(spec.get("cvar_floor_r"), -1.0)
    deferred_cost = _deferred_cost(costs)
    net_floor = gross_floor - deferred_cost

    # Keep only numeric audit fields here. Execution-cost source and the
    # human-readable rule already exist elsewhere in the policy snapshot.
    spec.update({
        "cvar_floor_r": round(gross_floor, 4),
        "gross_cvar_floor_r": round(gross_floor, 4),
        "net_cvar_floor_r": round(net_floor, 4),
        "unavoidable_deferred_cost_r": round(deferred_cost, 4),
    })
    return spec


def _floor_for_r(r_value: float) -> float | None:
    """Dynamic stress floor, net of the same deferred cost as path outcomes."""
    spec = _RISK_CTX.get()
    if not spec:
        return None
    gross_floor = _number(spec.get("effective_stop_floor_r"), -1.0)
    giveback = spec.get("max_giveback_r")
    if giveback is not None:
        gross_floor = max(gross_floor, float(r_value) - _number(giveback))
    return gross_floor - _deferred_cost(_COST_CTX.get())


def analyze_policies(engine, tick: dict, ridge: dict, trade: dict,
                     *, previous_policy_inputs: dict | None = None,
                     previous_evidence: dict | None = None):
    result = _BASE_ANALYZE(
        engine, tick, ridge, trade,
        previous_policy_inputs=previous_policy_inputs,
        previous_evidence=previous_evidence,
    )
    risk = result.get("risk_constraint") or {}
    rule = result.get("selection_rule") or {}
    if risk.get("net_cvar_floor_r") is not None:
        rule["cvar_floor_r"] = risk["net_cvar_floor_r"]
        rule["gross_cvar_floor_r"] = risk.get("gross_cvar_floor_r")
        rule["cvar_floor_basis"] = "net"
        result["selection_rule"] = rule
    # PR-C shadow contract: expose the derivative family to deterministic and
    # LLM attribution without changing policy scores, confirmations, hard-risk
    # gates or the selected action.  Promotion requires later OOS validation.
    option_state = deepcopy(tick.get("option_derivative_state") or {})
    interaction_state = deepcopy(tick.get("interaction_state") or {})
    if option_state:
        option_state.update({
            "family": "option_distribution", "independent_vote": False,
            "authority": "shadow_context", "shadow_mode": True,
            "policy_influence": "none",
        })
        evidence = result.setdefault("evidence", {})
        evidence["option_derivative_state"] = option_state
        evidence["interaction_state"] = interaction_state
        context = evidence.setdefault("context_observations", [])
        context.append({
            "metric": "option_derivative_state",
            "family": "option_distribution",
            "independent_vote": False,
            "authority": "shadow_context",
            "direction": "context",
            "meaning": (
                "Robust option-distribution derivatives are logged in shadow mode; "
                "they did not alter this policy action."
            ),
        })
        roles = evidence.setdefault("decision_roles", {})
        context_roles = roles.setdefault("context_only", [])
        if "option_derivative_state_shadow" not in context_roles:
            context_roles.append("option_derivative_state_shadow")
        result["option_derivative_state"] = option_state
        result["interaction_state"] = interaction_state
        result["shadow_policy_contract"] = {
            "old_policy": result.get("recommendation", {}).get("policy"),
            "new_candidate_policy": None,
            "reason_for_difference": "not evaluated in PR C; context-only collection",
            "action_changed": False,
        }
    result["version"] = "quant-policy-v14-net-hard-risk-floor-shadow-derivatives"
    return result


def _chain(root):
    seen = set()
    current = root
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = getattr(current, "_impl", None)


# v4 owns the cost/risk contexts and resolves these names dynamically. Patch the
# whole delegated chain so base, local stress and source-authority recalculations
# all use the same net hard-risk floor.
for module in _chain(_impl):
    module.risk_constraint = risk_constraint
    module._floor_for_r = _floor_for_r


globals()["risk_constraint"] = risk_constraint
globals()["_floor_for_r"] = _floor_for_r
globals()["analyze_policies"] = analyze_policies
