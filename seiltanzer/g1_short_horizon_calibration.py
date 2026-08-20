"""Causal probability calibration for G.1S prospective shadow models.

A calibrator is never fitted from a future label relative to the prediction it
calibrates.  Platt artifacts are learned only from already-resolved prospective
shadow predictions, frozen immutably, then applied only to later T0 observations.
Past raw predictions are never rewritten or retrospectively calibrated.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from typing import Any

import numpy as np

from . import g1_short_horizon_integration as _integration
from . import storage_runtime as _storage
from .g1_short_horizon_evidence_completion import (
    SERIOUS_OOS_REQUIRED,
    _causal_baselines,
    _candidate_blockers,
    _dependency_weights,
    _weighted_brier,
    _weighted_ece,
    _weighted_logloss,
)
from .g1_short_horizon_runtime import (
    MODEL_REFIT_INTERVAL_SEC,
    ShortHorizonRuntime,
    _finite,
)


CALIBRATION_CONTRACT_VERSION = "g1s-causal-platt-v1"
CALIBRATOR_ARTIFACT_VERSION = "g1s-platt-artifact-v1"
CALIBRATED_PREDICTION_VERSION = "g1s-calibrated-prediction-v1"
CALIBRATION_OOS_VERSION = "g1s-calibration-oos-v1"
CALIBRATOR_REFIT_EFFECTIVE_DELTA = 20
CALIBRATION_FIT_REQUIRED = {
    "raw_resolved": 240,
    "effective_n": 120,
    "positive_n": 60,
    "negative_n": 60,
    "temporal_blocks": 5,
}
CALIBRATION_TABLES = ("g1s_probability_calibrators", "g1s_calibrated_predictions")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _clip(p: float) -> float:
    return max(1e-6, min(1.0-1e-6, float(p)))


def _logit(p: float) -> float:
    p = _clip(p)
    return math.log(p/(1.0-p))


def _sigmoid(x: float) -> float:
    x = max(-35.0, min(35.0, float(x)))
    return 1.0/(1.0+math.exp(-x))


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_probability_calibrators(
                calibrator_id TEXT PRIMARY KEY,
                horizon_minutes INTEGER NOT NULL,
                feature_set TEXT NOT NULL,
                model_family TEXT NOT NULL,
                training_cutoff_ts REAL NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n REAL NOT NULL,
                positive_n INTEGER NOT NULL,
                negative_n INTEGER NOT NULL,
                temporal_blocks INTEGER NOT NULL,
                parameters_json TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                authority TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_calibrator_key_created "
            "ON g1s_probability_calibrators(horizon_minutes,feature_set,model_family,created_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_calibrated_predictions(
                calibrated_prediction_id TEXT PRIMARY KEY,
                raw_prediction_id TEXT NOT NULL,
                observation_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                calibrator_id TEXT NOT NULL,
                raw_p_up REAL NOT NULL,
                calibrated_p_up REAL NOT NULL,
                prediction_json TEXT NOT NULL,
                prediction_sha256 TEXT NOT NULL,
                production_used INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                UNIQUE(raw_prediction_id,calibrator_id)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_cal_pred_observation "
            "ON g1s_calibrated_predictions(observation_id,created_ts)")
        for table in CALIBRATION_TABLES:
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S calibration row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S calibration row'); END""")


