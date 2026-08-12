"""Read-only APIs for G.1S, Q maturity diagnostics and G.1-M.1 local feedback."""
from __future__ import annotations

from fastapi import FastAPI


def install_g1_short_horizon_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1s_routes_installed", False):
        return
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

    app.add_api_route("/api/research/g1s/horizons", horizons,
                      methods=["GET"], name="g1s_horizons")
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
        "/api/research/g1s/barriers",
        lambda limit=500: runtime.barriers(limit=int(limit)),
        methods=["GET"], name="g1s_barriers")
    app.add_api_route("/api/research/g1s/oos", runtime.prospective_oos,
                      methods=["GET"], name="g1s_oos")
    app.add_api_route("/api/research/g1s/ablation", runtime.ablation,
                      methods=["GET"], name="g1s_ablation")
    app.add_api_route("/api/research/g1s/trade-relevance", runtime.trade_relevance,
                      methods=["GET"], name="g1s_trade_relevance")
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
        return {
            "contract_version": "research-runtime-status-v1",
            "short_horizon": runtime.materializer_status(),
            "management_local": local.status(),
            "market_collection_separate_from_research": True,
            "production_authority": False,
        }

    app.add_api_route("/api/research/runtime/status", runtime_status,
                      methods=["GET"], name="research_runtime_status")
    app.state.g1s_routes_installed = True
