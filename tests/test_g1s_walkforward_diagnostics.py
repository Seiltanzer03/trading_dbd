from __future__ import annotations

import json

from seiltanzer.g1_short_horizon_walkforward import (
    WALK_FORWARD_VERSION,
    continuous_walk_forward,
    directional_walk_forward,
)


class _Runtime:
    @staticmethod
    def _dependency_key(row):
        horizon = int(row["horizon_minutes"])
        bucket = int(float(row["captured_ts"]) // (horizon * 60.0))
        return f"{row['instrument']}|{horizon}|{bucket}"

    @staticmethod
    def _feature_vector(row, feature_set):
        return [float(row["x"])], {"x": float(row["x"])}


def _row(index: int):
    captured = 1_780_000_000.0 + index * 1200.0
    x = -0.03 + index * 0.0005
    y = 0.75 * x
    features = {
        "g1s_intraday": {"ret_15m": x},
        "g1s_evidence_v2": {
            "contract_version": "g1s-feature-contract-v2",
            "intraday": {"available": True, "ret_15m": x},
        },
    }
    return {
        "observation_id": f"o-{index:03d}",
        "instrument": "NAS100",
        "horizon_minutes": 15,
        "captured_ts": captured,
        "target_ts": captured + 900.0,
        "resolved_ts": captured + 901.0,
        "direction_label": "UP" if y > 0 else "DOWN",
        "terminal_log_return": y,
        "frozen_features_json": json.dumps(features),
        "x": x,
    }


def test_directional_diagnostics_are_true_expanding_walk_forward_with_purge():
    rows = [_row(index) for index in range(120)]
    report = directional_walk_forward(_Runtime(), rows, "TEST", 15)

    assert report["diagnostics_contract_version"] == WALK_FORWARD_VERSION
    assert report["status"] == "HISTORICAL_EXPANDING_WALK_FORWARD"
    assert report["historical_walk_forward"] is True
    assert report["random_shuffle"] is False
    assert report["purge_applied"] is True
    assert report["embargo_sec"] == 900
    assert report["fold_count"] >= 2
    assert report["validation_n"] > 0
    assert report["dependency_group_total_weight_one"] is True
    for fold in report["folds"]:
        assert fold["max_training_target_ts"] < fold["validation_start_ts"]


def test_continuous_diagnostics_use_same_purged_expanding_contract():
    rows = [_row(index) for index in range(120)]
    report = continuous_walk_forward(_Runtime(), rows, "TEST")

    assert report["diagnostics_contract_version"] == WALK_FORWARD_VERSION
    assert report["status"] == "HISTORICAL_EXPANDING_WALK_FORWARD"
    assert report["historical_walk_forward"] is True
    assert report["random_shuffle"] is False
    assert report["purge_applied"] is True
    assert report["embargo_sec"] == 900
    assert report["fold_count"] >= 2
    assert report["model_mae"] is not None
    assert report["model_rmse"] is not None
    for fold in report["folds"]:
        assert fold["max_training_target_ts"] < fold["validation_start_ts"]