def _safe_raw_rows(runtime: ShortHorizonRuntime) -> list[dict[str, Any]]:
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT p.prediction_id,p.model_id,p.p_up,p.created_ts AS prediction_created_ts,
                   g.observation_id,g.instrument,g.horizon_minutes,g.captured_ts,g.target_ts,
                   g.market_regime,
                   CASE WHEN json_valid(g.frozen_features_json) THEN COALESCE(
                        json_extract(g.frozen_features_json,'$.g1s_intraday.ret_15m'),
                        json_extract(g.frozen_features_json,'$.ret_15m'),
                        json_extract(g.frozen_features_json,'$.return_15m'))
                        ELSE NULL END AS frozen_ret_15m,
                   r.direction_label,r.resolved_ts,
                   m.feature_set,m.model_family,m.created_ts AS model_created_ts,m.training_cutoff_ts
            FROM g1s_shadow_predictions p
            JOIN g1s_observations g USING(observation_id)
            JOIN g1s_resolutions r USING(observation_id)
            JOIN g1s_models m USING(model_id)
            WHERE p.production_used=0 AND g.oos_eligible=1 AND r.direction_label!='FLAT'
            ORDER BY g.captured_ts,g.observation_id,p.prediction_id
        """).fetchall()
    out = []
    for source in rows:
        row = dict(source)
        captured = _finite(row.get("captured_ts")); target = _finite(row.get("target_ts"))
        resolved = _finite(row.get("resolved_ts")); model_created = _finite(row.get("model_created_ts"))
        cutoff = _finite(row.get("training_cutoff_ts")); predicted = _finite(row.get("prediction_created_ts"))
        p_up = _finite(row.get("p_up"))
        if None in (captured, target, resolved, model_created, cutoff, predicted, p_up):
            continue
        if model_created > captured+1e-6 or cutoff >= captured-1e-9:
            continue
        if predicted >= target-1e-9 or resolved < target-1e-6:
            continue
        out.append(row)
    return out


def _fit_evidence(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]]) -> dict[str, Any]:
    _, effective = _dependency_weights(runtime, rows)
    positive = sum(str(row["direction_label"]) == "UP" for row in rows)
    negative = sum(str(row["direction_label"]) == "DOWN" for row in rows)
    days = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"]))) for row in rows})
    observed = {
        "raw_resolved": len(rows), "effective_n": effective,
        "positive_n": positive, "negative_n": negative, "temporal_blocks": days,
    }
    blockers = [key for key, required in CALIBRATION_FIT_REQUIRED.items()
                if observed[key] < int(required)]
    return {**observed, "fit_required": dict(CALIBRATION_FIT_REQUIRED),
            "fit_blockers": blockers, "fit_allowed": not blockers}


def _fit_platt(runtime: ShortHorizonRuntime,
               rows: list[dict[str, Any]]) -> tuple[float, float]:
    x = np.asarray([_logit(float(row["p_up"])) for row in rows], dtype=float)
    y = np.asarray([1.0 if str(row["direction_label"]) == "UP" else 0.0 for row in rows], dtype=float)
    weights_list, _ = _dependency_weights(runtime, rows)
    w = np.asarray(weights_list, dtype=float)
    design = np.column_stack([np.ones(len(x)), x])
    beta = np.asarray([0.0, 1.0], dtype=float)
    reg = np.diag([0.0, 0.10])
    for _ in range(80):
        score = np.clip(design@beta, -35.0, 35.0)
        p = 1.0/(1.0+np.exp(-score))
        variance = np.maximum(p*(1.0-p), 1e-6)
        grad = design.T@(w*(p-y)) + reg@beta
        hess = design.T@((w*variance)[:, None]*design) + reg
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hess)@grad
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return float(beta[0]), float(beta[1])


def _fit_calibrators(runtime: ShortHorizonRuntime, *, force: bool = False) -> int:
    _ensure_tables(runtime)
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in _safe_raw_rows(runtime):
        key = (int(row["horizon_minutes"]), str(row["feature_set"]), str(row["model_family"]))
        grouped[key].append(row)
    now = time.time()
    created = 0
    for (horizon, feature_set, family), rows in grouped.items():
        evidence = _fit_evidence(runtime, rows)
        if not evidence["fit_allowed"]:
            continue
        with runtime._lock:
            latest = runtime._conn.execute(
                "SELECT created_ts,effective_n FROM g1s_probability_calibrators "
                "WHERE horizon_minutes=? AND feature_set=? AND model_family=? "
                "ORDER BY created_ts DESC LIMIT 1", (horizon, feature_set, family)).fetchone()
        if latest and not force:
            if now-float(latest["created_ts"]) < MODEL_REFIT_INTERVAL_SEC:
                continue
            if evidence["effective_n"]-float(latest["effective_n"]) < CALIBRATOR_REFIT_EFFECTIVE_DELTA:
                continue
        intercept, slope = _fit_platt(runtime, rows)
        cutoff = max(float(row["resolved_ts"]) for row in rows)
        raw_ps = [float(row["p_up"]) for row in rows]
        ys = [1 if str(row["direction_label"]) == "UP" else 0 for row in rows]
        calibrated = [_sigmoid(intercept+slope*_logit(p)) for p in raw_ps]
        weights, _ = _dependency_weights(runtime, rows)
        diagnostics = {
            "diagnostic_scope": "already_resolved_prospective_predictions_used_for_future_calibrator_fit",
            "not_oos_validation": True,
            "raw_brier": _weighted_brier(raw_ps, ys, weights),
            "calibrated_brier": _weighted_brier(calibrated, ys, weights),
            "raw_log_loss": _weighted_logloss(raw_ps, ys, weights),
            "calibrated_log_loss": _weighted_logloss(calibrated, ys, weights),
        }
        params = {"platt_intercept": intercept, "platt_slope": slope,
                  "input": "raw_probability_logit", "l2_slope": 0.10}
        artifact = {
            "contract_version": CALIBRATOR_ARTIFACT_VERSION,
            "horizon_minutes": horizon, "feature_set": feature_set,
            "model_family": family, "training_cutoff_ts": cutoff,
            "source_prediction_ids": [str(row["prediction_id"]) for row in rows],
            "parameters": params,
        }
        artifact_sha = _sha(_json(artifact))
        calibrator_id = "g1s-cal-" + artifact_sha[:28]
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_probability_calibrators("
                "calibrator_id,horizon_minutes,feature_set,model_family,training_cutoff_ts,"
                "raw_n,effective_n,positive_n,negative_n,temporal_blocks,parameters_json,"
                "diagnostics_json,artifact_sha256,authority,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                (calibrator_id, horizon, feature_set, family, cutoff,
                 evidence["raw_resolved"], float(evidence["effective_n"]),
                 evidence["positive_n"], evidence["negative_n"], evidence["temporal_blocks"],
                 _json(params), _json(diagnostics), artifact_sha, now))
            created += int(cur.rowcount > 0)
    return created


def _apply_calibrators(runtime: ShortHorizonRuntime, observation_id: str,
                       captured_ts: float, horizon: int) -> int:
    _ensure_tables(runtime)
    with runtime._lock:
        raw_predictions = runtime._conn.execute("""
            SELECT p.prediction_id,p.model_id,p.p_up,m.feature_set,m.model_family
            FROM g1s_shadow_predictions p JOIN g1s_models m USING(model_id)
            WHERE p.observation_id=? AND p.production_used=0
        """, (str(observation_id),)).fetchall()
    written = 0
    for raw in raw_predictions:
        with runtime._lock:
            calibrator = runtime._conn.execute("""
                SELECT * FROM g1s_probability_calibrators
                WHERE horizon_minutes=? AND feature_set=? AND model_family=?
                  AND created_ts<=? AND training_cutoff_ts<?
                ORDER BY created_ts DESC LIMIT 1
            """, (int(horizon), str(raw["feature_set"]), str(raw["model_family"]),
                  float(captured_ts), float(captured_ts))).fetchone()
        if calibrator is None:
            continue
        params = _loads(calibrator["parameters_json"])
        intercept = _finite(params.get("platt_intercept")); slope = _finite(params.get("platt_slope"))
        raw_p = _finite(raw["p_up"])
        if intercept is None or slope is None or raw_p is None:
            continue
        calibrated = _sigmoid(intercept+slope*_logit(raw_p))
        payload = {
            "contract_version": CALIBRATED_PREDICTION_VERSION,
            "raw_prediction_id": str(raw["prediction_id"]),
            "observation_id": str(observation_id), "model_id": str(raw["model_id"]),
            "calibrator_id": str(calibrator["calibrator_id"]),
            "calibrator_created_ts": float(calibrator["created_ts"]),
            "calibrator_training_cutoff_ts": float(calibrator["training_cutoff_ts"]),
            "captured_ts": float(captured_ts), "raw_p_up": float(raw_p),
            "calibrated_p_up": float(calibrated),
            "research_only": True, "production_used": False,
        }
        raw_json = _json(payload)
        pred_id = "g1s-cal-pred-" + _sha(raw_json)[:28]
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_calibrated_predictions("
                "calibrated_prediction_id,raw_prediction_id,observation_id,model_id,calibrator_id,"
                "raw_p_up,calibrated_p_up,prediction_json,prediction_sha256,production_used,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                (pred_id, str(raw["prediction_id"]), str(observation_id), str(raw["model_id"]),
                 str(calibrator["calibrator_id"]), float(raw_p), float(calibrated),
                 raw_json, _sha(raw_json), time.time()))
            written += int(cur.rowcount > 0)
    return written


def _safe_calibrated_rows(runtime: ShortHorizonRuntime) -> dict[str, list[dict[str, Any]]]:
    _ensure_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT cp.calibrated_prediction_id,cp.model_id,cp.raw_p_up,cp.calibrated_p_up,
                   cp.created_ts AS calibrated_created_ts,
                   g.observation_id,g.instrument,g.horizon_minutes,g.captured_ts,g.target_ts,
                   g.market_regime,
                   CASE WHEN json_valid(g.frozen_features_json) THEN COALESCE(
                        json_extract(g.frozen_features_json,'$.g1s_intraday.ret_15m'),
                        json_extract(g.frozen_features_json,'$.ret_15m'),
                        json_extract(g.frozen_features_json,'$.return_15m'))
                        ELSE NULL END AS frozen_ret_15m,
                   r.direction_label,r.resolved_ts,
                   m.feature_set,m.model_family,m.created_ts AS model_created_ts,m.training_cutoff_ts,
                   c.calibrator_id,c.created_ts AS calibrator_created_ts,
                   c.training_cutoff_ts AS calibrator_training_cutoff_ts
            FROM g1s_calibrated_predictions cp
            JOIN g1s_observations g USING(observation_id)
            JOIN g1s_resolutions r USING(observation_id)
            JOIN g1s_models m USING(model_id)
            JOIN g1s_probability_calibrators c USING(calibrator_id)
            WHERE cp.production_used=0 AND g.oos_eligible=1 AND r.direction_label!='FLAT'
            ORDER BY g.captured_ts,g.observation_id,cp.calibrated_prediction_id
        """).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        captured = _finite(row.get("captured_ts")); target = _finite(row.get("target_ts"))
        resolved = _finite(row.get("resolved_ts")); model_created = _finite(row.get("model_created_ts"))
        model_cutoff = _finite(row.get("training_cutoff_ts")); cal_created = _finite(row.get("calibrator_created_ts"))
        cal_cutoff = _finite(row.get("calibrator_training_cutoff_ts")); pred_created = _finite(row.get("calibrated_created_ts"))
        if None in (captured, target, resolved, model_created, model_cutoff, cal_created, cal_cutoff, pred_created):
            continue
        if model_created > captured+1e-6 or model_cutoff >= captured-1e-9:
            continue
        if cal_created > captured+1e-6 or cal_cutoff >= captured-1e-9:
            continue
        if pred_created >= target-1e-9 or resolved < target-1e-6:
            continue
        grouped[str(row["model_id"])].append(row)
    return grouped


