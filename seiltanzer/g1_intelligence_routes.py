"""Phase G.1E Intelligence Cockpit routes."""
from __future__ import annotations

from fastapi import FastAPI

from .g1_intelligence_nonblocking import install_nonblocking_runtime
from .g1_intelligence_page_refinement import intelligence_page
from .g1_intelligence_performance import install_g1_intelligence_performance
from .g1_intelligence_refinement import install_g1_intelligence_refinement

# Keep the presentation contract aligned with G.1C's semantic-scope readiness and
# coalesce repeated read-only scans when one cockpit page opens several panels.
install_g1_intelligence_refinement()
install_g1_intelligence_performance()


def install_g1_intelligence_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_intelligence_routes_installed", False):
        return
    runtime = app.state.intelligence
    # Heavy G.1A/B/B.1/C aggregation must not delay service startup or a cold
    # browser request. The app-specific runtime is warmed in the background.
    install_nonblocking_runtime(runtime)

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
