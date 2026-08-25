from __future__ import annotations

from types import SimpleNamespace

from seiltanzer import llm_validated_active_edge_bridge as bridge


def _base_context(*, measurement_available: bool) -> dict:
    return {
        "available": False,
        "measurement_available": measurement_available,
        "report_state": (
            "CURRENT_SHA_REPORTS_COMPLETE"
            if measurement_available
            else "CURRENT_SHA_REPORTS_PARTIAL"
        ),
        "total_active_signal_n": 0,
        "matched_structured_signal_n": 0,
        "supporting_position_n": 0,
        "opposing_position_n": 0,
        "matched_groups": [],
        "signals": [],
    }


def test_validated_bridge_cannot_bypass_incomplete_current_sha_reports(monkeypatch):
    called = []

    def forbidden(*_args, **_kwargs):
        called.append(True)
        raise AssertionError("validated promotions must not be scanned while measurement is unavailable")

    monkeypatch.setattr(bridge, "_validated_rows", forbidden)
    result = bridge._augment_context(
        object(), {}, _base_context(measurement_available=False), SimpleNamespace()
    )

    assert called == []
    assert result["measurement_available"] is False
    assert result["report_state"] == "CURRENT_SHA_REPORTS_PARTIAL"
    assert result["available"] is False
    assert result["validated_llm_signal_n"] == 0
    assert result["validated_bridge_blocked_by_measurement"] is True
    assert result["total_active_signal_n"] == 0
    assert result["matched_structured_signal_n"] == 0
    assert result["supporting_position_n"] == 0
    assert result["opposing_position_n"] == 0
    assert result["matched_groups"] == []


def test_validated_bridge_remains_enabled_for_complete_measurement(monkeypatch):
    called = []

    def empty_rows(*_args, **_kwargs):
        called.append(True)
        return []

    monkeypatch.setattr(bridge, "_validated_rows", empty_rows)
    result = bridge._augment_context(
        object(), {}, _base_context(measurement_available=True), SimpleNamespace()
    )

    assert called == [True]
    assert result["measurement_available"] is True
    assert result["report_state"] == "CURRENT_SHA_REPORTS_COMPLETE"
    assert result["validated_llm_signal_n"] == 0
    assert result["validated_bridge_blocked_by_measurement"] is False
    assert result["validated_promotion_bridge"] is True
