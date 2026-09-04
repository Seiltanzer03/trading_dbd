"""Phase G.1S: fast physical-market learning on frozen fixed-horizon observations.

G.1S deliberately does *not* reinterpret option-native Q.  It materializes the
already prospective 15/30/60/120/240 minute rows produced by PassiveLearningEngine,
keeps their frozen T0 bytes, consumes only independently resolved future outcomes,
and provides dependency-adjusted baselines plus immutable shadow logistic models.
Nothing in this module has production decision authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from collections import defaultdict
from typing import Any

import numpy as np

from . import passive_learning as _pl
from .config import INSTRUMENTS


G1S_STAGE = "G.1S"
G1S_CONTRACT_VERSION = "g1s-short-horizon-v1"
G1S_OBSERVATION_VERSION = "g1s-observation-v1"
G1S_RESOLUTION_VERSION = "g1s-resolution-v1"
G1S_DEPENDENCY_VERSION = "g1s-overlap-bucket-v1"
G1S_MODEL_VERSION = "g1s-logistic-shadow-v1"
G1S_PREDICTION_VERSION = "g1s-prospective-shadow-prediction-v1"
G1S_Q_AUDIT_VERSION = "g1s-q-resolution-audit-v1"
G1S_TRADE_RELEVANCE_VERSION = "g1s-trade-relevance-v1"
G1S_MATERIALIZER_VERSION = "g1s-incremental-materializer-v1"

HORIZONS = (15, 30, 60, 120, 240)
PRIMARY_HORIZONS = (15, 30, 60)
DIRECTION_EPSILON = 0.0002  # 2 bp; versioned by G1S_RESOLUTION_VERSION.
MODEL_REFIT_INTERVAL_SEC = 6 * 60 * 60
MODEL_REFIT_MIN_EFFECTIVE_DELTA = 10
TRADE_LINK_MAX_AGE_SEC = 20 * 60

FIT_REQUIRED = {
    "raw_resolved": 120,
    "effective_n": 60,
    "positive_n": 20,
    "negative_n": 20,
    "trading_days": 3,
}
OOS_CANDIDATE_REQUIRED = {
    "raw_resolved": 500,
    "effective_n": 200,
    "positive_n": 60,
    "negative_n": 60,
    "temporal_blocks": 5,
}

FEATURE_SETS = {
    "PRICE_ONLY_V1": ("sigma_h", "annual_vol", "price_quality"),
    "PRICE_OPTIONS_V1": (
        "sigma_h", "annual_vol", "price_quality", "option_available",
        "option_quality", "option_skew", "option_width",
    ),
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(str(value)) if value is not None else default
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clip_probability(value: float) -> float:
    return max(1e-9, min(1.0 - 1e-9, float(value)))


def _brier(ps: list[float], ys: list[int]) -> float | None:
    if not ps:
        return None
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def _logloss(ps: list[float], ys: list[int]) -> float | None:
    if not ps:
        return None
    return -sum(y * math.log(_clip_probability(p)) +
                (1-y) * math.log(_clip_probability(1-p))
                for p, y in zip(ps, ys)) / len(ps)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))


def _fit_logistic(x: np.ndarray, y: np.ndarray, *, l2: float = 0.25) -> np.ndarray:
    """Deterministic small Newton solver; intercept is not regularized."""
    design = np.column_stack([np.ones(len(x)), x])
    beta = np.zeros(design.shape[1], dtype=float)
    reg = np.eye(design.shape[1], dtype=float) * l2
    reg[0, 0] = 0.0
    for _ in range(80):
        p = _sigmoid(design @ beta)
        w = np.maximum(p * (1.0 - p), 1e-6)
        grad = design.T @ (p - y) + reg @ beta
        hess = design.T @ (w[:, None] * design) + reg
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hess) @ grad
        beta -= step
        if float(np.linalg.norm(step)) < 1e-8:
            break
    return beta


class ShortHorizonRuntime:
    def __init__(self, engine):
        self.engine = engine
        self.passive = engine.passive
        self._conn = self.passive._conn
        self._lock = self.passive._lock
        self._last_step_started: float | None = None
        self._last_step_finished: float | None = None
        self._last_step_duration_ms: float | None = None
        self._last_error: str | None = None
        self._cached_materializer_items: list[dict[str, Any]] = []
        self._ensure_tables()
        self._refresh_materializer_cache_under_lock()
        self.activation_ts = self._activation_ts()

    def _refresh_materializer_cache_under_lock(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT * FROM g1s_materialization_state ORDER BY materializer"
            ).fetchall()
            self._cached_materializer_items = [dict(r) for r in rows]
        except Exception:
            pass

    # ---------------------------------------------------------------- schema
    def _ensure_tables(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_runtime_activation(
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    activation_ts REAL NOT NULL,
                    contract_version TEXT NOT NULL
                )""")
            self._conn.execute(
                "INSERT OR IGNORE INTO g1s_runtime_activation(id,activation_ts,contract_version) "
                "VALUES(1,?,?)", (time.time(), G1S_CONTRACT_VERSION))
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_materialization_state(
                    materializer TEXT PRIMARY KEY,
                    contract_version TEXT NOT NULL,
                    source_watermark INTEGER NOT NULL DEFAULT 0,
                    last_started_ts REAL,
                    last_success_ts REAL,
                    last_duration_ms REAL,
                    processed_n INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_observations(
                    observation_id TEXT PRIMARY KEY,
                    source_observation_id TEXT NOT NULL UNIQUE,
                    source_rowid INTEGER NOT NULL UNIQUE,
                    captured_ts REAL NOT NULL,
                    target_ts REAL NOT NULL,
                    instrument TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    market_price REAL NOT NULL,
                    price_source TEXT,
                    price_kind TEXT,
                    price_quality REAL,
                    option_source TEXT,
                    option_kind TEXT,
                    option_quality REAL,
                    market_regime TEXT,
                    session TEXT,
                    source_feature_contract TEXT NOT NULL,
                    source_forecast_contract TEXT NOT NULL,
                    features_sha256 TEXT NOT NULL,
                    forecast_sha256 TEXT NOT NULL,
                    t0_sha256 TEXT NOT NULL,
                    measurement_eligible INTEGER NOT NULL,
                    training_eligible INTEGER NOT NULL,
                    oos_eligible INTEGER NOT NULL,
                    exclusion_reason TEXT,
                    frozen_features_json TEXT NOT NULL,
                    frozen_forecast_json TEXT NOT NULL,
                    created_ts REAL NOT NULL
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_obs_horizon_capture "
                "ON g1s_observations(horizon_minutes,captured_ts)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_obs_instrument_horizon "
                "ON g1s_observations(instrument,horizon_minutes,captured_ts)")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_obs_captured_ts "
                "ON g1s_observations(captured_ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_resolutions(
                    observation_id TEXT PRIMARY KEY,
                    source_observation_id TEXT NOT NULL UNIQUE,
                    resolved_ts REAL NOT NULL,
                    terminal_log_return REAL NOT NULL,
                    direction_label TEXT NOT NULL,
                    mfe_log_return REAL,
                    mae_log_return REAL,
                    path_quality_status TEXT,
                    source_outcome_sha256 TEXT NOT NULL,
                    resolution_json TEXT NOT NULL,
                    resolution_sha256 TEXT NOT NULL,
                    created_ts REAL NOT NULL
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_resolution_created "
                "ON g1s_resolutions(resolved_ts,observation_id)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_models(
                    model_id TEXT PRIMARY KEY,
                    model_family TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    feature_set TEXT NOT NULL,
                    training_cutoff_ts REAL NOT NULL,
                    raw_n INTEGER NOT NULL,
                    effective_n REAL NOT NULL,
                    positive_n INTEGER NOT NULL,
                    negative_n INTEGER NOT NULL,
                    training_days INTEGER NOT NULL,
                    parameters_json TEXT NOT NULL,
                    artifact_sha256 TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    created_ts REAL NOT NULL
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_model_horizon_created "
                "ON g1s_models(horizon_minutes,created_ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_shadow_predictions(
                    prediction_id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    p_up REAL NOT NULL,
                    prediction_json TEXT NOT NULL,
                    prediction_sha256 TEXT NOT NULL,
                    production_used INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(observation_id,model_id)
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_pred_observation "
                "ON g1s_shadow_predictions(observation_id,created_ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_trade_links(
                    link_id TEXT PRIMARY KEY,
                    trade_id INTEGER NOT NULL,
                    observation_id TEXT NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    forecast_age_sec REAL NOT NULL,
                    link_json TEXT NOT NULL,
                    link_sha256 TEXT NOT NULL,
                    created_ts REAL NOT NULL,
                    UNIQUE(trade_id,horizon_minutes)
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS g1s_contract_errors(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    observation_id TEXT,
                    code TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    critical INTEGER NOT NULL DEFAULT 0
                )""")
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_g1s_error_code_ts "
                "ON g1s_contract_errors(code,ts)")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS research_materialization_state(
                    materializer TEXT PRIMARY KEY,
                    contract_version TEXT NOT NULL,
                    last_started_ts REAL,
                    last_finished_ts REAL,
                    last_success_ts REAL,
                    duration_ms REAL,
                    processed_n INTEGER NOT NULL DEFAULT 0,
                    source_watermark TEXT,
                    lag_sec REAL,
                    state TEXT NOT NULL,
                    last_error TEXT
                )""")
            for table in (
                "g1s_runtime_activation", "g1s_observations", "g1s_resolutions",
                "g1s_models", "g1s_shadow_predictions", "g1s_trade_links",
                "g1s_contract_errors",
            ):
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1S row'); END""")
                self._conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT,'immutable G1S row'); END""")

    def _activation_ts(self) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT activation_ts FROM g1s_runtime_activation WHERE id=1").fetchone()
        return float(row[0])

    def close(self) -> None:
        # Shares PassiveLearningEngine connection; its owner closes it.
        return None

    def _error(self, code: str, detail: str, *, observation_id: str | None = None,
               critical: bool = False) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO g1s_contract_errors(ts,observation_id,code,detail,critical) "
                "VALUES(?,?,?,?,?)",
                (time.time(), observation_id, code, str(detail)[:2000], int(critical)))

    # ---------------------------------------------------------- materializer
    def _watermark(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT source_watermark FROM g1s_materialization_state "
                "WHERE materializer='fixed_horizon_t0'").fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def _admission(row: sqlite3.Row, forecast: dict) -> tuple[str, bool, str | None]:
        if forecast.get("horizon_kind") != "fixed_trading_time":
            return "EXCLUDED", False, "NOT_FIXED_TRADING_HORIZON"
        if int(row["horizon_minutes"]) not in HORIZONS:
            return "EXCLUDED", False, "HORIZON_NOT_IN_G1S_V1"
        if int(row["retrospective_replay"] or 0) != 0:
            return "RESEARCH_BACKFILL", False, "RETROSPECTIVE_REPLAY"
        if str(row["observation_origin"] or "") != "background_collector":
            return "RESEARCH_BACKFILL", False, "NON_BACKGROUND_ORIGIN"
        if str(row["feature_contract_version"] or "") != _pl.PASSIVE_SCHEMA_VERSION:
            return "PREEXISTING_PROSPECTIVE", False, "OLD_SOURCE_CONTRACT"
        if str(row["forecast_model_version"] or "") != _pl.FORECAST_VERSION:
            return "PREEXISTING_PROSPECTIVE", False, "OLD_FORECAST_CONTRACT"
        if int(row["evidence_eligible"] or 0) != 1:
            return "PREEXISTING_PROSPECTIVE", False, "SOURCE_EVIDENCE_INELIGIBLE"
        if str(row["price_kind"] or "") != "direct":
            return "PREEXISTING_PROSPECTIVE", False, "TARGET_PRICE_NON_DIRECT"
        quality = _finite(row["price_quality"])
        if quality is None or quality < 0.90:
            return "PREEXISTING_PROSPECTIVE", False, "TARGET_PRICE_LOW_QUALITY"
        return "PREEXISTING_PROSPECTIVE", True, None

    def materialize_new(self, limit: int = 2000) -> int:
        watermark = self._watermark()
        started = time.time()
        with self._lock:
            rows = self._conn.execute(
                "SELECT rowid AS source_rowid,* FROM passive_market_observations "
                "WHERE rowid>? ORDER BY rowid LIMIT ?",
                (watermark, max(1, min(int(limit), 10000))),
            ).fetchall()
        processed = 0
        last_rowid = watermark
        for row in rows:
            last_rowid = max(last_rowid, int(row["source_rowid"]))
            forecast_raw = str(row["forecast_json"])
            features_raw = str(row["features_json"])
            forecast = _loads(forecast_raw, {})
            if not isinstance(forecast, dict):
                continue
            origin, eligible, exclusion = self._admission(row, forecast)
            if forecast.get("horizon_kind") != "fixed_trading_time" or int(row["horizon_minutes"]) not in HORIZONS:
                continue
            if float(row["captured_ts"]) >= self.activation_ts - 1e-9 and origin == "PREEXISTING_PROSPECTIVE":
                origin = "LIVE_PROSPECTIVE"
            feature_sha = _sha_text(features_raw)
            forecast_sha = _sha_text(forecast_raw)
            t0_payload = {
                "source_observation_id": str(row["observation_id"]),
                "captured_ts": float(row["captured_ts"]),
                "target_ts": float(row["target_ts"]),
                "instrument": str(row["instrument"]),
                "horizon_minutes": int(row["horizon_minutes"]),
                "market_price": float(row["market_price"]),
                "features_sha256": feature_sha,
                "forecast_sha256": forecast_sha,
                "source_feature_contract": str(row["feature_contract_version"]),
                "source_forecast_contract": str(row["forecast_model_version"]),
            }
            obs_id = "g1s-" + hashlib.sha256(
                f"{row['observation_id']}|{G1S_OBSERVATION_VERSION}".encode()).hexdigest()[:32]
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1s_observations("
                    "observation_id,source_observation_id,source_rowid,captured_ts,target_ts,"
                    "instrument,horizon_minutes,origin,market_price,price_source,price_kind,"
                    "price_quality,option_source,option_kind,option_quality,market_regime,session,"
                    "source_feature_contract,source_forecast_contract,features_sha256,"
                    "forecast_sha256,t0_sha256,measurement_eligible,training_eligible,oos_eligible,"
                    "exclusion_reason,frozen_features_json,frozen_forecast_json,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (obs_id, str(row["observation_id"]), int(row["source_rowid"]),
                     float(row["captured_ts"]), float(row["target_ts"]), str(row["instrument"]),
                     int(row["horizon_minutes"]), origin, float(row["market_price"]),
                     row["price_source"], row["price_kind"], _finite(row["price_quality"]),
                     row["option_source"], row["option_kind"], _finite(row["option_quality"]),
                     row["market_regime"], row["session"], str(row["feature_contract_version"]),
                     str(row["forecast_model_version"]), feature_sha, forecast_sha,
                     _sha_text(_json(t0_payload)), int(eligible), int(eligible), int(eligible),
                     exclusion, features_raw, forecast_raw, time.time()))
            self._create_prospective_predictions(obs_id, float(row["captured_ts"]),
                                                 int(row["horizon_minutes"]))
            processed += 1
        duration = (time.time() - started) * 1000.0
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO g1s_materialization_state(materializer,contract_version,"
                "source_watermark,last_started_ts,last_success_ts,last_duration_ms,processed_n,last_error)"
                " VALUES('fixed_horizon_t0',?,?,?,?,?,?,NULL) "
                "ON CONFLICT(materializer) DO UPDATE SET contract_version=excluded.contract_version,"
                "source_watermark=excluded.source_watermark,last_started_ts=excluded.last_started_ts,"
                "last_success_ts=excluded.last_success_ts,last_duration_ms=excluded.last_duration_ms,"
                "processed_n=g1s_materialization_state.processed_n+excluded.processed_n,last_error=NULL",
                (G1S_MATERIALIZER_VERSION, last_rowid, started, time.time(), duration, processed))
            self._refresh_materializer_cache_under_lock()
        return processed

    # ------------------------------------------------------------- resolution
    def _path_excursions(self, instrument: str, captured: float, target: float,
                         start: float) -> tuple[float | None, float | None]:
        with self._lock:
            bars = self._conn.execute(
                "SELECT high,low FROM passive_market_bars WHERE instrument=? "
                "AND bar_start_ts>=? AND bar_end_ts<=? ORDER BY bar_start_ts",
                (instrument, captured-1e-6, target+1e-6)).fetchall()
            if bars:
                highs = [math.log(float(r["high"]) / start) for r in bars if float(r["high"]) > 0]
                lows = [math.log(float(r["low"]) / start) for r in bars if float(r["low"]) > 0]
                return (max(highs) if highs else None, min(lows) if lows else None)
            points = self._conn.execute(
                "SELECT price FROM passive_market_path WHERE instrument=? AND ts>=? AND ts<=?",
                (instrument, captured-1e-6, target+1e-6)).fetchall()
        vals = [math.log(float(r["price"]) / start) for r in points if float(r["price"]) > 0]
        return (max(vals) if vals else None, min(vals) if vals else None)

    def resolve_new(self, limit: int = 2000) -> int:
        with self._lock:
            rows = self._conn.execute("""
                SELECT g.*,p.resolved_ts AS source_resolved_ts,p.outcome_json
                FROM g1s_observations g
                JOIN passive_market_observations p
                  ON p.observation_id=g.source_observation_id
                LEFT JOIN g1s_resolutions r ON r.observation_id=g.observation_id
                WHERE r.observation_id IS NULL AND p.resolution_status='resolved'
                  AND p.outcome_json IS NOT NULL
                ORDER BY p.resolved_ts,g.source_rowid LIMIT ?
            """, (max(1, min(int(limit), 10000)),)).fetchall()
        done = 0
        for row in rows:
            with self._lock:
                src = self._conn.execute(
                    "SELECT features_json,forecast_json FROM passive_market_observations "
                    "WHERE observation_id=?", (row["source_observation_id"],)).fetchone()
            if src is None or _sha_text(str(src["features_json"])) != str(row["features_sha256"]) \
                    or _sha_text(str(src["forecast_json"])) != str(row["forecast_sha256"]):
                self._error("T0_HASH_MISMATCH", "source T0 bytes changed",
                            observation_id=str(row["observation_id"]), critical=True)
                continue
            outcome_raw = str(row["outcome_json"])
            outcome = _loads(outcome_raw, {})
            terminal = outcome.get("terminal") if isinstance(outcome, dict) else {}
            logret = _finite((terminal or {}).get("terminal_log_return"))
            if logret is None:
                logret = _finite(outcome.get("future_log_return")) if isinstance(outcome, dict) else None
            if logret is None:
                self._error("MISSING_TERMINAL_RETURN", "resolved source lacks terminal return",
                            observation_id=str(row["observation_id"]))
                continue
            direction = "UP" if logret > DIRECTION_EPSILON else (
                "DOWN" if logret < -DIRECTION_EPSILON else "FLAT")
            mfe, mae = self._path_excursions(
                str(row["instrument"]), float(row["captured_ts"]),
                float(row["target_ts"]), float(row["market_price"]))
            payload = {
                "contract_version": G1S_RESOLUTION_VERSION,
                "observation_id": str(row["observation_id"]),
                "source_observation_id": str(row["source_observation_id"]),
                "terminal_log_return": logret,
                "direction_label": direction,
                "direction_epsilon": DIRECTION_EPSILON,
                "mfe_log_return": mfe,
                "mae_log_return": mae,
                "path_quality_status": outcome.get("path_quality_status") if isinstance(outcome, dict) else None,
                "source_outcome_sha256": _sha_text(outcome_raw),
                "source_resolver_version": outcome.get("version") if isinstance(outcome, dict) else None,
                "future_data_source": "independently_resolved_passive_market_observation",
            }
            raw = _json(payload)
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1s_resolutions("
                    "observation_id,source_observation_id,resolved_ts,terminal_log_return,"
                    "direction_label,mfe_log_return,mae_log_return,path_quality_status,"
                    "source_outcome_sha256,resolution_json,resolution_sha256,created_ts) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row["observation_id"], row["source_observation_id"],
                     float(row["source_resolved_ts"] or time.time()), logret, direction, mfe, mae,
                     payload["path_quality_status"], payload["source_outcome_sha256"], raw,
                     _sha_text(raw), time.time()))
            done += 1
        return done

    # --------------------------------------------------------- evidence math
    @staticmethod
    def _dependency_key(row: dict) -> str:
        horizon = int(row["horizon_minutes"])
        bucket = int(float(row["captured_ts"]) // (horizon * 60.0))
        return f"{row['instrument']}|{horizon}|{bucket}"

    def _resolved_eligible(self, horizon: int | None = None) -> list[dict]:
        where = "g.training_eligible=1 AND r.direction_label!='FLAT'"
        params: list[Any] = []
        if horizon is not None:
            where += " AND g.horizon_minutes=?"
            params.append(int(horizon))
        with self._lock:
            rows = self._conn.execute(f"""
                SELECT g.*,r.terminal_log_return,r.direction_label,r.mfe_log_return,r.mae_log_return,
                       r.resolved_ts
                FROM g1s_observations g JOIN g1s_resolutions r USING(observation_id)
                WHERE {where} ORDER BY g.captured_ts,g.observation_id
            """, params).fetchall()
        return [dict(row) for row in rows]

    def _evidence(self, rows: list[dict]) -> dict:
        groups = {self._dependency_key(row) for row in rows}
        pos = sum(row["direction_label"] == "UP" for row in rows)
        neg = sum(row["direction_label"] == "DOWN" for row in rows)
        days = {time.strftime("%Y-%m-%d", time.gmtime(float(row["captured_ts"]))) for row in rows}
        observed = {
            "raw_resolved": len(rows), "effective_n": len(groups),
            "positive_n": pos, "negative_n": neg, "trading_days": len(days),
        }
        fit_blockers = [k for k, req in FIT_REQUIRED.items() if observed.get(k, 0) < req]
        return {**observed, "dependency_groups": len(groups),
                "fit_required": dict(FIT_REQUIRED), "fit_blockers": fit_blockers,
                "fit_allowed": not fit_blockers}

    @staticmethod
    def _feature_vector(row: dict, feature_set: str) -> tuple[list[float], dict]:
        features = _loads(row.get("frozen_features_json"), {})
        forecast = _loads(row.get("frozen_forecast_json"), {})
        vol = features.get("volatility") or {}
        option = features.get("option_distribution") or {}
        values = {
            "sigma_h": _finite(forecast.get("sigma_h_return")),
            "annual_vol": _finite(forecast.get("reference_volatility_annual"))
                          or _finite(vol.get("reference_volatility_annual")),
            "price_quality": _finite(row.get("price_quality")),
            "option_available": 1.0 if (features.get("options") or {}).get("available") else 0.0,
            "option_quality": _finite(row.get("option_quality")),
            "option_skew": _finite(forecast.get("skew")) or _finite(option.get("skew")),
            "option_width": _finite(forecast.get("option_implied_width"))
                            or _finite(option.get("implied_move_frac")),
        }
        vector = []
        for name in FEATURE_SETS[feature_set]:
            value = values.get(name)
            vector.append(0.0 if value is None else float(value))
        # Static instrument one-hot keeps dimensions stable and is frozen identity, not future data.
        instruments = tuple(INSTRUMENTS)
        vector.extend(1.0 if row["instrument"] == code else 0.0 for code in instruments[1:])
        return vector, values

    def _training_arrays(self, rows: list[dict], feature_set: str):
        xs, ys = [], []
        for row in rows:
            vector, _ = self._feature_vector(row, feature_set)
            xs.append(vector)
            ys.append(1 if row["direction_label"] == "UP" else 0)
        return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)

    def _chronological_diagnostics(self, rows: list[dict], feature_set: str) -> dict:
        if len(rows) < 30:
            return {"status": "INSUFFICIENT", "historical_walk_forward": False}
        ordered = sorted(rows, key=lambda r: (float(r["captured_ts"]), r["observation_id"]))
        split = max(10, int(len(ordered) * 0.70))
        test_start = float(ordered[split]["captured_ts"]) if split < len(ordered) else None
        if test_start is None:
            return {"status": "INSUFFICIENT", "historical_walk_forward": False}
        train = [r for r in ordered[:split] if float(r["target_ts"]) < test_start]
        test = ordered[split:]
        if len(train) < 20 or len(test) < 10:
            return {"status": "INSUFFICIENT_AFTER_PURGE", "historical_walk_forward": True,
                    "train_n": len(train), "test_n": len(test)}
        x_train, y_train = self._training_arrays(train, feature_set)
        mean = x_train.mean(axis=0)
        std = x_train.std(axis=0)
        std[std < 1e-12] = 1.0
        beta = _fit_logistic((x_train - mean) / std, y_train)
        x_test, y_test = self._training_arrays(test, feature_set)
        probs = _sigmoid(np.column_stack([np.ones(len(x_test)), (x_test-mean)/std]) @ beta)
        p = [float(v) for v in probs]
        y = [int(v) for v in y_test]
        base_rate = float(y_train.mean()) if len(y_train) else 0.5
        base = [base_rate] * len(y)
        return {
            "status": "HISTORICAL_PURGED_TEST",
            "historical_walk_forward": True,
            "prospective_oos": False,
            "oos_validated": False,
            "random_shuffle": False,
            "purge_applied": True,
            "train_n": len(train), "test_n": len(test),
            "model_brier": _brier(p, y), "model_log_loss": _logloss(p, y),
            "base_rate": base_rate, "base_brier": _brier(base, y),
            "base_log_loss": _logloss(base, y),
            "delta_brier_vs_base": ((_brier(base, y) or 0) - (_brier(p, y) or 0)),
        }

    def fit_if_ready(self, *, force: bool = False) -> int:
        created = 0
        now = time.time()
        for horizon in HORIZONS:
            rows = self._resolved_eligible(horizon)
            evidence = self._evidence(rows)
            if not evidence["fit_allowed"]:
                continue
            for feature_set in FEATURE_SETS:
                with self._lock:
                    latest = self._conn.execute(
                        "SELECT created_ts,effective_n FROM g1s_models WHERE horizon_minutes=? "
                        "AND feature_set=? ORDER BY created_ts DESC LIMIT 1",
                        (horizon, feature_set)).fetchone()
                if latest and not force:
                    if now - float(latest["created_ts"]) < MODEL_REFIT_INTERVAL_SEC:
                        continue
                    if evidence["effective_n"] - float(latest["effective_n"]) < MODEL_REFIT_MIN_EFFECTIVE_DELTA:
                        continue
                x, y = self._training_arrays(rows, feature_set)
                mean = x.mean(axis=0); std = x.std(axis=0); std[std < 1e-12] = 1.0
                beta = _fit_logistic((x-mean)/std, y)
                cutoff = max(float(r["resolved_ts"]) for r in rows)
                params = {
                    "intercept_and_coefficients": [float(v) for v in beta],
                    "feature_mean": [float(v) for v in mean],
                    "feature_std": [float(v) for v in std],
                    "feature_names": list(FEATURE_SETS[feature_set]) +
                        [f"instrument:{code}" for code in tuple(INSTRUMENTS)[1:]],
                    "l2": 0.25,
                }
                diagnostics = self._chronological_diagnostics(rows, feature_set)
                artifact = {
                    "contract_version": G1S_MODEL_VERSION,
                    "model_family": "REGULARIZED_LOGISTIC",
                    "horizon_minutes": horizon,
                    "feature_set": feature_set,
                    "training_cutoff_ts": cutoff,
                    "source_observation_ids": [r["observation_id"] for r in rows],
                    "parameters": params,
                }
                artifact_sha = _sha_text(_json(artifact))
                model_id = "g1s-model-" + artifact_sha[:28]
                with self._lock, self._conn:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO g1s_models(model_id,model_family,horizon_minutes,"
                        "feature_set,training_cutoff_ts,raw_n,effective_n,positive_n,negative_n,"
                        "training_days,parameters_json,artifact_sha256,diagnostics_json,authority,created_ts)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'research_only',?)",
                        (model_id, "REGULARIZED_LOGISTIC", horizon, feature_set, cutoff,
                         evidence["raw_resolved"], float(evidence["effective_n"]),
                         evidence["positive_n"], evidence["negative_n"], evidence["trading_days"],
                         _json(params), artifact_sha, _json(diagnostics), now))
                created += 1
        return created

    def _create_prospective_predictions(self, observation_id: str, captured_ts: float,
                                        horizon: int) -> int:
        with self._lock:
            obs = self._conn.execute(
                "SELECT * FROM g1s_observations WHERE observation_id=?", (observation_id,)).fetchone()
            models = self._conn.execute(
                "SELECT * FROM g1s_models WHERE horizon_minutes=? AND created_ts<=? "
                "AND training_cutoff_ts<? ORDER BY created_ts DESC",
                (horizon, captured_ts, captured_ts)).fetchall()
        if obs is None:
            return 0
        # One latest frozen model per feature set.
        chosen = {}
        for model in models:
            chosen.setdefault(str(model["feature_set"]), model)
        written = 0
        for model in chosen.values():
            feature_set = str(model["feature_set"])
            if feature_set not in FEATURE_SETS:
                continue
            vector, _ = self._feature_vector(dict(obs), feature_set)
            params = _loads(model["parameters_json"], {})
            mean = np.asarray(params.get("feature_mean") or [], dtype=float)
            std = np.asarray(params.get("feature_std") or [], dtype=float)
            beta = np.asarray(params.get("intercept_and_coefficients") or [], dtype=float)
            x = np.asarray(vector, dtype=float)
            if len(mean) != len(x) or len(std) != len(x) or len(beta) != len(x)+1:
                self._error("MODEL_ARTIFACT_SHAPE_MISMATCH", str(model["model_id"]),
                            observation_id=observation_id, critical=True)
                continue
            z = (x-mean) / np.where(std < 1e-12, 1.0, std)
            p_up = float(_sigmoid(np.asarray([beta[0] + z @ beta[1:]]))[0])
            payload = {
                "contract_version": G1S_PREDICTION_VERSION,
                "observation_id": observation_id, "model_id": str(model["model_id"]),
                "model_created_ts": float(model["created_ts"]),
                "training_cutoff_ts": float(model["training_cutoff_ts"]),
                "captured_ts": captured_ts, "p_up": p_up,
                "research_only": True, "production_used": False,
            }
            raw = _json(payload)
            pred_id = "g1s-pred-" + _sha_text(raw)[:30]
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO g1s_shadow_predictions(prediction_id,observation_id,"
                    "model_id,created_ts,p_up,prediction_json,prediction_sha256,production_used)"
                    " VALUES(?,?,?,?,?,?,?,0)",
                    (pred_id, observation_id, model["model_id"], time.time(), p_up, raw,
                     _sha_text(raw)))
            written += 1
        return written

    # ---------------------------------------------------------- trade links
    def materialize_trade_links(self) -> int:
        with self._lock:
            trades = self._conn.execute(
                "SELECT id,opened_at,instrument,direction,setup,result_r,status FROM trades "
                "ORDER BY opened_at,id").fetchall()
        created = 0
        for trade in trades:
            for horizon in HORIZONS:
                with self._lock:
                    obs = self._conn.execute("""
                        SELECT observation_id,captured_ts FROM g1s_observations
                        WHERE instrument=? AND horizon_minutes=? AND captured_ts<=?
                          AND measurement_eligible=1
                        ORDER BY captured_ts DESC LIMIT 1
                    """, (trade["instrument"], horizon, float(trade["opened_at"]))).fetchone()
                if obs is None:
                    continue
                age = float(trade["opened_at"]) - float(obs["captured_ts"])
                if age < -1e-9 or age > TRADE_LINK_MAX_AGE_SEC:
                    continue
                payload = {
                    "contract_version": G1S_TRADE_RELEVANCE_VERSION,
                    "trade_id": int(trade["id"]), "observation_id": str(obs["observation_id"]),
                    "horizon_minutes": horizon, "forecast_age_sec": age,
                    "forecast_precedes_entry": True, "instrument": trade["instrument"],
                    "direction": trade["direction"], "setup": trade["setup"],
                    "trade_status": trade["status"], "trade_result_r": trade["result_r"],
                }
                raw = _json(payload)
                link_id = "g1s-trade-" + _sha_text(raw)[:28]
                with self._lock, self._conn:
                    cur = self._conn.execute(
                        "INSERT OR IGNORE INTO g1s_trade_links(link_id,trade_id,observation_id,"
                        "horizon_minutes,forecast_age_sec,link_json,link_sha256,created_ts)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (link_id, int(trade["id"]), obs["observation_id"], horizon, age,
                         raw, _sha_text(raw), time.time()))
                    created += int(cur.rowcount > 0)
        return created

    # --------------------------------------------------------------- reports
    def horizon_report(self, horizon: int) -> dict:
        rows = self._resolved_eligible(horizon)
        evidence = self._evidence(rows)
        ys = [1 if r["direction_label"] == "UP" else 0 for r in rows]
        chronological_ps: list[float] = []
        chronological_y: list[int] = []
        seen_up = seen_n = 0
        for y in ys:
            p = 0.5 if seen_n < 20 else seen_up / seen_n
            chronological_ps.append(p); chronological_y.append(y)
            seen_n += 1; seen_up += y
        baseline = {
            "constant_0_5": {"brier": _brier([0.5]*len(ys), ys),
                              "log_loss": _logloss([0.5]*len(ys), ys)},
            "chronological_base_rate": {"brier": _brier(chronological_ps, chronological_y),
                                        "log_loss": _logloss(chronological_ps, chronological_y)},
        }
        with self._lock:
            pending = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_observations g LEFT JOIN g1s_resolutions r USING(observation_id) "
                "WHERE g.horizon_minutes=? AND r.observation_id IS NULL", (horizon,)).fetchone()[0])
            models = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_models WHERE horizon_minutes=?", (horizon,)).fetchone()[0])
        state = "SHADOW_FIT_ALLOWED" if evidence["fit_allowed"] else (
            "EARLY" if evidence["raw_resolved"] > 0 else "COLLECTING")
        return {"horizon_minutes": horizon, "state": state, "pending": pending,
                **evidence, "baselines": baseline, "model_n": models}

    def status(self) -> dict:
        horizons = [self.horizon_report(h) for h in HORIZONS]
        with self._lock:
            total = int(self._conn.execute("SELECT COUNT(*) FROM g1s_observations").fetchone()[0])
            resolved = int(self._conn.execute("SELECT COUNT(*) FROM g1s_resolutions").fetchone()[0])
            models = int(self._conn.execute("SELECT COUNT(*) FROM g1s_models").fetchone()[0])
            preds = int(self._conn.execute("SELECT COUNT(*) FROM g1s_shadow_predictions").fetchone()[0])
            critical = int(self._conn.execute(
                "SELECT COUNT(*) FROM g1s_contract_errors WHERE critical=1").fetchone()[0])
        return {
            "g1_stage": G1S_STAGE, "contract_version": G1S_CONTRACT_VERSION,
            "activation_ts": self.activation_ts, "observations": total,
            "resolved": resolved, "pending": max(0, total-resolved),
            "models": models, "prospective_shadow_predictions": preds,
            "primary_horizons": list(PRIMARY_HORIZONS), "horizons": horizons,
            "critical_errors": critical,
            "last_step": {"started_ts": self._last_step_started,
                          "finished_ts": self._last_step_finished,
                          "duration_ms": self._last_step_duration_ms,
                          "error": self._last_error},
            "authority": {"research_only": True, "production_authority": False,
                          "auto_execution_allowed": False,
                          "policy_promotion_allowed": False,
                          "edge_claim_allowed": False, "oos_validated": False},
        }

    def observations(self, *, resolved: bool | None = None, limit: int = 100) -> dict:
        condition = ""
        if resolved is True:
            condition = "WHERE r.observation_id IS NOT NULL"
        elif resolved is False:
            condition = "WHERE r.observation_id IS NULL"
        with self._lock:
            rows = self._conn.execute(f"""
                SELECT g.observation_id,g.source_observation_id,g.captured_ts,g.target_ts,
                       g.instrument,g.horizon_minutes,g.origin,g.measurement_eligible,
                       g.exclusion_reason,r.resolved_ts,r.terminal_log_return,r.direction_label,
                       r.mfe_log_return,r.mae_log_return
                FROM g1s_observations g LEFT JOIN g1s_resolutions r USING(observation_id)
                {condition} ORDER BY g.captured_ts DESC LIMIT ?
            """, (max(1, min(int(limit), 1000)),)).fetchall()
        return {"contract_version": G1S_CONTRACT_VERSION, "items": [dict(r) for r in rows]}

    def models(self, limit: int = 100) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT model_id,model_family,horizon_minutes,feature_set,training_cutoff_ts,"
                "raw_n,effective_n,positive_n,negative_n,training_days,diagnostics_json,"
                "artifact_sha256,authority,created_ts FROM g1s_models "
                "ORDER BY created_ts DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
        items = []
        for row in rows:
            item = dict(row); item["diagnostics"] = _loads(item.pop("diagnostics_json"), {})
            items.append(item)
        return {"contract_version": G1S_MODEL_VERSION, "items": items}

    def prospective_oos(self) -> dict:
        with self._lock:
            rows = self._conn.execute("""
                SELECT p.model_id,p.p_up,r.direction_label,m.feature_set,m.horizon_minutes
                FROM g1s_shadow_predictions p
                JOIN g1s_resolutions r USING(observation_id)
                JOIN g1s_models m USING(model_id)
                WHERE r.direction_label!='FLAT' AND p.production_used=0
                ORDER BY p.created_ts
            """).fetchall()
        grouped: dict[tuple, list] = defaultdict(list)
        for row in rows:
            grouped[(row["horizon_minutes"], row["feature_set"])].append(row)
        items = []
        for (horizon, feature_set), group in grouped.items():
            ps = [float(r["p_up"]) for r in group]
            ys = [1 if r["direction_label"] == "UP" else 0 for r in group]
            items.append({"horizon_minutes": int(horizon), "feature_set": feature_set,
                          "n": len(group), "brier": _brier(ps, ys),
                          "log_loss": _logloss(ps, ys),
                          "baseline_0_5_brier": _brier([0.5]*len(ys), ys),
                          "oos_validated": False, "edge_claim_allowed": False})
        return {"contract_version": G1S_PREDICTION_VERSION,
                "prospective_only": True, "items": items,
                "production_authority": False}

    def ablation(self) -> dict:
        models = self.models(limit=500)["items"]
        latest: dict[tuple, dict] = {}
        for model in models:
            latest.setdefault((model["horizon_minutes"], model["feature_set"]), model)
        return {"contract_version": G1S_MODEL_VERSION,
                "question": "Do frozen option features improve short-horizon OOS loss over price-only?",
                "items": list(latest.values()), "causal_claim": False,
                "prospective_oos": self.prospective_oos()}

    def trade_relevance(self) -> dict:
        with self._lock:
            rows = self._conn.execute("""
                SELECT l.*,t.instrument,t.direction,t.setup,t.result_r,t.status,
                       r.direction_label,r.terminal_log_return
                FROM g1s_trade_links l JOIN trades t ON t.id=l.trade_id
                LEFT JOIN g1s_resolutions r USING(observation_id)
                ORDER BY t.opened_at,l.horizon_minutes
            """).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if row["direction_label"] in {"UP", "DOWN"}:
                predicted_direction = row["direction_label"]
                trade_direction = "UP" if row["direction"] == "long" else "DOWN"
                item["market_move_aligned_with_trade"] = predicted_direction == trade_direction
            else:
                item["market_move_aligned_with_trade"] = None
            items.append(item)
        return {"contract_version": G1S_TRADE_RELEVANCE_VERSION,
                "max_pre_entry_forecast_age_sec": TRADE_LINK_MAX_AGE_SEC,
                "items": items, "real_trades_are_validation_not_training": True,
                "edge_claim_allowed": False}

    def q_audit(self, *, now: float | None = None, limit: int = 500) -> dict:
        now = float(now or time.time())
        with self._lock:
            rows = self._conn.execute("""
                SELECT q.attempt_id,q.attempt_ts,q.target_instrument,q.observation_created,
                       q.created_observation_id,q.blocker_code,p.target_ts,p.resolution_status,
                       p.instrument
                FROM g1_q_capture_attempts q
                LEFT JOIN passive_market_observations p
                  ON p.observation_id=q.created_observation_id
                ORDER BY q.attempt_ts DESC LIMIT ?
            """, (max(1, min(int(limit), 5000)),)).fetchall()
            latest_path = {r["instrument"]: float(r["mx"] or 0) for r in self._conn.execute(
                "SELECT instrument,MAX(ts) mx FROM passive_market_path GROUP BY instrument").fetchall()}
            latest_bar = {r["instrument"]: float(r["mx"] or 0) for r in self._conn.execute(
                "SELECT instrument,MAX(bar_end_ts) mx FROM passive_market_bars GROUP BY instrument").fetchall()}
        counts = defaultdict(int); items = []
        targets = []
        for row in rows:
            target = _finite(row["target_ts"])
            if int(row["observation_created"] or 0) != 1 or not row["created_observation_id"]:
                state = "CONTRACT_REJECTED" if row["blocker_code"] else "RESOLUTION_BLOCKED"
            elif row["resolution_status"] == "resolved":
                state = "RESOLVED"
            elif target is not None and target > now:
                state = "NOT_DUE_YET"; targets.append(target)
            else:
                instrument = str(row["instrument"] or row["target_instrument"] or "")
                latest = max(latest_path.get(instrument, 0), latest_bar.get(instrument, 0))
                if target is not None and latest >= target - 1e-6:
                    state = "DUE_BUT_NOT_RESOLVED"
                else:
                    state = "RESOLUTION_BLOCKED"
            counts[state] += 1
            items.append({"attempt_id": row["attempt_id"], "attempt_ts": row["attempt_ts"],
                          "instrument": row["target_instrument"],
                          "observation_id": row["created_observation_id"],
                          "target_ts": target, "resolution_status": row["resolution_status"],
                          "blocker_code": row["blocker_code"], "audit_state": state})
        return {"contract_version": G1S_Q_AUDIT_VERSION, "now": now,
                "counts": dict(counts),
                "earliest_pending_target_ts": min(targets) if targets else None,
                "median_pending_target_ts": (sorted(targets)[len(targets)//2] if targets else None),
                "latest_pending_target_ts": max(targets) if targets else None,
                "overdue_is_contract_failure": counts.get("DUE_BUT_NOT_RESOLVED", 0) > 0,
                "items": items, "slow_q_semantics_unchanged": True}

    def materializer_status(self) -> dict:
        items = getattr(self, "_cached_materializer_items", None)
        if items is None:
            with self._lock:
                self._refresh_materializer_cache_under_lock()
                items = self._cached_materializer_items
        return {"contract_version": G1S_MATERIALIZER_VERSION,
                "items": list(items or []),
                "worker": {"last_started": self._last_step_started,
                           "last_finished": self._last_step_finished,
                           "duration_ms": self._last_step_duration_ms,
                           "error": self._last_error}}

    def step(self) -> dict:
        start = time.time(); self._last_step_started = start
        try:
            captured = self.materialize_new(limit=2500)
            resolved = self.resolve_new(limit=2500)
            links = self.materialize_trade_links()
            models = self.fit_if_ready()
            self._last_error = None
            result = {"materialized": captured, "resolved": resolved,
                      "trade_links": links, "models_created": models}
        except Exception as exc:  # fail visible but do not interrupt market collector
            self._last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            result = {"materialized": 0, "resolved": 0, "trade_links": 0,
                      "models_created": 0, "error": self._last_error}
        self._last_step_finished = time.time()
        self._last_step_duration_ms = (self._last_step_finished-start)*1000.0
        return result
