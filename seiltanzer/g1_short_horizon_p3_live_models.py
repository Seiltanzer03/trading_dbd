"""One-time historical proof and frozen model artifacts for P3L."""
from __future__ import annotations

import json
import time
from typing import Any

import numpy as np

from .g1_short_horizon_historical_wf import _json, _sha, _weighted_mean, _weights
from . import g1_short_horizon_p3_path_geometry as _p3
from .g1_short_horizon_p3_fast import build_rows_fast
from . import g1_short_horizon_p3_volatility_hardening as _p3b
from .g1_short_horizon_p3_live_schema import (
    P3L_CONTRACT_VERSION,
    P3L_MODEL_VERSION,
    P3L_PROOF_RETRY_SEC,
    ensure_p3l_tables,
    p3l_state,
    update_p3l_state,
)


def p3l_models_ready(runtime) -> bool:
    ensure_p3l_tables(runtime)
    with runtime._lock:
        proofs = runtime._conn.execute(
            "SELECT COUNT(*) n FROM g1s_volatility_historical_proofs "
            "WHERE contract_version=? AND historical_winner=1",
            (P3L_CONTRACT_VERSION,)).fetchone()["n"]
        models = runtime._conn.execute(
            "SELECT COUNT(*) n FROM g1s_volatility_models WHERE contract_version=?",
            (P3L_MODEL_VERSION,)).fetchone()["n"]
    return int(proofs or 0) == len(_p3.HORIZONS) and int(models or 0) == len(_p3.HORIZONS)


