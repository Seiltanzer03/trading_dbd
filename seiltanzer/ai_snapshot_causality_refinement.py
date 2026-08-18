"""Keep event-driven materializer runtime metadata outside causal decision evidence.

The deterministic snapshot has its own ``captured_ts``.  The materializer finishes
later, so its wall-clock ``built_at`` is operational telemetry, not market evidence.
Passing that later timestamp into ``canonical_snapshot`` correctly trips the global
no-future-timestamp guard.  Strip only that runtime wall-clock field at the cache
read boundary; build duration/version remain available and the full wall-clock
telemetry stays on ``/api/ai/snapshot/status`` via the materializer's private state.
"""
from __future__ import annotations

import copy
from typing import Any


REFINEMENT_VERSION = "ai-snapshot-causality-refinement-v1"
_INSTALLED = False


def strip_post_capture_runtime_metadata(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a decision-safe copy without post-capture operational timestamps."""
    out = copy.deepcopy(snapshot)
    materialization = out.get("materialization")
    if isinstance(materialization, dict):
        materialization.pop("built_at", None)
        materialization["causality_refinement"] = REFINEMENT_VERSION
        materialization["runtime_wall_clock_in_decision_snapshot"] = False
    return out


def install_ai_snapshot_causality_refinement(materializer: Any) -> None:
    """Wrap the already-installed cache reader used by the FastAPI AI route."""
    global _INSTALLED
    if _INSTALLED:
        return
    from . import app as app_module

    original = app_module.build_snapshot

    def causal_cached_snapshot(engine: Any) -> dict[str, Any]:
        return strip_post_capture_runtime_metadata(original(engine))

    causal_cached_snapshot.__name__ = "causal_cached_ai_snapshot"
    app_module.build_snapshot = causal_cached_snapshot
    materializer.causality_refinement_version = REFINEMENT_VERSION
    _INSTALLED = True
