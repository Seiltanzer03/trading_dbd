"""Model-artifact integrity gate for G.1C prospective predictions."""
from __future__ import annotations

import time
from typing import Any

from . import g1_shadow_refinement as _ref
from . import g1_shadow_runtime as _g1c
from . import passive_learning as _pl

_ENGINE = _pl.PassiveLearningEngine
ARTIFACT_REVALIDATION_VERSION = "g1c-model-artifact-revalidation-v1"
_PREVIOUS_MODELS = _ENGINE.g1c_models


def _artifact_payload(model: dict) -> dict:
    return {
        "g1c_contract_version": _g1c.G1C_CONTRACT_VERSION,
        "algorithm_version": model.get("algorithm_version"),
        "model_family": model.get("model_family"),
        "scope_key": model.get("scope_key"),
        "scope": _ref._loads(model.get("scope_json")) if "scope_json" in model else (model.get("scope") or {}),
        "training_cut_id": model.get("training_cut_id"),
        "training_cut_sha256": model.get("training_cut_sha256"),
        "target_contract_version": _g1c.G1C_TARGET_CONTRACT_VERSION,
        "fit_weight_contract_version": _g1c.G1C_WEIGHT_CONTRACT_VERSION,
        "parameters": _ref._loads(model.get("parameters_json")) if "parameters_json" in model else (model.get("parameters") or {}),
    }


def _artifact_valid(model: dict) -> bool:
    expected = str(model.get("artifact_sha256") or "")
    return bool(expected) and _g1c._sha(_artifact_payload(model)) == expected


