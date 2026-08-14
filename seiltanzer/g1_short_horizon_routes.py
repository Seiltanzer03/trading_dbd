"""Read-only APIs for G.1S, Q maturity diagnostics and G.1-M.1 local feedback."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

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

    def worker_status():
        """Lock-free worker lifecycle view.

        The full runtime status intentionally includes several SQLite-backed
        materializer summaries. Polling it while the research cycle owns those
        runtimes can make a health probe compete with maintenance. Lifecycle
        polling needs only in-memory state, so keep that path independent from
        every database connection and runtime lock.
        """
        state = getattr(app.state, "g1_research_worker", {}) or {}
        result = state.get("last_result")
        result_summary = None
        if isinstance(result, dict):
            g1s = result.get("g1s") or {}
            g1m_local = result.get("g1m_local") or {}
            ede_shadow = result.get("ede_v13_shadow") or {}
            result_summary = {
                "g1s": {"batch_limit": g1s.get("batch_limit")},
                "g1m_local": {"batch_limit": g1m_local.get("batch_limit")},
                "ede_v13_shadow": {
                    "refreshed": ede_shadow.get("refreshed"),
                    "reason": ede_shadow.get("reason"),
                    "summary": ede_shadow.get("summary"),
                },
            }
        keys = (
            "contract_version", "scalability_refinement_version", "running",
            "startup_grace_sec", "first_cycle_not_before_ts", "last_started_ts",
            "last_finished_ts", "last_duration_ms", "last_error",
        )
        return {
            "contract_version": "research-worker-status-v1",
            "worker": {key: state.get(key) for key in keys},
            "last_result": result_summary,
            "sqlite_access": False,
            "production_authority": False,
        }

    app.add_api_route("/api/research/runtime/worker-status", worker_status,
                      methods=["GET"], name="research_worker_status")

    def runtime_status():
        worker = dict(getattr(app.state, "g1_research_worker", {}) or {})
        return {
            "contract_version": "research-runtime-status-v2",
            "worker": worker,
            "short_horizon": runtime.materializer_status(),
            "evidence_materialization": runtime.evidence_materialization_status(),
            "historical_walk_forward": runtime.historical_walkforward_status(),
            "management_local": local.status(),
            "market_collection_separate_from_research": True,
            "request_time_full_history_evidence_scan": False,
            "request_time_historical_network_fetch": False,
            "production_authority": False,
        }

    app.add_api_route("/api/research/runtime/status", runtime_status,
                      methods=["GET"], name="research_runtime_status")
    app.state.g1s_routes_installed = True
