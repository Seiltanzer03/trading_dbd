"""Immutable research-only freeze for accepted PASS 6 ML challengers.

Frozen ML artifacts are produced and consumed only inside the trusted internal
research registry.  The exact feature schema, structural baseline, Python/
scikit-learn versions and SHA-256 of every serialized estimator are frozen before
prospective OOS begins.  Any mismatch fails closed; no generic untrusted pickle
loading API is exposed.
"""
from __future__ import annotations

import base64
import hashlib
import pickle
import sys
from typing import Any

import numpy as np

from .frozen_candidate import (
    frozen_structural_baseline_prediction,
    serialize_structural_baseline,
)
from .ml_challenger import (
    MODEL_FAMILY,
    NumericFeatureSchema,
    fit_numeric_feature_schema,
    fit_residual_model,
    predict_residual_models,
    sklearn_version,
    transform_numeric_features,
)
from .universal_target_scoring import (
    UniversalTargetSpec,
    fit_structural_baseline,
)


FROZEN_ML_SPEC_VERSION = "g1s-universal-ml-frozen-spec-v1"
MODEL_SERIALIZATION_VERSION = "PYTHON_PICKLE_PROTOCOL_5_INTERNAL_RESEARCH_V1"


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _encode_model(model: Any) -> dict[str, Any]:
    if type(model).__name__ != "HistGradientBoostingRegressor":
        raise ValueError("unsupported frozen ML estimator class")
    if not type(model).__module__.startswith("sklearn."):
        raise ValueError("frozen ML estimator is not a scikit-learn model")
    payload = pickle.dumps(model, protocol=5)
    return {
        "serialization": MODEL_SERIALIZATION_VERSION,
        "class_module": str(type(model).__module__),
        "class_name": str(type(model).__name__),
        "sha256": _sha_bytes(payload),
        "payload_b64": base64.b64encode(payload).decode("ascii"),
    }


