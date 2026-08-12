"""Continuous-return completion for strict G.1S V2 features.

Installed after the V2 directional layer.  It trains/predicts only on future
observations carrying the strict pre-T0 V2 contract.  It also reapplies the
causal calibrator after V2 directional prediction creation so V2 probabilities
are calibrated only by artifacts that existed before that same T0.
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np

from .g1_short_horizon_calibration import _apply_calibrators
from .g1_short_horizon_continuous_learning import (
    RETURN_FIT_REQUIRED,
    RETURN_RIDGE_L2,
    _ensure_tables,
    _evidence,
    _fit_ridge,
    _historical_diagnostics,
    _json,
    _loads,
    _rows,
    _sha,
)
from .g1_short_horizon_feature_contract_v2 import (
    FEATURE_CONTRACT_V2,
    V2_FEATURE_SETS,
    _has_v2,
)
from .g1_short_horizon_runtime import (
    HORIZONS,
    MODEL_REFIT_INTERVAL_SEC,
    MODEL_REFIT_MIN_EFFECTIVE_DELTA,
    ShortHorizonRuntime,
)


CONTINUOUS_V2_VERSION = "g1s-continuous-v2-v1"
RETURN_MODEL_V2_VERSION = "g1s-ridge-return-model-v2"
RETURN_PREDICTION_V2_VERSION = "g1s-prospective-return-prediction-v2"


def _fit_v2_return_models(runtime: ShortHorizonRuntime, *, force: bool = False) -> int:
    _ensure_tables(runtime)
    created = 0
    now = time.time()
    for horizon in HORIZONS:
        rows = [row for row in _rows(runtime, horizon) if _has_v2(row)]
        evidence = _evidence(runtime, rows)
        if not evidence["fit_allowed"]:
            continue
        for feature_set, names in V2_FEATURE_SETS.items():
            with runtime._lock:
                latest = runtime._conn.execute(
                    "SELECT created_ts,effective_n FROM g1s_return_models "
                    "WHERE horizon_minutes=? AND feature_set=? AND model_family=? "
                    "ORDER BY created_ts DESC LIMIT 1",
                    (int(horizon), str(feature_set), "DEPENDENCY_WEIGHTED_RIDGE_V2"),
                ).fetchone()
            if latest and not force:
                if now-float(latest["created_ts"]) < MODEL_REFIT_INTERVAL_SEC:
                    continue
                if evidence["effective_n"]-float(latest["effective_n"]) < MODEL_REFIT_MIN_EFFECTIVE_DELTA:
                    continue
            beta, mean, std = _fit_ridge(runtime, rows, feature_set)
            cutoff = max(float(row["resolved_ts"]) for row in rows)
            vector, _ = runtime._feature_vector(rows[0], feature_set)
            feature_names = list(names)
            feature_names.extend(
                f"instrument_one_hot:{index}"
                for index in range(max(0, len(vector)-len(feature_names)))
            )
            params = {
                "intercept_and_coefficients": [float(v) for v in beta],
                "feature_mean": [float(v) for v in mean],
                "feature_std": [float(v) for v in std],
                "feature_names": feature_names,
                "ridge_l2": RETURN_RIDGE_L2,
                "feature_contract_version": FEATURE_CONTRACT_V2,
            }
            artifact = {
                "contract_version": RETURN_MODEL_V2_VERSION,
                "model_family": "DEPENDENCY_WEIGHTED_RIDGE_V2",
                "target": "terminal_log_return",
                "feature_contract_version": FEATURE_CONTRACT_V2,
                "horizon_minutes": int(horizon),
                "feature_set": str(feature_set),
                "training_cutoff_ts": cutoff,
                "source_observation_ids": [str(row["observation_id"]) for row in rows],
                "parameters": params,
            }
            artifact_sha = _sha(_json(artifact))
            model_id = "g1s-ret-v2-"+artifact_sha[:27]
            diagnostics = _historical_diagnostics(runtime, rows, feature_set)
            diagnostics["feature_contract_version"] = FEATURE_CONTRACT_V2
            diagnostics["dependency_weighted_fit"] = True
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_return_models("
                    "model_id,model_family,horizon_minutes,feature_set,training_cutoff_ts,"
                    "raw_n,effective_n,training_days,parameters_json,diagnostics_json,"
                    "artifact_sha256,authority,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                    (model_id, "DEPENDENCY_WEIGHTED_RIDGE_V2", int(horizon), str(feature_set), cutoff,
                     evidence["raw_resolved"], float(evidence["effective_n"]), evidence["trading_days"],
                     _json(params), _json(diagnostics), artifact_sha, now),
                )
                created += int(cur.rowcount > 0)
    return created


def _write_v2_return_predictions(runtime: ShortHorizonRuntime, observation_id: str,
                                 captured_ts: float, horizon: int) -> int:
    _ensure_tables(runtime)
    with runtime._lock:
        obs = runtime._conn.execute(
            "SELECT * FROM g1s_observations WHERE observation_id=?", (str(observation_id),)
        ).fetchone()
        models = runtime._conn.execute(
            "SELECT * FROM g1s_return_models WHERE horizon_minutes=? AND model_family=? "
            "AND created_ts<=? AND training_cutoff_ts<? ORDER BY created_ts DESC",
            (int(horizon), "DEPENDENCY_WEIGHTED_RIDGE_V2", float(captured_ts), float(captured_ts)),
        ).fetchall()
    if obs is None or not _has_v2(dict(obs)):
        return 0
    chosen: dict[str, Any] = {}
    for model in models:
        chosen.setdefault(str(model["feature_set"]), model)
    written = 0
    for model in chosen.values():
        feature_set = str(model["feature_set"])
        if feature_set not in V2_FEATURE_SETS:
            continue
        vector, _ = runtime._feature_vector(dict(obs), feature_set)
        params = _loads(model["parameters_json"])
        mean = np.asarray(params.get("feature_mean") or [], dtype=float)
        std = np.asarray(params.get("feature_std") or [], dtype=float)
        beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
        x = np.asarray(vector, dtype=float)
        if len(mean) != len(x) or len(std) != len(x) or len(beta) != len(x)+1:
            runtime._error("RETURN_V2_MODEL_ARTIFACT_SHAPE_MISMATCH", str(model["model_id"]),
                           observation_id=str(observation_id), critical=True)
            continue
        z = (x-mean)/np.where(std < 1e-12, 1.0, std)
        predicted = float(beta[0]+z@beta[1:])
        payload = {
            "contract_version": RETURN_PREDICTION_V2_VERSION,
            "feature_contract_version": FEATURE_CONTRACT_V2,
            "observation_id": str(observation_id),
            "model_id": str(model["model_id"]),
            "model_created_ts": float(model["created_ts"]),
            "training_cutoff_ts": float(model["training_cutoff_ts"]),
            "captured_ts": float(captured_ts),
            "predicted_log_return": predicted,
            "target": "terminal_log_return",
            "research_only": True,
            "production_used": False,
        }
        raw = _json(payload)
        prediction_id = "g1s-ret-v2-pred-"+_sha(raw)[:25]
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_return_predictions("
                "prediction_id,observation_id,model_id,predicted_log_return,prediction_json,"
                "prediction_sha256,production_used,created_ts) VALUES(?,?,?,?,?,?,0,?)",
                (prediction_id, str(observation_id), str(model["model_id"]), predicted,
                 raw, _sha(raw), time.time()),
            )
            written += int(cur.rowcount > 0)
    return written


def install_g1_short_horizon_continuous_v2() -> None:
    if getattr(ShortHorizonRuntime, "_continuous_v2_version", None) == CONTINUOUS_V2_VERSION:
        return
    previous_fit = ShortHorizonRuntime.fit_if_ready
    previous_predict = ShortHorizonRuntime._create_prospective_predictions
    previous_status = ShortHorizonRuntime.status

    def fit_if_ready(self, *, force: bool = False):
        created = int(previous_fit(self, force=force) or 0)
        return created+_fit_v2_return_models(self, force=force)

    def create_predictions(self, observation_id: str, captured_ts: float, horizon: int):
        created = int(previous_predict(self, observation_id, captured_ts, horizon) or 0)
        created += _write_v2_return_predictions(self, observation_id, captured_ts, horizon)
        # V2 directional rows are created inside previous_predict after the older
        # calibration wrapper ran.  Reapply now; INSERT OR IGNORE makes this
        # idempotent for already-calibrated V1 predictions.
        created += _apply_calibrators(self, observation_id, captured_ts, horizon)
        return created

    def status(self):
        report = previous_status(self)
        with self._lock:
            model_n = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_return_models WHERE model_family=?",
                ("DEPENDENCY_WEIGHTED_RIDGE_V2",),
            ).fetchone()[0])
        report["continuous_learning_v2"] = {
            "contract_version": CONTINUOUS_V2_VERSION,
            "feature_contract_version": FEATURE_CONTRACT_V2,
            "target": "terminal_log_return",
            "return_models": model_n,
            "fit_required": dict(RETURN_FIT_REQUIRED),
            "prospective_predictions_only": True,
            "v2_calibration_order_closed": True,
            "production_authority": False,
            "auto_promotion": False,
        }
        return report

    ShortHorizonRuntime.fit_if_ready = fit_if_ready
    ShortHorizonRuntime._create_prospective_predictions = create_predictions
    ShortHorizonRuntime.fit_v2_return_models_if_ready = _fit_v2_return_models
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._continuous_v2_version = CONTINUOUS_V2_VERSION
