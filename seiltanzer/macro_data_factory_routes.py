"""Explicit, research-only API for the macro LLM data factory."""
from __future__ import annotations

import time

from fastapi import FastAPI

from .fomc_official_source import refresh_latest_fomc
from .macro_data_factory import MacroDataFactory
from .macro_data_factory_causality_refinement import install_macro_data_factory_causality_refinement
from .macro_t0_context import install_macro_t0_context
from .research_llm_cost_guard import (
    cost_guard_status,
    guarded_macro_extractor,
    reserve_macro_ingest_request,
)


def install_macro_data_factory_routes(app: FastAPI) -> None:
    if getattr(app.state, "macro_data_factory_routes_installed", False):
        return
    runtime = getattr(app.state.engine, "short_horizon", None)
    if runtime is None:
        raise RuntimeError("G.1S integration must be installed before macro data factory")
    install_macro_data_factory_causality_refinement()
    factory = MacroDataFactory(runtime)
    app.state.macro_data_factory = factory
    app.state.engine.macro_data_factory = factory
    # Future T0 rows receive only already-completed causal semantic records.
    # Existing ML feature vectors do not read macro_context_v1.
    install_macro_t0_context(app.state.engine, factory)

    def status():
        return {
            **factory.status(),
            "llm_cost_guard": cost_guard_status(),
            "public_ingest_mode": "official_federal_reserve_fomc_only",
            "arbitrary_document_post_enabled": False,
        }

    app.add_api_route(
        "/api/research/macro/status",
        status,
        methods=["GET"],
        name="macro_data_factory_status",
    )

    def refresh_fomc():
        # Bound both official network refreshes and DB writes before any work.
        try:
            reserve_macro_ingest_request()
        except RuntimeError as exc:
            return {
                "contract_version": "macro-data-factory-v1",
                "status": "RATE_LIMITED",
                "reason": str(exc)[:160],
                "research_only": True,
                "production_authority": False,
            }
        try:
            return refresh_latest_fomc(factory, extractor=guarded_macro_extractor)
        except (RuntimeError, ValueError) as exc:
            return {
                "contract_version": "macro-data-factory-v1",
                "status": "UNAVAILABLE",
                "reason": str(exc)[:180],
                "official_source_verified": False,
                "research_only": True,
                "production_authority": False,
            }

    app.add_api_route(
        "/api/research/macro/fomc/refresh",
        refresh_fomc,
        methods=["POST"],
        name="macro_data_factory_fomc_refresh",
    )

    def latest(captured_ts: float | None = None, family: str = "FOMC_STATEMENT"):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        return factory.latest_admissible(cutoff, family=family)

    app.add_api_route(
        "/api/research/macro/latest",
        latest,
        methods=["GET"],
        name="macro_data_factory_latest",
    )
    app.state.macro_data_factory_routes_installed = True
