"""Phase G.1E Intelligence Cockpit routes."""
from __future__ import annotations

from fastapi import FastAPI

from .g1_intelligence_page import intelligence_page


def install_g1_intelligence_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_intelligence_routes_installed", False):
        return
    runtime = app.state.intelligence

    app.add_api_route(
        "/intelligence", intelligence_page,
        methods=["GET"], name="intelligence_lab",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/status", runtime.status,
        methods=["GET"], name="g1_intelligence_status",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/pipeline", runtime.pipeline,
        methods=["GET"], name="g1_intelligence_pipeline",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/forecast-quality", runtime.forecast_quality,
        methods=["GET"], name="g1_intelligence_forecast_quality",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/calibration", runtime.calibration,
        methods=["GET"], name="g1_intelligence_calibration",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/pending", runtime.pending,
        methods=["GET"], name="g1_intelligence_pending",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/resolved", runtime.resolved,
        methods=["GET"], name="g1_intelligence_resolved",
    )
    app.add_api_route(
        "/api/research/g1/intelligence/history", runtime.history,
        methods=["GET"], name="g1_intelligence_history",
    )
    app.state.g1_intelligence_routes_installed = True
