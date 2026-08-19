"""Research-only APIs and runtimes for causal official macro data."""
from __future__ import annotations

import os
import time

from fastapi import FastAPI

from .fomc_official_source import refresh_latest_fomc
from .macro_data_factory import MacroDataFactory
from .macro_data_factory_causality_refinement import install_macro_data_factory_causality_refinement
from .macro_edge_evidence_refinement import install_macro_edge_evidence_refinement
from .macro_fomc_extraction_refinement import install_fomc_extraction_refinement
from .macro_fomc_runtime import FOMCOfficialRuntime
from .macro_ism_parser_refinement import install_ism_roundup_parser_refinement
from .macro_ism_resilience import install_ism_source_resilience
from .macro_numeric_data import NumericMacroRuntime, NumericMacroStore, research_context
from .macro_t0_context import install_macro_t0_context
from .macro_transport_refinement import (
    install_macro_transport_refinement,
    macro_transport_status,
)
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
    os.environ.setdefault("DATA_FACTORY_MODEL", "openai/gpt-4o-mini")
    install_macro_data_factory_causality_refinement()
    # A rejected v1 extraction stays immutable audit evidence. V2 changes the
    # prompt/cache key and asks OpenRouter for a strict six-field JSON schema.
    install_fomc_extraction_refinement()
    # The production VPS can be 403-blocked by public BLS/ISM endpoints while a
    # hosted runner succeeds. Reuse the configured outbound transport only; the
    # official URLs, parsers, first-seen timestamps and provenance remain exact.
    install_macro_transport_refinement()
    # The current Services roundup uses "increasing 0.1 percentage point to 54.1";
    # install the narrow official-prose refinement before the source wrapper.
    install_ism_roundup_parser_refinement()
    # ISM's newest full report can send non-browser clients to SSO/CAPTCHA.
    # The resilient source then uses ISM's own validated public roundup plus the
    # immediately previous official report. Missing components remain missing.
    install_ism_source_resilience()
    # Macro values already frozen into a T0 become canonical EDE features.  The
    # dependence unit for a macro-conditioned rule is the official release_id,
    # never the number of repeated market observations carrying that release.
    install_macro_edge_evidence_refinement()
    factory = MacroDataFactory(runtime)
    numeric_store = NumericMacroStore(runtime)
    numeric_runtime = NumericMacroRuntime(numeric_store)
    fomc_runtime = FOMCOfficialRuntime(factory)
    # T0 capture reads these already-materialized stores only. No network/LLM can
    # occur inside a market observation capture.
    factory.numeric_release_store = numeric_store
    app.state.macro_data_factory = factory
    app.state.macro_numeric_store = numeric_store
    app.state.macro_numeric_runtime = numeric_runtime
    app.state.macro_fomc_runtime = fomc_runtime
    app.state.engine.macro_data_factory = factory
    install_macro_t0_context(app.state.engine, factory)

    # Delayed low-frequency workers: AI startup/review math gets priority on the
    # 2 GB host. Thereafter each official family is checked at most hourly.
    numeric_runtime.start()
    fomc_runtime.start()

    def status():
        return {
            **factory.status(),
            "numeric": numeric_runtime.status(),
            "numeric_transport": macro_transport_status(),
            "fomc_runtime": fomc_runtime.status(),
            "llm_cost_guard": cost_guard_status(),
            "official_families": [
                "CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES", "FOMC_STATEMENT"
            ],
            "official_sources_only": True,
            "no_placeholders": True,
            "consensus_feed_available": False,
            "surprise_computed_without_consensus": False,
            "macro_ede_dependency_unit": "OFFICIAL_RELEASE_ID",
            "macro_repeated_t0_increases_effective_n": False,
            "arbitrary_document_post_enabled": False,
            "research_only": True,
            "production_authority": False,
        }

    app.add_api_route(
        "/api/research/macro/status",
        status,
        methods=["GET"],
        name="macro_data_factory_status",
    )

    def refresh_fomc():
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

    def refresh_numeric():
        # Deterministic BLS/ISM fetch. No LLM and no synthetic fallback.
        return numeric_runtime.refresh()

    app.add_api_route(
        "/api/research/macro/numeric/refresh",
        refresh_numeric,
        methods=["POST"],
        name="macro_numeric_refresh",
    )

    def latest(captured_ts: float | None = None, family: str = "FOMC_STATEMENT"):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        family_key = str(family or "").upper()
        if family_key in {"CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES"}:
            return numeric_store.latest_admissible(family_key, cutoff)
        return factory.latest_admissible(cutoff, family=family_key)

    app.add_api_route(
        "/api/research/macro/latest",
        latest,
        methods=["GET"],
        name="macro_data_factory_latest",
    )

    def context(captured_ts: float | None = None):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        return research_context(numeric_store, cutoff)

    app.add_api_route(
        "/api/research/macro/numeric/context",
        context,
        methods=["GET"],
        name="macro_numeric_context",
    )
    app.state.macro_data_factory_routes_installed = True