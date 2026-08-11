"""Fail-closed refinements for Phase G.1C shadow calibration."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from typing import Any

from . import g1_shadow_runtime as _g1c
from . import passive_learning as _pl
from .measurement_q_runtime import MEASUREMENT_RUNTIME_VERSION, valid_terminal_cdf
from .option_q_adapter import EXPIRY_CLOCK_VERSION, OPTION_Q_CONTRACT_VERSION

_ENGINE = _pl.PassiveLearningEngine
REFINEMENT_VERSION = "g1c-shadow-integrity-v2"
_PREVIOUS_PREDICT = _ENGINE.g1c_predict_observation
_PREVIOUS_REFIT = _ENGINE.g1c_refit
_PREVIOUS_STATUS = _ENGINE.g1c_status

_CRITICAL_ERRORS = {
    "TRAINING_CUT_INVALID",
    "TRAINING_CUT_MUTATED",
    "Q_CONTRACT_MISMATCH",
    "TARGET_CONTRACT_MISMATCH",
    "DEPENDENCY_CONTRACT_MISMATCH",
    "MODEL_ARTIFACT_INVALID",
    "MODEL_SHA_MISMATCH",
    "NONFINITE_PARAMETERS",
    "NON_MONOTONE_MAPPING",
}


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


def _semantic_tuple(row: dict) -> tuple[str, str]:
    base = row.get("base_cohort") if isinstance(row.get("base_cohort"), dict) else {}
    relation = str(base.get("q_relation") or "unknown").lower()
    transform = str(base.get("proxy_transform") or "unknown").lower()
    return relation, transform


def _semantic_scope_definitions(rows: list[dict]) -> list[tuple[str, dict, list[dict]]]:
    """Never pool native/direct/inverse observations behind an unlabeled model."""
    by_semantic: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_instrument_semantic: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    by_cohort: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        relation, transform = _semantic_tuple(row)
        instrument = str(row.get("instrument"))
        by_semantic[(relation, transform)].append(row)
        by_instrument_semantic[(instrument, relation, transform)].append(row)
        by_cohort[str(row.get("base_cohort_id"))].append(row)

    scopes: list[tuple[str, dict, list[dict]]] = []
    for relation, transform in sorted(by_semantic):
        scopes.append((
            f"GLOBAL_TERMINAL_Q:{relation}:{transform}",
            {
                "kind": "global_terminal_q_semantic",
                "q_relation": relation,
                "proxy_transform": transform,
            },
            by_semantic[(relation, transform)],
        ))
    for instrument, relation, transform in sorted(by_instrument_semantic):
        scopes.append((
            f"INSTRUMENT:{instrument}:{relation}:{transform}",
            {
                "kind": "instrument_semantic",
                "instrument": instrument,
                "q_relation": relation,
                "proxy_transform": transform,
            },
            by_instrument_semantic[(instrument, relation, transform)],
        ))
    for cohort_id in sorted(by_cohort):
        base = by_cohort[cohort_id][0].get("base_cohort") or {}
        scopes.append((
            f"COHORT:{cohort_id}",
            {
                "kind": "g1a_cohort",
                "cohort_id": cohort_id,
                "instrument": base.get("instrument"),
                "horizon_bucket": base.get("horizon_bucket"),
                "q_relation": base.get("q_relation"),
                "proxy_transform": base.get("proxy_transform"),
            },
            by_cohort[cohort_id],
        ))
    return scopes


def _semantic_model_matches(model: dict, observation: dict) -> bool:
    scope = _loads(model.get("scope_json"))
    relation, transform = _semantic_tuple(observation)
    kind = scope.get("kind")
    if kind == "global_terminal_q_semantic":
        return (
            str(scope.get("q_relation") or "").lower() == relation
            and str(scope.get("proxy_transform") or "").lower() == transform
        )
    if kind == "instrument_semantic":
        return (
            str(scope.get("instrument")) == str(observation.get("instrument"))
            and str(scope.get("q_relation") or "").lower() == relation
            and str(scope.get("proxy_transform") or "").lower() == transform
        )
    if kind == "g1a_cohort":
        return str(scope.get("cohort_id")) == str(observation.get("base_cohort_id"))
    return False


def _scope_fit_readiness(self: _ENGINE, rows: list[dict]) -> dict:
    scopes = _semantic_scope_definitions(rows)
    output = {}
    for family in ("PLATT", "BETA", "ISOTONIC", "PIT_ISOTONIC_CDF"):
        statuses = []
        for scope_key, _scope, members in scopes:
            stats = _g1c._stats(self, members)
            item = _g1c._threshold_status(stats, family)
            item = {**item, "scope_key": scope_key}
            statuses.append(item)
        if not statuses:
            base = _g1c._threshold_status(_g1c._stats(self, []), family)
            statuses = [{**base, "scope_key": None}]
        ready = [item for item in statuses if item["ready"]]
        blocker_counts = Counter()
        for item in statuses:
            blocker_counts.update(item["blockers"])
        output[family.lower().replace("pit_isotonic_cdf", "full_cdf")] = {
            "family": family,
            "threshold_contract_version": _g1c.G1C_FIT_THRESHOLD_VERSION,
            "ready": bool(ready),
            "status": "READY_TO_FIT" if ready else "INSUFFICIENT_EVIDENCE",
            "ready_scope_n": len(ready),
            "scope_n": len(statuses),
            "blockers": dict(blocker_counts),
            "scopes": statuses,
        }
    return output


def _semantic_refit(self: _ENGINE, *, force: bool = False, cutoff_ts: float | None = None) -> dict:
    rows = _g1c._q_rows(self)
    scopes = _semantic_scope_definitions(rows)
    ready_candidates = []
    for scope_key, _scope, members in scopes:
        stats = _g1c._stats(self, members)
        for family in ("PLATT", "BETA", "ISOTONIC", "PIT_ISOTONIC_CDF"):
            threshold = _g1c._threshold_status(stats, family)
            if threshold["ready"]:
                ready_candidates.append((scope_key, family, stats["effective_n"]))
    if not ready_candidates:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "models_created": 0,
            "stats": _g1c._stats(self, rows),
            "semantic_scope_n": len(scopes),
        }
    if not force and not any(
        _g1c._refit_delta_ready(self, scope_key, family, effective_n)
        for scope_key, family, effective_n in ready_candidates
    ):
        return {
            "status": "REFIT_DELTA_NOT_REACHED",
            "models_created": 0,
            "stats": _g1c._stats(self, rows),
            "semantic_scope_n": len(scopes),
        }
    # The predecessor's aggregate precheck is necessarily satisfied when any
    # semantic subgroup satisfies the same threshold. Its actual fitting loop
    # resolves `_scope_definitions` dynamically, replaced below with ours.
    return _PREVIOUS_REFIT(self, force=True, cutoff_ts=cutoff_ts)


def _critical_error_count(self: _ENGINE) -> int:
    placeholders = ",".join("?" for _ in _CRITICAL_ERRORS)
    with self._lock:
        return int(self._conn.execute(
            f"SELECT COUNT(*) FROM g1c_contract_errors WHERE error_type IN ({placeholders})",
            tuple(sorted(_CRITICAL_ERRORS)),
        ).fetchone()[0])


def _status_with_semantic_integrity(self: _ENGINE) -> dict:
    status = _PREVIOUS_STATUS(self)
    rows = _g1c._q_rows(self)
    stats = _g1c._stats(self, rows)
    readiness = _scope_fit_readiness(self, rows)
    critical_n = _critical_error_count(self)
    g1d = _g1c._g1d_status(stats, critical_contract_errors=critical_n)
    blocker_counts = Counter()
    for family in readiness.values():
        blocker_counts.update(family.get("blockers") or {})
    status["fit_readiness"] = readiness
    status["shadow_model_fitting_allowed"] = any(item["ready"] for item in readiness.values())
    status["top_fit_blockers"] = dict(blocker_counts.most_common())
    status["critical_contract_error_n"] = critical_n
    status["critical_contract_error_types"] = sorted(_CRITICAL_ERRORS)
    status["g1d_readiness"] = g1d
    status["ready_for_g1d"] = g1d["ready"]
    status["q_semantic_pooling"] = "separated_by_q_relation_and_proxy_transform"
    status["refinement_contract_version"] = REFINEMENT_VERSION
    return status


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
    # Runtime functions resolve these module globals dynamically. Replace both
    # the direct engine methods and runtime globals so collector/refit paths use
    # exactly the same semantic and no-lookahead gates.
    _g1c._scope_definitions = _semantic_scope_definitions
    _g1c._model_matches = _semantic_model_matches
    _g1c.g1c_refit = _semantic_refit
    _g1c.g1c_predict_observation = predict_with_t0_admission
    _ENGINE.g1c_refit = _semantic_refit
    _ENGINE.g1c_status = _status_with_semantic_integrity
    _ENGINE.g1c_predict_observation = predict_with_t0_admission
    _ENGINE._g1c_prediction_t0_blocker = _t0_blocker
    _ENGINE._g1c_semantic_scope_definitions = staticmethod(_semantic_scope_definitions)
    _ENGINE._g1_shadow_refinement = REFINEMENT_VERSION
