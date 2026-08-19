"""Stable public facade for the quantitative AI policy manager v16."""
from __future__ import annotations

from . import ai_policy_v16 as _impl
from . import llm_validated_active_edge_bridge as _llm_validated_bridge
from .active_edge_policy_weight import (
    install_active_edge_policy_weight as _install_active_edge_policy_weight,
)
from .llm_edge_active_promotion_reader import (
    active_promotions_readonly as _active_promotions_readonly,
)

# Keep the existing v16/v14 policy architecture intact. Historical Active Edge
# keeps its bounded soft-ranking blend. LLM promotions are read-only/fail-closed
# on the request path and become visible only after LIVE_PROSPECTIVE_OOS VALIDATED.
_install_active_edge_policy_weight(_impl)
_llm_validated_bridge.active_promotions = _active_promotions_readonly
_llm_validated_bridge.install_validated_llm_active_edge_bridge()
del _install_active_edge_policy_weight
del _active_promotions_readonly
del _llm_validated_bridge


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})
