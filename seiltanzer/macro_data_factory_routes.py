"""Research-only APIs and runtimes for causal official macro data."""
from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import FastAPI

from .fomc_official_source import refresh_latest_fomc
from .macro_bls_historical_bootstrap import (
    BLSHistoricalBootstrapRuntime,
    BLSHistoricalReleaseStore,
)
from .macro_bls_historical_ede_refinement import install_bls_historical_ede_refinement
from .macro_data_factory import MacroDataFactory
from .macro_data_factory_causality_refinement import install_macro_data_factory_causality_refinement
from .macro_edge_evidence_refinement import install_macro_edge_evidence_refinement
from .macro_fomc_deterministic_bootstrap import FOMCDeterministicBootstrapRuntime
from .macro_fomc_deterministic_ede_refinement import (
    install_fomc_deterministic_ede_refinement,
)
from .macro_fomc_deterministic_store_refinement import (
    StrictFOMCDeterministicReleaseStore,
)
from .macro_fomc_extraction_refinement import install_fomc_extraction_refinement
from .macro_fomc_runtime import FOMCOfficialRuntime
from .macro_ism_historical_bootstrap import (
    ISMHistoricalBootstrapRuntime,
    ISMHistoricalReleaseStore,
)
from .macro_ism_historical_ede_refinement import install_ism_historical_ede_refinement
from .macro_ism_parser_refinement import install_ism_roundup_parser_refinement
from .macro_ism_resilience import install_ism_source_resilience
from .macro_historical_offhost_bundle import (
    install_historical_offhost_transport,
    transport_status as historical_offhost_transport_status,
)
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
    # Macro values already frozen into a T0 become canonical EDE features. The
    # dependence unit is the official release_id, never repeated market T0 rows.
    install_macro_edge_evidence_refinement()
    # Old CPI/NFP rows receive a read-only official archive overlay.
    install_bls_historical_ede_refinement()
    # FOMC history uses deterministic statement measurements only; the six LLM
    # semantic scores remain prospective-only and are never reconstructed later.
    install_fomc_deterministic_ede_refinement()
    # ISM history fills only the already-canonical headline PMI/change IDs from
    # dated official roundups; no new subindex hypothesis family is introduced.
    install_ism_historical_ede_refinement()
    # Historical official pages are materialized by the exact-SHA GitHub runner;
    # production never needs BLS/ISM/Fed archive transport for an EDE snapshot.
    install_historical_offhost_transport()

    factory = MacroDataFactory(runtime)
    numeric_store = NumericMacroStore(runtime)
    numeric_runtime = NumericMacroRuntime(numeric_store)
    numeric_runtime.offhost_bundle_path = (
        Path(app.state.engine.settings.data_dir)
        / "research"
        / "official_macro_offhost_latest.json"
    )
    historical_bls_store = BLSHistoricalReleaseStore(runtime)
    historical_bls_runtime = BLSHistoricalBootstrapRuntime(historical_bls_store)
    historical_ism_store = ISMHistoricalReleaseStore(runtime)
    historical_ism_runtime = ISMHistoricalBootstrapRuntime(historical_ism_store)
    historical_bundle_path = (
        Path(app.state.engine.settings.data_dir)
        / "research"
        / "official_macro_historical_offhost_latest.json"
    )
    historical_bls_runtime.offhost_historical_bundle_path = historical_bundle_path
    historical_ism_runtime.offhost_historical_bundle_path = historical_bundle_path
    # Production FOMC ingestion is strict: a non-initial statement cannot be
    # frozen until its exact predecessor is already materialized.
    fomc_deterministic_store = StrictFOMCDeterministicReleaseStore(runtime)
    fomc_deterministic_runtime = FOMCDeterministicBootstrapRuntime(
        fomc_deterministic_store)
    fomc_deterministic_runtime.offhost_historical_bundle_path = historical_bundle_path
    fomc_runtime = FOMCOfficialRuntime(factory)

    # T0 capture reads live materialized stores only. Historical BLS/ISM overlays
    # are deliberately not injected into a current observation capture.
    factory.numeric_release_store = numeric_store
    factory.fomc_deterministic_store = fomc_deterministic_store
    app.state.macro_data_factory = factory
    app.state.macro_numeric_store = numeric_store
    app.state.macro_numeric_runtime = numeric_runtime
    app.state.macro_bls_historical_store = historical_bls_store
    app.state.macro_bls_historical_runtime = historical_bls_runtime
    app.state.macro_ism_historical_store = historical_ism_store
    app.state.macro_ism_historical_runtime = historical_ism_runtime
    app.state.macro_fomc_deterministic_store = fomc_deterministic_store
    app.state.macro_fomc_deterministic_runtime = fomc_deterministic_runtime
    app.state.macro_fomc_runtime = fomc_runtime
    app.state.engine.macro_data_factory = factory
    install_macro_t0_context(app.state.engine, factory)

    # Low-frequency workers. Historical dated pages are static and therefore
    # checked only daily; live numeric/FOMC acquisition retains its own cadence.
    numeric_runtime.start()
    fomc_runtime.start()
    historical_bls_runtime.start()
    historical_ism_runtime.start()
    fomc_deterministic_runtime.start()

    def status():
        return {
            **factory.status(),
            "numeric": numeric_runtime.status(),
            "numeric_transport": macro_transport_status(),
            "historical_bls": historical_bls_runtime.status(),
            "historical_ism": historical_ism_runtime.status(),
            "historical_offhost_transport": historical_offhost_transport_status(),
            "fomc_deterministic": fomc_deterministic_runtime.status(),
            "fomc_runtime": fomc_runtime.status(),
            "llm_cost_guard": cost_guard_status(),
            "official_families": [
                "CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES", "FOMC_STATEMENT"
            ],
            "historical_point_in_time_families": [
                "CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES",
                "FOMC_DETERMINISTIC",
            ],
            "historical_fomc_llm_semantics_backfilled": False,
            "historical_fomc_deterministic_only": True,
            "historical_ism_source_kind": (
                "OFFICIAL_DATED_ROUNDUP_POST_RELEASE_REPRODUCTION"
            ),
            "historical_ism_current_mutable_report_backfill": False,
            "official_sources_only": True,
            "no_placeholders": True,
            "consensus_feed_available": False,
            "surprise_computed_without_consensus": False,
            "macro_ede_dependency_unit": "OFFICIAL_RELEASE_ID",
            "macro_repeated_t0_increases_effective_n": False,
            "historical_macro_old_t0_rows_mutated": False,
            "historical_macro_current_revised_series_backfill": False,
            "arbitrary_document_post_enabled": False,
            "research_only": True,
            "production_authority": False,
        }

    app.add_api_route(
        "/api/research/macro/status", status, methods=["GET"],
        name="macro_data_factory_status")

    def refresh_fomc():
        try:
            reserve_macro_ingest_request()
        except RuntimeError as exc:
            return {
                "contract_version": "macro-data-factory-v1",
                "status": "RATE_LIMITED", "reason": str(exc)[:160],
                "research_only": True, "production_authority": False,
            }
        try:
            return refresh_latest_fomc(factory, extractor=guarded_macro_extractor)
        except (RuntimeError, ValueError) as exc:
            return {
                "contract_version": "macro-data-factory-v1",
                "status": "UNAVAILABLE", "reason": str(exc)[:180],
                "official_source_verified": False,
                "research_only": True, "production_authority": False,
            }

    app.add_api_route(
        "/api/research/macro/fomc/refresh", refresh_fomc, methods=["POST"],
        name="macro_data_factory_fomc_refresh")

    def refresh_numeric():
        return numeric_runtime.refresh()

    app.add_api_route(
        "/api/research/macro/numeric/refresh", refresh_numeric, methods=["POST"],
        name="macro_numeric_refresh")

    def refresh_historical_bls():
        return historical_bls_runtime.refresh()

    app.add_api_route(
        "/api/research/macro/historical-bls/refresh", refresh_historical_bls,
        methods=["POST"], name="macro_historical_bls_refresh")

    def refresh_historical_ism():
        return historical_ism_runtime.refresh()

    app.add_api_route(
        "/api/research/macro/historical-ism/refresh", refresh_historical_ism,
        methods=["POST"], name="macro_historical_ism_refresh")

    def refresh_fomc_deterministic():
        return fomc_deterministic_runtime.refresh()

    app.add_api_route(
        "/api/research/macro/fomc-deterministic/refresh",
        refresh_fomc_deterministic, methods=["POST"],
        name="macro_fomc_deterministic_refresh")

    def latest(captured_ts: float | None = None, family: str = "FOMC_STATEMENT"):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        family_key = str(family or "").upper()
        if family_key in {"CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES"}:
            return numeric_store.latest_admissible(family_key, cutoff)
        return factory.latest_admissible(cutoff, family=family_key)

    app.add_api_route(
        "/api/research/macro/latest", latest, methods=["GET"],
        name="macro_data_factory_latest")

    def latest_historical_bls(captured_ts: float | None = None, family: str = "CPI"):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        return historical_bls_store.latest_admissible(str(family or "").upper(), cutoff)

    app.add_api_route(
        "/api/research/macro/historical-bls/latest", latest_historical_bls,
        methods=["GET"], name="macro_historical_bls_latest")

    def latest_historical_ism(captured_ts: float | None = None,
                              family: str = "ISM_MANUFACTURING"):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        return historical_ism_store.latest_admissible(
            str(family or "").upper(), cutoff)

    app.add_api_route(
        "/api/research/macro/historical-ism/latest", latest_historical_ism,
        methods=["GET"], name="macro_historical_ism_latest")

    def latest_fomc_deterministic(captured_ts: float | None = None):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        return fomc_deterministic_store.latest_admissible(cutoff)

    app.add_api_route(
        "/api/research/macro/fomc-deterministic/latest",
        latest_fomc_deterministic, methods=["GET"],
        name="macro_fomc_deterministic_latest")

    def context(captured_ts: float | None = None):
        cutoff = time.time() if captured_ts is None else float(captured_ts)
        return research_context(numeric_store, cutoff)

    app.add_api_route(
        "/api/research/macro/numeric/context", context, methods=["GET"],
        name="macro_numeric_context")
    app.state.macro_data_factory_routes_installed = True
