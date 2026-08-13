from __future__ import annotations

import math

import numpy as np
import pytest

from seiltanzer.config import INSTRUMENTS
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import (
    ASSET_FAMILY_BY_INSTRUMENT,
    CANDIDATES,
    INNER_PRACTICAL_TIE_RELATIVE,
    L2_GRID,
    P2E_CONTRACT_VERSION,
    P2E_MODEL_FAMILY,
    SESSION_UTC_INTERVALS,
    _candidate_matrix,
    _fit_candidate,
    _predict_candidate,
    asset_family,
    candidate_feature_names,
    session_utc,
    winner_gate,
)
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import _persistence_prediction


def _row(index: int, *, instrument: str = "NAS100", horizon: int = 15,
         direction: str | None = None):
    captured = 1_700_000_000.0 + index * 300.0
    sign = 1.0 if index % 2 == 0 else -1.0
    rv15 = 0.002 + (index % 7) * 0.0001
    rv60 = 0.004 + (index % 11) * 0.0001
    ret5 = sign * 0.0007
    return {
        "instrument": instrument,
        "captured_ts": captured,
        "target_ts": captured + horizon * 60.0,
        "horizon_minutes": horizon,
        "direction_label": direction or ("UP" if sign > 0 else "DOWN"),
        "terminal_log_return": sign * 0.001,
        "features": {
            "ret_5m": ret5, "ret_15m": sign * 0.0012,
            "ret_60m": sign * 0.002,
        },
        "p2_features": {
            "realized_vol_15m": rv15,
            "realized_vol_60m": rv60,
            "rv15_over_rv60": rv15 / rv60,
            "ret5_over_rv15": ret5 / rv15,
            "ret15_over_rv60": sign * 0.0012 / rv60,
            "trend_agreement_5_15": 1.0,
            "trend_efficiency_60": 0.55,
        },
    }


def test_p2e_predeclared_contract_exactly_covers_current_instruments():
    assert set(ASSET_FAMILY_BY_INSTRUMENT) == set(INSTRUMENTS)
    assert tuple(CANDIDATES) == (
        "C0_NO_CORRECTION", "C1_ASSET_FAMILY_OFFSET",
        "C2_SESSION_OFFSET", "C3_INSTRUMENT_SHRUNK_OFFSET",
        "C4_FAMILY_SESSION_OFFSET", "C5_FAMILY_SESSION_SMALL_REGIME",
    )
    assert L2_GRID == (1.0, 4.0, 16.0, 64.0)
    assert SESSION_UTC_INTERVALS == {
        "ASIA": (0, 8), "EUROPE": (8, 13),
        "US": (13, 21), "LATE": (21, 24),
    }
    assert INNER_PRACTICAL_TIE_RELATIVE == 0.001


def test_session_and_asset_family_are_fixed_target_independent_classifiers():
    assert asset_family("NAS100") == "EQUITY_INDICES"
    assert asset_family("XAU") == "METALS"
    assert asset_family("EURUSD") == "FX"
    # 2023-11-14 22:13:20 UTC; classifier uses only captured_ts UTC hour.
    assert session_utc(1_700_000_000.0) == "LATE"
    with pytest.raises(ValueError):
        asset_family("UNDECLARED")


def test_candidate_shapes_are_frozen_and_finite():
    rows = [_row(i, instrument=tuple(INSTRUMENTS)[i % len(INSTRUMENTS)])
            for i in range(40)]
    for candidate in CANDIDATES[1:]:
        matrix = _candidate_matrix(rows, candidate)
        assert matrix.shape == (len(rows), len(candidate_feature_names(candidate)))
        assert np.isfinite(matrix).all()
    assert "persistence_sign*instrument:SP500" in candidate_feature_names(
        "C3_INSTRUMENT_SHRUNK_OFFSET")
    assert set(("rv15_over_rv60", "ret5_over_rv15", "ret15_over_rv60",
                "trend_agreement_5_15", "trend_efficiency_60")) <= set(
        candidate_feature_names("C5_FAMILY_SESSION_SMALL_REGIME"))


def test_beta_zero_recovers_ret5_persistence_exactly():
    train = [_row(i, instrument="NAS100" if i % 3 else "XAU") for i in range(500)]
    test = [_row(i + 500, instrument="SP500" if i % 2 else "EURUSD")
            for i in range(120)]
    candidate = "C4_FAMILY_SESSION_OFFSET"
    x = _candidate_matrix(train, candidate)
    artifact = {
        "candidate": candidate,
        "feature_mean": np.zeros(x.shape[1]).tolist(),
        "feature_std": np.ones(x.shape[1]).tolist(),
        "correction_intercept_and_coefficients": np.zeros(x.shape[1] + 1).tolist(),
    }
    assert np.max(np.abs(
        _predict_candidate(train, test, artifact)
        - _persistence_prediction(train, test))) < 1e-12


def test_segment_candidate_is_shrunk_offset_not_independent_models():
    train = [_row(i, instrument=tuple(INSTRUMENTS)[i % len(INSTRUMENTS)])
             for i in range(800)]
    artifact = _fit_candidate(train, "C3_INSTRUMENT_SHRUNK_OFFSET", 16.0)
    assert artifact["contract_version"] == P2E_CONTRACT_VERSION
    assert artifact["model_family"] == P2E_MODEL_FAMILY
    assert artifact["l2"] == 16.0
    assert artifact["beta_zero_recovers_baseline_exactly"] is True
    assert artifact["train_only_standardization"] is True


def test_winner_gate_requires_serious_sample_both_metrics_and_robust_folds():
    rows = []
    start = 1_700_000_000.0
    instruments = tuple(INSTRUMENTS)
    for i in range(5000):
        row = _row(i, instrument=instruments[i % len(instruments)])
        # Spread over >20 UTC dates and guarantee both frozen volatility regimes.
        row["captured_ts"] = start + i * 1200.0
        row["target_ts"] = row["captured_ts"] + 900.0
        if i % 2:
            row["p2_features"]["realized_vol_15m"] = 0.004
            row["p2_features"]["realized_vol_60m"] = 0.005
        rows.append(row)
    evaluation = {
        "fold_count": 4, "fold_joint_non_degrade_n": 3,
        "model": {"brier": 0.245, "logloss": 0.680},
        "baselines": {
            "ret5_persistence": {"brier": 0.250, "logloss": 0.690},
            "constant_0_5": {"brier": 0.251, "logloss": 0.693},
        },
    }
    gate = winner_gate(evaluation, rows, effective_n=1200)
    assert gate["historical_winner"] is True
    evaluation["fold_joint_non_degrade_n"] = 2
    assert winner_gate(evaluation, rows, 1200)["historical_winner"] is False
    evaluation["fold_joint_non_degrade_n"] = 4
    evaluation["model"]["brier"] = 0.2495
    assert winner_gate(evaluation, rows, 1200)["historical_winner"] is False
