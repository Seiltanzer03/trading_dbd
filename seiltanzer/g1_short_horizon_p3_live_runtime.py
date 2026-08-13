"""P3L prospective volatility shadow validation.

Historical P3/P3B found a robust pooled edge for *future 5m realized volatility*.
This runtime carries only that proven target into prospective evidence. Direction,
MFE and MAE remain rejected historical challengers and are not promoted here.

The live target is built from exact groups of five raw Yahoo 1m OHLC bars. The
terminal's broker/spot-offset bars are deliberately not used because an additive
basis shift changes log returns and would break historical-source parity.

Authority stays research-only. A historical winner only earns the right to start
an immutable LIVE_PROSPECTIVE_VOLATILITY_OOS cohort; it never changes AI Verdict,
execution, sizing, stops or takes.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections import defaultdict
from typing import Any

import numpy as np

from . import g1_short_horizon_integration as _integration
from . import storage_runtime as _storage
from .config import INSTRUMENTS
from .g1_short_horizon_historical_wf import _json, _sha, _weighted_mean, _weights
from . import g1_short_horizon_p3_path_geometry as _p3
from .g1_short_horizon_p3_fast import build_rows_fast
from . import g1_short_horizon_p3_volatility_hardening as _p3b
from .g1_short_horizon_runtime import ShortHorizonRuntime
from .passive_learning import _trading_seconds_between


P3L_CONTRACT_VERSION = "g1s-p3-live-volatility-oos-v1"
P3L_MODEL_VERSION = "g1s-p3-live-frozen-historical-model-v1"
P3L_T0_FEATURE_VERSION = "g1s-p3-live-5m-t0-parity-v1"
P3L_TARGET_VERSION = "g1s-p3-live-future-rv5m-v1"
P3L_EVIDENCE_LABEL = "LIVE_PROSPECTIVE_VOLATILITY_OOS"
P3L_RAW_BAR_VERSION = "g1s-p3-live-raw-yahoo-1m-bar-v1"
P3L_BAR5_VERSION = "g1s-p3-live-aggregated-yahoo-5m-bar-v1"
P3L_PROGRESS_VERSION = "g1s-p3-live-volatility-progress-v1"
P3L_MAX_PREDICTION_LATENCY_SEC = 150.0
P3L_RESOLUTION_GRACE_SEC = 15 * 60.0
P3L_BAR_COMPLETION_GRACE_SEC = 5.0
P3L_PROOF_RETRY_SEC = 60 * 60.0
P3L_SERIOUS_REQUIRED = {
    "raw_resolved": 1000,
    "effective_n": 400,
    "temporal_blocks": 20,
    "instrument_count": 6,
    "asset_family_count": 3,
}
P3L_REQUIRED_ROBUST_BLOCKS = 3
P3L_METRIC_MARGIN = 0.005

P3L_CRITICAL_TABLES = (
    "g1s_volatility_raw_1m_bars",
    "g1s_volatility_5m_bars",
    "g1s_volatility_historical_proofs",
    "g1s_volatility_models",
    "g1s_volatility_observations",
    "g1s_volatility_predictions",
    "g1s_volatility_resolutions",
)

_ASSET_FAMILY = {
    "NAS100": "equity", "SP500": "equity", "US30": "equity",
    "GER40": "equity", "UK100": "equity", "JPY100": "equity",
    "XAU": "metal", "XAG": "metal",
    "EURUSD": "fx", "USDCAD": "fx",
}


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _table_columns(conn, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_raw_1m_bars(
                instrument TEXT NOT NULL,
                bar_start_ts REAL NOT NULL,
                bar_end_ts REAL NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL,
                yahoo_ticker TEXT NOT NULL,
                source TEXT NOT NULL,
                source_fetched_ts REAL NOT NULL,
                contract_version TEXT NOT NULL,
                row_sha256 TEXT NOT NULL,
                created_ts REAL NOT NULL,
                PRIMARY KEY(instrument,bar_start_ts)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_vol_raw_end "
            "ON g1s_volatility_raw_1m_bars(instrument,bar_end_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_5m_bars(
                instrument TEXT NOT NULL,
                bar_start_ts REAL NOT NULL,
                bar_end_ts REAL NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL,
                source_1m_count INTEGER NOT NULL,
                source_rows_sha256 TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                created_ts REAL NOT NULL,
                PRIMARY KEY(instrument,bar_start_ts)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_vol_5m_end "
            "ON g1s_volatility_5m_bars(instrument,bar_end_ts)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_historical_proofs(
                proof_id TEXT PRIMARY KEY,
                source_set_sha256 TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                historical_winner INTEGER NOT NULL,
                proof_json TEXT NOT NULL,
                proof_sha256 TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                created_ts REAL NOT NULL,
                UNIQUE(contract_version,horizon_minutes)
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_models(
                model_id TEXT PRIMARY KEY,
                source_set_sha256 TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                training_cutoff_ts REAL NOT NULL,
                raw_n INTEGER NOT NULL,
                effective_n INTEGER NOT NULL,
                p3_model_json TEXT NOT NULL,
                baseline_artifacts_json TEXT NOT NULL,
                historical_proof_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                authority TEXT NOT NULL,
                auto_promotion INTEGER NOT NULL DEFAULT 0,
                production_used INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL,
                UNIQUE(contract_version,horizon_minutes),
                contract_version TEXT NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_observations(
                observation_id TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL,
                t0_close REAL NOT NULL,
                prediction_latency_sec REAL NOT NULL,
                features_json TEXT NOT NULL,
                features_sha256 TEXT NOT NULL,
                evidence_eligible INTEGER NOT NULL,
                exclusion_reason TEXT,
                contract_version TEXT NOT NULL,
                created_ts REAL NOT NULL,
                UNIQUE(instrument,horizon_minutes,captured_ts,model_id)
            )""")
        runtime._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_g1s_vol_obs_target "
            "ON g1s_volatility_observations(target_ts,evidence_eligible)")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_predictions(
                prediction_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL UNIQUE,
                model_id TEXT NOT NULL,
                predicted_volatility_5m REAL NOT NULL,
                baseline_predictions_json TEXT NOT NULL,
                prediction_sha256 TEXT NOT NULL,
                production_used INTEGER NOT NULL DEFAULT 0,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_resolutions(
                observation_id TEXT PRIMARY KEY,
                resolution_status TEXT NOT NULL,
                future_realized_volatility_5m REAL,
                future_realized_volatility_1m_secondary REAL,
                future_5m_steps INTEGER,
                future_1m_steps INTEGER,
                target_json TEXT NOT NULL,
                target_sha256 TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                resolved_ts REAL NOT NULL,
                created_ts REAL NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_progress(
                horizon_minutes INTEGER PRIMARY KEY,
                contract_version TEXT NOT NULL,
                raw_resolved INTEGER NOT NULL DEFAULT 0,
                effective_n INTEGER NOT NULL DEFAULT 0,
                temporal_blocks INTEGER NOT NULL DEFAULT 0,
                instrument_count INTEGER NOT NULL DEFAULT 0,
                asset_family_count INTEGER NOT NULL DEFAULT 0,
                robust_block_non_degrade_n INTEGER NOT NULL DEFAULT 0,
                model_mae REAL,
                model_rmse REAL,
                best_mae_baseline TEXT,
                best_mae REAL,
                best_rmse_baseline TEXT,
                best_rmse REAL,
                mae_relative_improvement REAL,
                rmse_relative_improvement REAL,
                verdict TEXT NOT NULL,
                latest_resolved_ts REAL,
                updated_ts REAL NOT NULL
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_volatility_state(
                id INTEGER PRIMARY KEY CHECK(id=1),
                contract_version TEXT NOT NULL,
                historical_state TEXT NOT NULL,
                historical_source_set_sha256 TEXT,
                last_proof_attempt_ts REAL,
                last_proof_success_ts REAL,
                last_proof_error TEXT,
                last_cycle_ts REAL,
                last_cycle_error TEXT,
                raw_1m_rows_ingested INTEGER NOT NULL DEFAULT 0,
                bars_5m_created INTEGER NOT NULL DEFAULT 0,
                observations_created INTEGER NOT NULL DEFAULT 0,
                resolutions_created INTEGER NOT NULL DEFAULT 0,
                updated_ts REAL NOT NULL
            )""")
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_volatility_state("
            "id,contract_version,historical_state,updated_ts) VALUES(1,?,'PENDING',?)",
            (P3L_CONTRACT_VERSION, time.time()))
        for table in P3L_CRITICAL_TABLES:
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable P3L evidence row'); END""")
            runtime._conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT,'immutable P3L evidence row'); END""")


def _update_state(runtime: ShortHorizonRuntime, **updates: Any) -> None:
    updates = dict(updates)
    updates["updated_ts"] = time.time()
    assignments = ",".join(f"{key}=?" for key in updates)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"UPDATE g1s_volatility_state SET {assignments} WHERE id=1",
            tuple(updates.values()))


def _state(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    with runtime._lock:
        row = runtime._conn.execute("SELECT * FROM g1s_volatility_state WHERE id=1").fetchone()
    return dict(row) if row is not None else {}


def _proof_and_models_ready(runtime: ShortHorizonRuntime) -> bool:
    with runtime._lock:
        proofs = runtime._conn.execute(
            "SELECT COUNT(*) n FROM g1s_volatility_historical_proofs "
            "WHERE contract_version=? AND historical_winner=1",
            (P3L_CONTRACT_VERSION,)).fetchone()["n"]
        models = runtime._conn.execute(
            "SELECT COUNT(*) n FROM g1s_volatility_models WHERE contract_version=?",
            (P3L_MODEL_VERSION,)).fetchone()["n"]
    return int(proofs or 0) == len(_p3.HORIZONS) and int(models or 0) == len(_p3.HORIZONS)


def _materialize_historical_proof_and_models(runtime: ShortHorizonRuntime,
                                             *, force: bool = False) -> dict[str, Any]:
    """One-time historical gate + frozen full-history artifacts; no network I/O."""
    _ensure_tables(runtime)
    if _proof_and_models_ready(runtime) and not force:
        return {"refreshed": False, "reason": "FROZEN_MODELS_READY"}
    state = _state(runtime)
    now = time.time()
    last_attempt = _finite(state.get("last_proof_attempt_ts")) or 0.0
    if (not force and last_attempt and now-last_attempt < P3L_PROOF_RETRY_SEC
            and state.get("historical_state") == "ERROR"):
        return {"refreshed": False, "reason": "PROOF_RETRY_COOLDOWN",
                "retry_in_sec": P3L_PROOF_RETRY_SEC-(now-last_attempt)}

    _update_state(runtime, historical_state="RUNNING", last_proof_attempt_ts=now,
                  last_proof_error=None)
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
            proof_payload = {
                "contract_version": P3L_CONTRACT_VERSION,
                "parent_p3_contract": _p3.P3_CONTRACT_VERSION,
                "parent_p3b_contract": _p3b.P3B_CONTRACT_VERSION,
                "evidence_label": "HISTORICAL_WALK_FORWARD_5M_VOLATILITY_HARDENED",
                "source_set_sha256": source_set,
                "horizon_minutes": int(horizon),
                "target": _p3.TARGET_FUTURE_RV,
                "raw_n": len(rows), "effective_n": int(effective),
                "evaluation": evaluation,
                "selection_gate": gate,
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
            proof_raw = _json(proof_payload)
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
                training_cutoff = max(float(row["target_ts"]) for row in rows)
                created_ts = time.time()
                if training_cutoff >= created_ts-1e-6:
                    raise RuntimeError(
                        f"historical training cutoff not before model creation H{horizon}")
                p3_model = _p3._fit_model(rows, _p3.TARGET_FUTURE_RV)
                y = np.asarray([float(row[_p3.TARGET_FUTURE_RV]) for row in rows], dtype=float)
                historical_mean = _weighted_mean(y, weights)
                baseline_artifacts = {
                    "historical_mean": historical_mean,
                    "scaled_ewma240_factor": _p3b._fit_scalar(
                        rows, "current_ewma_volatility_5m_240m"),
                    "har_5m_log_vol_ridge": _p3b._fit_har(rows),
                    "baseline_names": proof_payload["strong_baselines"],
                }
                artifact = {
                    "contract_version": P3L_MODEL_VERSION,
                    "source_set_sha256": source_set,
                    "horizon_minutes": int(horizon),
                    "target": _p3.TARGET_FUTURE_RV,
                    "training_cutoff_ts": training_cutoff,
                    "raw_n": len(rows), "effective_n": int(effective),
                    "p3_model": p3_model,
                    "baseline_artifacts": baseline_artifacts,
                    "historical_proof_id": proof_id,
                    "historical_winner_required": True,
                    "frozen_after_creation": True,
                    "production_authority": False,
                    "auto_promotion": False,
                }
                artifact_raw = _json(artifact)
                artifact_sha = _sha(artifact_raw)
                model_id = "g1s-p3l-model-" + artifact_sha[:25]
                with runtime._lock, runtime._conn:
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_volatility_models("
                        "model_id,source_set_sha256,horizon_minutes,training_cutoff_ts,"
                        "raw_n,effective_n,p3_model_json,baseline_artifacts_json,"
                        "historical_proof_id,artifact_sha256,authority,auto_promotion,"
                        "production_used,created_ts,contract_version) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,'research_only',0,0,?,?,?)",
                        (model_id, source_set, int(horizon), training_cutoff,
                         len(rows), int(effective), _json(p3_model),
                         _json(baseline_artifacts), proof_id, artifact_sha,
                         created_ts, P3L_MODEL_VERSION))
            results.append({"horizon_minutes": int(horizon),
                            "historical_winner": winner, "model_id": model_id})
            del rows
        if not all(bool(item["historical_winner"]) for item in results):
            _update_state(runtime, historical_state="REJECTED",
                          historical_source_set_sha256=source_set,
                          last_proof_success_ts=time.time())
            return {"refreshed": True, "historical_state": "REJECTED",
                    "source_set_sha256": source_set, "results": results}
        _update_state(runtime, historical_state="FROZEN_READY",
                      historical_source_set_sha256=source_set,
                      last_proof_success_ts=time.time(), last_proof_error=None)
        return {"refreshed": True, "historical_state": "FROZEN_READY",
                "source_set_sha256": source_set, "results": results}
    except Exception as exc:
        _update_state(runtime, historical_state="ERROR",
                      last_proof_error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise


def _ingest_raw_bars(runtime: ShortHorizonRuntime, passive, *, now: float) -> int:
    written = 0
    feeds = getattr(passive, "_feeds", {}) or {}
    for instrument, feed in list(feeds.items()):
        if instrument not in INSTRUMENTS:
            continue
        raw = getattr(feed, "intraday_ohlcv_raw", None) or []
        fetched_ts = _finite(getattr(feed, "intraday_raw_fetched_ts", None))
        source = str(getattr(feed, "intraday_raw_source", "") or "")
        if not raw or fetched_ts is None or not source.startswith("yfinance "):
            continue
        ticker = INSTRUMENTS[instrument].yahoo
        for item in raw:
            try:
                bar_start, open_p, high, low, close, volume = item
                bar_start = float(bar_start); bar_end = bar_start+60.0
                values = [float(open_p), float(high), float(low), float(close)]
                volume_value = _finite(volume)
            except (TypeError, ValueError):
                continue
            if bar_end > now-P3L_BAR_COMPLETION_GRACE_SEC:
                continue
            if not all(math.isfinite(value) and value > 0 for value in values):
                continue
            payload = {
                "contract_version": P3L_RAW_BAR_VERSION,
                "instrument": instrument, "yahoo_ticker": ticker,
                "bar_start_ts": bar_start, "bar_end_ts": bar_end,
                "open": values[0], "high": values[1], "low": values[2],
                "close": values[3], "volume": volume_value,
                "source": source, "source_fetched_ts": fetched_ts,
            }
            raw_json = _json(payload)
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_volatility_raw_1m_bars("
                    "instrument,bar_start_ts,bar_end_ts,open,high,low,close,volume,"
                    "yahoo_ticker,source,source_fetched_ts,contract_version,row_sha256,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (instrument, bar_start, bar_end, *values, volume_value, ticker,
                     source, fetched_ts, P3L_RAW_BAR_VERSION, _sha(raw_json), time.time()))
            written += int(cur.rowcount > 0)
    return written


def _aggregate_5m(runtime: ShortHorizonRuntime, *, now: float) -> int:
    written = 0
    cutoff = now-P3L_BAR_COMPLETION_GRACE_SEC
    for instrument in INSTRUMENTS:
        with runtime._lock:
            rows = [dict(row) for row in runtime._conn.execute(
                "SELECT * FROM g1s_volatility_raw_1m_bars WHERE instrument=? "
                "AND bar_end_ts>=? AND bar_end_ts<=? ORDER BY bar_start_ts",
                (instrument, now-3*86400.0, cutoff)).fetchall()]
        grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            bucket = math.floor(float(row["bar_start_ts"])/300.0)*300.0
            grouped[bucket].append(row)
        for bucket, members in grouped.items():
            if bucket+300.0 > cutoff:
                continue
            ordered = sorted(members, key=lambda row: float(row["bar_start_ts"]))
            expected = [bucket+60.0*i for i in range(5)]
            starts = [float(row["bar_start_ts"]) for row in ordered]
            if len(ordered) != 5 or any(abs(a-b) > 1e-6 for a,b in zip(starts, expected)):
                continue
            source_material = [{
                "bar_start_ts": row["bar_start_ts"], "row_sha256": row["row_sha256"]
            } for row in ordered]
            volume_values = [_finite(row.get("volume")) for row in ordered]
            volume = (sum(value for value in volume_values if value is not None)
                      if any(value is not None for value in volume_values) else None)
            with runtime._lock, runtime._conn:
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_volatility_5m_bars("
                    "instrument,bar_start_ts,bar_end_ts,open,high,low,close,volume,"
                    "source_1m_count,source_rows_sha256,contract_version,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (instrument, bucket, bucket+300.0,
                     float(ordered[0]["open"]), max(float(row["high"]) for row in ordered),
                     min(float(row["low"]) for row in ordered), float(ordered[-1]["close"]),
                     volume, 5, _sha(_json(source_material)), P3L_BAR5_VERSION, time.time()))
            written += int(cur.rowcount > 0)
    return written


def _live_sources(runtime: ShortHorizonRuntime, *, now: float) -> list[dict[str, Any]]:
    sources = []
    for instrument in INSTRUMENTS:
        with runtime._lock:
            rows = [dict(row) for row in runtime._conn.execute(
                "SELECT bar_start_ts,bar_end_ts,open,high,low,close,volume "
                "FROM g1s_volatility_5m_bars WHERE instrument=? AND bar_end_ts>=? "
                "ORDER BY bar_start_ts",
                (instrument, now-3*86400.0)).fetchall()]
        if rows:
            sources.append({
                "instrument": instrument, "ticker": INSTRUMENTS[instrument].yahoo,
                "source_id": f"live-5m-{instrument}", "bars": rows,
            })
    return sources


def _load_models(runtime: ShortHorizonRuntime) -> dict[int, dict[str, Any]]:
    with runtime._lock:
        rows = runtime._conn.execute(
            "SELECT * FROM g1s_volatility_models WHERE contract_version=? "
            "ORDER BY horizon_minutes,created_ts",
            (P3L_MODEL_VERSION,)).fetchall()
    result = {}
    for row in rows:
        item = dict(row)
        horizon = int(item["horizon_minutes"])
        # First frozen artifact wins forever; later research challengers must use
        # another contract rather than silently resetting prospective OOS.
        result.setdefault(horizon, item)
    return result


def _baseline_predictions(context: dict[str, Any], model: dict[str, Any]) -> dict[str, float]:
    p3_model = json.loads(model["p3_model_json"])
    artifacts = json.loads(model["baseline_artifacts_json"])
    rv15 = float(context["current_realized_volatility_5m_15m"])
    rv60 = float(context["current_realized_volatility_5m_60m"])
    rv240 = float(context["current_realized_volatility_5m_240m"])
    ewma = float(context["current_ewma_volatility_5m_240m"])
    anchor = max(0.0, float(p3_model["anchor_factor"])*rv60)
    har = float(_p3b._predict_har([context], artifacts["har_5m_log_vol_ridge"])[0])
    return {
        "zero": 0.0,
        "causal_historical_mean": max(0.0, float(artifacts["historical_mean"])),
        "causal_vol_anchor": anchor,
        "current_rv60_persistence": max(0.0, rv60),
        "current_rv15_persistence": max(0.0, rv15),
        "current_rv240_persistence": max(0.0, rv240),
        "ewma240_persistence": max(0.0, ewma),
        "causal_scaled_ewma240": max(0.0, float(artifacts["scaled_ewma240_factor"])*ewma),
        "har_5m_log_vol_ridge": max(0.0, har),
    }


def _create_predictions(runtime: ShortHorizonRuntime, *, now: float) -> int:
    models = _load_models(runtime)
    if not models:
        return 0
    sources = _live_sources(runtime, now=now)
    if not sources:
        return 0
    precomputed = _p3b._enriched_precompute(sources)
    created = 0
    for instrument, item in precomputed.items():
        contexts = item["contexts"]
        if not contexts:
            continue
        # Only a truly near-real-time completed 5m T0 may start a prospective
        # validation row. Catch-up/backfill can never be relabeled live OOS.
        for captured_ts in sorted(contexts, reverse=True):
            latency = now-float(captured_ts)
            if latency < -1e-6:
                continue
            if latency > P3L_MAX_PREDICTION_LATENCY_SEC:
                break
            context = dict(contexts[captured_ts])
            for horizon, model in models.items():
                target_ts = float(captured_ts)+int(horizon)*60.0
                if float(model["created_ts"]) >= float(captured_ts)-1e-9:
                    continue
                if float(model["training_cutoff_ts"]) >= float(captured_ts)-1e-9:
                    continue
                if now >= target_ts-1e-9:
                    continue
                # Historical P3B excluded observations whose fixed calendar
                # horizon crossed a known session closure; apply the same causal
                # schedule gate before creating live evidence.
                open_seconds = _trading_seconds_between(
                    instrument, float(captured_ts), target_ts)
                if abs(open_seconds-int(horizon)*60.0) > 1e-6:
                    continue
                feature_payload = {
                    "contract_version": P3L_T0_FEATURE_VERSION,
                    "instrument": instrument,
                    "captured_ts": float(captured_ts),
                    "horizon_minutes": int(horizon),
                    "target_ts": target_ts,
                    "prediction_latency_sec": latency,
                    "source": "raw_yahoo_1m_exact_5_bar_aggregation",
                    "historical_source": "Yahoo native 5m",
                    "frequency_parity": True,
                    "native_vs_aggregated_bar_parity_claim": False,
                    "future_bars_used": False,
                    "features": {name: context.get(name) for name in (
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
                    )},
                    "t0_close": float(context["current_close"]),
                    "model_id": str(model["model_id"]),
                    "model_created_ts": float(model["created_ts"]),
                    "training_cutoff_ts": float(model["training_cutoff_ts"]),
                    "research_only": True,
                }
                feature_raw = _json(feature_payload)
                observation_id = "g1s-p3l-obs-" + _sha(
                    f"{instrument}|{horizon}|{captured_ts:.6f}|{model['model_id']}")[:26]
                prediction_ts = time.time()
                if prediction_ts >= target_ts-1e-9:
                    continue
                model_prediction = float(_p3._predict_model(
                    [context], json.loads(model["p3_model_json"]))[0])
                baselines = _baseline_predictions(context, model)
                prediction_payload = {
                    "contract_version": P3L_CONTRACT_VERSION,
                    "observation_id": observation_id,
                    "model_id": str(model["model_id"]),
                    "target": _p3.TARGET_FUTURE_RV,
                    "predicted_volatility_5m": model_prediction,
                    "baseline_predictions": baselines,
                    "prediction_created_ts": prediction_ts,
                    "captured_ts": float(captured_ts),
                    "target_ts": target_ts,
                    "prediction_precedes_target": True,
                    "model_precedes_t0": float(model["created_ts"]) < float(captured_ts),
                    "training_cutoff_precedes_t0": (
                        float(model["training_cutoff_ts"]) < float(captured_ts)),
                    "production_used": False, "auto_promotion": False,
                }
                prediction_raw = _json(prediction_payload)
                prediction_id = "g1s-p3l-pred-" + _sha(prediction_raw)[:25]
                with runtime._lock, runtime._conn:
                    before = runtime._conn.total_changes
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_volatility_observations("
                        "observation_id,instrument,horizon_minutes,model_id,captured_ts,target_ts,"
                        "t0_close,prediction_latency_sec,features_json,features_sha256,"
                        "evidence_eligible,exclusion_reason,contract_version,created_ts) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,1,NULL,?,?)",
                        (observation_id, instrument, int(horizon), str(model["model_id"]),
                         float(captured_ts), target_ts, float(context["current_close"]), latency,
                         feature_raw, _sha(feature_raw), P3L_CONTRACT_VERSION, prediction_ts))
                    runtime._conn.execute(
                        "INSERT OR IGNORE INTO g1s_volatility_predictions("
                        "prediction_id,observation_id,model_id,predicted_volatility_5m,"
                        "baseline_predictions_json,prediction_sha256,production_used,created_ts) "
                        "VALUES(?,?,?,?,?,?,0,?)",
                        (prediction_id, observation_id, str(model["model_id"]),
                         model_prediction, _json(baselines), _sha(prediction_raw), prediction_ts))
                    if runtime._conn.total_changes > before:
                        created += 1
            break
    return created


def _exact_future_5m(runtime: ShortHorizonRuntime, instrument: str,
                     captured_ts: float, target_ts: float) -> list[dict[str, Any]] | None:
    expected = [captured_ts+300.0*i for i in range(1, int(round((target_ts-captured_ts)/300.0))+1)]
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute(
            "SELECT * FROM g1s_volatility_5m_bars WHERE instrument=? "
            "AND bar_end_ts>? AND bar_end_ts<=? ORDER BY bar_end_ts",
            (instrument, captured_ts+1e-6, target_ts+1e-6)).fetchall()]
    by_end = {float(row["bar_end_ts"]): row for row in rows}
    if any(ts not in by_end for ts in expected):
        return None
    return [by_end[ts] for ts in expected]


def _secondary_1m(runtime: ShortHorizonRuntime, instrument: str, captured_ts: float,
                  target_ts: float, t0_close: float) -> tuple[float | None, int]:
    expected_n = int(round((target_ts-captured_ts)/60.0))
    expected_starts = [captured_ts+60.0*i for i in range(expected_n)]
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute(
            "SELECT bar_start_ts,bar_end_ts,close FROM g1s_volatility_raw_1m_bars "
            "WHERE instrument=? AND bar_start_ts>=? AND bar_end_ts<=? ORDER BY bar_start_ts",
            (instrument, captured_ts-1e-6, target_ts+1e-6)).fetchall()]
    by_start = {float(row["bar_start_ts"]): row for row in rows}
    if any(ts not in by_start for ts in expected_starts):
        return None, len(rows)
    closes = [float(t0_close)] + [float(by_start[ts]["close"]) for ts in expected_starts]
    returns = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))
               if closes[i] > 0 and closes[i-1] > 0]
    if len(returns) < 2:
        return None, len(returns)
    return float(statistics.pstdev(returns)), len(returns)


def _resolve_due(runtime: ShortHorizonRuntime, *, now: float, limit: int = 500) -> int:
    with runtime._lock:
        rows = [dict(row) for row in runtime._conn.execute("""
            SELECT o.* FROM g1s_volatility_observations o
            LEFT JOIN g1s_volatility_resolutions r USING(observation_id)
            WHERE r.observation_id IS NULL AND o.target_ts<=?
            ORDER BY o.target_ts LIMIT ?
        """, (now, max(1, min(int(limit), 2000)))).fetchall()]
    written = 0
    for row in rows:
        future = _exact_future_5m(
            runtime, str(row["instrument"]), float(row["captured_ts"]),
            float(row["target_ts"]))
        if future is None:
            if now <= float(row["target_ts"])+P3L_RESOLUTION_GRACE_SEC:
                continue
            payload = {
                "contract_version": P3L_TARGET_VERSION,
                "observation_id": str(row["observation_id"]),
                "resolution_status": "INSUFFICIENT_FUTURE_5M",
                "target": _p3.TARGET_FUTURE_RV,
                "future_data_used_after_t0_only": True,
                "historical_sampling_interval": "5m",
                "production_authority": False,
            }
            target_raw = _json(payload)
            values = (str(row["observation_id"]), "INSUFFICIENT_FUTURE_5M",
                      None, None, None, None, target_raw, _sha(target_raw),
                      P3L_TARGET_VERSION, now, time.time())
        else:
            closes = [float(row["t0_close"])] + [float(bar["close"]) for bar in future]
            returns = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
            future_rv5 = float(statistics.pstdev(returns))
            secondary, one_minute_n = _secondary_1m(
                runtime, str(row["instrument"]), float(row["captured_ts"]),
                float(row["target_ts"]), float(row["t0_close"]))
            payload = {
                "contract_version": P3L_TARGET_VERSION,
                "observation_id": str(row["observation_id"]),
                "resolution_status": "RESOLVED",
                "target": _p3.TARGET_FUTURE_RV,
                "future_realized_volatility_5m": future_rv5,
                "future_realized_volatility_1m_secondary": secondary,
                "future_5m_steps": len(returns),
                "future_1m_steps": one_minute_n,
                "source": "immutable_raw_yahoo_1m_aggregated_to_exact_5m",
                "future_data_used_after_t0_only": True,
                "primary_evidence_frequency": "5m",
                "secondary_1m_not_used_for_primary_edge": True,
                "production_authority": False,
            }
            target_raw = _json(payload)
            values = (str(row["observation_id"]), "RESOLVED", future_rv5,
                      secondary, len(returns), one_minute_n, target_raw,
                      _sha(target_raw), P3L_TARGET_VERSION, now, time.time())
        with runtime._lock, runtime._conn:
            cur = runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_volatility_resolutions("
                "observation_id,resolution_status,future_realized_volatility_5m,"
                "future_realized_volatility_1m_secondary,future_5m_steps,future_1m_steps,"
                "target_json,target_sha256,contract_version,resolved_ts,created_ts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)", values)
        written += int(cur.rowcount > 0)
    return written


def _metric_pair(rows: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, dict[str, float]], np.ndarray]:
    y = np.asarray([float(row["future_realized_volatility_5m"]) for row in rows], dtype=float)
    model_prediction = np.asarray([float(row["predicted_volatility_5m"]) for row in rows], dtype=float)
    weights, _ = _weights(rows)
    model_metrics = _p3._metrics(y, model_prediction, weights)
    baseline_names = sorted(json.loads(rows[0]["baseline_predictions_json"])) if rows else []
    baselines = {}
    for name in baseline_names:
        prediction = np.asarray([
            float(json.loads(row["baseline_predictions_json"])[name]) for row in rows], dtype=float)
        baselines[name] = _p3._metrics(y, prediction, weights)
    return model_metrics, baselines, weights


def _robust_blocks(rows: list[dict[str, Any]], n_blocks: int = 4) -> tuple[int, list[dict[str, Any]]]:
    if len(rows) < n_blocks:
        return 0, []
    ordered = sorted(rows, key=lambda row: (float(row["captured_ts"]), str(row["observation_id"])))
    chunks = np.array_split(np.arange(len(ordered)), n_blocks)
    passed = 0; reports = []
    for block_index, indices in enumerate(chunks, 1):
        block = [ordered[int(index)] for index in indices]
        if not block:
            continue
        model, baselines, _weights_array = _metric_pair(block)
        mae_name, best_mae = _p3._best(baselines, "mae")
        rmse_name, best_rmse = _p3._best(baselines, "rmse")
        joint = model["mae"] <= best_mae and model["rmse"] <= best_rmse
        passed += int(joint)
        reports.append({
            "block": block_index, "n": len(block),
            "first_captured_ts": float(block[0]["captured_ts"]),
            "last_captured_ts": float(block[-1]["captured_ts"]),
            "model_mae": model["mae"], "best_mae_baseline": mae_name,
            "best_mae": best_mae, "model_rmse": model["rmse"],
            "best_rmse_baseline": rmse_name, "best_rmse": best_rmse,
            "joint_non_degrade": joint,
        })
    return passed, reports


def _instrument_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["instrument"])].append(row)
    for instrument, group in sorted(grouped.items()):
        model, baselines, _ = _metric_pair(group)
        mae_name, best_mae = _p3._best(baselines, "mae")
        rmse_name, best_rmse = _p3._best(baselines, "rmse")
        out.append({
            "instrument": instrument, "n": len(group),
            "model_mae": model["mae"], "best_mae_baseline": mae_name,
            "mae_relative_improvement": (
                (best_mae-model["mae"])/best_mae if best_mae > _p3.EPS else None),
            "model_rmse": model["rmse"], "best_rmse_baseline": rmse_name,
            "rmse_relative_improvement": (
                (best_rmse-model["rmse"])/best_rmse if best_rmse > _p3.EPS else None),
            "descriptive_only": True,
        })
    return out


def _refresh_progress(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    result = {}
    for horizon in _p3.HORIZONS:
        with runtime._lock:
            rows = [dict(row) for row in runtime._conn.execute("""
                SELECT o.observation_id,o.instrument,o.horizon_minutes,o.captured_ts,
                       o.target_ts,p.predicted_volatility_5m,p.baseline_predictions_json,
                       r.future_realized_volatility_5m,r.resolved_ts
                FROM g1s_volatility_observations o
                JOIN g1s_volatility_predictions p USING(observation_id)
                JOIN g1s_volatility_resolutions r USING(observation_id)
                WHERE o.horizon_minutes=? AND o.evidence_eligible=1
                  AND r.resolution_status='RESOLVED'
                ORDER BY o.captured_ts,o.observation_id
            """, (int(horizon),)).fetchall()]
        if rows:
            model, baselines, weights = _metric_pair(rows)
            _, effective = _weights(rows)
            mae_name, best_mae = _p3._best(baselines, "mae")
            rmse_name, best_rmse = _p3._best(baselines, "rmse")
            mae_improvement = ((best_mae-model["mae"])/best_mae
                               if best_mae > _p3.EPS else None)
            rmse_improvement = ((best_rmse-model["rmse"])/best_rmse
                                if best_rmse > _p3.EPS else None)
            dates = {time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"])))
                     for row in rows}
            instruments = {str(row["instrument"]) for row in rows}
            families = {_ASSET_FAMILY.get(instrument, "other") for instrument in instruments}
            robust_n, robust_report = _robust_blocks(rows)
            observed = {
                "raw_resolved": len(rows), "effective_n": int(effective),
                "temporal_blocks": len(dates), "instrument_count": len(instruments),
                "asset_family_count": len(families),
            }
            sample_gate = all(int(observed[name]) >= int(required)
                              for name, required in P3L_SERIOUS_REQUIRED.items())
            metric_gate = bool(
                mae_improvement is not None and rmse_improvement is not None
                and mae_improvement >= P3L_METRIC_MARGIN
                and rmse_improvement >= P3L_METRIC_MARGIN)
            robust_gate = robust_n >= P3L_REQUIRED_ROBUST_BLOCKS
            verdict = ("INSUFFICIENT" if not sample_gate else
                       "YES" if metric_gate and robust_gate else "NO")
            latest_resolved = max(float(row["resolved_ts"]) for row in rows)
            instrument_report = _instrument_report(rows)
        else:
            model = {"mae": None, "rmse": None}; baselines = {}
            effective = 0; mae_name = rmse_name = None; best_mae = best_rmse = None
            mae_improvement = rmse_improvement = None
            robust_n = 0; robust_report = []; instrument_report = []
            observed = {name: 0 for name in P3L_SERIOUS_REQUIRED}
            verdict = "INSUFFICIENT"; latest_resolved = None
        with runtime._lock, runtime._conn:
            runtime._conn.execute("""
                INSERT INTO g1s_volatility_progress(
                    horizon_minutes,contract_version,raw_resolved,effective_n,temporal_blocks,
                    instrument_count,asset_family_count,robust_block_non_degrade_n,
                    model_mae,model_rmse,best_mae_baseline,best_mae,best_rmse_baseline,best_rmse,
                    mae_relative_improvement,rmse_relative_improvement,verdict,latest_resolved_ts,
                    updated_ts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(horizon_minutes) DO UPDATE SET
                    contract_version=excluded.contract_version,
                    raw_resolved=excluded.raw_resolved,effective_n=excluded.effective_n,
                    temporal_blocks=excluded.temporal_blocks,
                    instrument_count=excluded.instrument_count,
                    asset_family_count=excluded.asset_family_count,
                    robust_block_non_degrade_n=excluded.robust_block_non_degrade_n,
                    model_mae=excluded.model_mae,model_rmse=excluded.model_rmse,
                    best_mae_baseline=excluded.best_mae_baseline,best_mae=excluded.best_mae,
                    best_rmse_baseline=excluded.best_rmse_baseline,best_rmse=excluded.best_rmse,
                    mae_relative_improvement=excluded.mae_relative_improvement,
                    rmse_relative_improvement=excluded.rmse_relative_improvement,
                    verdict=excluded.verdict,latest_resolved_ts=excluded.latest_resolved_ts,
                    updated_ts=excluded.updated_ts
            """, (int(horizon), P3L_PROGRESS_VERSION,
                    int(observed["raw_resolved"]), int(observed["effective_n"]),
                    int(observed["temporal_blocks"]), int(observed["instrument_count"]),
                    int(observed["asset_family_count"]), int(robust_n),
                    model["mae"], model["rmse"], mae_name, best_mae, rmse_name, best_rmse,
                    mae_improvement, rmse_improvement, verdict, latest_resolved, time.time()))
        result[int(horizon)] = {
            **observed, "robust_block_non_degrade_n": robust_n,
            "model": model, "baselines": baselines,
            "best_mae_baseline": mae_name, "best_rmse_baseline": rmse_name,
            "mae_relative_improvement": mae_improvement,
            "rmse_relative_improvement": rmse_improvement,
            "verdict": verdict, "robust_blocks": robust_report,
            "instrument_heterogeneity": instrument_report,
        }
    return result


def _status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    _ensure_tables(runtime)
    state = _state(runtime)
    with runtime._lock:
        models = [dict(row) for row in runtime._conn.execute(
            "SELECT model_id,source_set_sha256,horizon_minutes,training_cutoff_ts,raw_n,"
            "effective_n,historical_proof_id,authority,auto_promotion,production_used,created_ts "
            "FROM g1s_volatility_models WHERE contract_version=? ORDER BY horizon_minutes",
            (P3L_MODEL_VERSION,)).fetchall()]
        progress = [dict(row) for row in runtime._conn.execute(
            "SELECT * FROM g1s_volatility_progress ORDER BY horizon_minutes").fetchall()]
        latest_prediction = runtime._conn.execute(
            "SELECT MAX(created_ts) ts,COUNT(*) n FROM g1s_volatility_predictions").fetchone()
        pending = runtime._conn.execute("""
            SELECT COUNT(*) n FROM g1s_volatility_observations o
            LEFT JOIN g1s_volatility_resolutions r USING(observation_id)
            WHERE r.observation_id IS NULL
        """).fetchone()["n"]
    return {
        "contract_version": P3L_CONTRACT_VERSION,
        "evidence_label": P3L_EVIDENCE_LABEL,
        "target": _p3.TARGET_FUTURE_RV,
        "historical_parent": {
            "p3": _p3.P3_CONTRACT_VERSION,
            "p3b": _p3b.P3B_CONTRACT_VERSION,
            "state": state.get("historical_state"),
            "source_set_sha256": state.get("historical_source_set_sha256"),
            "last_error": state.get("last_proof_error"),
        },
        "models": models,
        "progress": progress,
        "latest_prediction_ts": latest_prediction["ts"],
        "prediction_count": int(latest_prediction["n"] or 0),
        "pending_resolutions": int(pending or 0),
        "serious_oos_required": dict(P3L_SERIOUS_REQUIRED),
        "metric_margin_required": P3L_METRIC_MARGIN,
        "robust_blocks_required": P3L_REQUIRED_ROBUST_BLOCKS,
        "max_prediction_latency_sec": P3L_MAX_PREDICTION_LATENCY_SEC,
        "live_5m_source": "exact aggregation of five frozen raw Yahoo 1m bars",
        "historical_5m_source": "Yahoo native 5m bars",
        "frequency_parity": True,
        "native_vs_aggregated_bar_parity_verified": False,
        "primary_live_target": "future_realized_volatility_5m",
        "secondary_diagnostic_target": "future_realized_volatility_1m",
        "secondary_target_used_for_edge": False,
        "instrument_heterogeneity_descriptive_only": True,
        "posthoc_instrument_selection_allowed": False,
        "request_time_network_fetch": False,
        "request_time_full_history_scan": False,
        "historical_options_used": False,
        "auto_refit": False,
        "auto_promotion": False,
        "production_authority": False,
        "edge_claim_allowed": False,
        "state": state,
    }


def _run_cycle(runtime: ShortHorizonRuntime, passive, *, now: float | None = None) -> dict[str, Any]:
    _ensure_tables(runtime)
    now = float(now or time.time())
    historical = _materialize_historical_proof_and_models(runtime)
    raw = _ingest_raw_bars(runtime, passive, now=now)
    bars5 = _aggregate_5m(runtime, now=now)
    predictions = _create_predictions(runtime, now=now) if _proof_and_models_ready(runtime) else 0
    resolutions = _resolve_due(runtime, now=now)
    progress = _refresh_progress(runtime) if (predictions or resolutions) else {}
    _update_state(runtime, last_cycle_ts=now, last_cycle_error=None,
                  raw_1m_rows_ingested=int(_state(runtime).get("raw_1m_rows_ingested") or 0)+raw,
                  bars_5m_created=int(_state(runtime).get("bars_5m_created") or 0)+bars5,
                  observations_created=int(_state(runtime).get("observations_created") or 0)+predictions,
                  resolutions_created=int(_state(runtime).get("resolutions_created") or 0)+resolutions)
    return {
        "contract_version": P3L_CONTRACT_VERSION,
        "historical": historical,
        "raw_1m_rows_ingested": raw,
        "bars_5m_created": bars5,
        "observations_created": predictions,
        "resolutions_created": resolutions,
        "progress_refreshed": bool(progress),
        "production_authority": False,
    }


def install_g1_short_horizon_p3_live_runtime() -> None:
    if getattr(ShortHorizonRuntime, "_p3_live_runtime_version", None) == P3L_CONTRACT_VERSION:
        return
    previous_init = ShortHorizonRuntime.__init__
    previous_status = ShortHorizonRuntime.status

    def runtime_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        _ensure_tables(self)

    def status(self):
        report = previous_status(self)
        report["volatility_live_oos"] = _status(self)
        return report

    ShortHorizonRuntime.__init__ = runtime_init
    ShortHorizonRuntime.materialize_volatility_historical_proof = _materialize_historical_proof_and_models
    ShortHorizonRuntime.run_volatility_live_cycle = _run_cycle
    ShortHorizonRuntime.volatility_live_status = _status
    ShortHorizonRuntime.refresh_volatility_progress = _refresh_progress
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._p3_live_runtime_version = P3L_CONTRACT_VERSION

    _storage.CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_storage.CRITICAL_TABLES, *P3L_CRITICAL_TABLES)))
    _integration.G1S_CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_integration.G1S_CRITICAL_TABLES, *P3L_CRITICAL_TABLES)))
