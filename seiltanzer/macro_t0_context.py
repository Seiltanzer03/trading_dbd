"""Freeze already-observed official macro context into future T0 rows only.

FOMC semantics and deterministic CPI/NFP/ISM numbers are additive research
context.  They do not enter the current production policy or ML feature vector in
this pass: the prospective outcome machinery must first measure their actual OOS
value.  No network or LLM call can occur here.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .macro_data_factory import DATA_FACTORY_CONTRACT_VERSION, MacroDataFactory
from .macro_numeric_data import research_context as numeric_research_context
from .passive_learning import PassiveLearningEngine


MACRO_T0_CONTEXT_VERSION = "macro-t0-context-v2"
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


def _fomc_context(factory: MacroDataFactory, captured_ts: float) -> dict[str, Any]:
    try:
        record = factory.latest_admissible(float(captured_ts), family="FOMC_STATEMENT")
    except Exception as exc:
        return {
            "available": False,
            "reason": f"FACTORY_READ_ERROR:{type(exc).__name__}",
            "research_only": True,
            "production_authority": False,
        }
    if record.get("status") != "VALID":
        return {
            "available": False,
            "reason": record.get("reason") or "NO_CAUSAL_FOMC_CONTEXT",
            "captured_ts": float(captured_ts),
            "research_only": True,
            "production_authority": False,
        }
    available_at = record.get("available_at")
    if available_at is None or float(available_at) > float(captured_ts) + 1e-6:
        return {
            "available": False,
            "reason": "FOMC_CONTEXT_AFTER_T0",
            "captured_ts": float(captured_ts),
            "research_only": True,
            "production_authority": False,
        }
    official = _official_fed_record(record)
    return {
        "available": True,
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
        "eligible_for_future_ml_research": official,
        "causal_rule": "available_at<=captured_ts",
        "research_only": True,
        "production_authority": False,
    }


def build_macro_t0_context(factory: MacroDataFactory, captured_ts: float) -> dict[str, Any]:
    captured_ts = float(captured_ts)
    fomc = _fomc_context(factory, captured_ts)
    numeric_store = getattr(factory, "numeric_release_store", None)
    if numeric_store is None:
        numeric = {
            "available_families": [],
            "candidate_vector": {},
            "reason": "NUMERIC_STORE_NOT_INSTALLED",
            "research_only": True,
            "production_authority": False,
        }
    else:
        try:
            numeric = numeric_research_context(numeric_store, captured_ts)
        except Exception as exc:
            numeric = {
                "available_families": [],
                "candidate_vector": {},
                "reason": f"NUMERIC_STORE_READ_ERROR:{type(exc).__name__}",
                "research_only": True,
                "production_authority": False,
            }
    available = bool(fomc.get("available") or numeric.get("available_families"))
    result: dict[str, Any] = {
        "contract_version": MACRO_T0_CONTEXT_VERSION,
        "available": available,
        "captured_ts": captured_ts,
        "data_factory_contract": DATA_FACTORY_CONTRACT_VERSION,
        "fomc": fomc,
        "numeric_macro": numeric,
        "candidate_vector": dict(numeric.get("candidate_vector") or {}),
        "historical_backfill_allowed": False,
        "causal_rule": "each_record.available_at<=captured_ts",
        "research_only": True,
        "production_authority": False,
        "current_ml_feature_vector_reads_macro_context": False,
        "promotion_rule": "prospective_OOS_evidence_required_before_active_edge_weight",
    }
    # Backward-compatible FOMC root fields for existing research readers.
    if fomc.get("available"):
        for key in (
            "family", "document_id", "document_sha256", "source", "source_url",
            "official_source_verified", "published_at", "available_at", "semantic",
            "numeric", "prompt_version", "model", "retrospective_publication",
            "prospectively_usable_since", "eligible_for_future_ml_research",
        ):
            result[key] = fomc.get(key)
    if not available:
        result["reason"] = "NO_CAUSAL_MACRO_CONTEXT"
    return result


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