def _decode_model(record: dict[str, Any]) -> Any:
    if record.get("serialization") != MODEL_SERIALIZATION_VERSION:
        raise ValueError("unsupported frozen ML serialization contract")
    try:
        payload = base64.b64decode(
            str(record["payload_b64"]).encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError("invalid frozen ML model payload") from exc
    if _sha_bytes(payload) != str(record.get("sha256")):
        raise ValueError("frozen ML model payload hash mismatch")
    # Only trusted registry-produced research artifacts reach this function. The
    # digest is integrity protection, not a substitute for source trust.
    model = pickle.loads(payload)
    if str(type(model).__module__) != str(record.get("class_module")):
        raise ValueError("frozen ML estimator module mismatch")
    if str(type(model).__name__) != str(record.get("class_name")):
        raise ValueError("frozen ML estimator class mismatch")
    if type(model).__name__ != "HistGradientBoostingRegressor":
        raise ValueError("unsupported frozen ML estimator class")
    if not type(model).__module__.startswith("sklearn."):
        raise ValueError("frozen ML estimator is not a scikit-learn model")
    return model


def _target_spec_from_frozen(frozen_spec: dict[str, Any]) -> UniversalTargetSpec:
    return UniversalTargetSpec(
        str(frozen_spec["target_id"]),
        str(frozen_spec.get("target_family") or "UNKNOWN"),
        str(frozen_spec["target_kind"]),
        tuple(str(item) for item in frozen_spec.get("target_classes") or []),
        tuple(str(item) for item in frozen_spec.get("primary_metrics") or []),
    )


def _output_prediction(value: np.ndarray, spec: UniversalTargetSpec) -> float | list[float]:
    array = np.asarray(value, dtype=float)
    if spec.kind == "MULTICLASS":
        if array.shape != (1, len(spec.classes)):
            raise ValueError("invalid frozen multiclass prediction shape")
        return [float(item) for item in array[0]]
    if array.shape != (1,):
        raise ValueError("invalid frozen scalar prediction shape")
    return float(array[0])


def build_ml_frozen_spec(
    candidate: dict[str, Any], rows: list[dict[str, Any]], spec: UniversalTargetSpec,
    *, source_set_sha256: str,
) -> dict[str, Any]:
    """Refit the fixed PASS 6 challenger once on the full pre-freeze cohort."""
    if not rows:
        raise ValueError("cannot freeze ML candidate without historical rows")
    if candidate.get("target_id") != spec.target_id:
        raise ValueError("candidate target does not match frozen ML target")
    if int(candidate.get("horizon_minutes") or 0) <= 0:
        raise ValueError("candidate horizon is missing")
    if candidate.get("model_family") != MODEL_FAMILY:
        raise ValueError("candidate model family does not match accepted PASS 6 family")
    if any(row.get("universal_target_id") != spec.target_id for row in rows):
        raise ValueError("frozen ML cohort contains a different target")

    schema = fit_numeric_feature_schema(rows)
    if len(schema.feature_ids) < 2:
        raise ValueError("ML freeze requires at least two admissible numeric features")
    x_train = transform_numeric_features(rows, schema)
    _prediction, _baseline, models = fit_residual_model(
        rows, rows, x_train, x_train, spec)
    baseline_model = fit_structural_baseline(rows, spec)
    training_cutoff_ts = max(float(row["target_ts"]) for row in rows)
    feature_cutoff_ts = max(float(row["captured_ts"]) for row in rows)
    if feature_cutoff_ts > training_cutoff_ts + 1e-6:
        raise ValueError("historical target chronology is malformed")

    frozen_library_version = sklearn_version()
    candidate_library_version = candidate.get("model_library_version")
    if candidate_library_version not in {None, frozen_library_version}:
        raise ValueError("candidate scikit-learn version differs from freeze runtime")

    return {
        "contract_version": FROZEN_ML_SPEC_VERSION,
        "candidate_id": candidate.get("candidate_id"),
        "hypothesis_id": candidate.get("hypothesis_id"),
        "model_family": MODEL_FAMILY,
        "model_library": "scikit-learn",
        "model_library_version": frozen_library_version,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "model_hyperparameters": candidate.get("model_hyperparameters") or {},
        "feature_schema": schema.as_dict(),
        "structural_baseline": serialize_structural_baseline(baseline_model),
        "models": [_encode_model(model) for model in models],
        "target_id": spec.target_id,
        "target_family": spec.family,
        "target_kind": spec.kind,
        "target_classes": list(spec.classes),
        "primary_metrics": list(spec.primary_metrics),
        "horizon_minutes": int(candidate["horizon_minutes"]),
        "historical_raw_n": len(rows),
        "feature_cutoff_ts": feature_cutoff_ts,
        "training_cutoff_ts": training_cutoff_ts,
        "source_set_sha256": str(source_set_sha256),
        "trusted_internal_research_pickle": True,
        "evidence_label": "HISTORICAL_WALK_FORWARD",
        "prospective_evidence_counted": False,
        "production_authority": False,
        "auto_promotion": False,
    }


def predict_ml_frozen(
    frozen_spec: dict[str, Any], row: dict[str, Any],
) -> dict[str, Any]:
    """Run one immutable PASS 6 challenger without any live refit."""
    if frozen_spec.get("contract_version") != FROZEN_ML_SPEC_VERSION:
        raise ValueError("unsupported frozen ML candidate contract")
    if frozen_spec.get("trusted_internal_research_pickle") is not True:
        raise ValueError("frozen ML artifact is not marked as trusted internal research")
    if frozen_spec.get("model_family") != MODEL_FAMILY:
        raise ValueError("frozen ML model family mismatch")
    if str(frozen_spec.get("model_library_version")) != sklearn_version():
        raise ValueError("frozen ML scikit-learn version mismatch")
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if str(frozen_spec.get("python_version")) != current_python:
        raise ValueError("frozen ML Python version mismatch")

    schema_payload = frozen_spec.get("feature_schema") or {}
    schema = NumericFeatureSchema(
        tuple(str(value) for value in schema_payload.get("feature_ids") or []),
        tuple(float(value) for value in schema_payload.get("medians") or []),
        tuple(float(value) for value in schema_payload.get("coverage") or []),
    )
    if not schema.feature_ids or not (
            len(schema.feature_ids) == len(schema.medians) == len(schema.coverage)):
        raise ValueError("invalid frozen ML feature schema")
    spec = _target_spec_from_frozen(frozen_spec)
    x = transform_numeric_features([row], schema)
    baseline = frozen_structural_baseline_prediction(
        frozen_spec["structural_baseline"], row, spec)
    model_records = frozen_spec.get("models") or []
    expected_models = len(spec.classes) if spec.kind == "MULTICLASS" else 1
    if len(model_records) != expected_models:
        raise ValueError("frozen ML estimator count does not match target kind")
    models = tuple(_decode_model(record) for record in model_records)
    prediction = predict_residual_models(models, x, baseline, spec)
    return {
        "qualified": True,
        "candidate_prediction": _output_prediction(prediction, spec),
        "baseline_prediction": _output_prediction(baseline, spec),
        "target_id": frozen_spec["target_id"],
        "target_kind": frozen_spec["target_kind"],
        "horizon_minutes": frozen_spec["horizon_minutes"],
        "frozen_training_cutoff_ts": frozen_spec["training_cutoff_ts"],
        "production_authority": False,
        "auto_promotion": False,
    }
