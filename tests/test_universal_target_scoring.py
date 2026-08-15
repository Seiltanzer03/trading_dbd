from __future__ import annotations

import numpy as np
import pytest

from seiltanzer.edge_discovery.universal_target_scoring import (
    UniversalTargetSpec,
    eligible_target_rows,
    fitted_constant_predictions,
    relative_target_improvement,
    target_metrics,
    target_value,
    universal_target_specs,
)


def _row(index: int, value, *, target_id: str = "RETURN_SIGMA") -> dict:
    return {
        "instrument": "NAS100",
        "horizon_minutes": 30,
        "captured_ts": float(index * 1800),
        "target_ts": float(index * 1800 + 1800),
        "universal_target_id": target_id,
        "universal_target_value": value,
    }


def test_universal_target_extraction_is_strategy_agnostic() -> None:
    outcome = {
        "available": True,
        "path_complete": True,
        "t0_local_sigma_h": 0.01,
        "terminal_log_return": 0.005,
        "direction_label": "UP",
        "mfe_sigma": 1.2,
        "mae_sigma": -0.4,
        "forward_rv_log_return": 0.008,
        "barriers": {
            "up_1s_down_1s": {"clean_label": True, "label": "UP_FIRST"},
        },
        "contains_user_entry": False,
        "contains_user_stop": False,
        "contains_user_take": False,
        "contains_user_rr": False,
    }
    row = {"universal_outcome": outcome}
    specs = {spec.target_id: spec for spec in universal_target_specs(["up_1s_down_1s"])}
    assert target_value(row, specs["DIRECTION"]) == "UP"
    assert target_value(row, specs["RETURN_SIGMA"]) == pytest.approx(0.5)
    assert target_value(row, specs["MFE_SIGMA"]) == pytest.approx(1.2)
    assert target_value(row, specs["MAE_SIGMA"]) == pytest.approx(-0.4)
    assert target_value(row, specs["FORWARD_VOL_RATIO"]) == pytest.approx(0.8)
    assert target_value(row, specs["FIRST_TOUCH:up_1s_down_1s"]) == "UP_FIRST"


def test_censored_or_ambiguous_first_touch_is_not_research_label() -> None:
    spec = UniversalTargetSpec(
        "FIRST_TOUCH:up_1s_down_1s", "FIRST_TOUCH", "MULTICLASS",
        ("DOWN_FIRST", "NO_TOUCH", "UP_FIRST"), ("brier", "logloss"))
    base = {
        "available": True, "path_complete": True, "t0_local_sigma_h": 0.01,
        "barriers": {"up_1s_down_1s": {"clean_label": False,
                                         "label": "AMBIGUOUS_SAME_BAR"}},
    }
    assert target_value({"universal_outcome": base}, spec) is None


def test_continuous_conditional_predictor_measures_state_shift_vs_global_baseline() -> None:
    spec = UniversalTargetSpec("RETURN_SIGMA", "RETURN", "CONTINUOUS", (),
                               ("mae", "rmse"))
    global_train = [_row(index, value) for index, value in enumerate(
        [-1.0, -0.8, -0.6, 0.6, 0.8, 1.0], start=1)]
    conditional_train = global_train[3:]
    test = [_row(index, value) for index, value in enumerate([0.7, 0.9, 0.8], start=20)]
    model, baseline = fitted_constant_predictions(global_train, conditional_train, test, spec)
    assert model[0] > baseline[0]
    model_metrics = target_metrics(test, model, spec)
    baseline_metrics = target_metrics(test, baseline, spec)
    improvement = relative_target_improvement(model_metrics, baseline_metrics, spec)
    assert improvement["mae"] > 0.0
    assert improvement["rmse"] > 0.0


def test_multiclass_first_touch_uses_train_only_distribution() -> None:
    spec = UniversalTargetSpec(
        "FIRST_TOUCH:x", "FIRST_TOUCH", "MULTICLASS",
        ("DOWN_FIRST", "NO_TOUCH", "UP_FIRST"), ("brier", "logloss"))
    values = ["DOWN_FIRST", "NO_TOUCH", "UP_FIRST", "UP_FIRST", "UP_FIRST", "UP_FIRST"]
    global_train = [_row(index, value, target_id=spec.target_id)
                    for index, value in enumerate(values, start=1)]
    conditional_train = global_train[-4:]
    test = [_row(20, "UP_FIRST", target_id=spec.target_id),
            _row(21, "UP_FIRST", target_id=spec.target_id)]
    model, baseline = fitted_constant_predictions(global_train, conditional_train, test, spec)
    assert model.shape == (2, 3)
    assert baseline.shape == (2, 3)
    assert np.allclose(model.sum(axis=1), 1.0)
    assert model[0, 2] > baseline[0, 2]


def test_eligible_target_rows_never_invent_missing_outcomes() -> None:
    spec = UniversalTargetSpec("RETURN_SIGMA", "RETURN", "CONTINUOUS", (),
                               ("mae", "rmse"))
    rows = [
        {"universal_outcome": {"available": False}},
        {"universal_outcome": {
            "available": True, "path_complete": True,
            "t0_local_sigma_h": 0.01, "terminal_log_return": 0.002}},
    ]
    eligible = eligible_target_rows(rows, spec)
    assert len(eligible) == 1
    assert eligible[0]["universal_target_value"] == pytest.approx(0.2)
