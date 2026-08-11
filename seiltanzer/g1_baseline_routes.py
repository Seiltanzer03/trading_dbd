"""Read-only Phase G.1B baseline measurement API routes."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def install_g1_baseline_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_baseline_routes_installed", False):
        return

    def baseline_status(cut_id: str | None = None):
        try:
            return app.state.engine.passive.g1_baseline_status(cut_id=cut_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def baseline_cohorts(cut_id: str | None = None):
        try:
            return app.state.engine.passive.g1_baseline_cohorts(cut_id=cut_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.add_api_route(
        "/api/research/g1/baselines/status", baseline_status,
        methods=["GET"], name="g1_baseline_status",
    )
    app.add_api_route(
        "/api/research/g1/baselines/cohorts", baseline_cohorts,
        methods=["GET"], name="g1_baseline_cohorts",
    )
    app.state.g1_baseline_routes_installed = True
