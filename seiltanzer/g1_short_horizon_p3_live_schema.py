"""Schema and fixed contracts for prospective P3 volatility validation."""
from __future__ import annotations

import time
from typing import Any


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
P3L_METRIC_MARGIN = 0.005
P3L_REQUIRED_ROBUST_BLOCKS = 3
P3L_SERIOUS_REQUIRED = {
    "raw_resolved": 1000,
    "effective_n": 400,
    "temporal_blocks": 20,
    "instrument_count": 6,
    "asset_family_count": 3,
}

P3L_CRITICAL_TABLES = (
    "g1s_volatility_raw_1m_bars",
    "g1s_volatility_5m_bars",
    "g1s_volatility_historical_proofs",
    "g1s_volatility_models",
    "g1s_volatility_observations",
    "g1s_volatility_predictions",
    "g1s_volatility_resolutions",
)

ASSET_FAMILY = {
    "NAS100": "equity", "SP500": "equity", "US30": "equity",
    "GER40": "equity", "UK100": "equity", "JPY100": "equity",
    "XAU": "metal", "XAG": "metal",
    "EURUSD": "fx", "USDCAD": "fx",
}


def ensure_p3l_tables(runtime) -> None:
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
                contract_version TEXT NOT NULL,
                UNIQUE(contract_version,horizon_minutes)
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
                model_mae REAL, model_rmse REAL,
                best_mae_baseline TEXT, best_mae REAL,
                best_rmse_baseline TEXT, best_rmse REAL,
                mae_relative_improvement REAL, rmse_relative_improvement REAL,
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


def p3l_state(runtime) -> dict[str, Any]:
    ensure_p3l_tables(runtime)
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT * FROM g1s_volatility_state WHERE id=1").fetchone()
    return dict(row) if row is not None else {}


def update_p3l_state(runtime, **updates: Any) -> None:
    ensure_p3l_tables(runtime)
    updates = dict(updates); updates["updated_ts"] = time.time()
    assignments = ",".join(f"{key}=?" for key in updates)
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            f"UPDATE g1s_volatility_state SET {assignments} WHERE id=1",
            tuple(updates.values()))
