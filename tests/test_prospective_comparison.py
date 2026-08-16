from __future__ import annotations

from seiltanzer.edge_discovery.prospective_comparison import compare_shared_prospective_oos
from seiltanzer.edge_discovery.prospective_confirmation import ProspectiveConfirmationLedger
from seiltanzer.edge_discovery.universal_target_scoring import UniversalTargetSpec


def _frozen(candidate_id: str, cohort: str, role: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "target_id": "RETURN_SIGMA",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 30,
        "status": "FROZEN_FOR_VALIDATION",
        "validation": {
            "role": role,
            "scope_id": "shared-scope",
            "validation_cohort_id": cohort,
            "frozen_at": 1000.0,
            "training_cutoff_ts": 900.0,
            "evidence_label": "LIVE_PROSPECTIVE_OOS",
            "frozen_spec": {"conditions": []},
            "frozen_spec_sha256": "sha-"+candidate_id,
            "production_authority": False,
            "auto_promotion": False,
        },
    }


def _record(
    ledger: ProspectiveConfirmationLedger, candidate: dict, *,
    instrument: str, t0: float, prediction: float, outcome: float,
) -> None:
    target_ts = t0+1800.0
    record_id = ledger.record_prediction(
        candidate=candidate,
        instrument=instrument,
        t0=t0,
        target_ts=target_ts,
        prediction=prediction,
        qualified=True,
        feature_values={},
        recorded_ts=t0+1.0,
    )
    ledger.resolve(record_id, outcome=outcome, observed_ts=target_ts)


def test_challenger_is_compared_only_on_shared_prospective_t0(tmp_path) -> None:
    ledger = ProspectiveConfirmationLedger(tmp_path / "prospective.jsonl")
    champion = _frozen("champion", "champion-cohort", "CHAMPION")
    challenger = _frozen("challenger", "challenger-cohort", "CHALLENGER")

    # Eight separate UTC days -> four daily clusters in each parity cohort.
    for day in range(8):
        t0 = 86_400.0*(day+1)+3600.0
        _record(
            ledger, champion, instrument="NAS100", t0=t0,
            prediction=0.0, outcome=1.0)
        _record(
            ledger, challenger, instrument="NAS100", t0=t0,
            prediction=0.9, outcome=1.0)

    # Challenger-only easy observation must never enter paired evidence.
    _record(
        ledger, challenger, instrument="NAS100",
        t0=86_400.0*10+3600.0, prediction=1.0, outcome=1.0)

    spec = UniversalTargetSpec(
        "RETURN_SIGMA", "RETURN", "CONTINUOUS", (), ("mae", "rmse"))
    result = compare_shared_prospective_oos(
        ledger,
        champion_cohort_id="champion-cohort",
        challenger_cohort_id="challenger-cohort",
        spec=spec,
    )
    assert result["champion_resolved_n"] == 8
    assert result["challenger_resolved_n"] == 9
    assert result["shared_resolved_n"] == 8
    assert result["shared_coverage_vs_champion"] == 1.0
    assert result["challenger_relative_improvement"]["mae"] > 0.0
    assert result["challenger_relative_improvement"]["rmse"] > 0.0
    assert result["paired_dependency_p_value"] < 0.10
    assert result["same_t0_only"] is True
    assert result["automatic_champion_replacement"] is False
    assert result["production_authority"] is False
    assert result["auto_promotion"] is False


def test_comparison_refuses_to_invent_evidence_when_no_t0_is_shared(tmp_path) -> None:
    ledger = ProspectiveConfirmationLedger(tmp_path / "prospective.jsonl")
    champion = _frozen("champion", "champion-cohort", "CHAMPION")
    challenger = _frozen("challenger", "challenger-cohort", "CHALLENGER")
    _record(
        ledger, champion, instrument="NAS100", t0=86_400.0+3600.0,
        prediction=0.0, outcome=1.0)
    _record(
        ledger, challenger, instrument="NAS100", t0=2*86_400.0+3600.0,
        prediction=0.9, outcome=1.0)
    spec = UniversalTargetSpec(
        "RETURN_SIGMA", "RETURN", "CONTINUOUS", (), ("mae", "rmse"))
    result = compare_shared_prospective_oos(
        ledger,
        champion_cohort_id="champion-cohort",
        challenger_cohort_id="challenger-cohort",
        spec=spec,
    )
    assert result["comparison_available"] is False
    assert result["shared_resolved_n"] == 0
    assert result["reason"] == "NO_SHARED_RESOLVED_PROSPECTIVE_T0"
    assert result["automatic_champion_replacement"] is False
    assert result["auto_promotion"] is False
