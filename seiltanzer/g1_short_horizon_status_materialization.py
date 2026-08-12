"""Incremental bounded status materialization for G.1S.

Request-time status must remain cheap when the prospective ledger grows to millions
of rows.  This refinement maintains only sufficient evidence counters from new
immutable observations/resolutions.  Detailed baseline/model evaluation remains a
separate research path and never runs merely because the UI requests /status.
"""
from __future__ import annotations

import math
import time
import types
from typing import Any

from .g1_short_horizon_runtime import (
    FIT_REQUIRED,
    HORIZONS,
    OOS_CANDIDATE_REQUIRED,
    PRIMARY_HORIZONS,
    G1S_CONTRACT_VERSION,
    G1S_STAGE,
    ShortHorizonRuntime,
)


MATERIALIZATION_VERSION = "g1s-status-materialization-v1"
STATUS_CONTRACT_VERSION = "g1s-bounded-status-v1"
STATUS_REFRESH_BATCH = 10000


def _ensure_tables(runtime: ShortHorizonRuntime) -> None:
    with runtime._lock, runtime._conn:
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_status_materialization_state(
                id INTEGER PRIMARY KEY CHECK(id=1),
                observation_rowid_watermark INTEGER NOT NULL DEFAULT 0,
                resolution_rowid_watermark INTEGER NOT NULL DEFAULT 0,
                last_started_ts REAL,
                last_success_ts REAL,
                last_duration_ms REAL,
                last_processed_n INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                contract_version TEXT NOT NULL
            )""")
        runtime._conn.execute(
            "INSERT OR IGNORE INTO g1s_status_materialization_state("
            "id,contract_version) VALUES(1,?)", (MATERIALIZATION_VERSION,))
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_horizon_materialized_status(
                horizon_minutes INTEGER PRIMARY KEY,
                observation_n INTEGER NOT NULL DEFAULT 0,
                resolved_n INTEGER NOT NULL DEFAULT 0,
                raw_resolved INTEGER NOT NULL DEFAULT 0,
                positive_n INTEGER NOT NULL DEFAULT 0,
                negative_n INTEGER NOT NULL DEFAULT 0,
                effective_n INTEGER NOT NULL DEFAULT 0,
                trading_days INTEGER NOT NULL DEFAULT 0,
                volatility_regime_count INTEGER NOT NULL DEFAULT 0,
                last_observation_ts REAL,
                last_resolution_ts REAL,
                updated_ts REAL NOT NULL DEFAULT 0,
                contract_version TEXT NOT NULL
            )""")
        for horizon in HORIZONS:
            runtime._conn.execute(
                "INSERT OR IGNORE INTO g1s_horizon_materialized_status("
                "horizon_minutes,contract_version) VALUES(?,?)",
                (int(horizon), MATERIALIZATION_VERSION))
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_status_dependency_keys(
                horizon_minutes INTEGER NOT NULL,
                dependency_key TEXT NOT NULL,
                PRIMARY KEY(horizon_minutes,dependency_key)
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_status_trading_days(
                horizon_minutes INTEGER NOT NULL,
                trading_day TEXT NOT NULL,
                PRIMARY KEY(horizon_minutes,trading_day)
            )""")
        runtime._conn.execute("""
            CREATE TABLE IF NOT EXISTS g1s_status_regimes(
                horizon_minutes INTEGER NOT NULL,
                regime TEXT NOT NULL,
                PRIMARY KEY(horizon_minutes,regime)
            )""")


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(float(ts)))


def _refresh(runtime: ShortHorizonRuntime, limit: int = STATUS_REFRESH_BATCH) -> dict[str, Any]:
    """Consume only source rowids beyond durable watermarks."""
    _ensure_tables(runtime)
    started = time.time()
    limit = max(1, min(int(limit), 50000))
    try:
        with runtime._lock:
            state = runtime._conn.execute(
                "SELECT * FROM g1s_status_materialization_state WHERE id=1").fetchone()
            obs_wm = int(state["observation_rowid_watermark"] or 0)
            res_wm = int(state["resolution_rowid_watermark"] or 0)
            observations = runtime._conn.execute("""
                SELECT rowid source_rowid,horizon_minutes,captured_ts
                FROM g1s_observations WHERE rowid>? ORDER BY rowid LIMIT ?
            """, (obs_wm, limit)).fetchall()
            resolutions = runtime._conn.execute("""
                SELECT r.rowid resolution_rowid,r.resolved_ts,r.direction_label,
                       g.instrument,g.horizon_minutes,g.captured_ts,g.market_regime,
                       g.training_eligible
                FROM g1s_resolutions r JOIN g1s_observations g USING(observation_id)
                WHERE r.rowid>? ORDER BY r.rowid LIMIT ?
            """, (res_wm, limit)).fetchall()

        obs_updates: dict[int, dict[str, Any]] = {}
        for row in observations:
            h = int(row["horizon_minutes"])
            agg = obs_updates.setdefault(h, {"n": 0, "last": None})
            agg["n"] += 1
            ts = float(row["captured_ts"])
            agg["last"] = ts if agg["last"] is None else max(agg["last"], ts)

        resolved_updates: dict[int, dict[str, Any]] = {}
        with runtime._lock, runtime._conn:
            for row in resolutions:
                h = int(row["horizon_minutes"])
                agg = resolved_updates.setdefault(h, {
                    "resolved": 0, "raw": 0, "pos": 0, "neg": 0,
                    "effective_add": 0, "day_add": 0, "regime_add": 0,
                    "last": None,
                })
                agg["resolved"] += 1
                resolved_ts = float(row["resolved_ts"])
                agg["last"] = resolved_ts if agg["last"] is None else max(agg["last"], resolved_ts)
                if int(row["training_eligible"] or 0) != 1 or row["direction_label"] == "FLAT":
                    continue
                agg["raw"] += 1
                agg["pos"] += int(row["direction_label"] == "UP")
                agg["neg"] += int(row["direction_label"] == "DOWN")
                payload = dict(row)
                dep = runtime._dependency_key(payload)
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_status_dependency_keys("
                    "horizon_minutes,dependency_key) VALUES(?,?)", (h, dep))
                agg["effective_add"] += int(cur.rowcount > 0)
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_status_trading_days("
                    "horizon_minutes,trading_day) VALUES(?,?)", (h, _day(row["captured_ts"])))
                agg["day_add"] += int(cur.rowcount > 0)
                regime = str(row["market_regime"] or "UNKNOWN")
                cur = runtime._conn.execute(
                    "INSERT OR IGNORE INTO g1s_status_regimes(horizon_minutes,regime) VALUES(?,?)",
                    (h, regime))
                agg["regime_add"] += int(cur.rowcount > 0)

            now = time.time()
            for h, agg in obs_updates.items():
                runtime._conn.execute("""
                    UPDATE g1s_horizon_materialized_status
                    SET observation_n=observation_n+?,
                        last_observation_ts=CASE
                            WHEN last_observation_ts IS NULL OR last_observation_ts<? THEN ?
                            ELSE last_observation_ts END,
                        updated_ts=?
                    WHERE horizon_minutes=?
                """, (agg["n"], agg["last"], agg["last"], now, h))
            for h, agg in resolved_updates.items():
                runtime._conn.execute("""
                    UPDATE g1s_horizon_materialized_status
                    SET resolved_n=resolved_n+?,raw_resolved=raw_resolved+?,
                        positive_n=positive_n+?,negative_n=negative_n+?,
                        effective_n=effective_n+?,trading_days=trading_days+?,
                        volatility_regime_count=volatility_regime_count+?,
                        last_resolution_ts=CASE
                            WHEN last_resolution_ts IS NULL OR last_resolution_ts<? THEN ?
                            ELSE last_resolution_ts END,
                        updated_ts=?
                    WHERE horizon_minutes=?
                """, (agg["resolved"], agg["raw"], agg["pos"], agg["neg"],
                      agg["effective_add"], agg["day_add"], agg["regime_add"],
                      agg["last"], agg["last"], now, h))

            new_obs_wm = max([obs_wm] + [int(r["source_rowid"]) for r in observations])
            new_res_wm = max([res_wm] + [int(r["resolution_rowid"]) for r in resolutions])
            duration = (time.time()-started)*1000.0
            runtime._conn.execute("""
                UPDATE g1s_status_materialization_state
                SET observation_rowid_watermark=?,resolution_rowid_watermark=?,
                    last_started_ts=?,last_success_ts=?,last_duration_ms=?,
                    last_processed_n=?,last_error=NULL,contract_version=? WHERE id=1
            """, (new_obs_wm, new_res_wm, started, time.time(), duration,
                  len(observations)+len(resolutions), MATERIALIZATION_VERSION))
        return {
            "contract_version": MATERIALIZATION_VERSION,
            "observations_processed": len(observations),
            "resolutions_processed": len(resolutions),
            "observation_rowid_watermark": new_obs_wm,
            "resolution_rowid_watermark": new_res_wm,
            "duration_ms": duration,
        }
    except Exception as exc:
        with runtime._lock, runtime._conn:
            runtime._conn.execute("""
                UPDATE g1s_status_materialization_state
                SET last_started_ts=?,last_duration_ms=?,last_error=? WHERE id=1
            """, (started, (time.time()-started)*1000.0,
                  f"{type(exc).__name__}: {str(exc)[:500]}"))
        raise


def _horizon_summary(runtime: ShortHorizonRuntime, horizon: int) -> dict[str, Any]:
    with runtime._lock:
        row = runtime._conn.execute(
            "SELECT * FROM g1s_horizon_materialized_status WHERE horizon_minutes=?",
            (int(horizon),)).fetchone()
        model_n = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM g1s_models WHERE horizon_minutes=?", (int(horizon),)).fetchone()[0])
    if row is None:
        raw = pos = neg = eff = days = regimes = observations = resolved = 0
    else:
        observations = int(row["observation_n"] or 0)
        resolved = int(row["resolved_n"] or 0)
        raw = int(row["raw_resolved"] or 0)
        pos = int(row["positive_n"] or 0)
        neg = int(row["negative_n"] or 0)
        eff = int(row["effective_n"] or 0)
        days = int(row["trading_days"] or 0)
        regimes = int(row["volatility_regime_count"] or 0)
    observed = {
        "raw_resolved": raw, "effective_n": eff,
        "positive_n": pos, "negative_n": neg, "trading_days": days,
    }
    blockers = [key for key, required in FIT_REQUIRED.items()
                if observed.get(key, 0) < int(required)]
    candidate_observed = {
        "raw_resolved": raw, "effective_n": eff, "positive_n": pos,
        "negative_n": neg, "temporal_blocks": days,
    }
    candidate_blockers = [f"INSUFFICIENT_{key.upper()}"
                          for key, required in OOS_CANDIDATE_REQUIRED.items()
                          if candidate_observed.get(key, 0) < int(required)]
    if regimes < 2:
        candidate_blockers.append("INSUFFICIENT_VOLATILITY_REGIME_DIVERSITY")
    state = "OOS_CANDIDATE" if not candidate_blockers else (
        "SHADOW_FIT_ALLOWED" if not blockers else ("EARLY" if raw > 0 else "COLLECTING"))
    return {
        "horizon_minutes": int(horizon),
        "state": state,
        "pending": max(0, observations-resolved),
        **observed,
        "dependency_groups": eff,
        "fit_required": dict(FIT_REQUIRED),
        "fit_blockers": blockers,
        "fit_allowed": not blockers,
        "baselines": {
            "constant_0_5": {
                "brier": 0.25 if raw else None,
                "log_loss": math.log(2.0) if raw else None,
                "closed_form_binary_reference": True,
            },
            "detailed_baselines": {"status": "DEFERRED_TO_RESEARCH_EVALUATION"},
        },
        "model_n": model_n,
        "dependency_contract_version": "g1s-overlap-bucket-v1",
        "dependency_groups_finalized": eff,
        "dependency_weight_sum": float(eff),
        "unique_temporal_anchors": None,
        "volatility_regime_count": regimes,
        "oos_candidate": not candidate_blockers,
        "oos_candidate_blockers": candidate_blockers,
        "supported": False,
        "supported_requires_future_walk_forward_superiority": True,
        "status_materialized": True,
        "status_contract_version": STATUS_CONTRACT_VERSION,
    }


def _status(runtime: ShortHorizonRuntime) -> dict[str, Any]:
    _ensure_tables(runtime)
    horizons = [_horizon_summary(runtime, h) for h in HORIZONS]
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT * FROM g1s_status_materialization_state WHERE id=1").fetchone()
        models = int(runtime._conn.execute("SELECT COUNT(*) FROM g1s_models").fetchone()[0])
        preds = int(runtime._conn.execute("SELECT COUNT(*) FROM g1s_shadow_predictions").fetchone()[0])
        critical = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM g1s_contract_errors WHERE critical=1").fetchone()[0])
        max_obs = int(runtime._conn.execute("SELECT COALESCE(MAX(rowid),0) FROM g1s_observations").fetchone()[0])
        max_res = int(runtime._conn.execute("SELECT COALESCE(MAX(rowid),0) FROM g1s_resolutions").fetchone()[0])
    total = sum(int(h.get("pending", 0)) + int(h.get("raw_resolved", 0)) for h in horizons)
    resolved = sum(int(h.get("raw_resolved", 0)) for h in horizons)
    obs_wm = int(state["observation_rowid_watermark"] or 0)
    res_wm = int(state["resolution_rowid_watermark"] or 0)
    lag = max(0, max_obs-obs_wm) + max(0, max_res-res_wm)
    return {
        "g1_stage": G1S_STAGE,
        "contract_version": G1S_CONTRACT_VERSION,
        "activation_ts": runtime.activation_ts,
        "observations": sum(int(runtime._conn.execute(
            "SELECT observation_n FROM g1s_horizon_materialized_status WHERE horizon_minutes=?",
            (h,)).fetchone()[0]) for h in HORIZONS),
        "resolved": sum(int(runtime._conn.execute(
            "SELECT resolved_n FROM g1s_horizon_materialized_status WHERE horizon_minutes=?",
            (h,)).fetchone()[0]) for h in HORIZONS),
        "pending": sum(int(h["pending"]) for h in horizons),
        "models": models,
        "prospective_shadow_predictions": preds,
        "primary_horizons": list(PRIMARY_HORIZONS),
        "horizons": horizons,
        "critical_errors": critical,
        "last_step": {
            "started_ts": state["last_started_ts"],
            "finished_ts": state["last_success_ts"],
            "duration_ms": state["last_duration_ms"],
            "error": state["last_error"],
        },
        "status_materialization": {
            "contract_version": MATERIALIZATION_VERSION,
            "observation_rowid_watermark": obs_wm,
            "resolution_rowid_watermark": res_wm,
            "lag_rows": lag,
            "presentation_state": "CURRENT" if lag == 0 else "BUILDING",
            "last_success_ts": state["last_success_ts"],
        },
        "authority": {
            "research_only": True,
            "production_authority": False,
            "auto_execution_allowed": False,
            "policy_promotion_allowed": False,
            "edge_claim_allowed": False,
            "oos_validated": False,
        },
    }


def install_g1_short_horizon_status_materialization() -> None:
    if getattr(ShortHorizonRuntime, "_status_materialization_version", None) == MATERIALIZATION_VERSION:
        return
    original_init = ShortHorizonRuntime.__init__

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        _ensure_tables(self)

    ShortHorizonRuntime.__init__ = init
    ShortHorizonRuntime.refresh_materialized_status = _refresh
    ShortHorizonRuntime.materialized_horizon_summary = _horizon_summary
    ShortHorizonRuntime.status = _status
    ShortHorizonRuntime._status_materialization_version = MATERIALIZATION_VERSION
