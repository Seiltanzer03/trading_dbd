"""Thread-safe read facade for the materialized G.1S status."""
from __future__ import annotations

from .g1_short_horizon_runtime import (
    G1S_CONTRACT_VERSION,
    G1S_STAGE,
    HORIZONS,
    PRIMARY_HORIZONS,
    ShortHorizonRuntime,
)
from .g1_short_horizon_status_materialization import MATERIALIZATION_VERSION


INTEGRITY_VERSION = "g1s-bounded-status-read-integrity-v1"


def _status_thread_safe(runtime: ShortHorizonRuntime) -> dict:
    horizons = [runtime.materialized_horizon_summary(h) for h in HORIZONS]
    with runtime._lock:
        state = runtime._conn.execute(
            "SELECT * FROM g1s_status_materialization_state WHERE id=1").fetchone()
        totals = runtime._conn.execute("""
            SELECT COALESCE(SUM(observation_n),0) observations,
                   COALESCE(SUM(resolved_n),0) resolved
            FROM g1s_horizon_materialized_status
        """).fetchone()
        models = int(runtime._conn.execute("SELECT COUNT(*) FROM g1s_models").fetchone()[0])
        preds = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM g1s_shadow_predictions").fetchone()[0])
        critical = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM g1s_contract_errors WHERE critical=1").fetchone()[0])
        max_obs = int(runtime._conn.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM g1s_observations").fetchone()[0])
        max_res = int(runtime._conn.execute(
            "SELECT COALESCE(MAX(rowid),0) FROM g1s_resolutions").fetchone()[0])

    observations = int(totals["observations"] or 0)
    resolved = int(totals["resolved"] or 0)
    obs_wm = int(state["observation_rowid_watermark"] or 0)
    res_wm = int(state["resolution_rowid_watermark"] or 0)
    lag = max(0, max_obs-obs_wm) + max(0, max_res-res_wm)
    return {
        "g1_stage": G1S_STAGE,
        "contract_version": G1S_CONTRACT_VERSION,
        "activation_ts": runtime.activation_ts,
        "observations": observations,
        "resolved": resolved,
        "pending": max(0, observations-resolved),
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
            "read_integrity_version": INTEGRITY_VERSION,
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


def install_g1_short_horizon_status_integrity() -> None:
    if getattr(ShortHorizonRuntime, "_status_read_integrity", None) == INTEGRITY_VERSION:
        return
    ShortHorizonRuntime.status = _status_thread_safe
    ShortHorizonRuntime._status_read_integrity = INTEGRITY_VERSION
