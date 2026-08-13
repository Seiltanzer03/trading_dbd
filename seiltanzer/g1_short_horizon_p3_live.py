"""Installer/orchestrator for prospective P3L volatility research."""
from __future__ import annotations

import time

from . import g1_short_horizon_integration as _integration
from . import storage_runtime as _storage
from .g1_short_horizon_runtime import ShortHorizonRuntime
from .g1_short_horizon_p3_live_bars import aggregate_p3l_5m, ingest_p3l_raw_1m
from .g1_short_horizon_p3_live_evidence import p3l_evidence_report, refresh_p3l_progress
from .g1_short_horizon_p3_live_models import (
    load_p3l_models,
    materialize_p3l_historical_models,
    p3l_models_ready,
)
from .g1_short_horizon_p3_live_predictions import create_p3l_predictions
from .g1_short_horizon_p3_live_resolution import resolve_p3l_due
from .g1_short_horizon_p3_live_schema import (
    P3L_CONTRACT_VERSION,
    P3L_CRITICAL_TABLES,
    P3L_EVIDENCE_LABEL,
    P3L_MAX_PREDICTION_LATENCY_SEC,
    P3L_MODEL_VERSION,
    P3L_SERIOUS_REQUIRED,
    ensure_p3l_tables,
    p3l_state,
    update_p3l_state,
)


def p3l_status(runtime) -> dict:
    ensure_p3l_tables(runtime)
    state = p3l_state(runtime)
    models = load_p3l_models(runtime)
    with runtime._lock:
        latest_prediction = runtime._conn.execute(
            "SELECT MAX(created_ts) ts,COUNT(*) n FROM g1s_volatility_predictions").fetchone()
        pending = runtime._conn.execute("""
            SELECT COUNT(*) n FROM g1s_volatility_observations o
            LEFT JOIN g1s_volatility_resolutions r USING(observation_id)
            WHERE r.observation_id IS NULL
        """).fetchone()["n"]
        resolved = runtime._conn.execute(
            "SELECT COUNT(*) n FROM g1s_volatility_resolutions "
            "WHERE resolution_status='RESOLVED'").fetchone()["n"]
    return {
        "contract_version": P3L_CONTRACT_VERSION,
        "model_contract_version": P3L_MODEL_VERSION,
        "evidence_label": P3L_EVIDENCE_LABEL,
        "target": "future_realized_volatility_5m",
        "historical_state": state.get("historical_state"),
        "historical_source_set_sha256": state.get("historical_source_set_sha256"),
        "last_proof_error": state.get("last_proof_error"),
        "models": [{
            "model_id": row["model_id"],
            "horizon_minutes": int(row["horizon_minutes"]),
            "training_cutoff_ts": float(row["training_cutoff_ts"]),
            "created_ts": float(row["created_ts"]),
            "raw_n": int(row["raw_n"]), "effective_n": int(row["effective_n"]),
            "authority": row["authority"],
            "auto_promotion": bool(row["auto_promotion"]),
            "production_used": bool(row["production_used"]),
        } for row in models.values()],
        "models_ready": p3l_models_ready(runtime),
        "prediction_count": int(latest_prediction["n"] or 0),
        "latest_prediction_ts": latest_prediction["ts"],
        "resolved_count": int(resolved or 0),
        "pending_resolutions": int(pending or 0),
        "serious_oos_required": dict(P3L_SERIOUS_REQUIRED),
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
        "auto_refit": False,
        "auto_promotion": False,
        "production_authority": False,
        "edge_claim_allowed": False,
        "state": state,
    }


def run_p3l_cycle(runtime, passive, *, now: float | None = None) -> dict:
    """Low-priority cycle. It never owns a market/network request."""
    ensure_p3l_tables(runtime)
    now = float(now or time.time())
    try:
        historical = materialize_p3l_historical_models(runtime)
        raw = ingest_p3l_raw_1m(runtime, passive, now=now)
        bars5 = aggregate_p3l_5m(runtime, now=now)
        predictions = create_p3l_predictions(runtime, now=now) if p3l_models_ready(runtime) else 0
        resolutions = resolve_p3l_due(runtime, now=now)
        progress = refresh_p3l_progress(runtime) if resolutions else {}
        state = p3l_state(runtime)
        update_p3l_state(
            runtime,
            last_cycle_ts=now,
            last_cycle_error=None,
            raw_1m_rows_ingested=int(state.get("raw_1m_rows_ingested") or 0)+raw,
            bars_5m_created=int(state.get("bars_5m_created") or 0)+bars5,
            observations_created=int(state.get("observations_created") or 0)+predictions,
            resolutions_created=int(state.get("resolutions_created") or 0)+resolutions,
        )
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
    except Exception as exc:
        update_p3l_state(runtime, last_cycle_ts=now,
                         last_cycle_error=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise


def install_g1_short_horizon_p3_live() -> None:
    if getattr(ShortHorizonRuntime, "_p3_live_version", None) == P3L_CONTRACT_VERSION:
        return
    previous_init = ShortHorizonRuntime.__init__
    previous_status = ShortHorizonRuntime.status

    def runtime_init(self, *args, **kwargs):
        previous_init(self, *args, **kwargs)
        ensure_p3l_tables(self)

    def status(self):
        report = previous_status(self)
        report["volatility_live_oos"] = p3l_status(self)
        return report

    ShortHorizonRuntime.__init__ = runtime_init
    ShortHorizonRuntime.run_volatility_live_cycle = run_p3l_cycle
    ShortHorizonRuntime.volatility_live_status = p3l_status
    ShortHorizonRuntime.volatility_live_evidence = p3l_evidence_report
    ShortHorizonRuntime.refresh_volatility_progress = refresh_p3l_progress
    ShortHorizonRuntime.materialize_volatility_historical_models = materialize_p3l_historical_models
    ShortHorizonRuntime.status = status
    ShortHorizonRuntime._p3_live_version = P3L_CONTRACT_VERSION

    _storage.CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_storage.CRITICAL_TABLES, *P3L_CRITICAL_TABLES)))
    _integration.G1S_CRITICAL_TABLES = tuple(dict.fromkeys(
        (*_integration.G1S_CRITICAL_TABLES, *P3L_CRITICAL_TABLES)))
