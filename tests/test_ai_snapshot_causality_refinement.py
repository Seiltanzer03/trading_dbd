import pytest

from seiltanzer.ai_snapshot_causality_refinement import (
    REFINEMENT_VERSION,
    strip_post_capture_runtime_metadata,
)
from seiltanzer.decision_research import canonical_snapshot


def test_post_capture_materializer_wall_clock_is_removed_not_causal_guard_weakened():
    captured = 1_700_000_000.0
    snapshot = {
        "captured_ts": captured,
        "trade_id": 17,
        "policy_manager": {},
        "materialization": {
            "version": "ai-snapshot-materializer-v2-event-driven",
            "built_at": captured + 6.2,
            "build_ms": 6179.8,
            "request_path_recomputed": False,
            "deterministic_snapshot": True,
        },
    }

    # Reproduce the exact production failure and prove the global causal guard
    # still rejects post-T0 evidence.
    with pytest.raises(ValueError, match=r"materialization\.built_at"):
        canonical_snapshot(snapshot)

    safe = strip_post_capture_runtime_metadata(snapshot)

    assert snapshot["materialization"]["built_at"] == captured + 6.2
    assert "built_at" not in safe["materialization"]
    assert safe["materialization"]["build_ms"] == 6179.8
    assert safe["materialization"]["causality_refinement"] == REFINEMENT_VERSION
    assert safe["materialization"]["runtime_wall_clock_in_decision_snapshot"] is False

    record = canonical_snapshot(safe)
    assert record["trade_id"] == 17
    assert record["captured_ts"] == captured
