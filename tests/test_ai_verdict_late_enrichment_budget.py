from __future__ import annotations

import inspect

from seiltanzer import active_edge_ai_integration, ai_verdict
from seiltanzer.ai_verdict_budget_bridge import enforce_public_snapshot_budget


def _snapshot_with_edge_details() -> dict:
    return {
        "ede_causal_context": {
            "active_high_risk": {
                "matched_group_n": 2,
                "serialized_signal_n": 2,
                "details_truncated": False,
                "signals": [{"candidate_id": "x", "payload": "x" * 1000}],
                "matched_groups": [{"target_id": "RETURN"}, {"target_id": "FIRST_TOUCH"}],
            },
        },
        "policy_manager": {
            "scenario_geometry": {"scenario_count": 6500},
            "raw_optimizer_stability": {"checks": 8},
            "stability": {"checks": 8},
            "risk_tradeoff": {"status": "close"},
            "monte_carlo_validation": {"checks": 8},
            "active_edge_provisional_weight": {"weight_fraction": 0.3},
        },
        "report_integrity": {"contract_version": "test", "policies": {"HOLD": {"expected_final_r": 0.2}}},
        "metric_availability_contract": {"contract_version": "test", "inputs": {"price": {"available": True}}},
    }


def test_active_edge_integration_uses_public_budget_bridge():
    source = inspect.getsource(active_edge_ai_integration.install_active_edge_ai_integration)
    assert "enforce_public_snapshot_budget(snapshot)" in source
    assert 'getattr(ai_verdict, "_enforce_snapshot_budget"' not in source


def test_late_enrichment_compacts_verbose_edge_before_failing(monkeypatch):
    calls = []

    def integrity_enforcer(snapshot):
        calls.append(1)
        active = snapshot["ede_causal_context"]["active_high_risk"]
        if active.get("signals") or active.get("matched_groups"):
            raise RuntimeError("AI snapshot byte budget exceeded after report-integrity preservation")
        snapshot["snapshot_budget"] = {"final_bytes": 59000}

    monkeypatch.setattr(
        ai_verdict, "_enforce_snapshot_budget_with_report_integrity",
        integrity_enforcer,
    )
    snapshot = _snapshot_with_edge_details()
    enforce_public_snapshot_budget(snapshot)

    active = snapshot["ede_causal_context"]["active_high_risk"]
    assert len(calls) == 2
    assert active["signals"] == []
    assert active["matched_groups"] == []
    assert active["matched_group_n"] == 2
    assert active["details_truncated"] is True
    assert snapshot["snapshot_budget"]["late_enrichment_compacted"] is True
    assert snapshot["report_integrity"]["policies"]["HOLD"]["expected_final_r"] == 0.2


def test_late_enrichment_last_resort_preserves_decision_snapshot(monkeypatch):
    def always_over_budget(snapshot):
        raise RuntimeError("AI snapshot byte budget exceeded")

    base_calls = []

    def base_enforcer(snapshot):
        base_calls.append(1)
        # Simulate the established v18 compactor succeeding after optional
        # presentation contracts are removed.
        assert "report_integrity" not in snapshot
        assert "metric_availability_contract" not in snapshot
        snapshot["snapshot_budget"] = {"final_bytes": 54000}

    monkeypatch.setattr(
        ai_verdict, "_enforce_snapshot_budget_with_report_integrity",
        always_over_budget,
    )
    monkeypatch.setattr(ai_verdict, "_BASE_ENFORCE_SNAPSHOT_BUDGET_V18", base_enforcer)

    snapshot = _snapshot_with_edge_details()
    snapshot["policy_manager"]["management_decision"] = {"policy": "HOLD"}
    enforce_public_snapshot_budget(snapshot)

    assert base_calls == [1]
    assert snapshot["policy_manager"]["management_decision"]["policy"] == "HOLD"
    assert snapshot["snapshot_budget"]["report_integrity_degraded"] is True
    assert snapshot["snapshot_budget"]["degrade_reason"] == "LATE_ENRICHMENT_BYTE_BUDGET"
