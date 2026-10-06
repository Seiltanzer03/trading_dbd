from __future__ import annotations

import pytest

from seiltanzer import ai_snapshot_budget_guard as guard
from seiltanzer import ai_verdict
from seiltanzer.ai_report_semantics_guard import authoritative_current_price_available


def test_base_snapshot_integrity_overflow_degrades_instead_of_raising():
    original_public = ai_verdict._enforce_snapshot_budget_with_report_integrity
    original_impl = ai_verdict._impl._enforce_snapshot_budget
    original_base = ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18
    guard._INSTALLED = False

    def overflowing(snapshot: dict) -> None:
        snapshot["policy_manager"] = {
            "management_decision": "HOLD",
            "recommendation": "HOLD",
            "policies": {"HOLD": {"expected_final_r": 0.12, "cvar10_r": -0.4}},
        }
        snapshot["report_integrity"] = {"blob": "x" * 70_000}
        snapshot["metric_availability_contract"] = {"blob": "y" * 10_000}
        snapshot["snapshot_budget"] = {}
        raise RuntimeError(
            "AI snapshot byte budget exceeded after report-integrity preservation"
        )

    def base(snapshot: dict) -> None:
        snapshot.setdefault("snapshot_budget", {})["compacted"] = True

    try:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = overflowing
        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = base
        guard.install_ai_snapshot_budget_guard()

        snapshot: dict = {}
        ai_verdict._impl._enforce_snapshot_budget(snapshot)

        assert snapshot["policy_manager"]["management_decision"] == "HOLD"
        assert snapshot["policy_manager"]["policies"]["HOLD"]["expected_final_r"] == 0.12
        assert "report_integrity" not in snapshot
        assert "metric_availability_contract" not in snapshot
        assert snapshot["snapshot_budget"]["report_integrity_degraded"] is True
        assert snapshot["snapshot_budget"]["degrade_reason"] == (
            "BASE_REPORT_INTEGRITY_BYTE_BUDGET"
        )
        assert snapshot["snapshot_budget"]["degrade_level"] == "EXPLANATION_ONLY"
        assert snapshot["snapshot_budget"]["final_bytes"] < ai_verdict.SNAPSHOT_LIMIT_BYTES
    finally:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = original_public
        ai_verdict._impl._enforce_snapshot_budget = original_impl
        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = original_base
        guard._INSTALLED = False


def test_non_budget_runtime_error_is_not_hidden():
    original_public = ai_verdict._enforce_snapshot_budget_with_report_integrity
    original_impl = ai_verdict._impl._enforce_snapshot_budget
    guard._INSTALLED = False

    def broken(_snapshot: dict) -> None:
        raise RuntimeError("unrelated deterministic failure")

    try:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = broken
        guard.install_ai_snapshot_budget_guard()
        try:
            ai_verdict._impl._enforce_snapshot_budget({})
        except RuntimeError as exc:
            assert str(exc) == "unrelated deterministic failure"
        else:
            raise AssertionError("non-budget RuntimeError must be preserved")
    finally:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = original_public
        ai_verdict._impl._enforce_snapshot_budget = original_impl
        guard._INSTALLED = False


def test_base_overflow_retries_with_strict_authoritative_compaction():
    original_public = ai_verdict._enforce_snapshot_budget_with_report_integrity
    original_impl = ai_verdict._impl._enforce_snapshot_budget
    original_base = ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18
    guard._INSTALLED = False
    calls = []

    def overflowing(snapshot: dict) -> None:
        snapshot.update({
            "captured_ts": 1_790_024_400.0,
            "trade_id": "128",
            "strategy": {"instrument": "NAS100"},
            "trade_geometry": {"current": 4328.75},
            "policy_manager": {
                "management_decision": "CLOSE_25",
                "recommendation": "CLOSE_25",
                "policies": {
                    "HOLD": {"expected_final_r": 0.1, "cvar10_r": -0.8},
                    "CLOSE_25": {"expected_final_r": 0.08, "cvar10_r": -0.4},
                },
                "risk_constraint": {"cvar_floor_r": -0.5},
                "input_audit": {
                    "snapshot_utc": "2026-09-22T14:00:00Z",
                    "available_count": 7,
                    "total_count": 8,
                    "rows": {
                        "instrument_price": {
                            "available": True,
                            "status": "live",
                            "source": "broker",
                            "value": 4328.75,
                            "oversized": "z" * 20_000,
                        }
                    },
                },
                "scenario_geometry": {
                    "scenario_count": 10_000,
                    "take_first_probability": 0.41,
                    "paths": ["oversized"] * 10_000,
                },
                "evidence": {"oversized": "x" * 70_000},
            },
            "metric_coverage": {
                "summary": {"available_groups": 7, "total_groups": 8}
            },
            "ede_causal_context": {"oversized": "y" * 70_000},
            "snapshot_budget": {},
        })
        raise RuntimeError("AI snapshot byte budget exceeded")

    def base(snapshot: dict) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("AI snapshot byte budget exceeded")
        snapshot.setdefault("snapshot_budget", {})["compacted"] = True

    try:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = overflowing
        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = base
        guard.install_ai_snapshot_budget_guard()

        snapshot: dict = {}
        ai_verdict._impl._enforce_snapshot_budget(snapshot)

        manager = snapshot["policy_manager"]
        assert snapshot["captured_ts"] == 1_790_024_400.0
        assert manager["management_decision"] == "CLOSE_25"
        assert manager["policies"]["HOLD"]["cvar10_r"] == -0.8
        assert manager["risk_constraint"]["cvar_floor_r"] == -0.5
        assert manager["input_audit"]["available_count"] == 7
        assert manager["input_audit"]["total_count"] == 8
        assert manager["input_audit"]["rows"]["instrument_price"] == {
            "available": True,
            "status": "live",
            "source": "broker",
            "value": 4328.75,
        }
        assert manager["scenario_geometry"] == {
            "scenario_count": 10_000,
            "take_first_probability": 0.41,
        }
        assert snapshot["metric_coverage"]["summary"]["available_groups"] == 7
        assert authoritative_current_price_available(snapshot) is True
        assert "evidence" not in manager
        assert "ede_causal_context" not in snapshot
        assert len(calls) == 2
        assert snapshot["snapshot_budget"]["report_integrity_degraded"] is True
        assert snapshot["snapshot_budget"]["degrade_level"] == "STRICT_AUTHORITATIVE"
        assert snapshot["snapshot_budget"]["final_bytes"] < ai_verdict.SNAPSHOT_LIMIT_BYTES
    finally:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = original_public
        ai_verdict._impl._enforce_snapshot_budget = original_impl
        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = original_base
        guard._INSTALLED = False


