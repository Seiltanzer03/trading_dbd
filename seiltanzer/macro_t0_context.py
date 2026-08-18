"""Add already-extracted macro semantics to future frozen T0 observations only.

This is intentionally additive research context. Existing ML feature vectors do
not read this block, so no current model, policy or production decision changes.
No LLM call can occur here: only MacroDataFactory.latest_admissible() is read.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .macro_data_factory import DATA_FACTORY_CONTRACT_VERSION, MacroDataFactory
from .passive_learning import PassiveLearningEngine


MACRO_T0_CONTEXT_VERSION = "macro-t0-context-v1"
_INSTALLED = False


def _official_fed_record(record: dict[str, Any]) -> bool:
    try:
        parsed = urlparse(str(record.get("source_url") or ""))
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    return (
        str(record.get("source") or "") == "Federal Reserve Board"
        and parsed.scheme == "https"
        and host in {"www.federalreserve.gov", "federalreserve.gov"}
    )


def build_macro_t0_context(factory: MacroDataFactory, captured_ts: float) -> dict[str, Any]:
    try:
        record = factory.latest_admissible(float(captured_ts), family="FOMC_STATEMENT")
    except Exception as exc:  # fail-soft; collection must never depend on this research context
        return {
            "contract_version": MACRO_T0_CONTEXT_VERSION,
            "available": False,
            "reason": f"FACTORY_READ_ERROR:{type(exc).__name__}",
            "research_only": True,
            "production_authority": False,
        }
    if record.get("status") != "VALID":
        return {
            "contract_version": MACRO_T0_CONTEXT_VERSION,
            "available": False,
            "reason": record.get("reason") or "NO_CAUSAL_MACRO_CONTEXT",
            "captured_ts": float(captured_ts),
            "data_factory_contract": DATA_FACTORY_CONTRACT_VERSION,
            "research_only": True,
            "production_authority": False,
        }
    available_at = record.get("available_at")
    if available_at is None or float(available_at) > float(captured_ts) + 1e-6:
        return {
            "contract_version": MACRO_T0_CONTEXT_VERSION,
            "available": False,
            "reason": "MACRO_CONTEXT_AFTER_T0",
            "captured_ts": float(captured_ts),
            "research_only": True,
            "production_authority": False,
        }
    official = _official_fed_record(record)
    return {
        "contract_version": MACRO_T0_CONTEXT_VERSION,
        "available": True,
        "captured_ts": float(captured_ts),
        "family": record.get("family"),
        "document_id": record.get("document_id"),
        "document_sha256": record.get("document_sha256"),
        "source": record.get("source"),
        "source_url": record.get("source_url"),
        "official_source_verified": official,
        "published_at": record.get("published_at"),
        "available_at": float(available_at),
        "semantic": record.get("semantic"),
        "numeric": record.get("numeric"),
        "prompt_version": record.get("prompt_version"),
        "model": record.get("model"),
        "retrospective_publication": bool(record.get("retrospective_only")),
        "prospectively_usable_since": float(available_at),
        "historical_backfill_allowed": False,
        "eligible_for_future_ml_research": official,
        "causal_rule": "available_at<=captured_ts",
        "research_only": True,
        "production_authority": False,
        "current_ml_feature_vector_reads_macro_context": False,
    }


def install_macro_t0_context(engine, factory: MacroDataFactory) -> None:
    """Install one lightweight capture wrapper and attach factory per engine."""
    global _INSTALLED
    engine.passive._macro_data_factory = factory
    if _INSTALLED:
        return
    _INSTALLED = True
    previous_capture = PassiveLearningEngine.capture_observation

    def capture_observation(self, *, instrument: str, captured_ts: float,
                            market_price: float, features: dict, forecast: dict,
                            provenance: dict, trigger_reason: str = "cadence",
                            evidence_eligible: bool = True,
                            observation_origin: str = "background_collector"):
        local_factory = getattr(self, "_macro_data_factory", None)
        frozen = dict(features)
        if local_factory is not None:
            frozen["macro_context_v1"] = build_macro_t0_context(
                local_factory, float(captured_ts))
        return previous_capture(
            self, instrument=instrument, captured_ts=captured_ts,
            market_price=market_price, features=frozen, forecast=forecast,
            provenance=provenance, trigger_reason=trigger_reason,
            evidence_eligible=evidence_eligible,
            observation_origin=observation_origin,
        )

    PassiveLearningEngine.capture_observation = capture_observation
