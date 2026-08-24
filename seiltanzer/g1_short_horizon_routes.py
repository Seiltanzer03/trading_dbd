"""Read-only APIs for G.1S, Q maturity diagnostics and G.1-M.1 local feedback."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Response

from .g1_short_horizon_final_report import install_g1_short_horizon_final_report
from .g1_short_horizon_historical_wf_integrity import install_g1_short_horizon_historical_wf_integrity
from .storage_restore_drill import last_restore_drill
from .edge_discovery.evidence_ledger import evidence_ledger_path, latest_frozen_evidence
from .edge_discovery.shadow_cache import load_shadow_summary_cache


def install_g1_short_horizon_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1s_routes_installed", False):
        return
    install_g1_short_horizon_final_report()
    install_g1_short_horizon_historical_wf_integrity()
    runtime = getattr(app.state.engine, "short_horizon", None)
    local = getattr(app.state.engine, "management_local", None)
    if runtime is None or local is None:
        raise RuntimeError("G.1S integration must be installed before routes")

    app.add_api_route("/api/research/g1s/status", runtime.status,
                      methods=["GET"], name="g1s_status")

    def horizons():
        # runtime.status() already materializes horizon_report() for every
        # canonical horizon. Re-running those five reports here doubled the
        # resolved-evidence/SQLite work of this request and could exceed the
        # production 3s latency budget while the research worker was active.
        status = runtime.status()
        return {"contract_version": status["contract_version"],
                "items": list(status.get("horizons") or [])}

    def cached(name: str):
        return runtime.materialized_evidence_report(name)

    def preencoded(name: str):
        async def endpoint():
            # Evidence reports can contain many frozen model/cohort/reliability
            # rows. They are already worker-materialized and process-local, so
            # request-time recursive JSON encoding only adds latency. Reuse the
            # startup/worker-refresh encoding without moving SQLite, refits or
            # methodology work onto HTTP.
            renderer = getattr(runtime, "materialized_evidence_json", None)
            if not callable(renderer):
                return cached(name)
            return Response(content=renderer(name), media_type="application/json")
        return endpoint

    def final_report():
        body = cached("final_report")
        storage = getattr(app.state, "storage", None)
        if storage is None:
            body["backup_restore"] = {"status": "UNAVAILABLE"}
            return body
        storage_status = storage.status(engine=app.state.engine)
        drill = last_restore_drill(storage)
        body["backup_restore"] = {
            "storage_health": storage_status.get("health"),
            "last_local_backup_age_sec": storage_status.get("last_local_backup_age_sec"),
            "offhost_configured": storage_status.get("offhost_configured"),
            "startup_integrity": storage_status.get("sqlite_integrity"),
            "restore_drill": drill,
            "rpo_target_sec": storage_status.get("rpo_target_sec"),
        }
        return body

    app.add_api_route("/api/research/g1s/horizons", horizons,
                      methods=["GET"], name="g1s_horizons")
    app.add_api_route("/api/research/g1s/final-report", final_report,
                      methods=["GET"], name="g1s_final_report")
    app.add_api_route("/api/research/g1s/historical-wf", runtime.historical_walkforward_status,
                      methods=["GET"], name="g1s_historical_walkforward")
    app.add_api_route(
        "/api/research/g1s/observations",
        lambda limit=100: runtime.observations(limit=int(limit)),
        methods=["GET"], name="g1s_observations")
    app.add_api_route(
        "/api/research/g1s/resolved",
        lambda limit=100: runtime.observations(resolved=True, limit=int(limit)),
        methods=["GET"], name="g1s_resolved")
    app.add_api_route(
        "/api/research/g1s/models",
        lambda limit=100: runtime.models(limit=int(limit)),
        methods=["GET"], name="g1s_models")
    app.add_api_route(
        "/api/research/g1s/return-models",
        lambda limit=100: runtime.return_models(limit=int(limit)),
        methods=["GET"], name="g1s_return_models")
    app.add_api_route(
        "/api/research/g1s/calibrators",
        lambda limit=100: runtime.calibrators(limit=int(limit)),
        methods=["GET"], name="g1s_calibrators")
    app.add_api_route(
        "/api/research/g1s/cuts",
        lambda limit=100: runtime.cuts(limit=int(limit)),
        methods=["GET"], name="g1s_cuts")
    app.add_api_route(
        "/api/research/g1s/barriers",
        lambda limit=500: runtime.barriers(limit=int(limit)),
        methods=["GET"], name="g1s_barriers")
    app.add_api_route(
        "/api/research/g1s/path-metrics",
        lambda limit=500: runtime.path_metrics(limit=int(limit)),
        methods=["GET"], name="g1s_path_metrics")

    # Full-history metrics are worker-materialized. A request can never trigger
    # an OOS scan, refit or economic replay merely because the UI opened. All
    # immutable evidence payloads use the same proven pre-encoded transport so
    # readiness latency does not depend on which report happens to be largest.
    app.add_api_route("/api/research/g1s/oos", preencoded("probability_oos"),
                      methods=["GET"], name="g1s_oos")
    app.add_api_route("/api/research/g1s/continuous-oos", preencoded("continuous_oos"),
                      methods=["GET"], name="g1s_continuous_oos")
    app.add_api_route("/api/research/g1s/calibration-oos", preencoded("calibration_oos"),
                      methods=["GET"], name="g1s_calibration_oos")
    app.add_api_route("/api/research/g1s/ablation", preencoded("ablation"),
                      methods=["GET"], name="g1s_ablation")
    app.add_api_route("/api/research/g1s/trade-relevance", preencoded("trade_relevance"),
                      methods=["GET"], name="g1s_trade_relevance")
    app.add_api_route("/api/research/g1s/evidence-materialization",
                      runtime.evidence_materialization_status,
                      methods=["GET"], name="g1s_evidence_materialization")

    def ede_frozen_evidence():
        record = latest_frozen_evidence(evidence_ledger_path(app.state.engine), float("inf"))
        return record or {
            "contract_version": "g1s-ede-frozen-evidence-v1.2.2",
            "status": "INSUFFICIENT_DATA", "edge_candidates": [],
            "production_authority": False,
            "production_directional_authority": False,
            "auto_promotion": False, "may_trigger_exit_or_close": False,
        }

    app.add_api_route("/api/research/ede/frozen-evidence", ede_frozen_evidence,
                      methods=["GET"], name="ede_frozen_evidence")

    def ede_latest_audit():
        data_dir = Path(getattr(app.state.engine.settings, "data_dir", "."))
        paths = (
            data_dir / "research" / "ede_v13_latest_audit.json",
            data_dir / "research" / "ede_v121_latest_audit.json",
        )
        path = next((candidate for candidate in paths if candidate.exists()), None)
        if path is None:
            return {"status": "INSUFFICIENT_DATA", "report_available": False,
                    "production_authority": False, "auto_promotion": False}
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(body, dict):
                body.setdefault("report_available", True)
                body.setdefault("report_path_version", "v1.3" if "v13" in path.name else "v1.2.1")
            return body
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "UNAVAILABLE", "report_available": False,
                    "production_authority": False, "auto_promotion": False}

    app.add_api_route("/api/research/ede/audit", ede_latest_audit,
                      methods=["GET"], name="ede_latest_audit")

    def ede_shadow_status():
        cached_shadow = load_shadow_summary_cache(app.state.engine, cutoff_ts=float("inf"))
        if cached_shadow is None:
            return {
                "contract_version": "g1s-ede-shadow-summary-cache-v1.3.1",
                "available": False, "request_time_ledger_scan": False,
                "production_authority": False, "auto_promotion": False,
            }
        return {
            **cached_shadow,
            "available": True,
            "request_time_ledger_scan": False,
        }

    app.add_api_route("/api/research/ede/shadow", ede_shadow_status,
                      methods=["GET"], name="ede_shadow_status")

    app.add_api_route("/api/research/g1/q/audit", runtime.q_audit,
                      methods=["GET"], name="g1_q_maturity_audit")
    app.add_api_route(
        "/api/research/g1/management/local-outcomes",
        lambda limit=200: local.outcomes(limit=int(limit)),
        methods=["GET"], name="g1m_local_outcomes")
    app.add_api_route("/api/research/g1/management/local-edge", local.edge,
                      methods=["GET"], name="g1m_local_edge")
    app.add_api_route("/api/research/g1/management/local-status", local.status,
                      methods=["GET"], name="g1m_local_status")
    app.add_api_route("/api/research/runtime/materializers", runtime.materializer_status,
                      methods=["GET"], name="research_materializers")

    def _worker_status_body():
        """Lock-free worker lifecycle view.

        Lifecycle polling needs only in-memory state, so keep this path
        independent from every database connection and runtime lock.
        """
        state = getattr(app.state, "g1_research_worker", {}) or {}
        result = state.get("last_result")
        result_summary = None
        if isinstance(result, dict):
            g1s = result.get("g1s") or {}
            g1m_local = result.get("g1m_local") or {}
            result_summary = {
                "g1s": {"batch_limit": g1s.get("batch_limit")},
                "g1m_local": {"batch_limit": g1m_local.get("batch_limit")},
            }
            if "ede_v13_shadow" in result:
                ede_shadow = result.get("ede_v13_shadow") or {}
                result_summary["ede_v13_shadow"] = {
                    "refreshed": ede_shadow.get("refreshed"),
                    "reason": ede_shadow.get("reason"),
                    "summary": ede_shadow.get("summary"),
                }
        keys = (
            "contract_version", "scalability_refinement_version", "running",
            "startup_grace_sec", "first_cycle_not_before_ts", "last_started_ts",
            "last_finished_ts", "last_duration_ms", "last_error",
            "current_phase", "maintenance_running", "maintenance_phase",
            "last_maintenance_started_ts", "last_maintenance_finished_ts",
            "last_maintenance_duration_ms", "last_maintenance_error",
            "acceptance_gate_active", "acceptance_pause_active",
            "acceptance_gate_reason", "acceptance_gate_run_id",
            "acceptance_gate_expected_sha", "acceptance_gate_expires_at",
            "evidence_reports_request_time_scan",
            "historical_walkforward_runs_on_research_worker",
            "historical_walkforward_request_time_network_fetch",
            "ede_v13_shadow_runs_on_research_worker",
            "ede_v13_full_discovery_runs_on_request_path",
            "memory_pause_active", "memory_pressure",
        )
        return {
            "contract_version": "research-worker-status-v1",
            "worker": {key: state.get(key) for key in keys},
            "last_result": result_summary,
            "sqlite_access": False,
            "production_authority": False,
        }

    async def worker_status():
        """Event-loop-native status; never queue behind sync worker threads."""
        return _worker_status_body()

    app.add_api_route("/api/research/runtime/worker-status", worker_status,
                      methods=["GET"], name="research_worker_status")

    def runtime_status():
        """Fast aggregate health contract; never contend with research SQLite.

        Detailed G.1S/evidence/historical/management status remains available on
        the dedicated endpoints below. The aggregate route is used as an
        operational readiness probe, so executing those four SQLite-backed
        reports again here only created lock contention and false production
        failures while the research worker was legitimately maintaining data.
        """
        worker = _worker_status_body()["worker"]
        return {
            "contract_version": "research-runtime-status-v2",
            "worker": worker,
            "short_horizon": {
                "status_endpoint": "/api/research/runtime/materializers",
                "request_time_materialization": False,
            },
            "evidence_materialization": {
                "status_endpoint": "/api/research/g1s/evidence-materialization",
                "request_time_full_history_scan": False,
            },
            "historical_walk_forward": {
                "status_endpoint": "/api/research/g1s/historical-wf",
                "request_time_network_fetch": False,
            },
            "management_local": {
                "status_endpoint": "/api/research/g1/management/local-status",
                "request_time_materialization": False,
            },
            "aggregate_status_mode": "LOCK_FREE_LIFECYCLE",
            "sqlite_access": False,
            "market_collection_separate_from_research": True,
            "request_time_full_history_evidence_scan": False,
            "request_time_historical_network_fetch": False,
            "production_authority": False,
        }

    app.add_api_route("/api/research/runtime/status", runtime_status,
                      methods=["GET"], name="research_runtime_status")
    app.state.g1s_routes_installed = True
