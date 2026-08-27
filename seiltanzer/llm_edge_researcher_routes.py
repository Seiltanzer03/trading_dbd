"""Research-only routes for the LLM Edge Researcher."""
from __future__ import annotations

from fastapi import FastAPI, Response

from .llm_edge_evaluator import edge_evaluator_status, evaluate_edge_research_run
from .llm_edge_lifecycle import read_cached_materialized_lifecycle_json
from .llm_edge_prospective_journal import initialize_journal_storage
from .llm_edge_researcher import edge_researcher_status, propose_edge_hypotheses
from .research_llm_cost_guard import guarded_edge_researcher_provider


def install_llm_edge_researcher_routes(app: FastAPI) -> None:
    if getattr(app.state, "llm_edge_researcher_routes_installed", False):
        return
    runtime = getattr(app.state.engine, "short_horizon", None)
    if runtime is None:
        raise RuntimeError("G.1S integration must be installed before LLM edge researcher")

    # Startup-only additive DDL. GET endpoints below remain materialized reads.
    initialize_journal_storage(runtime)

    # PR C must be visible immediately after app startup instead of waiting for
    # the low-priority research worker.  The startup upgrader preserves the
    # previous materialized counts/candidates and adds only versioned scheduler
    # metadata; it performs no evaluation, feature-history scan or provider call.
    from .llm_edge_pr_c_startup import initialize_pr_c_materialized_state
    initialize_pr_c_materialized_state(runtime)

    def status():
        return {
            **edge_researcher_status(runtime),
            "deterministic_evaluator": edge_evaluator_status(runtime),
        }

    app.add_api_route(
        "/api/research/g1s/edge-researcher/status",
        status,
        methods=["GET"],
        name="g1s_llm_edge_researcher_status",
    )

    def lifecycle():
        return Response(
            content=read_cached_materialized_lifecycle_json(runtime),
            media_type="application/json",
        )

    app.add_api_route(
        "/api/research/g1s/edge-researcher/lifecycle",
        lifecycle,
        methods=["GET"],
        name="g1s_llm_edge_researcher_lifecycle",
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

    def evaluate(run_id: str | None = None):
        # Deterministic only: no provider call and no Active Edge write.
        return evaluate_edge_research_run(runtime, run_id)

    app.add_api_route(
        "/api/research/g1s/edge-researcher/evaluate",
        evaluate,
        methods=["POST"],
        name="g1s_llm_edge_researcher_evaluate",
    )
    app.state.llm_edge_researcher_routes_installed = True
