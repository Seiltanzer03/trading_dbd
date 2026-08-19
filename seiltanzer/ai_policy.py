"""Stable public facade for the quantitative AI policy manager v16."""
from __future__ import annotations

from . import ai_policy_v16 as _impl
from .active_edge_policy_weight import (
    install_active_edge_policy_weight as _install_active_edge_policy_weight,
)
from .llm_validated_active_edge_bridge import (
    install_validated_llm_active_edge_bridge as _install_validated_llm_active_edge_bridge,
)

# Keep the existing v16/v14 policy architecture intact. Historical Active Edge
# keeps its bounded soft-ranking blend; the second installer only adds LLM rules
# after immutable LIVE_PROSPECTIVE_OOS confirmation has reached VALIDATED.
_install_active_edge_policy_weight(_impl)
_install_validated_llm_active_edge_bridge()
del _install_active_edge_policy_weight
del _install_validated_llm_active_edge_bridge


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})
