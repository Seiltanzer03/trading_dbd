from __future__ import annotations

import numpy as np
import pytest

from seiltanzer import g1_short_horizon_p2_regime_research as p2
from seiltanzer.g1_short_horizon_historical_wf import _prob_metrics, _weights
from seiltanzer.g1_short_horizon_p2_persistence_offset import (
    OFFSET_MODEL_FAMILY,
    _persistence_prediction,
    offset_winner_gate,
)
from seiltanzer.g1_short_horizon_p2_offset_interactions import (
    INTERACTION_CONTRACT_VERSION,
    fit_offset_logistic_interactions,
    interaction_feature_names,
    offset_interaction_matrix,
    predict_offset_interactions,
)


def _row(index: int, *, high_vol: bool = False, follow: bool = True):
    sign = 1.0 if index % 2 == 0 else -1.0
    ret5 = sign * (0.0015 if high_vol else 0.0007)
    direction_sign = sign if follow else -sign
    rv15 = 0.012 if high_vol else 0.002
    rv60 = 0.020 if high_vol else 0.004
    return {
        "instrument": "NAS100" if index % 3 else "SP500",
        "captured_ts": 1_700_000_000.0 + index * 300.0,
        "target_ts": 1_700_000_000.0 + index * 300.0 + 900.0,
        "horizon_minutes": 15,
        "direction_label": "UP" if direction_sign > 0 else "DOWN",
        "terminal_log_return": direction_sign * 0.001,
        "features": {
            "ret_5m": ret5,
            "ret_15m": sign * 0.0012,
            "ret_60m": sign * 0.0020,
        },
        "p2_features": {
            "ret_5m": ret5,
            "ret_15m": sign * 0.0012,
            "ret_60m": sign * 0.0020,
            "realized_vol_15m": rv15,
            "realized_vol_60m": rv60,
        },
    }


def _confidence_regime_rows(n: int, offset: int = 0):
    rows = []
    for j in range(n):
        i = offset + j
        high = i % 5 == 0
        # Persistence is globally strong (80% of observations), but is exactly
        # wrong in a clearly observable high-volatility regime.
        rows.append(_row(i, high_vol=high, follow=not high))
    return rows


def test_zero_correction_recovers_ret5_persistence_probability_exactly():
    train = _confidence_regime_rows(400)
    test = _confidence_regime_rows(80, 400)
    x = offset_interaction_matrix(train, "BASE_P2")
    artifact = {
        "feature_family": "BASE_P2",
        "feature_mean": np.zeros(x.shape[1]).tolist(),
        "feature_std": np.ones(x.shape[1]).tolist(),
        "correction_intercept_and_coefficients": np.zeros(x.shape[1] + 1).tolist(),
    }
    baseline = _persistence_prediction(train, test)
    predicted = predict_offset_interactions(train, test, artifact)
    assert np.max(np.abs(predicted - baseline)) < 1e-12


def test_sign_context_interaction_can_reduce_persistence_confidence_in_bad_regime():
    train = _confidence_regime_rows(800)
    test = _confidence_regime_rows(200, 800)
    artifact = fit_offset_logistic_interactions(train, "BASE_P2", 1.0)
    assert artifact["contract_version"] == INTERACTION_CONTRACT_VERSION
    assert artifact["model_family"] == OFFSET_MODEL_FAMILY
    assert artifact["sign_context_interactions"] is True
    assert any(name == "persistence_sign*realized_vol_60m"
               for name in artifact["feature_names"])

    predicted = predict_offset_interactions(train, test, artifact)
    baseline = _persistence_prediction(train, test)
    y = np.asarray([1.0 if row["direction_label"] == "UP" else 0.0 for row in test])
    weights, _ = _weights(test)
    model = _prob_metrics(y, predicted, weights)
    base = _prob_metrics(y, baseline, weights)
    assert model["brier"] < base["brier"] * 0.8
    assert model["logloss"] < base["logloss"] * 0.8


def test_interaction_matrix_is_pre_t0_only_shape_and_finite():
    rows = _confidence_regime_rows(20)
    matrix = offset_interaction_matrix(rows, "BASE_P2")
    names = interaction_feature_names("BASE_P2")
    assert matrix.shape == (20, len(names))
    assert np.isfinite(matrix).all()
    assert names[0] == "persistence_sign"
    for base_name in p2.FEATURE_FAMILIES["BASE_P2"]:
        assert f"persistence_sign*{base_name}" in names


def test_offset_winner_still_requires_serious_sample_both_metrics_and_robust_folds():
    evaluation = {
        "fold_count": 4,
        "fold_joint_non_degrade_n": 3,
        "model": {"brier": 0.245, "logloss": 0.680},
        "baselines": {
            "ret5_persistence": {"brier": 0.250, "logloss": 0.690},
            "constant_0_5": {"brier": 0.251, "logloss": 0.693},
        },
    }
    gate = offset_winner_gate(evaluation, 5000, 1200)
    assert gate["historical_winner"] is True
    evaluation["fold_joint_non_degrade_n"] = 2
    assert offset_winner_gate(evaluation, 5000, 1200)["historical_winner"] is False
    evaluation["fold_joint_non_degrade_n"] = 4
    evaluation["model"]["brier"] = 0.2495
    assert offset_winner_gate(evaluation, 5000, 1200)["historical_winner"] is False
