"""Research-only continuous-return learning for G.1S.

The directional classifier is secondary evidence.  This module adds the primary
continuous target requested by the G.1S contract: future terminal log return.
A deterministic ridge model is trained on dependency-adjusted frozen T0 rows and
may predict only observations captured after its immutable training cut.
Nothing here has production decision authority or automatic promotion rights.
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
from . import g1_short_horizon_runtime as _runtime_module
from . import storage_runtime as _storage
from .g1_short_horizon_evidence_completion import SERIOUS_OOS_REQUIRED
from .g1_short_horizon_runtime import (
    HORIZONS,
    MODEL_REFIT_INTERVAL_SEC,
    MODEL_REFIT_MIN_EFFECTIVE_DELTA,
    ShortHorizonRuntime,
    _finite,
)


CONTINUOUS_CONTRACT_VERSION = "g1s-continuous-return-v1"
RETURN_MODEL_VERSION = "g1s-ridge-return-model-v1"
RETURN_PREDICTION_VERSION = "g1s-prospective-return-prediction-v1"
RETURN_OOS_VERSION = "g1s-continuous-oos-v1"
RETURN_RIDGE_L2 = 1.0
RETURN_FIT_REQUIRED = {
    "raw_resolved": 120,
    "effective_n": 60,
    "trading_days": 3,
}
RETURN_TABLES = ("g1s_return_models", "g1s_return_predictions")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ret15(row: dict[str, Any]) -> float:
    if "frozen_ret_15m" in row:
        value = _finite(row.get("frozen_ret_15m"))
        return 0.0 if value is None else float(value)
    features = _loads(row.get("frozen_features_json"))
    intraday = features.get("g1s_intraday")
    if isinstance(intraday, dict):
        value = _finite(intraday.get("ret_15m"))
        if value is not None:
            return float(value)
    for key in ("ret_15m", "return_15m"):
        value = _finite(features.get(key))
        if value is not None:
            return float(value)
    return 0.0


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_return_models(
                model_id TEXT PRIMARY KEY,
                model_family TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                feature_set TEXT NOT NULL,
                training_cutoff_ts REAL NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n REAL NOT NULL,
                training_days INTEGER NOT NULL,
                parameters_json TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                authority TEXT NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_return_model_horizon_created "
            "ON g1s_return_models(horizon_minutes,created_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_return_predictions(
                prediction_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                predicted_log_return REAL NOT NULL,
                prediction_json TEXT NOT NULL,
                prediction_sha256 TEXT NOT NULL,
                production_used INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                UNIQUE(observation_id,model_id)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_return_pred_observation "
            "ON g1s_return_predictions(observation_id,created_ts)")
        for table in RETURN_TABLES:
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S continuous row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable G1S continuous row'); END""")


