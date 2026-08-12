"""Purged chronological diagnostics for the strict G.1S V2 directional models."""
from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from . import g1_short_horizon_feature_contract_v2 as _v2
from . import g1_short_horizon_runtime as _runtime_module
from .g1_short_horizon_runtime import (
    MODEL_REFIT_INTERVAL_SEC,
    MODEL_REFIT_MIN_EFFECTIVE_DELTA,
    ShortHorizonRuntime,
    _json,
    _sha_text,
)


V2_DIAGNOSTICS_VERSION = "g1s-v2-purged-diagnostics-v1"


def _brier(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    den = sum(weights)
    return None if den <= 0 else sum(w*(p-y)**2 for p, y, w in zip(ps, ys, weights))/den


def _logloss(ps: list[float], ys: list[int], weights: list[float]) -> float | None:
    den = sum(weights)
    if den <= 0:
        return None
    total = 0.0
    for p, y, w in zip(ps, ys, weights):
        p = max(1e-9, min(1.0-1e-9, float(p)))
        total += w*(-(y*math.log(p)+(1-y)*math.log(1-p)))
    return total/den


def _weights(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]]) -> np.ndarray:
    return _v2._dependency_weights(runtime, rows)


def _fit_on(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]], feature_set: str):
    xs = []
    ys = []
    for row in rows:
        vector, _ = runtime._feature_vector(row, feature_set)
        xs.append(vector)
        ys.append(1 if row["direction_label"] == "UP" else 0)
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    w = _weights(runtime, rows)
    den = max(float(w.sum()), 1e-12)
    mean = (w[:, None]*x).sum(axis=0)/den
    variance = (w[:, None]*(x-mean)**2).sum(axis=0)/den
    std = np.sqrt(np.maximum(variance, 0.0))
    std[std < 1e-12] = 1.0
    beta = _v2._fit_weighted_logistic((x-mean)/std, y, w)
    return beta, mean, std


def _historical_diagnostics(runtime: ShortHorizonRuntime, rows: list[dict[str, Any]],
                            feature_set: str, horizon: int) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
    if len(ordered) < 30:
        return {"status": "INSUFFICIENT", "historical_walk_forward": False,
                "prospective_oos": False, "oos_validated": False}
    split = max(10, int(len(ordered)*0.70))
    if split >= len(ordered):
        return {"status": "INSUFFICIENT", "historical_walk_forward": False,
                "prospective_oos": False, "oos_validated": False}
    test_start = float(ordered[split]["captured_ts"])
    # For fixed H labels, target_ts < test_start is the exact no-overlap purge;
    # the removed tail is also the H-minute embargo before the validation block.
    train = [row for row in ordered[:split] if float(row["target_ts"]) < test_start-1e-9]
    test = ordered[split:]
    if len(train) < 20 or len(test) < 10:
        return {
            "status": "INSUFFICIENT_AFTER_PURGE", "historical_walk_forward": True,
            "train_n": len(train), "test_n": len(test), "purge_applied": True,
            "embargo_sec": int(horizon)*60, "random_shuffle": False,
            "prospective_oos": False, "oos_validated": False,
        }
    beta, mean, std = _fit_on(runtime, train, feature_set)
    x_test = np.asarray([runtime._feature_vector(row, feature_set)[0] for row in test], dtype=float)
    z = (x_test-mean)/std
    ps = [float(1.0/(1.0+math.exp(-max(-35.0, min(35.0, float(v))))))
          for v in (beta[0]+z@beta[1:])]
    ys = [1 if row["direction_label"] == "UP" else 0 for row in test]
    w_test = [float(value) for value in _weights(runtime, test)]
    w_train = [float(value) for value in _weights(runtime, train)]
    train_ys = [1 if row["direction_label"] == "UP" else 0 for row in train]
    train_den = max(sum(w_train), 1e-12)
    base_rate = sum(w*y for w, y in zip(w_train, train_ys))/train_den
    momentum = []
    for row in test:
        ret15 = _v2._v2_values(row).get("ret_15m")
        momentum.append(0.5 if ret15 is None or abs(ret15) < 1e-12 else (0.55 if ret15 > 0 else 0.45))
    base_ps = [base_rate]*len(test)
    half = [0.5]*len(test)
    return {
        "status": "HISTORICAL_PURGED_TEST",
        "diagnostics_contract_version": V2_DIAGNOSTICS_VERSION,
        "historical_walk_forward": True,
        "chronological_split": "70_30",
        "random_shuffle": False,
        "purge_applied": True,
        "embargo_sec": int(horizon)*60,
        "train_n": len(train), "test_n": len(test),
        "test_effective_n": float(sum(w_test)),
        "model_brier": _brier(ps, ys, w_test),
        "model_log_loss": _logloss(ps, ys, w_test),
        "constant_0_5_brier": _brier(half, ys, w_test),
        "constant_0_5_log_loss": _logloss(half, ys, w_test),
        "train_base_rate_brier": _brier(base_ps, ys, w_test),
        "train_base_rate_log_loss": _logloss(base_ps, ys, w_test),
        "fixed_15m_momentum_brier": _brier(momentum, ys, w_test),
        "fixed_15m_momentum_log_loss": _logloss(momentum, ys, w_test),
        "dependency_group_total_weight_one": True,
        "prospective_oos": False,
        "oos_validated": False,
        "production_authority": False,
    }


