"""Isolated read-only route for causal G.1S historical analogs."""
from __future__ import annotations

from fastapi import FastAPI

from .g1_historical_analog import DEFAULT_FEATURE_SET, DEFAULT_K, historical_analogs


def install_g1_historical_analog_routes(app: FastAPI) -> None:
    if getattr(app.state, "g1_historical_analog_routes_installed", False):
        return
    runtime = getattr(app.state.engine, "short_horizon", None)
    if runtime is None:
        raise RuntimeError("G.1S integration must be installed before analog routes")

    def analogs(observation_id: str, k: int = DEFAULT_K,
                feature_set: str = DEFAULT_FEATURE_SET):
        return historical_analogs(runtime, observation_id, k=k, feature_set=feature_set)

    app.add_api_route(
        "/api/research/g1s/analogs",
        analogs,
        methods=["GET"],
        name="g1s_historical_analogs",
    )
    app.state.g1_historical_analog_routes_installed = True