def _rows(runtime: ShortHorizonRuntime, horizon: int) -> list[dict[str, Any]]:
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT g.*,r.terminal_log_return,r.direction_label,r.resolved_ts
            FROM g1s_observations g
            JOIN g1s_resolutions r USING(observation_id)
            WHERE g.training_eligible=1 AND g.horizon_minutes=?
              AND r.terminal_log_return IS NOT NULL
            ORDER BY g.captured_ts,g.observation_id
        """, (int(horizon),)).fetchall()
    return [dict(row) for row in rows]


def _dependency_weights(runtime: ShortHorizonRuntime,
                        rows: list[dict[str, Any]]) -> tuple[np.ndarray, int]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[runtime._dependency_key(row)].append(index)
    weights = np.zeros(len(rows), dtype=float)
    for members in groups.values():
        weight = 1.0/len(members)
        for index in members:
            weights[index] = weight
    return weights, len(groups)


def _evidence(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]]) -> dict[str, Any]:
    _, effective = _dependency_weights(runtime, rows)
    days = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"]))) for row in rows})
    observed = {
        "raw_resolved": len(rows),
        "effective_n": effective,
        "trading_days": days,
    }
    blockers = [key for key, required in RETURN_FIT_REQUIRED.items()
                if observed[key] < int(required)]
    return {**observed, "fit_required": dict(RETURN_FIT_REQUIRED),
            "fit_blockers": blockers, "fit_allowed": not blockers}


def _arrays(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]], feature_set: str):
    xs: list[list[float]] = []
    ys: list[float] = []
    for row in rows:
        vector, _ = runtime._feature_vector(row, feature_set)
        xs.append([float(v) for v in vector])
        ys.append(float(row["terminal_log_return"]))
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def _fit_ridge(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]], feature_set: str):
    x, y = _arrays(runtime, rows, feature_set)
    weights, _ = _dependency_weights(runtime, rows)
    den = max(float(weights.sum()), 1e-12)
    mean = (weights[:, None]*x).sum(axis=0)/den
    variance = (weights[:, None]*(x-mean)**2).sum(axis=0)/den
    std = np.sqrt(np.maximum(variance, 0.0))
    std[std < 1e-12] = 1.0
    z = (x-mean)/std
    design = np.column_stack([np.ones(len(z)), z])
    xtw = design.T*weights
    penalty = np.eye(design.shape[1], dtype=float)*RETURN_RIDGE_L2
    penalty[0, 0] = 0.0
    system = xtw@design + penalty
    rhs = xtw@y
    try:
        beta = np.linalg.solve(system, rhs)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(system)@rhs
    return beta, mean, std


def _mae(pred: list[float], actual: list[float], weights: list[float] | None = None) -> float | None:
    if not pred:
        return None
    w = weights or [1.0]*len(pred)
    den = sum(w)
    return None if den <= 0 else sum(weight*abs(p-y) for p, y, weight in zip(pred, actual, w))/den


def _rmse(pred: list[float], actual: list[float], weights: list[float] | None = None) -> float | None:
    if not pred:
        return None
    w = weights or [1.0]*len(pred)
    den = sum(w)
    if den <= 0:
        return None
    return math.sqrt(sum(weight*(p-y)**2 for p, y, weight in zip(pred, actual, w))/den)


def _sign_accuracy(pred: list[float], actual: list[float], weights: list[float]) -> float | None:
    if not pred or sum(weights) <= 0:
        return None
    return sum(w*int((p >= 0) == (y >= 0)) for p, y, w in zip(pred, actual, weights))/sum(weights)


def _historical_diagnostics(runtime: ShortHorizonRuntime,
                            rows: list[dict[str, Any]], feature_set: str) -> dict[str, Any]:
    if len(rows) < 30:
        return {"status": "INSUFFICIENT", "historical_walk_forward": False}
    ordered = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
    split = max(10, int(len(ordered)*0.70))
    if split >= len(ordered):
        return {"status": "INSUFFICIENT", "historical_walk_forward": False}
    test_start = float(ordered[split]["captured_ts"])
    train = [row for row in ordered[:split] if float(row["target_ts"]) < test_start]
    test = ordered[split:]
    if len(train) < 20 or len(test) < 10:
        return {"status": "INSUFFICIENT_AFTER_PURGE", "historical_walk_forward": True,
                "train_n": len(train), "test_n": len(test), "purge_applied": True}
    beta, mean, std = _fit_ridge(runtime, train, feature_set)
    x_test, y_test = _arrays(runtime, test, feature_set)
    z = (x_test-mean)/std
    pred = beta[0] + z@beta[1:]
    predictions = [float(v) for v in pred]
    actual = [float(v) for v in y_test]
    zero = [0.0]*len(actual)
    persistence = [_ret15(row) for row in test]
    return {
        "status": "HISTORICAL_PURGED_TEST",
        "historical_walk_forward": True,
        "prospective_oos": False,
        "oos_validated": False,
        "random_shuffle": False,
        "purge_applied": True,
        "train_n": len(train),
        "test_n": len(test),
        "model_mae": _mae(predictions, actual),
        "model_rmse": _rmse(predictions, actual),
        "zero_mae": _mae(zero, actual),
        "zero_rmse": _rmse(zero, actual),
        "fixed_ret15_persistence_mae": _mae(persistence, actual),
        "fixed_ret15_persistence_rmse": _rmse(persistence, actual),
    }


def _fit_return_models(runtime: ShortHorizonRuntime, *, force: bool = False) -> int:
    _ensure_tables(runtime)
    created = 0
    now = time.time()
    for horizon in HORIZONS:
        rows = _rows(runtime, horizon)
        evidence = _evidence(runtime, rows)
        if not evidence["fit_allowed"]:
            continue
        for feature_set in _runtime_module.FEATURE_SETS:
            with runtime._lock:
                latest = runtime._conn.execute(
                    "SELECT created_ts,effective_n FROM g1s_return_models "
                    "WHERE horizon_minutes=? AND feature_set=? ORDER BY created_ts DESC LIMIT 1",
                    (int(horizon), str(feature_set))).fetchone()
            if latest and not force:
                if now-float(latest["created_ts"]) < MODEL_REFIT_INTERVAL_SEC:
                    continue
                if evidence["effective_n"]-float(latest["effective_n"]) < MODEL_REFIT_MIN_EFFECTIVE_DELTA:
                    continue
            beta, mean, std = _fit_ridge(runtime, rows, feature_set)
            cutoff = max(float(row["resolved_ts"]) for row in rows)
            feature_names = list(_runtime_module.FEATURE_SETS[feature_set])
            # _feature_vector appends static instrument one-hot columns.  Derive
            # the final dimension instead of importing a second identity source.
            vector, _ = runtime._feature_vector(rows[0], feature_set)
            extra_n = max(0, len(vector)-len(feature_names))
            feature_names.extend(f"instrument_one_hot:{index}" for index in range(extra_n))
            params = {
                "intercept_and_coefficients": [float(v) for v in beta],
                "feature_mean": [float(v) for v in mean],
                "feature_std": [float(v) for v in std],
                "feature_names": feature_names,
                "ridge_l2": RETURN_RIDGE_L2,
            }
            artifact = {
                "contract_version": RETURN_MODEL_VERSION,
                "model_family": "DEPENDENCY_WEIGHTED_RIDGE",
                "target": "terminal_log_return",
                "horizon_minutes": int(horizon),
                "feature_set": str(feature_set),
                "training_cutoff_ts": cutoff,
                "source_observation_ids": [str(row["observation_id"]) for row in rows],
                "parameters": params,
            }
            artifact_sha = _sha(_json(artifact))
            model_id = "g1s-ret-model-" + artifact_sha[:26]
            diagnostics = _historical_diagnostics(runtime, rows, feature_set)
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_return_models("
                    "model_id,model_family,horizon_minutes,feature_set,training_cutoff_ts,"
                    "raw_n,effective_n,training_days,parameters_json,diagnostics_json,"
                    "artifact_sha256,authority,created_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                    (model_id, "DEPENDENCY_WEIGHTED_RIDGE", int(horizon), str(feature_set), cutoff,
                     evidence["raw_resolved"], float(evidence["effective_n"]),
                     evidence["trading_days"], _json(params), _json(diagnostics), artifact_sha, now))
                created += int(cur.rowcount > 0)
    return created


def _write_return_predictions(runtime: ShortHorizonRuntime, observation_id: str,
                              captured_ts: float, horizon: int) -> int:
    _ensure_tables(runtime)
    with runtime._lock:
        obs = runtime._conn.execute(
            "SELECT * FROM g1s_observations WHERE observation_id=?", (str(observation_id),)).fetchone()
        models = runtime._conn.execute(
            "SELECT * FROM g1s_return_models WHERE horizon_minutes=? AND created_ts<=? "
            "AND training_cutoff_ts<? ORDER BY created_ts DESC",
            (int(horizon), float(captured_ts), float(captured_ts))).fetchall()
    if obs is None:
        return 0
    chosen: dict[str, Any] = {}
    for model in models:
        chosen.setdefault(str(model["feature_set"]), model)
    written = 0
    for model in chosen.values():
        feature_set = str(model["feature_set"])
        if feature_set not in _runtime_module.FEATURE_SETS:
            continue
        vector, _ = runtime._feature_vector(dict(obs), feature_set)
        params = _loads(model["parameters_json"])
        mean = np.asarray(params.get("feature_mean") or [], dtype=float)
        std = np.asarray(params.get("feature_std") or [], dtype=float)
        beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
        x = np.asarray(vector, dtype=float)
        if len(mean) != len(x) or len(std) != len(x) or len(beta) != len(x)+1:
            runtime._error("RETURN_MODEL_ARTIFACT_SHAPE_MISMATCH", str(model["model_id"]),
                           observation_id=str(observation_id), critical=True)
            continue
        z = (x-mean)/np.where(std < 1e-12, 1.0, std)
        predicted = float(beta[0] + z@beta[1:])
        payload = {
            "contract_version": RETURN_PREDICTION_VERSION,
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
        prediction_id = "g1s-ret-pred-" + _sha(raw)[:28]
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_return_predictions("
                "prediction_id,observation_id,model_id,predicted_log_return,prediction_json,"
                "prediction_sha256,production_used,created_ts) VALUES(?,?,?,?,?,?,0,?)",
                (prediction_id, str(observation_id), str(model["model_id"]), predicted,
                 raw, _sha(raw), time.time()))
            written += int(cur.rowcount > 0)
    return written


def _safe_prediction_rows(runtime: ShortHorizonRuntime) -> dict[str, list[dict[str, Any]]]:
    _ensure_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute("""
            SELECT p.model_id,p.predicted_log_return,p.created_ts AS prediction_created_ts,
                   g.observation_id,g.instrument,g.horizon_minutes,g.captured_ts,g.target_ts,
                   g.market_regime,
                   CASE WHEN json_valid(g.frozen_features_json) THEN COALESCE(
                        json_extract(g.frozen_features_json,'$.g1s_intraday.ret_15m'),
                        json_extract(g.frozen_features_json,'$.ret_15m'),
                        json_extract(g.frozen_features_json,'$.return_15m'))
                        ELSE NULL END AS frozen_ret_15m,
                   r.terminal_log_return,r.direction_label,
                   r.resolved_ts,m.feature_set,m.model_family,m.created_ts AS model_created_ts,
                   m.training_cutoff_ts
            FROM g1s_return_predictions p
            JOIN g1s_observations g USING(observation_id)
            JOIN g1s_resolutions r USING(observation_id)
            JOIN g1s_return_models m USING(model_id)
            WHERE p.production_used=0 AND g.oos_eligible=1
            ORDER BY g.captured_ts,g.observation_id,p.model_id
        """).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in rows:
        row = dict(source)
        captured = _finite(row.get("captured_ts")); target = _finite(row.get("target_ts"))
        resolved = _finite(row.get("resolved_ts")); model_created = _finite(row.get("model_created_ts"))
        cutoff = _finite(row.get("training_cutoff_ts")); predicted = _finite(row.get("prediction_created_ts"))
        value = _finite(row.get("predicted_log_return")); actual = _finite(row.get("terminal_log_return"))
        if None in (captured, target, resolved, model_created, cutoff, predicted, value, actual):
            continue
        if model_created > captured+1e-6 or cutoff >= captured-1e-9:
            continue
        if predicted >= target-1e-9 or resolved < target-1e-6:
            continue
        grouped[str(row["model_id"])].append(row)
    return grouped


def _causal_return_baselines(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    ordered = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
    events = sorted(ordered, key=lambda row: (float(row["resolved_ts"]), float(row["captured_ts"])))
    event_index = 0
    visible_sum = 0.0
    visible_n = 0
    mean_baseline: list[float] = []
    persistence: list[float] = []
    for row in ordered:
        captured = float(row["captured_ts"])
        while event_index < len(events) and float(events[event_index]["resolved_ts"]) < captured-1e-9:
            event = events[event_index]
            if float(event["captured_ts"]) < captured-1e-9:
                visible_sum += float(event["terminal_log_return"])
                visible_n += 1
            event_index += 1
        mean_baseline.append(0.0 if visible_n < 20 else visible_sum/visible_n)
        persistence.append(_ret15(row))
    return {
        "zero_return": [0.0]*len(ordered),
        "causal_historical_mean": mean_baseline,
        "fixed_ret15_persistence": persistence,
    }


def _continuous_candidate(rows: list[dict[str, Any]], effective_n: int) -> tuple[dict[str, int], list[str]]:
    positive = sum(float(row["terminal_log_return"]) > 0 for row in rows)
    negative = sum(float(row["terminal_log_return"]) < 0 for row in rows)
    days = len({time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"]))) for row in rows})
    regimes = len({str(row.get("market_regime") or "UNKNOWN") for row in rows})
    observed = {
        "raw_resolved": len(rows), "effective_n": int(effective_n),
        "positive_n": int(positive), "negative_n": int(negative),
        "temporal_blocks": int(days), "volatility_regime_count": int(regimes),
    }
    blockers = [f"INSUFFICIENT_{key.upper()}" for key, required in SERIOUS_OOS_REQUIRED.items()
                if observed[key] < int(required)]
    if regimes < 2:
        blockers.append("INSUFFICIENT_VOLATILITY_REGIME_DIVERSITY")
    return observed, blockers


def _continuous_oos(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    by_model = _safe_prediction_rows(runtime)
    with runtime._lock:
        models = runtime._conn.execute(
            "SELECT model_id,model_family,horizon_minutes,feature_set,training_cutoff_ts,"
            "raw_n,effective_n,training_days,diagnostics_json,artifact_sha256,authority,created_ts "
            "FROM g1s_return_models ORDER BY created_ts DESC").fetchall()
    items: list[dict[str, Any]] = []
    for model in models:
        model_id = str(model["model_id"])
        rows = sorted(by_model.get(model_id, []),
                      key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
        if not rows:
            metrics = {"raw_n": 0, "effective_n": 0,
                       "candidate_blockers": ["NO_SAFE_PROSPECTIVE_ROWS"],
                       "verdict": "INSUFFICIENT"}
        else:
            np_weights, effective = _dependency_weights(runtime, rows)
            weights = [float(v) for v in np_weights]
            predicted = [float(row["predicted_log_return"]) for row in rows]
            actual = [float(row["terminal_log_return"]) for row in rows]
            baselines_values = _causal_return_baselines(rows)
            baselines = {
                name: {"mae": _mae(values, actual, weights),
                       "rmse": _rmse(values, actual, weights)}
                for name, values in baselines_values.items()
            }
            model_mae = _mae(predicted, actual, weights)
            model_rmse = _rmse(predicted, actual, weights)
            observed, blockers = _continuous_candidate(rows, effective)
            baseline_mae = [item["mae"] for item in baselines.values() if item["mae"] is not None]
            baseline_rmse = [item["rmse"] for item in baselines.values() if item["rmse"] is not None]
            if blockers:
                verdict = "INSUFFICIENT"
            elif (model_mae is not None and model_rmse is not None and baseline_mae and baseline_rmse
                  and model_mae < min(baseline_mae) and model_rmse < min(baseline_rmse)):
                verdict = "YES"
            else:
                verdict = "NO"
            metrics = {
                "raw_n": len(rows), "effective_n": effective, "weight_sum": sum(weights),
                "positive_n": observed["positive_n"], "negative_n": observed["negative_n"],
                "temporal_blocks": observed["temporal_blocks"],
                "volatility_regime_count": observed["volatility_regime_count"],
                "candidate_blockers": blockers,
                "mae": model_mae, "rmse": model_rmse,
                "sign_accuracy_secondary": _sign_accuracy(predicted, actual, weights),
                "baselines": baselines, "verdict": verdict,
                "metric_weighting": "dependency_group_total_weight_one",
            }
        diagnostics = _loads(model["diagnostics_json"])
        items.append({
            "model_id": model_id, "model_family": str(model["model_family"]),
            "horizon_minutes": int(model["horizon_minutes"]), "feature_set": str(model["feature_set"]),
            "training_cutoff_ts": float(model["training_cutoff_ts"]),
            "training_raw_n": int(model["raw_n"]), "training_effective_n": float(model["effective_n"]),
            "training_days": int(model["training_days"]), "artifact_sha256": str(model["artifact_sha256"]),
            "authority": str(model["authority"]), "created_ts": float(model["created_ts"]),
            "historical_diagnostics": diagnostics,
            "oos": metrics, "does_continuous_model_beat_baseline_oos": metrics["verdict"],
        })
    verdicts = [item["does_continuous_model_beat_baseline_oos"] for item in items]
    overall = "YES" if "YES" in verdicts else ("NO" if "NO" in verdicts else "INSUFFICIENT")
    return {
        "contract_version": RETURN_OOS_VERSION,
        "target": "terminal_log_return",
        "continuous_labels_primary": True,
        "oos_candidate_required": dict(SERIOUS_OOS_REQUIRED),
        "items": items,
        "does_continuous_model_beat_baseline_oos": overall,
        "oos_validated": False,
        "edge_claim_allowed": False,
        "production_authority": False,
        "auto_promotion": False,
    }


def _return_models(runtime: ShortHorizonRuntime, limit: int = 100) -> dict[str, Any]:
    _ensure_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT model_id,model_family,horizon_minutes,feature_set,training_cutoff_ts,raw_n,"
            "effective_n,training_days,diagnostics_json,artifact_sha256,authority,created_ts "
            "FROM g1s_return_models ORDER BY created_ts DESC LIMIT ?",
            (max(1, min(int(limit), 500)),)).fetchall()
    items = []
    for source in rows:
        item = dict(source)
        item["diagnostics"] = _loads(item.pop("diagnostics_json"))
        items.append(item)
    return {"contract_version": RETURN_MODEL_VERSION, "target": "terminal_log_return",
            "items": items, "production_authority": False}


def install_g1_short_horizon_continuous_learning() -> None:
    if getattr(ShortHorizonRuntime, "_continuous_learning_version", None) == CONTINUOUS_CONTRACT_VERSION:
        return

    previous_init = ShortHorizonRuntime.__init__
    previous_fit = ShortHorizonRuntime.fit_if_ready
    previous_predict = ShortHorizonRuntime._create_prospective_predictions
    previous_status = ShortHorizonRuntime.status

    def runtime_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _ensure_tables(self)

    def fit_if_ready(self, *, force: bool = False):
        directional = previous_fit(self, force=force)
        continuous = _fit_return_models(self, force=force)
        self._last_continuous_models_created = continuous
        return int(directional or 0) + int(continuous or 0)

    def create_predictions(self, observation_id: str, captured_ts: float, horizon: int):
        directional = previous_predict(self, observation_id, captured_ts, horizon)
        continuous = _write_return_predictions(self, observation_id, captured_ts, horizon)
        return int(directional or 0) + int(continuous or 0)

    def status(self):
        report = previous_status(self)
        with self._lock:
            model_n = int(self._conn.execute("SELECT COUNT(*) FROM g1s_return_models").fetchone()[0])
        report["continuous_learning"] = {
            "contract_version": CONTINUOUS_CONTRACT_VERSION,
            "target": "terminal_log_return",
            "continuous_labels_primary": True,
            "return_models": model_n,
            "fit_required": dict(RETURN_FIT_REQUIRED),
            "oos_candidate_required": dict(SERIOUS_OOS_REQUIRED),
            "production_authority": False,
            "auto_promotion": False,
        }
        return report

    ShortHorizonRuntime.__init__ = runtime_init
    ShortHorizonRuntime.fit_if_ready = fit_if_ready
    ShortHorizonRuntime._create_prospective_predictions = create_predictions
    ShortHorizonRuntime.fit_return_models_if_ready = _fit_return_models
    ShortHorizonRuntime.continuous_oos = _continuous_oos
    ShortHorizonRuntime.return_models = _return_models
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._continuous_learning_version = CONTINUOUS_CONTRACT_VERSION

    # The new immutable research evidence must participate in verified backup
    # manifests and the post-schema identity snapshot.
    _storage.CRITICAL_TABLES = tuple(dict.fromkeys((*_storage.CRITICAL_TABLES, *RETURN_TABLES)))
    _integration.G1S_CRITICAL_TABLES = tuple(dict.fromkeys((*_integration.G1S_CRITICAL_TABLES, *RETURN_TABLES)))
