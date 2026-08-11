"""Read-only Phase G.1B.1 Q evidence diagnostics routes."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def install_g1_q_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_q_routes_installed", False):
        return

    def q_status():
        return app.state.engine.passive.g1_q_status()

    def q_instruments():
        return app.state.engine.passive.g1_q_instruments()

    def q_blockers():
        return app.state.engine.passive.g1_q_blockers()

    def q_attempts(limit: int = 100, instrument: str | None = None):
        try:
            return app.state.engine.passive.g1_q_attempts(limit=limit, instrument=instrument)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.add_api_route(
        "/api/research/g1/q/status", q_status,
        methods=["GET"], name="g1_q_status",
    )
    app.add_api_route(
        "/api/research/g1/q/instruments", q_instruments,
        methods=["GET"], name="g1_q_instruments",
    )
    app.add_api_route(
        "/api/research/g1/q/blockers", q_blockers,
        methods=["GET"], name="g1_q_blockers",
    )
    app.add_api_route(
        "/api/research/g1/q/attempts", q_attempts,
        methods=["GET"], name="g1_q_attempts",
    )
    app.state.g1_q_routes_installed = True
