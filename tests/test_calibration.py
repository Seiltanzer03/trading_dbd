import math

from seiltanzer.calibration import (
    binary_scorecard,
    brier_score,
    calibration_authority,
    calibration_bins,
    log_loss,
    pinball_loss,
    purged_walk_forward_splits,
    quantile_scorecard,
)


def test_brier_log_loss_and_naive_baseline_are_explicit():
    probabilities = [0.9, 0.8, 0.2, 0.1]
    outcomes = [1, 1, 0, 0]
    assert math.isclose(brier_score(probabilities, outcomes), 0.025)
    assert log_loss(probabilities, outcomes) < log_loss([0.5] * 4, outcomes)
    score = binary_scorecard(probabilities, outcomes)
    assert score["q_model_brier"] < score["naive_base_rate_brier"]
    assert score["probability_measure"] == "risk_neutral_Q"


def test_calibration_bins_publish_counts_frequency_and_interval():
    bins = calibration_bins([0.05, 0.08, 0.55, 0.58, 1.0], [0, 1, 0, 1, 1])
    assert bins[0]["n"] == 2
    assert bins[-1]["hi"] == 1.0
    assert bins[-1]["n"] == 1
    assert all(0 <= bound <= 1 for row in bins
               for bound in row["actual_frequency_ci95"])


def test_quantile_coverage_and_pinball_loss():
    rows = [
        {"q10": 0, "q25": 1, "q50": 2, "q75": 3, "q90": 4,
         "realized_r": value}
        for value in (-1, 1, 2, 3, 5)
    ]
    result = quantile_scorecard(rows)
    assert result["q50"]["n"] == 5
    assert result["q50"]["empirical_below_fraction"] == 0.6
    assert pinball_loss([2] * 5, [-1, 1, 2, 3, 5], 0.5) >= 0


def test_purged_walk_forward_has_no_horizon_overlap_or_shuffle():
    records = [
        {"prediction_ts": float(index * 100), "horizon_sec": 150.0}
        for index in range(12)
    ]
    splits = purged_walk_forward_splits(records, n_splits=3, embargo_sec=25.0)
    assert len(splits) == 3
    for split in splits:
        validation_start = split["validation_start_ts"]
        for index in split["train_indices"]:
            row = records[index]
            assert row["prediction_ts"] + row["horizon_sec"] <= validation_start - 25.0
        assert split["train_indices"] == sorted(split["train_indices"])
        assert split["validation_indices"] == sorted(split["validation_indices"])


def test_small_sample_never_publishes_physical_probability():
    authority = calibration_authority(40, 12, effective_independent_n=18)
    assert authority["status"] == "insufficient_evidence"
    assert authority["p_calibrated_shadow"] is None
    assert authority["production_replacement_allowed"] is False