def predict_with_artifact_revalidation(self: _ENGINE, observation_id: str) -> dict:
    observation_id = str(observation_id)
    blocker = _ref._t0_blocker(self, observation_id)
    if blocker is not None:
        _g1c._record_error(
            self,
            "PREDICTION_T0_CONTRACT_REJECTED",
            observation_id=observation_id,
            detail=blocker,
        )
        return {
            "observation_id": observation_id,
            "predictions_created": 0,
            "status": "PREDICTION_T0_CONTRACT_REJECTED",
            "blocker": blocker,
            "prediction_admission_contract_version": _ref.REFINEMENT_VERSION,
            "artifact_revalidation_contract_version": ARTIFACT_REVALIDATION_VERSION,
            "production_used": False,
        }

    observation = _g1c._pending_q_observation(self, observation_id)
    if observation is None:
        return {
            "observation_id": observation_id,
            "predictions_created": 0,
            "status": "NOT_Q_OBSERVATION",
            "artifact_revalidation_contract_version": ARTIFACT_REVALIDATION_VERSION,
            "production_used": False,
        }
    captured = float(observation["captured_ts"])
    target = _g1c._finite(observation.get("target_ts"))
    if target is None or target <= captured:
        return {
            "observation_id": observation_id,
            "predictions_created": 0,
            "status": "TIME_CONTRACT_INVALID",
            "artifact_revalidation_contract_version": ARTIFACT_REVALIDATION_VERSION,
            "production_used": False,
        }
    if time.time() >= target:
        return {
            "observation_id": observation_id,
            "predictions_created": 0,
            "status": "PREDICTION_TOO_LATE",
            "artifact_revalidation_contract_version": ARTIFACT_REVALIDATION_VERSION,
            "production_used": False,
        }

    with self._lock:
        models = [dict(row) for row in self._conn.execute(
            "SELECT * FROM g1c_shadow_models WHERE status='FITTED_UNVALIDATED' "
            "AND oos_validated=0 AND production_authority=0 AND created_ts<=? AND training_cutoff<? "
            "ORDER BY created_ts,model_id",
            (captured, captured),
        ).fetchall()]

    created = []
    invalid_artifact_n = 0
    for model in models:
        if model["model_family"] not in {"PLATT", "BETA", "ISOTONIC"}:
            continue
        if not _ref._semantic_model_matches(model, observation):
            continue
        if not _artifact_valid(model):
            invalid_artifact_n += 1
            _g1c._record_error(
                self,
                "MODEL_SHA_MISMATCH",
                model_id=model["model_id"],
                observation_id=observation_id,
                detail=f"artifact={model.get('artifact_sha256')}",
            )
            continue
        with self._lock:
            overlap = self._conn.execute(
                "SELECT 1 FROM g1_dataset_cut_members WHERE cut_id=? AND observation_id=? LIMIT 1",
                (model["training_cut_id"], observation_id),
            ).fetchone()
        if overlap is not None:
            _g1c._record_error(
                self,
                "PREDICTION_TRAINING_OVERLAP",
                model_id=model["model_id"],
                observation_id=observation_id,
            )
            continue
        params = _ref._loads(model.get("parameters_json"))
        try:
            shadow_p = _g1c._predict_parameters(
                model["model_family"], params, float(observation["raw_q"])
            )
        except ValueError as exc:
            _g1c._record_error(
                self,
                str(exc),
                model_id=model["model_id"],
                observation_id=observation_id,
            )
            continue
        identity = {
            "prediction_contract_version": _g1c.G1C_PREDICTION_CONTRACT_VERSION,
            "observation_id": observation_id,
            "model_id": model["model_id"],
            "model_artifact_sha256": model["artifact_sha256"],
            "raw_q": round(float(observation["raw_q"]), 12),
            "shadow_p": round(float(shadow_p), 12),
        }
        prediction_id = "g1c-pred-" + _g1c._sha(identity)[:24]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO g1c_shadow_predictions("
                "prediction_id,observation_id,captured_ts,model_id,model_artifact_sha256,training_cut_id,"
                "training_cutoff,model_family,model_scope_key,raw_q,shadow_calibrated_probability,"
                "target_contract_version,prediction_contract_version,prediction_status,authority,production_used,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    prediction_id, observation_id, captured, model["model_id"], model["artifact_sha256"],
                    model["training_cut_id"], float(model["training_cutoff"]), model["model_family"],
                    model["scope_key"], float(observation["raw_q"]), float(shadow_p),
                    _g1c.G1C_TARGET_CONTRACT_VERSION, _g1c.G1C_PREDICTION_CONTRACT_VERSION,
                    "PENDING_OUTCOME", "research_only", 0, time.time(),
                ),
            )
        if cursor.rowcount:
            created.append(prediction_id)

    return {
        "observation_id": observation_id,
        "predictions_created": len(created),
        "prediction_ids": created,
        "invalid_model_artifact_n": invalid_artifact_n,
        "status": "PREDICTED" if created else (
            "MODEL_ARTIFACT_REJECTED" if invalid_artifact_n else "NO_ELIGIBLE_FROZEN_MODEL"
        ),
        "prediction_admission_contract_version": _ref.REFINEMENT_VERSION,
        "artifact_revalidation_contract_version": ARTIFACT_REVALIDATION_VERSION,
        "production_used": False,
    }


def models_with_artifact_integrity(self: _ENGINE, limit: int = 200) -> dict:
    payload = _PREVIOUS_MODELS(self, limit=limit)
    for item in payload.get("items", []):
        item["artifact_valid"] = _artifact_valid(item)
        item["artifact_revalidation_contract_version"] = ARTIFACT_REVALIDATION_VERSION
    payload["artifact_revalidation_contract_version"] = ARTIFACT_REVALIDATION_VERSION
    return payload


def install_g1_shadow_artifact_refinement() -> None:
    if getattr(_ENGINE, "_g1_shadow_artifact_refinement", None) == ARTIFACT_REVALIDATION_VERSION:
        return
    _ENGINE.g1c_predict_observation = predict_with_artifact_revalidation
    _ENGINE.g1c_models = models_with_artifact_integrity
    # The automatic collector calls this runtime-global symbol after writing a
    # new native-expiry Q observation, so it must use the integrity gate too.
    _g1c.g1c_predict_observation = predict_with_artifact_revalidation
    _ENGINE._g1c_model_artifact_valid = staticmethod(_artifact_valid)
    _ENGINE._g1_shadow_artifact_refinement = ARTIFACT_REVALIDATION_VERSION