def _calibration_oos(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    grouped = _safe_calibrated_rows(runtime)
    items = []
    for model_id, rows in grouped.items():
        rows = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
        calibrated = [float(row["calibrated_p_up"]) for row in rows]
        raw = [float(row["raw_p_up"]) for row in rows]
        ys = [1 if str(row["direction_label"]) == "UP" else 0 for row in rows]
        weights, effective = _dependency_weights(runtime, rows)
        observed, blockers = _candidate_blockers(rows, effective)
        baselines_p = _causal_baselines(rows)
        baselines = {name: {"brier": _weighted_brier(values, ys, weights),
                            "log_loss": _weighted_logloss(values, ys, weights)}
                     for name, values in baselines_p.items()}
        cal_brier = _weighted_brier(calibrated, ys, weights)
        cal_log = _weighted_logloss(calibrated, ys, weights)
        raw_brier = _weighted_brier(raw, ys, weights)
        raw_log = _weighted_logloss(raw, ys, weights)
        ece, reliability = _weighted_ece(calibrated, ys, weights)
        baseline_brier = [value["brier"] for value in baselines.values() if value["brier"] is not None]
        baseline_log = [value["log_loss"] for value in baselines.values() if value["log_loss"] is not None]
        if blockers:
            verdict = "INSUFFICIENT"
        elif (cal_brier is not None and cal_log is not None and raw_brier is not None and raw_log is not None
              and baseline_brier and baseline_log and cal_brier < raw_brier and cal_log < raw_log
              and cal_brier < min(baseline_brier) and cal_log < min(baseline_log)):
            verdict = "YES"
        else:
            verdict = "NO"
        items.append({
            "model_id": model_id,
            "horizon_minutes": int(rows[0]["horizon_minutes"]),
            "feature_set": str(rows[0]["feature_set"]),
            "model_family": str(rows[0]["model_family"]),
            "raw_n": len(rows), "effective_n": effective,
            "positive_n": observed["positive_n"], "negative_n": observed["negative_n"],
            "temporal_blocks": observed["temporal_blocks"],
            "volatility_regime_count": observed["volatility_regime_count"],
            "candidate_blockers": blockers,
            "raw_brier": raw_brier, "calibrated_brier": cal_brier,
            "raw_log_loss": raw_log, "calibrated_log_loss": cal_log,
            "calibrated_ece": ece, "calibrated_reliability": reliability,
            "baselines": baselines,
            "does_calibration_beat_raw_and_baselines_oos": verdict,
            "metric_weighting": "dependency_group_total_weight_one",
            "calibrator_must_exist_by_t0": True,
            "calibrator_training_cutoff_strictly_before_t0": True,
        })
    verdicts = [item["does_calibration_beat_raw_and_baselines_oos"] for item in items]
    overall = "YES" if "YES" in verdicts else ("NO" if "NO" in verdicts else "INSUFFICIENT")
    return {
        "contract_version": CALIBRATION_OOS_VERSION,
        "calibration_method": "PLATT_LOGIT",
        "oos_candidate_required": dict(SERIOUS_OOS_REQUIRED),
        "items": items,
        "does_calibration_beat_raw_and_baselines_oos": overall,
        "retroactive_prediction_rewrite": False,
        "oos_validated": False,
        "edge_claim_allowed": False,
        "production_authority": False,
    }


def _calibrators(runtime: ShortHorizonRuntime, limit: int = 100) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT calibrator_id,horizon_minutes,feature_set,model_family,training_cutoff_ts,"
            "raw_n,effective_n,positive_n,negative_n,temporal_blocks,diagnostics_json,"
            "artifact_sha256,authority,created_ts FROM g1s_probability_calibrators "
            "ORDER BY created_ts DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
    items = []
    for source in rows:
        item = dict(source)
        item["diagnostics"] = _loads(item.pop("diagnostics_json"))
        items.append(item)
    return {"contract_version": CALIBRATOR_ARTIFACT_VERSION, "items": items,
            "production_authority": False}


def install_g1_short_horizon_calibration() -> None:
    if getattr(ShortHorizonRuntime, "_calibration_contract_version", None) == CALIBRATION_CONTRACT_VERSION:
        return
    previous_init = ShortHorizonRuntime.__init__
    previous_fit = ShortHorizonRuntime.fit_if_ready
    previous_predict = ShortHorizonRuntime._create_prospective_predictions
    previous_oos = ShortHorizonRuntime.prospective_oos
    previous_status = ShortHorizonRuntime.status

    def runtime_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _ensure_tables(self)

    def fit_if_ready(self, *, force: bool = False):
        created = int(previous_fit(self, force=force) or 0)
        calibrators = _fit_calibrators(self, force=force)
        self._last_calibrators_created = calibrators
        return created+calibrators

    def create_predictions(self, observation_id: str, captured_ts: float, horizon: int):
        created = int(previous_predict(self, observation_id, captured_ts, horizon) or 0)
        calibrated = _apply_calibrators(self, observation_id, captured_ts, horizon)
        return created+calibrated

    def prospective_oos(self):
        report = previous_oos(self)
        calibration = _calibration_oos(self)
        by_model = {str(item["model_id"]): item for item in calibration["items"]}
        for item in report.get("items", []):
            item["calibration_oos"] = by_model.get(str(item.get("model_id")), {
                "raw_n": 0, "effective_n": 0,
                "candidate_blockers": ["NO_CAUSAL_CALIBRATED_PREDICTIONS"],
                "does_calibration_beat_raw_and_baselines_oos": "INSUFFICIENT",
            })
        report["calibration_contract_version"] = CALIBRATION_CONTRACT_VERSION
        report["calibration_oos_summary"] = calibration["does_calibration_beat_raw_and_baselines_oos"]
        report["retroactive_calibration_allowed"] = False
        return report

    def status(self):
        report = previous_status(self)
        with self._lock:
            calibrator_n = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_probability_calibrators").fetchone()[0])
        report["probability_calibration"] = {
            "contract_version": CALIBRATION_CONTRACT_VERSION,
            "method": "PLATT_LOGIT",
            "calibrators": calibrator_n,
            "fit_required": dict(CALIBRATION_FIT_REQUIRED),
            "causal_future_only_application": True,
            "retroactive_prediction_rewrite": False,
            "production_authority": False,
        }
        return report

    ShortHorizonRuntime.__init__ = runtime_init
    ShortHorizonRuntime.fit_if_ready = fit_if_ready
    ShortHorizonRuntime._create_prospective_predictions = create_predictions
    ShortHorizonRuntime.fit_calibrators_if_ready = _fit_calibrators
    ShortHorizonRuntime.calibration_oos = _calibration_oos
    ShortHorizonRuntime.calibrators = _calibrators
    ShortHorizonRuntime.prospective_oos = prospective_oos
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._calibration_contract_version = CALIBRATION_CONTRACT_VERSION

    _storage.CRITICAL_TABLES = tuple(dict.fromkeys((*_storage.CRITICAL_TABLES, *CALIBRATION_TABLES)))
    _integration.G1S_CRITICAL_TABLES = tuple(dict.fromkeys((*_integration.G1S_CRITICAL_TABLES, *CALIBRATION_TABLES)))
