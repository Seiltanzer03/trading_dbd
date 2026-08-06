"""Policy manager v14: compare net CVaR with a net hard-risk floor.

All policy outcome distributions already include immediate/deferred execution
costs. The hard stop/BE floor must therefore be expressed on the same net
basis. Otherwise an unavoidable 0.01R fallback cost can exclude HOLD solely
because -1.00R gross becomes -1.01R net.
"""
from __future__ import annotations

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
    """Return the hard CVaR floor on the same net basis as policy outcomes."""
    spec = dict(_BASE_RISK_CONSTRAINT(inputs, tick, trade))
    costs = execution_cost_model(tick, trade)
    gross_floor = _number(spec.get("cvar_floor_r"), -1.0)
    deferred_cost = _deferred_cost(costs)
    net_floor = gross_floor - deferred_cost

    spec.update({
        "gross_cvar_floor_r": round(gross_floor, 4),
        "unavoidable_deferred_cost_r": round(deferred_cost, 4),
        "cvar_floor_r": round(net_floor, 4),
        "cvar_floor_basis": "net_after_unavoidable_deferred_close_cost",
        "execution_cost_source": costs.get("deferred_source") or costs.get("source"),
        "rule": (
            f"{spec.get('rule') or 'active stop/BE'}; net floor = gross floor "
            "minus unavoidable deferred close cost"
        ),
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
    result["version"] = "quant-policy-v14-net-hard-risk-floor"
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
