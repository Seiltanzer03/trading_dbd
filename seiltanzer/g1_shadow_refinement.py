"""Fail-closed T0 admission for Phase G.1C prospective shadow predictions."""
from __future__ import annotations

import json
import math
from typing import Any

from . import g1_shadow_runtime as _g1c
from . import passive_learning as _pl
from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION, valid_terminal_cdf
from .option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION

_ENGINE = _pl.PassiveLearningEngine
REFINEMENT_VERSION = "g1c-shadow-t0-admission-v1"
_PREVIOUS_PREDICT = _ENGINE.g1c_predict_observation


def _loads(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _t0_blocker(self: _ENGINE, observation_id: str) -> str | None:
    with self._lock:
        row = self._conn.execute(
            "SELECT observation_id,captured_ts,target_ts,instrument,feature_contract_version,"
            "forecast_json,evidence_eligible,observation_origin,retrospective_replay,price_kind "
            "FROM passive_market_observations WHERE observation_id=?",
            (str(observation_id),),
        ).fetchone()
    if row is None:
        return "OBSERVATION_NOT_FOUND"
    row = dict(row)
    forecast = _loads(row.get("forecast_json"))
    captured = _finite(row.get("captured_ts"))
    target = _finite(row.get("target_ts"))
    expiry = _finite(forecast.get("source_expiry_ts_utc"))
    if row.get("feature_contract_version") != _pl.PASSIVE_SCHEMA_VERSION:
        return "WRONG_SOURCE_SCHEMA"
    if forecast.get("measurement_runtime_contract") != MEASUREMENT_RUNTIME_VERSION:
        return "WRONG_MEASUREMENT_RUNTIME"
    if row.get("observation_origin") != "background_collector":
        return "NOT_BACKGROUND_COLLECTOR"
    if bool(row.get("retrospective_replay")):
        return "RETROSPECTIVE_REPLAY"
    if not bool(row.get("evidence_eligible")):
        return "EVIDENCE_INELIGIBLE"
    if row.get("price_kind") != "direct":
        return "NON_DIRECT_T0_PRICE"
    if forecast.get("horizon_kind") != "option_native_expiry":
        return "HORIZON_SEMANTIC_MISMATCH"
    if forecast.get("probability_measure") != "risk_neutral_Q_terminal":
        return "Q_SEMANTIC_UNAVAILABLE"
    if forecast.get("q_source_contract") != OPTION_Q_CONTRACT_VERSION:
        return "Q_CONTRACT_MISMATCH"
    if forecast.get("expiry_clock_version") != EXPIRY_CLOCK_VERSION:
        return "EXPIRY_CONTRACT_MISMATCH"
    if not bool(forecast.get("q_terminal_distribution_available")):
        return "Q_DISTRIBUTION_UNAVAILABLE"
    if not valid_terminal_cdf(forecast.get("terminal_q_cdf")):
        return "INVALID_FROZEN_Q_CDF"
    transform = str(forecast.get("proxy_transform") or "").lower()
    if transform not in {"direct", "inverse"}:
        return "PROXY_TRANSFORM_UNKNOWN"
    if str(forecast.get("q_target_instrument") or "") != str(row.get("instrument") or ""):
        return "Q_TARGET_MISMATCH"
    if captured is None or target is None or target <= captured:
        return "INVALID_TIME_CONTRACT"
    if expiry is None or abs(expiry - target) > 1.0:
        return "EXPIRY_CONTRACT_MISMATCH"
    return None


def predict_with_t0_admission(self: _ENGINE, observation_id: str) -> dict:
    blocker = _t0_blocker(self, str(observation_id))
    if blocker is not None:
        _g1c._record_error(
            self,
            "PREDICTION_T0_CONTRACT_REJECTED",
            observation_id=str(observation_id),
            detail=blocker,
        )
        return {
            "observation_id": str(observation_id),
            "predictions_created": 0,
            "status": "PREDICTION_T0_CONTRACT_REJECTED",
            "blocker": blocker,
            "prediction_admission_contract_version": REFINEMENT_VERSION,
            "production_used": False,
        }
    result = _PREVIOUS_PREDICT(self, str(observation_id))
    result["prediction_admission_contract_version"] = REFINEMENT_VERSION
    return result


def install_g1_shadow_refinement() -> None:
    if getattr(_ENGINE, "_g1_shadow_refinement", None) == REFINEMENT_VERSION:
        return
    _ENGINE.g1c_predict_observation = predict_with_t0_admission
    _ENGINE._g1c_prediction_t0_blocker = _t0_blocker
    _ENGINE._g1_shadow_refinement = REFINEMENT_VERSION
