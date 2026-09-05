"""Research-only routes for the LLM Edge Researcher."""
from __future__ import annotations

import logging
import threading
import time
from fastapi import FastAPI, Response

from .llm_edge_evaluator import (
    edge_evaluator_status,
    evaluate_edge_research_run,
    evaluate_pending_edge_research_runs,
    pending_edge_research_summary,
)
from .llm_edge_lifecycle import read_cached_materialized_lifecycle_json
from .llm_edge_prospective_journal import initialize_journal_storage
from .llm_edge_researcher import edge_researcher_status, propose_edge_hypotheses
from .research_llm_cost_guard import guarded_edge_researcher_provider
from .ml_research_broadcast import install_ml_research_broadcast

logger = logging.getLogger("seiltanzer.edge_researcher_routes")


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

    eval_lock = threading.Lock()
    eval_state = {
        "running": False,
        "started_ts": None,
        "finished_ts": None,
        "last_result": None,
        "last_error": None,
    }

    def status():
        return {
            **edge_researcher_status(runtime),
            "deterministic_evaluator": edge_evaluator_status(runtime),
            "evaluator_job": dict(eval_state),
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

    def _execute_background_evaluation(target_run_id: str | None, max_runs: int):
        with eval_lock:
            eval_state["running"] = True
            eval_state["started_ts"] = time.time()
            eval_state["last_error"] = None
            try:
                if target_run_id and target_run_id not in {"pending", "all"}:
                    res = evaluate_edge_research_run(runtime, target_run_id)
                else:
                    res = evaluate_pending_edge_research_runs(runtime, max_runs=max_runs)
                eval_state["last_result"] = res

                engine = getattr(app.state, "engine", None)
                if engine is not None:
                    try:
                        from .llm_edge_candidate_lifecycle import freeze_discovery_signals
                        freeze_discovery_signals(engine)
                    except Exception as e:
                        logger.warning("freeze_discovery_signals failed: %s", e)
                    try:
                        from .llm_edge_lifecycle import materialize_lifecycle
                        materialize_lifecycle(engine)
                    except Exception as e:
                        logger.warning("materialize_lifecycle failed: %s", e)
            except Exception as exc:
                eval_state["last_error"] = f"{type(exc).__name__}: {str(exc)}"
                logger.exception("Evaluation background worker failed")
            finally:
                eval_state["finished_ts"] = time.time()
                eval_state["running"] = False

    def evaluate(run_id: str | None = None, background: bool = True, max_runs: int = 20):
        # Deterministic only: no provider call and no Active Edge write.
        if not background:
            if run_id and run_id not in {"pending", "all"}:
                return evaluate_edge_research_run(runtime, run_id)
            return evaluate_pending_edge_research_runs(runtime, max_runs=max_runs)

        if eval_state["running"]:
            return {
                "status": "ALREADY_RUNNING",
                "message": "Deterministic evaluation is already running in background",
                "started_ts": eval_state["started_ts"],
            }

        worker_thread = threading.Thread(
            target=_execute_background_evaluation,
            args=(run_id, max_runs),
            daemon=True,
            name="edge-research-evaluator",
        )
        worker_thread.start()
        return {
            "status": "ACCEPTED",
            "message": "Background evaluation job started",
            "run_id": run_id,
            "max_runs": max_runs,
        }

    app.add_api_route(
        "/api/research/g1s/edge-researcher/evaluate",
        evaluate,
        methods=["POST"],
        name="g1s_llm_edge_researcher_evaluate",
    )

    def evaluate_status():
        summary = pending_edge_research_summary(runtime)
        return {
            "status": "OK",
            "job": dict(eval_state),
            "pending_summary": summary,
        }

    app.add_api_route(
        "/api/research/g1s/edge-researcher/evaluate/status",
        evaluate_status,
        methods=["GET"],
        name="g1s_llm_edge_researcher_evaluate_status",
    )

    install_ml_research_broadcast(app)
    app.state.llm_edge_researcher_routes_installed = True