def load_p3l_models(runtime) -> dict[int, dict[str, Any]]:
    ensure_p3l_tables(runtime)
    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT * FROM g1s_volatility_models WHERE contract_version=? "
            "ORDER BY horizon_minutes,created_ts,model_id",
            (P3L_MODEL_VERSION,)).fetchall()
    result: dict[int, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        # First artifact under this fixed contract remains the frozen champion.
        result.setdefault(int(item["horizon_minutes"]), item)
    return result


def materialize_p3l_historical_models(runtime, *, force: bool = False) -> dict[str, Any]:
    """Re-run frozen P3B math on persisted P1B sources and freeze winners.

    This performs no network I/O. If the hardened historical gate does not pass
    exactly as predeclared, P3L refuses to create a prospective model.
    """
    ensure_p3l_tables(runtime)
    if p3l_models_ready(runtime) and not force:
        return {"refreshed": False, "reason": "FROZEN_MODELS_READY"}
    state = p3l_state(runtime)
    now = time.time()
    if state.get("historical_state") == "REJECTED" and not force:
        return {"refreshed": False, "reason": "HISTORICAL_GATE_REJECTED"}
    last_attempt = float(state.get("last_proof_attempt_ts") or 0.0)
    if (not force and state.get("historical_state") == "ERROR"
            and last_attempt and now-last_attempt < P3L_PROOF_RETRY_SEC):
        return {"refreshed": False, "reason": "PROOF_RETRY_COOLDOWN",
                "retry_in_sec": P3L_PROOF_RETRY_SEC-(now-last_attempt)}

    update_p3l_state(runtime, historical_state="RUNNING",
                     last_proof_attempt_ts=now, last_proof_error=None)
    try:
        source_set, sources = _p3._current_sources(runtime)
        precomputed = _p3b._enriched_precompute(sources)
        results = []
        for horizon in _p3.HORIZONS:
            rows = build_rows_fast(precomputed, int(horizon))
            weights, effective = _weights(rows)
            evaluation = _p3b.evaluate_hardened(rows, int(horizon))
            gate = _p3.winner_gate(evaluation, len(rows), effective)
            winner = bool(gate["historical_winner"])
            proof = {
                "contract_version": P3L_CONTRACT_VERSION,
                "parent_p3_contract": _p3.P3_CONTRACT_VERSION,
                "parent_p3b_contract": _p3b.P3B_CONTRACT_VERSION,
                "source_set_sha256": source_set,
                "target": _p3.TARGET_FUTURE_RV,
                "horizon_minutes": int(horizon),
                "raw_n": len(rows), "effective_n": int(effective),
                "evaluation": evaluation, "selection_gate": gate,
                "historical_winner": winner,
                "historical_sampling_interval": "5m",
                "strong_baselines": [
                    "zero", "causal_historical_mean", "causal_vol_anchor",
                    "current_rv60_persistence", "current_rv15_persistence",
                    "current_rv240_persistence", "ewma240_persistence",
                    "causal_scaled_ewma240", "har_5m_log_vol_ridge",
                ],
                "live_authority": False, "auto_promotion": False,
            }
            proof_raw = _json(proof)
            proof_id = "g1s-p3l-proof-" + _sha(
                f"{source_set}|{horizon}|{_sha(proof_raw)}")[:26]
            with runtime._lock, runtime._conn:
                runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_volatility_historical_proofs("
                    "proof_id,source_set_sha256,horizon_minutes,historical_winner,proof_json,"
                    "proof_sha256,contract_version,created_ts) VALUES(?,?,?,?,?,?,?,?)",
                    (proof_id, source_set, int(horizon), int(winner), proof_raw,
                     _sha(proof_raw), P3L_CONTRACT_VERSION, time.time()))

            model_id = None
            if winner:
                cutoff = max(float(row["target_ts"]) for row in rows)
                created_ts = time.time()
                if cutoff >= created_ts-1e-6:
                    raise RuntimeError(
                        f"historical cutoff must precede model creation H{horizon}")
                p3_model = _p3._fit_model(rows, _p3.TARGET_FUTURE_RV)
                y = np.asarray([float(row[_p3.TARGET_FUTURE_RV]) for row in rows], dtype=float)
                baselines = {
                    "historical_mean": _weighted_mean(y, weights),
                    "scaled_ewma240_factor": _p3b._fit_scalar(
                        rows, "current_ewma_volatility_5m_240m"),
                    "har_5m_log_vol_ridge": _p3b._fit_har(rows),
                    "baseline_names": proof["strong_baselines"],
                }
                artifact = {
                    "contract_version": P3L_MODEL_VERSION,
                    "source_set_sha256": source_set,
                    "target": _p3.TARGET_FUTURE_RV,
                    "horizon_minutes": int(horizon),
                    "training_cutoff_ts": cutoff,
                    "raw_n": len(rows), "effective_n": int(effective),
                    "p3_model": p3_model, "baseline_artifacts": baselines,
                    "historical_proof_id": proof_id,
                    "frozen_after_creation": True,
                    "production_authority": False, "auto_promotion": False,
                }
                artifact_raw = _json(artifact); artifact_sha = _sha(artifact_raw)
                model_id = "g1s-p3l-model-" + artifact_sha[:25]
                with runtime._lock, runtime._conn:
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_volatility_models("
                        "model_id,source_set_sha256,horizon_minutes,training_cutoff_ts,raw_n,"
                        "effective_n,p3_model_json,baseline_artifacts_json,historical_proof_id,"
                        "artifact_sha256,authority,auto_promotion,production_used,created_ts,"
                        "contract_version) VALUES(?,?,?,?,?,?,?,?,?,?,'research_only',0,0,?,?)",
                        (model_id, source_set, int(horizon), cutoff, len(rows), int(effective),
                         _json(p3_model), _json(baselines), proof_id, artifact_sha,
                         created_ts, P3L_MODEL_VERSION))
            results.append({"horizon_minutes": int(horizon),
                            "historical_winner": winner, "model_id": model_id})
            del rows

        if not all(bool(row["historical_winner"]) for row in results):
            update_p3l_state(runtime, historical_state="REJECTED",
                             historical_source_set_sha256=source_set,
                             last_proof_success_ts=time.time())
            return {"refreshed": True, "historical_state": "REJECTED",
                    "source_set_sha256": source_set, "results": results}
        update_p3l_state(runtime, historical_state="FROZEN_READY",
                         historical_source_set_sha256=source_set,
                         last_proof_success_ts=time.time(), last_proof_error=None)
        return {"refreshed": True, "historical_state": "FROZEN_READY",
                "source_set_sha256": source_set, "results": results}
    except Exception as exc:
        update_p3l_state(runtime, historical_state="ERROR",
                         last_proof_error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise
