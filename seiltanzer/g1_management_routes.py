"""Read-only Phase G.1-M research APIs."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .g1_management_page import management_edge_page


def install_g1_management_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_management_routes_installed", False):
        return
    runtime = app.state.engine.management

    app.add_api_route(
        "/management-edge", management_edge_page,
        methods=["GET"], name="g1m_management_edge_page",
    )
    app.add_api_route(
        "/api/research/g1/management/status", runtime.status,
        methods=["GET"], name="g1m_status",
    )
    app.add_api_route(
        "/api/research/g1/management/observations",
        lambda limit=100: runtime.observations(limit=limit),
        methods=["GET"], name="g1m_observations",
    )
    app.add_api_route(
        "/api/research/g1/management/pending",
        lambda limit=100: runtime.observations(resolved=False, limit=limit),
        methods=["GET"], name="g1m_pending",
    )
    app.add_api_route(
        "/api/research/g1/management/resolved",
        lambda limit=100: runtime.observations(resolved=True, limit=limit),
        methods=["GET"], name="g1m_resolved",
    )
    app.add_api_route(
        "/api/research/g1/management/policies", runtime.policies,
        methods=["GET"], name="g1m_policies",
    )
    app.add_api_route(
        "/api/research/g1/management/cohorts", runtime.cohorts,
        methods=["GET"], name="g1m_cohorts",
    )
    app.add_api_route(
        "/api/research/g1/management/edge", runtime.edge,
        methods=["GET"], name="g1m_edge",
    )

    def decision(observation_id: str):
        body = runtime.decision(observation_id)
        if body is None:
            raise HTTPException(404, "management observation not found")
        return body

    app.add_api_route(
        "/api/research/g1/management/decision/{observation_id}", decision,
        methods=["GET"], name="g1m_decision",
    )
    app.state.g1_management_routes_installed = True
