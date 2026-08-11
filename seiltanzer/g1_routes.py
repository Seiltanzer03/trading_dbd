"""Read-only Phase G.1A research API routes."""
from __future__ import annotations

from fastapi import FastAPI


def install_g1_dataset_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_dataset_routes_installed", False):
        return

    def dataset_status():
        return app.state.engine.passive.g1_dataset_status()

    def dataset_cohorts():
        return app.state.engine.passive.g1_dataset_cohorts()

    def dataset_exclusions():
        return app.state.engine.passive.g1_dataset_exclusions()

    def dataset_cuts(limit: int = 20):
        return app.state.engine.passive.g1_dataset_cuts(limit=limit)

    app.add_api_route(
        "/api/research/g1/dataset/status", dataset_status,
        methods=["GET"], name="g1_dataset_status",
    )
    app.add_api_route(
        "/api/research/g1/dataset/cohorts", dataset_cohorts,
        methods=["GET"], name="g1_dataset_cohorts",
    )
    app.add_api_route(
        "/api/research/g1/dataset/exclusions", dataset_exclusions,
        methods=["GET"], name="g1_dataset_exclusions",
    )
    app.add_api_route(
        "/api/research/g1/dataset/cuts", dataset_cuts,
        methods=["GET"], name="g1_dataset_cuts",
    )
    app.state.g1_dataset_routes_installed = True
