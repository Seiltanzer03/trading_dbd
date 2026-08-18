"""Runtime correctness refinements for the removable Universe experiment.

Two production gaps are closed here without changing trading authority:

* Passive G1S observations previously copied ``feed.correlation`` without ever
  refreshing it. The interactive Universe endpoint computed a fresh cross-asset
  graph, while the immutable canonical T0 therefore showed cross.* as unavailable.
  A shared observed correlation snapshot is now refreshed *before* the price/T0
  capture and reused for five minutes. No historical row is backfilled.
* Active-edge current-T0 matches may legitimately be non-directional (for example
  forward-volatility candidates). Expose that distinction explicitly so eight
  matched conditions can no longer look like a broken zero-vote aggregator.
"""
from __future__ import annotations

import copy
import threading
import time
from typing import Any

from .passive_learning import PassiveLearningEngine


CONTRACT_VERSION = "universe-runtime-refinement-v2"
CORRELATION_REFRESH_TTL_SEC = 300.0
CORRELATION_MAX_REUSE_SEC = 30.0 * 60.0

_LOCK = threading.Lock()
_SHARED_CORRELATION: dict[str, Any] | None = None
_SHARED_CAPTURED_AT = 0.0
_INSTALLED = False


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _usable_correlation(state: Any, now: float) -> bool:
    if not isinstance(state, dict):
        return False
    value = state.get("value")
    if not isinstance(value, dict) or not value.get("assets") or not value.get("matrix"):
        return False
    asof = _finite(value.get("asof")) or _finite(state.get("ts"))
    return asof is not None and 0.0 <= now-asof <= CORRELATION_MAX_REUSE_SEC


def _prepare_correlation_before_t0(engine: PassiveLearningEngine, instrument: str) -> None:
    """Ensure one observed cross-asset snapshot exists before price fixes T0."""
    global _SHARED_CORRELATION, _SHARED_CAPTURED_AT
    feed = engine._feed(instrument)
    now = time.time()

    with _LOCK:
        cached = copy.deepcopy(_SHARED_CORRELATION)
        cached_at = float(_SHARED_CAPTURED_AT)
    if cached is not None and now-cached_at <= CORRELATION_REFRESH_TTL_SEC:
        feed.correlation = cached
        return

    try:
        feed.refresh_correlation()
    except Exception:
        # MarketData normally fail-softs into its status object. Preserve the same
        # semantics if an unexpected provider exception escapes.
        pass

    state = copy.deepcopy(getattr(feed, "correlation", None))
    if _usable_correlation(state, now):
        with _LOCK:
            _SHARED_CORRELATION = state
            _SHARED_CAPTURED_AT = now
        return

    # A source outage must not erase a still-observed, reasonably recent snapshot.
    # The V3 quality contract will independently mark it stale after 30 minutes.
    with _LOCK:
        cached = copy.deepcopy(_SHARED_CORRELATION)
    if _usable_correlation(cached, now):
        feed.correlation = cached


def _install_passive_cross_refresh() -> None:
    if getattr(PassiveLearningEngine, "_universe_cross_refresh_version", None) == CONTRACT_VERSION:
        return
    original = PassiveLearningEngine._collect_instrument

    def collect_instrument(self, instrument: str, now: float):
        # Critical ordering: correlation is observed first; original then refreshes
        # the instrument price and uses that later source timestamp as captured_ts.
        # Thus admitted cross observations can be <= T0, never future relative to it.
        _prepare_correlation_before_t0(self, instrument)
        return original(self, instrument, now)

    PassiveLearningEngine._collect_instrument = collect_instrument
    PassiveLearningEngine._universe_cross_refresh_version = CONTRACT_VERSION


def _install_active_edge_diagnostics() -> None:
    from . import active_edge_ai_integration as active_module
    from . import visual_universe_routes as universe_routes

    if getattr(active_module, "_universe_diagnostics_version", None) == CONTRACT_VERSION:
        return
    original = active_module.build_active_edge_context

    def build_active_edge_context(engine, snapshot):
        context = original(engine, snapshot)
        if not isinstance(context, dict):
            return context
        matched = max(0, int(context.get("matched_structured_signal_n") or 0))
        supporting = max(0, int(context.get("supporting_position_n") or 0))
        opposing = max(0, int(context.get("opposing_position_n") or 0))
        directional = min(matched, supporting + opposing)
        groups = context.get("matched_groups") or []
        directional_groups = sum(
            1 for row in groups
            if isinstance(row, dict) and int(row.get("net_vote") or 0) != 0)
        nondirectional_groups = sum(
            1 for row in groups
            if isinstance(row, dict) and int(row.get("supporting_n") or 0) == 0
            and int(row.get("opposing_n") or 0) == 0
        )
        context.update({
            "directional_matched_signal_n": directional,
            "non_directional_matched_signal_n": max(0, matched-directional),
            "directional_matched_group_n": directional_groups,
            "non_directional_matched_group_n": nondirectional_groups,
            "directional_weight_available": directional_groups > 0,
            "directional_weight_reason": (
                "DIRECTIONAL_MATCHES_AVAILABLE" if directional_groups > 0
                else "CURRENT_T0_MATCHES_ARE_NON_DIRECTIONAL" if matched > 0
                else "NO_CURRENT_T0_MATCHES"),
        })
        return context

    # active_edge_policy_weight imports this function dynamically, while the
    # Universe route captured a module-local reference at import time. Patch both.
    active_module.build_active_edge_context = build_active_edge_context
    universe_routes.build_active_edge_context = build_active_edge_context
    active_module._universe_diagnostics_version = CONTRACT_VERSION


def install_universe_runtime_refinement() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_passive_cross_refresh()
    _install_active_edge_diagnostics()
    _INSTALLED = True
