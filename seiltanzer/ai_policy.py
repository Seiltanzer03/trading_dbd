"""Stable public facade for the quantitative AI policy manager v16."""
from __future__ import annotations

from . import ai_policy_v16 as _impl
from .active_edge_policy_weight import (
    install_active_edge_policy_weight as _install_active_edge_policy_weight,
)

# Keep the existing v16/v14 policy architecture intact. The installer only wraps
# the already-existing soft expected-R selector with a bounded active-edge blend;
# CVaR eligibility, hard-risk floors, simulations and execution remain unchanged.
_install_active_edge_policy_weight(_impl)
del _install_active_edge_policy_weight


globals().update({
    name: value for name, value in vars(_impl).items()
    if name not in {"__name__", "__loader__", "__package__", "__spec__", "_impl"}
})
