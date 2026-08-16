from __future__ import annotations

import math

import numpy as np
import pytest

from seiltanzer.edge_discovery.ml_challenger import (
    MODEL_FAMILY,
    _baseline_predictions,
    fit_numeric_feature_schema,
    fit_residual_model,
    sklearn_version,
    transform_numeric_features,
)
from seiltanzer.edge_discovery.universal_target_scoring import (
    BASELINE_METHOD,
    DEPENDENCY_PVALUE_METHOD,
    UniversalTargetSpec,
    relative_target_improvement,
    target_metrics,
)


def _row(
    index: int, x1: float, x2: float, target: float, *,
    instrument: str = "NAS100",
) -> dict:
    return {
        "instrument": instrument,
        "captured_ts": float(index * 300),
        "target_ts": float(index * 300 + 1800),
        "horizon_minutes": 30,
        "ede_features": {
            "market.x1": x1,
            "market.x2": x2,
            "rates.us10y_yield": 4.2,
            "cross_asof": float(index),
            "market_breadth_coverage": 0.9,
        },
        "universal_target_id": "RETURN_SIGMA",
        "universal_target_value": target,
    }


def test_feature_schema_is_outcome_blind_and_excludes_rates_and_quality_fields() -> None:
    rows = [
        _row(index, float(index % 5), float(index % 7),
             999.0 if index % 2 else -999.0)
        for index in range(100)
    ]
    schema = fit_numeric_feature_schema(rows)
    assert schema.feature_ids == ("market.x1", "market.x2")
    assert "rates.us10y_yield" not in schema.feature_ids
    assert "cross_asof" not in schema.feature_ids
    assert "market_breadth_coverage" not in schema.feature_ids
    matrix = transform_numeric_features(rows[:3], schema)
    assert matrix.shape == (3, 2)


def test_research_dependency_is_optional_not_production_runtime_requirement() -> None:
    assert MODEL_FAMILY.startswith("sklearn.")
    # Importing this module succeeds without importing sklearn at module import
    # time. Version resolution/fitting is exercised only by research CI.


def test_ml_baseline_is_the_accepted_instrument_structural_baseline() -> None:
    spec = UniversalTargetSpec(
        "RETURN_SIGMA", "RETURN", "CONTINUOUS", (), ("mae", "rmse"))
    train = []
    for index in range(60):
        train.append(_row(
            index+1, float(index % 5), float(index % 7), 1.0,
            instrument="USDCAD"))
        train.append(_row(
            index+1000, float(index % 5), float(index % 7), -1.0,
            instrument="NAS100"))
    test = [
        _row(3000+index, float(index % 5), float(index % 7), 1.0,
             instrument="USDCAD")
        for index in range(10)
    ]
    _train_baseline, test_baseline = _baseline_predictions(train, test, spec)
    assert np.allclose(test_baseline, 1.0)
    assert BASELINE_METHOD == "TRAIN_ONLY_INSTRUMENT_FAMILY_GLOBAL_RESIDUAL_V1"
    assert "DAY_PARITY_CLUSTER" in DEPENDENCY_PVALUE_METHOD


def test_shallow_residual_boosting_finds_nonlinear_interaction_oos() -> None:
    pytest.importorskip("sklearn")
    rng = np.random.default_rng(123)
    train = []
    test = []
    for index in range(800):
        x1, x2 = rng.normal(size=2)
        signal = 0.75 if x1*x2 >= 0 else -0.75
        target = signal+float(rng.normal(scale=0.08))
        row = _row(index+1, float(x1), float(x2), target)
        (train if index < 600 else test).append(row)
    schema = fit_numeric_feature_schema(train)
    x_train = transform_numeric_features(train, schema)
    x_test = transform_numeric_features(test, schema)
    spec = UniversalTargetSpec(
        "RETURN_SIGMA", "RETURN", "CONTINUOUS", (), ("mae", "rmse"))
    prediction, baseline_prediction, models = fit_residual_model(
        train, test, x_train, x_test, spec)
    assert len(models) == 1
    model_metrics = target_metrics(test, prediction, spec)
    baseline_metrics = target_metrics(test, baseline_prediction, spec)
    improvement = relative_target_improvement(
        model_metrics, baseline_metrics, spec)
    assert improvement["mae"] > 0.20
    assert improvement["rmse"] > 0.20
    assert math.isfinite(float(prediction.mean()))
    assert sklearn_version()


def test_no_target_value_enters_feature_matrix() -> None:
    rows_a = [
        _row(index, float(index % 4), float(index % 6), 1.0)
        for index in range(80)
    ]
    rows_b = [
        _row(index, float(index % 4), float(index % 6), -1000.0)
        for index in range(80)
    ]
    schema_a = fit_numeric_feature_schema(rows_a)
    schema_b = fit_numeric_feature_schema(rows_b)
    assert schema_a == schema_b
    assert np.array_equal(
        transform_numeric_features(rows_a, schema_a),
        transform_numeric_features(rows_b, schema_b),
    )
