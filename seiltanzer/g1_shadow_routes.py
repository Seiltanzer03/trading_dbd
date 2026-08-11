"""Read-only Phase G.1C shadow calibration research routes."""
from __future__ import annotations

from fastapi import FastAPI


def install_g1_shadow_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_shadow_routes_installed", False):
        return

    def status():
        return app.state.engine.passive.g1c_status()

    def models(limit: int = 200):
        return app.state.engine.passive.g1c_models(limit=limit)

    def cohorts():
        return app.state.engine.passive.g1c_cohorts()

    def predictions(limit: int = 200, instrument: str | None = None):
        return app.state.engine.passive.g1c_predictions(limit=limit, instrument=instrument)

    app.add_api_route(
        "/api/research/g1/calibrators/status", status,
        methods=["GET"], name="g1c_calibrator_status",
    )
    app.add_api_route(
        "/api/research/g1/calibrators/models", models,
        methods=["GET"], name="g1c_calibrator_models",
    )
    app.add_api_route(
        "/api/research/g1/calibrators/cohorts", cohorts,
        methods=["GET"], name="g1c_calibrator_cohorts",
    )
    app.add_api_route(
        "/api/research/g1/calibrators/predictions", predictions,
        methods=["GET"], name="g1c_calibrator_predictions",
    )
    app.state.g1_shadow_routes_installed = True
