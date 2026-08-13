"""Prospective P3L T0 feature capture and frozen shadow predictions."""
from __future__ import annotations

import json
import time
from typing import Any

from .g1_short_horizon_historical_wf import _json, _sha
from . import g1_short_horizon_p3_path_geometry as _p3
from . import g1_short_horizon_p3_volatility_hardening as _p3b
from .g1_short_horizon_p3_live_bars import p3l_live_sources
from .g1_short_horizon_p3_live_models import load_p3l_models
from .g1_short_horizon_p3_live_schema import (
    P3L_CONTRACT_VERSION,
    P3L_MAX_PREDICTION_LATENCY_SEC,
    P3L_T0_FEATURE_VERSION,
    ensure_p3l_tables,
)
from .passive_learning import _trading_seconds_between


def p3l_baseline_predictions(context: dict[str, Any],
                             model: dict[str, Any]) -> dict[str, float]:
    p3_model = json.loads(model["p3_model_json"])
    artifacts = json.loads(model["baseline_artifacts_json"])
    rv15 = float(context["current_realized_volatility_5m_15m"])
    rv60 = float(context["current_realized_volatility_5m_60m"])
    rv240 = float(context["current_realized_volatility_5m_240m"])
    ewma = float(context["current_ewma_volatility_5m_240m"])
    har = float(_p3b._predict_har([context], artifacts["har_5m_log_vol_ridge"])[0])
    return {
        "zero": 0.0,
        "causal_historical_mean": max(0.0, float(artifacts["historical_mean"])),
        "causal_vol_anchor": max(0.0, float(p3_model["anchor_factor"])*rv60),
        "current_rv60_persistence": max(0.0, rv60),
        "current_rv15_persistence": max(0.0, rv15),
        "current_rv240_persistence": max(0.0, rv240),
        "ewma240_persistence": max(0.0, ewma),
        "causal_scaled_ewma240": max(
            0.0, float(artifacts["scaled_ewma240_factor"])*ewma),
        "har_5m_log_vol_ridge": max(0.0, har),
    }


def _feature_payload(context: dict[str, Any], model: dict[str, Any],
                     *, captured_ts: float, target_ts: float,
                     latency: float) -> dict[str, Any]:
    names = (
        "ret_5m", "ret_15m", "ret_60m",
        "current_realized_volatility_5m_15m",
        "current_realized_volatility_5m_60m",
        "current_realized_volatility_5m_240m",
        "current_ewma_volatility_5m_240m",
        "range60_log", "drawup60_log", "drawdown60_magnitude_log",
        "log_current_rv60_5m", "log_current_rv15_5m",
        "ret5_over_rv60", "ret15_over_rv60", "ret60_over_rv60",
        "rv15_over_rv60", "range60_over_rv60",
        "drawup60_over_rv60", "drawdown60_over_rv60",
        "trend_agreement_5_15", "trend_agreement_15_60",
        "utc_sin", "utc_cos",
    )
    return {
        "contract_version": P3L_T0_FEATURE_VERSION,
        "instrument": str(context["instrument"]),
        "captured_ts": float(captured_ts),
        "target_ts": float(target_ts),
        "horizon_minutes": int(model["horizon_minutes"]),
        "t0_close": float(context["current_close"]),
        "prediction_latency_sec": float(latency),
        "source": "raw_yahoo_1m_exact_5_bar_aggregation",
        "historical_source": "Yahoo native 5m",
        "frequency_parity": True,
        "native_vs_aggregated_bar_parity_claim": False,
        "future_bars_used": False,
        "features": {name: context.get(name) for name in names},
        "model_id": str(model["model_id"]),
        "model_created_ts": float(model["created_ts"]),
        "training_cutoff_ts": float(model["training_cutoff_ts"]),
        "research_only": True,
    }


