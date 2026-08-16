from __future__ import annotations

import pytest

from seiltanzer.edge_discovery.candidate_admission import admit_discovery_candidate


def _candidate(status: str = "DISCOVERY_SIGNAL") -> dict:
    return {
        "candidate_id": "g1s-universal-test",
        "target_id": "RETURN_SIGMA",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 60,
        "status": status,
        "contract_version": "discovery-v1",
        "production_authority": False,
        "auto_promotion": False,
        "prospective_confirmation": False,
    }


def test_discovery_signal_is_admitted_only_as_historical_candidate() -> None:
    source = _candidate()
    admitted = admit_discovery_candidate(source)
    assert admitted is not source
    assert source["status"] == "DISCOVERY_SIGNAL"
    assert admitted["status"] == "HISTORICAL_CANDIDATE"
    assert admitted["source_discovery_status"] == "DISCOVERY_SIGNAL"
    assert admitted["evidence_label"] == "HISTORICAL_WALK_FORWARD"
    assert admitted["prospective_confirmation"] is False
    assert admitted["production_authority"] is False
    assert admitted["auto_promotion"] is False


def test_ml_discovery_signal_uses_same_fail_closed_admission_boundary() -> None:
    admitted = admit_discovery_candidate(_candidate("ML_DISCOVERY_SIGNAL"))
    assert admitted["status"] == "HISTORICAL_CANDIDATE"
    assert admitted["source_discovery_status"] == "ML_DISCOVERY_SIGNAL"


@pytest.mark.parametrize("status", [
    "RESEARCH_DIAGNOSTIC",
    "RESEARCH_DIAGNOSTIC_RATES_DEPENDENCY_PENDING",
    "ML_RESEARCH_DIAGNOSTIC",
    "EXPLORATORY",
    "VALIDATED",
    "",
])
def test_diagnostics_and_non_discovery_states_cannot_enter_validation(status: str) -> None:
    with pytest.raises(ValueError, match="not an admissible discovery signal"):
        admit_discovery_candidate(_candidate(status))


def test_admission_refuses_any_authority_or_implicit_auto_promotion() -> None:
    unsafe = _candidate()
    unsafe["production_authority"] = True
    with pytest.raises(ValueError, match="no production authority"):
        admit_discovery_candidate(unsafe)

    missing = _candidate()
    missing.pop("auto_promotion")
    with pytest.raises(ValueError, match="disable auto promotion"):
        admit_discovery_candidate(missing)
