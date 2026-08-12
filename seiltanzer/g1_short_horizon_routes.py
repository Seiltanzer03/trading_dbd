"""Read-only APIs for G.1S, Q maturity diagnostics and G.1-M.1 local feedback."""
from __future__ import annotations

from fastapi import FastAPI

from .g1_short_horizon_final_report import install_g1_short_horizon_final_report
from .storage_restore_drill import last_restore_drill


def install_g1_short_horizon_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1s_routes_installed", False):
        return
    install_g1_short_horizon_final_report()
    runtime = getattr(app.state.engine, "short_horizon", None)
    local = getattr(app.state.engine, "management_local", None)
    if runtime is None or local is None:
        raise RuntimeError("G.1S integration must be installed before routes")

    app.add_api_route("/api/research/g1s/status", runtime.status,
                      methods=["GET"], name="g1s_status")

    def horizons():
        status = runtime.status()
        return {"contract_version": status["contract_version"],
                "items": [runtime.horizon_report(h) for h in (15,30,60,120,240)]}

    def cached(name: str):
        return runtime.materialized_evidence_report(name)

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

    # Full-history metrics are worker-materialized.  A request can never trigger
    # an OOS scan, refit or economic replay merely because the UI opened.
    app.add_api_route("/api/research/g1s/oos", lambda: cached("probability_oos"),
                      methods=["GET"], name="g1s_oos")
    app.add_api_route("/api/research/g1s/continuous-oos", lambda: cached("continuous_oos"),
                      methods=["GET"], name="g1s_continuous_oos")
    app.add_api_route("/api/research/g1s/calibration-oos", lambda: cached("calibration_oos"),
                      methods=["GET"], name="g1s_calibration_oos")
    app.add_api_route("/api/research/g1s/ablation", lambda: cached("ablation"),
                      methods=["GET"], name="g1s_ablation")
    app.add_api_route("/api/research/g1s/trade-relevance", lambda: cached("trade_relevance"),
                      methods=["GET"], name="g1s_trade_relevance")
    app.add_api_route("/api/research/g1s/evidence-materialization",
                      runtime.evidence_materialization_status,
                      methods=["GET"], name="g1s_evidence_materialization")

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

    def runtime_status():
        worker = dict(getattr(app.state, "g1_research_worker", {}) or {})
        return {
            "contract_version": "research-runtime-status-v2",
            "worker": worker,
            "short_horizon": runtime.materializer_status(),
            "evidence_materialization": runtime.evidence_materialization_status(),
            "management_local": local.status(),
            "market_collection_separate_from_research": True,
            "request_time_full_history_evidence_scan": False,
            "production_authority": False,
        }

    app.add_api_route("/api/research/runtime/status", runtime_status,
                      methods=["GET"], name="research_runtime_status")
    app.state.g1s_routes_installed = True
