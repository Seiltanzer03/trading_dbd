"""Research-only routes for the LLM Edge Researcher."""
from __future__ import annotations

from fastapi import FastAPI

from .llm_edge_researcher import edge_researcher_status, propose_edge_hypotheses
from .research_llm_cost_guard import guarded_edge_researcher_provider


def install_llm_edge_researcher_routes(app: FastAPI) -> None:
    if getattr(app.state, "llm_edge_researcher_routes_installed", False):
        return
    runtime = getattr(app.state.engine, "short_horizon", None)
    if runtime is None:
        raise RuntimeError("G.1S integration must be installed before LLM edge researcher")

    def status():
        return edge_researcher_status(runtime)

    app.add_api_route(
        "/api/research/g1s/edge-researcher/status",
        status,
        methods=["GET"],
        name="g1s_llm_edge_researcher_status",
    )

    def propose(observation_id: str | None = None, max_hypotheses: int = 5):
        return propose_edge_hypotheses(
            runtime,
            observation_id,
            max_hypotheses=max_hypotheses,
            provider=guarded_edge_researcher_provider,
        )

    app.add_api_route(
        "/api/research/g1s/edge-researcher/propose",
        propose,
        methods=["POST"],
        name="g1s_llm_edge_researcher_propose",
    )
    app.state.llm_edge_researcher_routes_installed = True