def _fit_v2_models_with_diagnostics(runtime: ShortHorizonRuntime, *, force: bool = False) -> int:
    created = 0
    now = time.time()
    for horizon in _runtime_module.HORIZONS:
        rows = [row for row in runtime._resolved_eligible(horizon) if _v2._has_v2(row)]
        evidence = runtime._evidence(rows)
        if not evidence.get("fit_allowed"):
            continue
        for feature_set, names in _v2.V2_FEATURE_SETS.items():
            with runtime._lock:
                latest = runtime._conn.execute(
                    "SELECT created_ts,effective_n FROM g1s_models WHERE horizon_minutes=? "
                    "AND feature_set=? ORDER BY created_ts DESC LIMIT 1",
                    (horizon, feature_set)).fetchone()
            if latest and not force:
                if now-float(latest["created_ts"]) < MODEL_REFIT_INTERVAL_SEC:
                    continue
                if evidence["effective_n"]-float(latest["effective_n"]) < MODEL_REFIT_MIN_EFFECTIVE_DELTA:
                    continue
            beta, mean, std = _fit_on(runtime, rows, feature_set)
            cutoff = max(float(row["resolved_ts"]) for row in rows)
            feature_names = list(names)+[
                f"instrument:{code}" for code in tuple(_runtime_module.INSTRUMENTS)[1:]
            params = {
                "intercept_and_coefficients": [float(v) for v in beta],
                "feature_mean": [float(v) for v in mean],
                "feature_std": [float(v) for v in std],
                "feature_names": feature_names,
                "l2": 0.25,
                "dependency_group_total_weight_one": True,
            }
            artifact = {
                "contract_version": _v2.V2_MODEL_VERSION,
                "model_family": "DEPENDENCY_WEIGHTED_LOGISTIC_V2",
                "feature_contract_version": _v2.FEATURE_CONTRACT_V2,
                "horizon_minutes": horizon, "feature_set": feature_set,
                "training_cutoff_ts": cutoff,
                "source_observation_ids": [row["observation_id"] for row in rows],
                "parameters": params,
            }
            artifact_sha = _sha_text(_v2._plain_json(artifact))
            model_id = "g1s-v2-model-"+artifact_sha[:25]
            diagnostics = _historical_diagnostics(runtime, rows, feature_set, horizon)
            diagnostics["source_contract"] = _v2.FEATURE_CONTRACT_V2
            diagnostics["dependency_weighted_fit"] = True
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_models(model_id,model_family,horizon_minutes,"
                    "feature_set,training_cutoff_ts,raw_n,effective_n,positive_n,negative_n,"
                    "training_days,parameters_json,artifact_sha256,diagnostics_json,authority,created_ts)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                    (model_id, "DEPENDENCY_WEIGHTED_LOGISTIC_V2", horizon, feature_set, cutoff,
                     evidence["raw_resolved"], float(evidence["effective_n"]),
                     evidence["positive_n"], evidence["negative_n"], evidence["trading_days"],
                     _json(params), artifact_sha, _json(diagnostics), now))
                created += int(cur.rowcount > 0)
    return created


def install_g1_short_horizon_v2_diagnostics() -> None:
    if getattr(ShortHorizonRuntime, "_v2_diagnostics_version", None) == V2_DIAGNOSTICS_VERSION:
        return
    # V2 fit wrapper resolves this module global at call time. Replacing it here
    # is safe before the service research worker starts and preserves V1 artifacts.
    _v2._fit_v2_models = _fit_v2_models_with_diagnostics
    ShortHorizonRuntime._v2_diagnostics_version = V2_DIAGNOSTICS_VERSION
