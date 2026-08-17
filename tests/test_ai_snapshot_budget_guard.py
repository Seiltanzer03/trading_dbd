from __future__ import annotations

from seiltanzer import ai_snapshot_budget_guard as guard
from seiltanzer import ai_verdict


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