@pytest.mark.parametrize("base_raises", [False, True])
def test_live_chain_overflow_preserves_decision_and_policy_metrics(base_raises):
    original_public = ai_verdict._enforce_snapshot_budget_with_report_integrity
    original_impl = ai_verdict._impl._enforce_snapshot_budget
    original_base = ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18
    guard._INSTALLED = False
    decision = {"policy": "HOLD", "decision_id": "review-129", "instruction_ru": "Удерживать"}

    def overflowing(snapshot: dict) -> None:
        snapshot.update({
            "trade_id": 129, "captured_ts": 1_790_742_322.0,
            "trade_geometry": {"entry": 100, "current": 102, "original_stop": 90},
            "policy_manager": {
                "management_decision": decision,
                "recommendation": {"policy": "HOLD"},
                "policies": {
                    name: {"expected_final_r": .12, "cvar10_r": -.4,
                           "monte_carlo": "x" * 14_000}
                    for name in ("HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT")
                },
                "inputs": {"r0": .2, "T": 2.5},
                "risk_constraint": {"gross_cvar_floor_r": -.8},
                "input_audit": {"rows": {"instrument_price": {
                    "available": True, "source": "direct", "value": 102}}},
                "selection_rule": {"eligible": ["HOLD"], "explanation": "y" * 20_000},
            },
            "snapshot_budget": {},
        })
        raise RuntimeError("AI snapshot byte budget exceeded")

    try:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = overflowing
        def base(_snapshot: dict) -> None:
            if base_raises:
                raise RuntimeError("AI snapshot byte budget exceeded")

        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = base
        guard.install_ai_snapshot_budget_guard()
        snapshot: dict = {}
        ai_verdict._impl._enforce_snapshot_budget(snapshot)
        manager = snapshot["policy_manager"]
        assert manager["management_decision"] == decision
        assert set(manager["policies"]) == {"HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"}
        assert manager["policies"]["HOLD"] == {
            "expected_final_r": .12, "cvar10_r": -.4}
        assert manager["risk_constraint"]["gross_cvar_floor_r"] == -.8
        assert manager["input_audit"]["rows"]["instrument_price"]["value"] == 102
        assert snapshot["snapshot_budget"]["degrade_level"] == (
            "EMERGENCY_AUTHORITATIVE_TRANSPORT")
        assert snapshot["snapshot_budget"]["final_bytes"] == (
            ai_verdict._impl._snapshot_bytes(snapshot))
        assert snapshot["snapshot_budget"]["final_bytes"] < ai_verdict.SNAPSHOT_LIMIT_BYTES
    finally:
        ai_verdict._enforce_snapshot_budget_with_report_integrity = original_public
        ai_verdict._impl._enforce_snapshot_budget = original_impl
        ai_verdict._BASE_ENFORCE_SNAPSHOT_BUDGET_V18 = original_base
        guard._INSTALLED = False
def test_strict_compaction_preserves_frozen_runtime_code_generation():
    snapshot = {'runtime_code_sha': 'a'*40, 'captured_ts': 100, 'policy_manager': {}}
    guard._strict_authoritative_compaction(snapshot)
    assert snapshot['runtime_code_sha'] == 'a'*40
