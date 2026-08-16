from __future__ import annotations

from copy import deepcopy
import math

import pytest

from seiltanzer.edge_discovery.frozen_ml_candidate import (
    build_ml_frozen_spec,
    predict_ml_frozen,
)
from seiltanzer.edge_discovery.ml_challenger import MODEL_FAMILY, sklearn_version
from seiltanzer.edge_discovery.universal_target_scoring import UniversalTargetSpec


def _rows() -> list[dict]:
    rows = []
    timestamp = 1000.0
    for instrument, base in (("USDCAD", 1.0), ("NAS100", -1.0)):
        for index in range(120):
            x1 = float((index % 20)-10)/10.0
            x2 = float(((index*7) % 20)-10)/10.0
            residual = 0.5 if x1*x2 >= 0.0 else -0.5
            rows.append({
                "instrument": instrument,
                "captured_ts": timestamp,
                "target_ts": timestamp+1800.0,
                "horizon_minutes": 30,
                "ede_features": {
                    "market.x1": x1,
                    "market.x2": x2,
                },
                "universal_target_id": "RETURN_SIGMA",
                "universal_target_value": base+residual,
            })
            timestamp += 1800.0
    return rows


def _candidate() -> dict:
    return {
        "candidate_id": "ml-candidate",
        "target_id": "RETURN_SIGMA",
        "target_family": "RETURN",
        "target_kind": "CONTINUOUS",
        "horizon_minutes": 30,
        "model_family": MODEL_FAMILY,
        "model_library_version": sklearn_version(),
        "model_hyperparameters": {
            "learning_rate": 0.05,
            "max_iter": 80,
            "max_leaf_nodes": 15,
            "max_depth": 3,
            "min_samples_leaf": 40,
            "l2_regularization": 1.0,
            "early_stopping": False,
            "random_state": 17,
            "tuning": "NONE_FIXED_A_PRIORI_V2",
        },
        "production_authority": False,
        "auto_promotion": False,
    }


def _spec() -> UniversalTargetSpec:
    return UniversalTargetSpec(
        "RETURN_SIGMA", "RETURN", "CONTINUOUS", (), ("mae", "rmse"))


def test_ml_freeze_roundtrips_exact_version_schema_baseline_and_model() -> None:
    pytest.importorskip("sklearn")
    frozen = build_ml_frozen_spec(
        _candidate(), _rows(), _spec(), source_set_sha256="dataset-v1")
    assert frozen["model_library_version"] == sklearn_version()
    assert frozen["feature_schema"]["feature_ids"] == ["market.x1", "market.x2"]
    assert frozen["trusted_internal_research_pickle"] is True
    assert frozen["models"][0]["sha256"]
    assert frozen["production_authority"] is False
    assert frozen["auto_promotion"] is False

    future_ts = float(frozen["training_cutoff_ts"])+3600.0
    usdcad = predict_ml_frozen(frozen, {
        "instrument": "USDCAD",
        "captured_ts": future_ts,
        "ede_features": {"market.x1": 0.8, "market.x2": 0.7},
    })
    nas100 = predict_ml_frozen(frozen, {
        "instrument": "NAS100",
        "captured_ts": future_ts,
        "ede_features": {"market.x1": 0.8, "market.x2": 0.7},
    })
    assert math.isfinite(float(usdcad["candidate_prediction"]))
    assert math.isfinite(float(nas100["candidate_prediction"]))
    assert float(usdcad["baseline_prediction"]) > float(nas100["baseline_prediction"])
    assert float(usdcad["candidate_prediction"]) > float(nas100["candidate_prediction"])


def test_ml_freeze_fails_closed_on_version_or_payload_integrity_mismatch() -> None:
    pytest.importorskip("sklearn")
    frozen = build_ml_frozen_spec(
        _candidate(), _rows(), _spec(), source_set_sha256="dataset-v1")
    row = {
        "instrument": "USDCAD",
        "captured_ts": float(frozen["training_cutoff_ts"])+3600.0,
        "ede_features": {"market.x1": 0.8, "market.x2": 0.7},
    }

    wrong_version = deepcopy(frozen)
    wrong_version["model_library_version"] = "0.0.invalid"
    with pytest.raises(ValueError, match="scikit-learn version mismatch"):
        predict_ml_frozen(wrong_version, row)

    tampered = deepcopy(frozen)
    tampered["models"][0]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="payload hash mismatch"):
        predict_ml_frozen(tampered, row)


def test_ml_freeze_refuses_candidate_from_different_library_version() -> None:
    pytest.importorskip("sklearn")
    candidate = _candidate()
    candidate["model_library_version"] = "0.0.invalid"
    with pytest.raises(ValueError, match="differs from freeze runtime"):
        build_ml_frozen_spec(
            candidate, _rows(), _spec(), source_set_sha256="dataset-v1")
