"""Explicit, research-only API for the macro LLM data factory."""
from __future__ import annotations

import time

from fastapi import Body, FastAPI

from .macro_data_factory import MacroDataFactory
from .macro_t0_context import install_macro_t0_context


def install_macro_data_factory_routes(app: FastAPI) -> None:
    if getattr(app.state, "macro_data_factory_routes_installed", False):
        return
    runtime = getattr(app.state.engine, "short_horizon", None)
    if runtime is None:
        raise RuntimeError("G.1S integration must be installed before macro data factory")
    factory = MacroDataFactory(runtime)
    app.state.macro_data_factory = factory
    app.state.engine.macro_data_factory = factory
    # Future T0 rows receive only already-completed causal semantic records.
    # Existing ML feature vectors do not read macro_context_v1.
    install_macro_t0_context(app.state.engine, factory)

    app.add_api_route(
        "/api/research/macro/status",
        factory.status,
        methods=["GET"],
        name="macro_data_factory_status",
    )

    def extract(document: dict = Body(...)):
        # This is the only route that may call the extractor. Merely reading
        # state/status/latest can never spend LLM tokens or wait on a provider.
        return factory.extract_document(document)

    app.add_api_route(
        "/api/research/macro/extract",
        extract,
        methods=["POST"],
        name="macro_data_factory_extract",
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