def create_p3l_predictions(runtime, *, now: float | None = None) -> int:
    ensure_p3l_tables(runtime)
    now = float(now or time.time())
    models = load_p3l_models(runtime)
    if len(models) != len(_p3.HORIZONS):
        return 0
    sources = p3l_live_sources(runtime, now=now)
    if not sources:
        return 0
    precomputed = _p3b._enriched_precompute(sources)
    created = 0
    for instrument, item in precomputed.items():
        contexts = item["contexts"]
        if not contexts:
            continue
        for captured_ts in sorted(contexts, reverse=True):
            captured_ts = float(captured_ts)
            latency = now-captured_ts
            if latency < -1e-6:
                continue
            if latency > P3L_MAX_PREDICTION_LATENCY_SEC:
                break
            context = dict(contexts[captured_ts])
            for horizon, model in models.items():
                target_ts = captured_ts+int(horizon)*60.0
                if float(model["created_ts"]) >= captured_ts-1e-9:
                    continue
                if float(model["training_cutoff_ts"]) >= captured_ts-1e-9:
                    continue
                if now >= target_ts-1e-9:
                    continue
                # Same causal session-continuity rule used by historical fixed
                # calendar horizons: never open a row that is known at T0 to cross
                # a scheduled market closure.
                open_seconds = _trading_seconds_between(instrument, captured_ts, target_ts)
                if abs(open_seconds-int(horizon)*60.0) > 1e-6:
                    continue
                feature_payload = _feature_payload(
                    context, model, captured_ts=captured_ts,
                    target_ts=target_ts, latency=latency)
                feature_raw = _json(feature_payload)
                observation_id = "g1s-p3l-obs-" + _sha(
                    f"{instrument}|{horizon}|{captured_ts:.6f}|{model['model_id']}")[:26]
                prediction_ts = time.time()
                if prediction_ts >= target_ts-1e-9:
                    continue
                predicted = float(_p3._predict_model(
                    [context], json.loads(model["p3_model_json"]))[0])
                baselines = p3l_baseline_predictions(context, model)
                prediction_payload = {
                    "contract_version": P3L_CONTRACT_VERSION,
                    "observation_id": observation_id,
                    "model_id": str(model["model_id"]),
                    "target": _p3.TARGET_FUTURE_RV,
                    "predicted_volatility_5m": predicted,
                    "baseline_predictions": baselines,
                    "captured_ts": captured_ts,
                    "target_ts": target_ts,
                    "prediction_created_ts": prediction_ts,
                    "prediction_precedes_target": prediction_ts < target_ts,
                    "model_precedes_t0": float(model["created_ts"]) < captured_ts,
                    "training_cutoff_precedes_t0": (
                        float(model["training_cutoff_ts"]) < captured_ts),
                    "production_used": False, "auto_promotion": False,
                }
                prediction_raw = _json(prediction_payload)
                prediction_id = "g1s-p3l-pred-" + _sha(prediction_raw)[:25]
                with runtime._lock, runtime._conn:
                    before_obs = runtime._conn.total_changes
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_volatility_observations("
                        "observation_id,instrument,horizon_minutes,model_id,captured_ts,target_ts,"
                        "t0_close,prediction_latency_sec,features_json,features_sha256,"
                        "evidence_eligible,exclusion_reason,contract_version,created_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,1,NULL,?,?)",
                        (observation_id, instrument, int(horizon), str(model["model_id"]),
                         captured_ts, target_ts, float(context["current_close"]), latency,
                         feature_raw, _sha(feature_raw), P3L_CONTRACT_VERSION, prediction_ts))
                    obs_inserted = runtime._conn.total_changes > before_obs
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_volatility_predictions("
                        "prediction_id,observation_id,model_id,predicted_volatility_5m,"
                        "baseline_predictions_json,prediction_sha256,production_used,created_ts) "
                        "VALUES(?,?,?,?,?,?,0,?)",
                        (prediction_id, observation_id, str(model["model_id"]), predicted,
                         _json(baselines), _sha(prediction_raw), prediction_ts))
                created += int(obs_inserted)
            # At most one near-real-time T0 per instrument per worker iteration.
            break
    return created
