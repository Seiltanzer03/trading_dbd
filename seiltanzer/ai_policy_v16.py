"""Policy manager v16: Phase E semantics over the shadow-only v15 manager.

The production recommendation is deliberately inherited unchanged.  V16 makes
materiality, sensitivity and promotion authority explicit in the public state.
"""
from __future__ import annotations

from . import ai_policy_v15 as _impl
from .ai_policy_base import extract_policy_inputs, first_touch_clock
from .ai_policy_v4 import _event_geometry


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})

_BASE_ANALYZE = _impl.analyze_policies


def analyze_policies(*args, **kwargs):
    result = _BASE_ANALYZE(*args, **kwargs)
    # The policy distribution and the TAKE/STOP clock must describe the same
    # execution paths.  v2 stopped publishing the clock even though the engine
    # retained the path bank; the report consequently showed UNAVAILABLE while
    # displaying precise Expected/CVaR from those paths.
    engine = args[0] if args else kwargs.get("engine")
    tick = args[1] if len(args) > 1 else kwargs.get("tick")
    if engine is not None and isinstance(tick, dict):
        inputs = extract_policy_inputs(tick)
        if hasattr(engine, "authoritative_execution_mc"):
            sim = engine.authoritative_execution_mc(inputs)
            result["first_touch_clock"] = first_touch_clock(sim, inputs)
            result["scenario_geometry"] = _event_geometry(sim, inputs)
    result["version"] = "quant-policy-v16-phase-e-shadow-materiality"
    result["phase_e_authority_contract"] = {
        "production_recommendation_source": "authoritative v14 policy path",
        "derived_scenario_role": "shadow robustness candidate only",
        "promotion_allowed": False,
        "sample_count_auto_promotion": False,
    }
    contract = result.setdefault("shadow_policy_contract", {})
    contract["promotion_allowed"] = False
    contract["action_changed"] = False
    return result


globals()["analyze_policies"] = analyze_policies
