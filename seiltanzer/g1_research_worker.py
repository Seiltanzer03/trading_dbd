"""Low-priority research worker for G.1S and G.1-M.1.

The acceptance-visible core is deliberately small and bounded. Full-history or
presentation/research maintenance runs only after that core has completed, one
phase at a time, and never while the exact-SHA production acceptance gate is active.
No production decision authority lives here. On the small production VPS the
worker also yields completely to the live terminal under process-memory pressure.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

from .production_resource_guard import memory_pressure_state, trim_memory_for_pressure
from .research_acceptance_gate import worker_acceptance_gate_state


RESEARCH_WORKER_VERSION = "g1-research-worker-v1"
# Keep the established readiness contract identifier: memory-pressure yielding is
# an operational guard around the same bounded-v5 research semantics, not a new
# research/scalability algorithm.
RESEARCH_WORKER_SCALABILITY_VERSION = "g1-research-worker-bounded-v5"
RESEARCH_INTERVAL_SEC = 10.0
RESEARCH_STARTUP_GRACE_SEC = 5 * 60.0
G1S_BATCH = 500
G1M_LOCAL_BATCH = 100
STATUS_REFRESH_BATCH = 1000
TRADE_LINK_BATCH = 50
AUX_BATCH = 500
FIT_GATE_INTERVAL_SEC = 15 * 60.0
EVIDENCE_INTERVAL_SEC = 5 * 60.0
HISTORICAL_WF_INTERVAL_SEC = 15 * 60.0
MAINTENANCE_PHASES = (
    "status_refresh",
    "trade_links",
    "barriers",
    "path_metrics",
    "ede_shadow",
    "evidence_reports",
    "historical_walk_forward",
    "fit_models",
)


def _run_g1s_core(runtime) -> dict:
    """Only bounded source materialization/resolution required for worker health."""
    return {
        "materialized": runtime.materialize_new(limit=G1S_BATCH),
        "resolved": runtime.resolve_new(limit=G1S_BATCH),
        "batch_limit": G1S_BATCH,
    }


def _run_g1m_local_core(runtime) -> dict:
    return {
        "windows_created": runtime.materialize_windows(limit=G1M_LOCAL_BATCH),
        "outcomes_resolved": runtime.resolve_due(limit=G1M_LOCAL_BATCH),
        "batch_limit": G1M_LOCAL_BATCH,
    }


def _run_ede_shadow_bounded(engine) -> dict:
    try:
        from .edge_discovery.shadow_runtime import materialize_runtime_shadow
        return materialize_runtime_shadow(engine)
    except Exception as exc:
        return {
            "contract_version": "g1s-ede-shadow-runtime-v1.3",
            "refreshed": False,
            "reason": "SHADOW_MATERIALIZER_ERROR",
            "error": f"{type(exc).__name__}: {str(exc)[:500]}",
            "production_authority": False,
            "auto_promotion": False,
        }


def _cadence_due(runtime, attr: str, interval_sec: float, now: float) -> bool:
    previous = float(getattr(runtime, attr, 0.0) or 0.0)
    if now - previous < interval_sec:
        return False
    setattr(runtime, attr, now)
    return True


def _run_maintenance_phase(runtime, engine, phase: str) -> dict:
    """Run exactly one optional phase; heavy phases never belong to core health."""
    now = time.time()
    if phase == "status_refresh":
        return {
            "phase": phase,
            "result": runtime.refresh_materialized_status(limit=STATUS_REFRESH_BATCH),
            "batch_limit": STATUS_REFRESH_BATCH,
        }
    if phase == "trade_links":
        from .g1_trade_link_catchup import materialize_trade_links_bounded
        return {
            "phase": phase,
            "result": materialize_trade_links_bounded(runtime, limit=TRADE_LINK_BATCH),
        }
    if phase == "barriers":
        from .g1_short_horizon_refinement import _materialize_barriers
        return {
            "phase": phase,
            "rows_created": _materialize_barriers(runtime, limit=AUX_BATCH),
            "batch_limit": AUX_BATCH,
        }
    if phase == "path_metrics":
        from .g1_short_horizon_metrics_refinement import _materialize_path_metrics
        return {
            "phase": phase,
            "rows_created": _materialize_path_metrics(runtime, limit=AUX_BATCH),
            "batch_limit": AUX_BATCH,
        }
    if phase == "ede_shadow":
        return {"phase": phase, "result": _run_ede_shadow_bounded(engine)}
    if phase == "evidence_reports":
        if not _cadence_due(runtime, "_g1s_worker_last_evidence_ts", EVIDENCE_INTERVAL_SEC, now):
            return {"phase": phase, "skipped": True, "reason": "CADENCE_NOT_DUE"}
        fn = getattr(runtime, "materialize_evidence_reports", None)
        return {
            "phase": phase,
            "result": fn() if callable(fn) else {
                "refreshed": False, "reason": "MATERIALIZER_UNAVAILABLE"
            },
        }
    if phase == "historical_walk_forward":
        if not _cadence_due(runtime, "_g1s_worker_last_historical_wf_ts", HISTORICAL_WF_INTERVAL_SEC, now):
            return {"phase": phase, "skipped": True, "reason": "CADENCE_NOT_DUE"}
        fn = getattr(runtime, "materialize_historical_walkforward", None)
        return {
            "phase": phase,
            "result": fn() if callable(fn) else {
                "refreshed": False, "reason": "HISTORICAL_WF_UNAVAILABLE"
            },
        }
    if phase == "fit_models":
        if not _cadence_due(runtime, "_g1s_worker_last_fit_gate_ts", FIT_GATE_INTERVAL_SEC, now):
            return {"phase": phase, "skipped": True, "reason": "CADENCE_NOT_DUE"}
        return {"phase": phase, "models_created": runtime.fit_if_ready()}
    raise ValueError(f"unknown research maintenance phase: {phase}")


def _apply_gate_state(state: dict, gate: dict) -> None:
    state["acceptance_gate_active"] = bool(gate["active"])
    state["acceptance_pause_active"] = bool(gate["pause"])
    state["acceptance_gate_reason"] = gate["reason"]
    state["acceptance_gate_run_id"] = gate["acceptance_run_id"]
    state["acceptance_gate_expected_sha"] = gate["expected_sha"]
    state["acceptance_gate_expires_at"] = gate["expires_at"]


def _apply_memory_pressure(state: dict) -> bool:
    pressure = memory_pressure_state()
    state["memory_pressure"] = pressure
    state["memory_pause_active"] = bool(pressure["pause_background"])
    if not pressure["pause_background"]:
        return False
    state["current_phase"] = "memory_pressure_pause"
    state["maintenance_running"] = False
    state["maintenance_phase"] = None
    trim_memory_for_pressure()
    return True


def install_research_worker(app) -> None:
    if getattr(app.state, "g1_research_worker_installed", False):
        return
    engine = app.state.engine
    app.state.g1_research_worker = {
        "contract_version": RESEARCH_WORKER_VERSION,
        "scalability_refinement_version": RESEARCH_WORKER_SCALABILITY_VERSION,
        "running": False,
        "process_started_ts": None,
        "last_started_ts": None,
        "last_finished_ts": None,
        "last_duration_ms": None,
        "last_result": None,
        "last_error": None,
        "current_phase": "startup_grace",
        "maintenance_running": False,
        "maintenance_phase": None,
        "last_maintenance_started_ts": None,
        "last_maintenance_finished_ts": None,
        "last_maintenance_duration_ms": None,
        "last_maintenance_result": None,
        "last_maintenance_error": None,
        "maintenance_phase_index": 0,
        "g1s_batch_limit": G1S_BATCH,
        "g1m_local_batch_limit": G1M_LOCAL_BATCH,
        "trade_link_batch_limit": TRADE_LINK_BATCH,
        "status_refresh_batch_limit": STATUS_REFRESH_BATCH,
        "fit_gate_interval_sec": FIT_GATE_INTERVAL_SEC,
        "startup_grace_sec": RESEARCH_STARTUP_GRACE_SEC,
        "first_cycle_not_before_ts": None,
        "acceptance_gate_active": False,
        "acceptance_pause_active": False,
        "acceptance_gate_reason": "NO_ACTIVE_ACCEPTANCE_GATE",
        "acceptance_gate_run_id": None,
        "acceptance_gate_expected_sha": None,
        "acceptance_gate_expires_at": None,
        "memory_pause_active": False,
        "memory_pressure": memory_pressure_state(),
        "evidence_reports_request_time_scan": False,
        "historical_walkforward_runs_on_research_worker": True,
        "historical_walkforward_request_time_network_fetch": False,
        "ede_v13_shadow_runs_on_research_worker": True,
        "ede_v13_full_discovery_runs_on_request_path": False,
    }
    original_lifespan = app.router.lifespan_context

    async def loop():
        state = app.state.g1_research_worker
        state["running"] = True
        process_started_ts = time.time()
        state["process_started_ts"] = process_started_ts
        try:
            state["first_cycle_not_before_ts"] = time.time() + RESEARCH_STARTUP_GRACE_SEC
            await asyncio.sleep(RESEARCH_STARTUP_GRACE_SEC)
            while True:
                gate = worker_acceptance_gate_state(
                    process_started_ts=process_started_ts,
                    last_finished_ts=state.get("last_finished_ts"),
                )
                _apply_gate_state(state, gate)
                if gate["pause"]:
                    state["current_phase"] = "acceptance_pause"
                    await asyncio.sleep(RESEARCH_INTERVAL_SEC)
                    continue
                if _apply_memory_pressure(state):
                    await asyncio.sleep(RESEARCH_INTERVAL_SEC)
                    continue

                started = time.time()
                state["current_phase"] = "core"
                state["last_started_ts"] = started
                try:
                    g1s_result = await asyncio.to_thread(_run_g1s_core, engine.short_horizon)
                    await asyncio.sleep(0)
                    g1m_result = await asyncio.to_thread(
                        _run_g1m_local_core, engine.management_local
                    )
                    state["last_result"] = {
                        "g1s": g1s_result,
                        "g1m_local": g1m_result,
                    }
                    state["last_error"] = None
                except Exception as exc:
                    state["last_error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
                finally:
                    state["last_finished_ts"] = time.time()
                    state["last_duration_ms"] = (
                        state["last_finished_ts"] - started
                    ) * 1000.0

                gate = worker_acceptance_gate_state(
                    process_started_ts=process_started_ts,
                    last_finished_ts=state.get("last_finished_ts"),
                )
                _apply_gate_state(state, gate)
                if gate["pause"]:
                    state["current_phase"] = "acceptance_pause"
                    await asyncio.sleep(RESEARCH_INTERVAL_SEC)
                    continue
                if _apply_memory_pressure(state):
                    await asyncio.sleep(RESEARCH_INTERVAL_SEC)
                    continue

                phase_index = int(state.get("maintenance_phase_index") or 0)
                phase = MAINTENANCE_PHASES[phase_index % len(MAINTENANCE_PHASES)]
                state["maintenance_phase_index"] = (phase_index + 1) % len(MAINTENANCE_PHASES)
                state["maintenance_running"] = True
                state["maintenance_phase"] = phase
                state["current_phase"] = f"maintenance:{phase}"
                maintenance_started = time.time()
                state["last_maintenance_started_ts"] = maintenance_started
                try:
                    state["last_maintenance_result"] = await asyncio.to_thread(
                        _run_maintenance_phase, engine.short_horizon, engine, phase
                    )
                    state["last_maintenance_error"] = None
                except Exception as exc:
                    state["last_maintenance_error"] = (
                        f"{type(exc).__name__}: {str(exc)[:500]}"
                    )
                finally:
                    state["last_maintenance_finished_ts"] = time.time()
                    state["last_maintenance_duration_ms"] = (
                        state["last_maintenance_finished_ts"] - maintenance_started
                    ) * 1000.0
                    state["maintenance_running"] = False
                    state["maintenance_phase"] = None
                    state["current_phase"] = "idle"

                await asyncio.sleep(RESEARCH_INTERVAL_SEC)
        finally:
            state["running"] = False
            state["maintenance_running"] = False
            state["current_phase"] = "stopped"

    @contextlib.asynccontextmanager
    async def research_lifespan(inner_app):
        task = None
        async with original_lifespan(inner_app):
            task = asyncio.create_task(loop())
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app.router.lifespan_context = research_lifespan
    app.state.g1_research_worker_installed = True
