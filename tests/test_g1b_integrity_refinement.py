from seiltanzer.g1_baseline_refinement import (
    _aggregate_dependency_manifest,
    _report_for_rows_refined,
    _strict_prequential_base_rate,
)


def _row(
    observation_id,
    *,
    cohort="c",
    dependency="a",
    captured=0.0,
    target=50.0,
    resolved=50.0,
    outcome=1,
    horizon=15,
):
    return {
        "observation_id": observation_id,
        "source_record_sha256": f"sha-{observation_id}",
        "instrument": "NAS100",
        "base_cohort_id": cohort,
        "dependency_group_id": dependency,
        "captured_ts": captured,
        "target_ts": target,
        "resolved_ts": resolved,
        "forecast_family": "FIXED_HORIZON_MARKET_FORECAST",
        "horizon_minutes": horizon,
        "forecast": {
            "gaussian_reference_quantiles_log_return": {
                "q10": -0.02,
                "q25": -0.01,
                "q50": 0.0,
                "q75": 0.01,
                "q90": 0.02,
            }
        },
        "outcome": {"future_log_return": 0.01 if outcome else -0.01},
        "q_to_p_eligible": 0,
        "regime_stratum": "NORMAL",
        "session_stratum": "OPEN",
    }


def test_prequential_base_rate_waits_for_recorded_resolution_time():
    rows = [
        _row("r1", captured=0.0, target=50.0, resolved=150.0, outcome=1),
        _row("r2", captured=100.0, target=140.0, resolved=140.0, outcome=0),
        _row("r3", captured=200.0, target=240.0, resolved=240.0, outcome=1),
    ]
    probabilities, outcomes, meta = _strict_prequential_base_rate(rows)
    # r1 has not resolved at r2 T0, so r2 cannot see its positive outcome.
    assert probabilities[0] == 0.5
    assert probabilities[1] == 0.5
    # At r3 both prior outcomes are known: one success out of two -> 0.5.
    assert probabilities[2] == 0.5
    assert outcomes == [1, 0, 1]
    assert meta["availability_basis"] == "prior_resolved_ts_lte_current_captured_ts"
    assert meta["unavailable_prior_comparisons_n"] >= 1


def test_missing_resolution_timestamp_never_enters_history():
    rows = [
        _row("missing", captured=0.0, target=50.0, resolved=None, outcome=1),
        _row("next", captured=100.0, target=150.0, resolved=150.0, outcome=0),
    ]
    probabilities, _, meta = _strict_prequential_base_rate(rows)
    assert probabilities == [0.5, 0.5]
    assert meta["missing_resolved_ts_comparisons_n"] >= 1


def test_top_level_effective_n_collapses_same_t0_across_horizon_cohorts():
    rows = [
        _row(
            "a-15", cohort="c15", dependency="anchor-a",
            captured=0.0, target=900.0, resolved=900.0, horizon=15,
        ),
        _row(
            "a-30", cohort="c30", dependency="anchor-a",
            captured=0.0, target=1800.0, resolved=1800.0, horizon=30,
        ),
    ]
    report = _report_for_rows_refined(
        rows,
        {"source_scope": "test", "cut_id": None, "data_cutoff_ts": 2000.0},
    )
    assert report["pooled_metric_task_n"] == 2
    assert report["effective_n"] == 1
    assert report["selected_dependency_interval_n"] == 1
    assert report["effective_n_contract_mismatch"] is False
    assert report["directional_baselines"]["system_independent_effective_n"] == 1
    assert report["directional_baselines"]["edge_claim"] is False


def test_aggregate_manifest_is_deterministic_and_matches_dependency_count():
    rows = [
        _row("a-15", cohort="c15", dependency="a", captured=0, target=100, resolved=100),
        _row("a-30", cohort="c30", dependency="a", captured=0, target=200, resolved=200),
        _row("b", cohort="c15", dependency="b", captured=150, target=250, resolved=250),
        _row("c", cohort="c15", dependency="c", captured=200, target=300, resolved=300),
    ]
    n1, hash1, selected1 = _aggregate_dependency_manifest(rows)
    n2, hash2, selected2 = _aggregate_dependency_manifest(list(reversed(rows)))
    assert n1 == 2
    assert n2 == 2
    assert hash1 == hash2
    assert selected1 == selected2
