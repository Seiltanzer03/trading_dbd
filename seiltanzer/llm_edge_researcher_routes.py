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
from .llm_edge_exploratory import (
    ensure_tables as ensure_exploratory_tables,
    evaluate_all as evaluate_all_exploratory,
)
from .llm_edge_lifecycle import (
    read_cached_materialized_lifecycle,
    read_cached_materialized_lifecycle_json,
    read_cached_materialized_status,
)
from .llm_edge_prospective_journal import initialize_journal_storage
from .llm_edge_researcher import edge_researcher_status, propose_edge_hypotheses
from .production_resource_guard import trim_memory_for_pressure
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
    ensure_exploratory_tables(runtime)

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
    explore_lock = threading.Lock()
    explore_state = {
        "running": False,
        "started_ts": None,
        "finished_ts": None,
        "last_result": None,
        "last_error": None,
    }

    def status():
        cached = read_cached_materialized_status(runtime)
        summary = cached.get("researcher") or {}
        return {
            **edge_researcher_status(runtime, materialized_payload=cached),
            "deterministic_evaluator": edge_evaluator_status(
                runtime, materialized_payload=cached
            ),
            "evaluator_job": dict(eval_state),
            "exploratory": {
                "status": cached.get("status", "INITIALIZING"),
                "evaluated_n": int(summary.get("early_evaluated") or 0),
                "advantage_n": int(summary.get("early_advantage") or 0),
                "disadvantage_n": int(summary.get("early_disadvantage") or 0),
                "mixed_n": int(summary.get("early_mixed") or 0),
                "undecided_n": int(summary.get("early_undecided") or 0),
                "production_authority": False,
                "position_manager_weight_cap": 0.15,
                "position_manager_weight_requires": "LIMITED_AND_CURRENT_T0_MATCH",
            },
            "exploratory_job": dict(explore_state),
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
                try:
                    trim_memory_for_pressure()
                except Exception:
                    pass

    def evaluate(run_id: str | None = None, background: bool = True, max_runs: int = 2):
        # Deterministic only: no provider call and no Active Edge write.
        max_runs = max(1, min(int(max_runs), 5))
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
        cached = read_cached_materialized_status(runtime)
        researcher = cached.get("researcher") or cached.get("summary") or {}
        return {
            "status": "OK",
            "job": dict(eval_state),
            "pending_summary": {
                "total_hypotheses": int(researcher.get("hypotheses", researcher.get("hypotheses_total", 0)) or 0),
                "pending_hypotheses": int(researcher.get("pending_hypotheses", 0) or 0),
                "discovery_signals": int(researcher.get("discovery_signals", 0) or 0),
                "rejected": int(researcher.get("rejected", 0) or 0),
            },
        }

    app.add_api_route(
        "/api/research/g1s/edge-researcher/evaluate/status",
        evaluate_status,
        methods=["GET"],
        name="g1s_llm_edge_researcher_evaluate_status",
    )

    def _execute_exploratory(max_hypotheses: int):
        try:
            result = evaluate_all_exploratory(
                runtime, max_hypotheses=max_hypotheses)
            with explore_lock:
                explore_state["last_result"] = result
            engine = getattr(app.state, "engine", None)
            if engine is not None:
                from .llm_edge_lifecycle import materialize_lifecycle
                materialize_lifecycle(engine)
        except Exception as exc:
            with explore_lock:
                explore_state["last_error"] = f"{type(exc).__name__}: {str(exc)}"
            logger.exception("Exploratory verdict background worker failed")
        finally:
            with explore_lock:
                explore_state["finished_ts"] = time.time()
                explore_state["running"] = False
            try:
                trim_memory_for_pressure()
            except Exception:
                pass

    def explore(max_hypotheses: int = 200):
        max_hypotheses = max(1, min(int(max_hypotheses), 500))
        with explore_lock:
            if explore_state["running"]:
                return {
                    "status": "ALREADY_RUNNING",
                    "started_ts": explore_state["started_ts"],
                }
            explore_state["running"] = True
            explore_state["started_ts"] = time.time()
            explore_state["last_error"] = None
        worker_thread = threading.Thread(
            target=_execute_exploratory,
            args=(max_hypotheses,),
            daemon=True,
            name="edge-research-exploratory",
        )
        worker_thread.start()
        return {
            "status": "ACCEPTED",
            "max_hypotheses": max_hypotheses,
            "production_authority": False,
        }

    app.add_api_route(
        "/api/research/g1s/edge-researcher/explore",
        explore,
        methods=["POST"],
        name="g1s_llm_edge_researcher_explore",
    )

    install_ml_research_broadcast(app)
    app.state.llm_edge_researcher_routes_installed = True
