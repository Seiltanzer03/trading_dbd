"""Bridge deterministic FOMC statement measurements into EDE read paths.

This refinement deliberately does not historical-backfill the six LLM semantic
FOMC fields.  It overlays only deterministic measurements from official dated
statements and keeps the common release-ID dependence/maturity contract.
"""
from __future__ import annotations

from typing import Any

from .macro_bls_historical_ede_refinement import (
    _refresh_selective_registry,
    install_bls_historical_ede_refinement,
)
from .macro_fomc_deterministic_bootstrap import (
    FOMC_DETERMINISTIC_FEATURES,
    feature_records_from_runtime,
)


FOMC_DETERMINISTIC_EDE_REFINEMENT_VERSION = "fomc-deterministic-ede-overlay-v1"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _frozen_records(*, frozen: dict[str, Any], instrument: str,
                    t0: float, horizon: int):
    """Read only the deterministic FOMC record actually frozen at this T0."""
    from .edge_discovery.feature_view import feature_value

    context = frozen.get("macro_context_v1") if isinstance(frozen, dict) else None
    context = context if isinstance(context, dict) else {}
    release = context.get("fomc_deterministic")
    release = release if isinstance(release, dict) else {}
    payload = release.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    available_at = _finite(release.get("available_at"))
    release_id = str(release.get("release_id") or "")
    if not (
        release.get("available") is True
        and release.get("official_source_verified") is True
        and available_at is not None
        and available_at <= float(t0)+1e-6
        and release_id
    ):
        return {}, {}

    values: dict[str, Any] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for source_name, feature_id in FOMC_DETERMINISTIC_FEATURES.items():
        value = _finite(payload.get(source_name))
        if value is None:
            continue
        record = feature_value(
            instrument=instrument, t0=t0, horizon=horizon,
            feature_id=feature_id, value=value, asof=available_at,
            historical_available=True, live_available=True,
            training_eligible=True,
            dependency_group="macro_release:FOMC_STATEMENT",
        )
        if not record.training_eligible:
            continue
        values[feature_id] = record
        provenance[feature_id] = {
            "provenance": "FROZEN_OFFICIAL_FOMC_DETERMINISTIC_T0",
            "release_id": release_id,
            "release_family": "FOMC_STATEMENT",
            "release_period": release.get("date_code"),
            "published_at": release.get("published_at"),
            "available_at": available_at,
            "source_url": release.get("source_url"),
            "body_sha256": release.get("body_sha256"),
            "previous_release_id": release.get("previous_release_id"),
            "official_source_verified": True,
            "llm_used": False,
            "future_points_used": False,
        }
    return values, provenance


def install_fomc_deterministic_ede_refinement() -> None:
    """Install deterministic FOMC IDs on prospective, historical and v1.3 paths."""
    install_bls_historical_ede_refinement()
    from .edge_discovery import ai_context, filters, prospective, registry, selective
    from . import macro_edge_evidence_refinement as macro_edge

    if getattr(prospective, "_fomc_deterministic_ede_refinement", None) == (
            FOMC_DETERMINISTIC_EDE_REFINEMENT_VERSION):
        return

    # Update the shared dict in place so the already-installed release-aware
    # candidate weighting and inventory maturity wrappers immediately see these
    # IDs as one FOMC_STATEMENT dependence family.
    macro_edge.MACRO_FEATURE_FAMILY.update({
        feature_id: "FOMC_STATEMENT"
        for feature_id in FOMC_DETERMINISTIC_FEATURES.values()
    })

    definitions = tuple(registry.FEATURES)
    prospective.FEATURES = definitions
    filters.FEATURES = definitions
    ai_context.FEATURES = definitions
    _refresh_selective_registry(selective, definitions)
    prospective.DERIVED_IMPLEMENTED_IDS = set(prospective.DERIVED_IMPLEMENTED_IDS) | set(
        FOMC_DETERMINISTIC_FEATURES.values())
    filters.PROSPECTIVE_NUMERIC_FEATURES = tuple(
        item.feature_id for item in definitions
        if item.research_scope == "G1S" and item.training_eligibility
        and item.feature_id not in {
            "price.ret_5m", "regime.asset", "regime.asset_family",
            "regime.session_utc", "regime.trend", "regime.volatility",
            "regime.macro", "cross.confirmation",
        }
    )

    previous_feature_values = prospective.ProspectiveFeatureAdapter._feature_values

    def feature_values(self, row: dict[str, Any], *, strict: bool):
        values, rejected, provenance = previous_feature_values(self, row, strict=strict)
        raw_frozen = prospective._loads(row.get("frozen_features_json"))
        frozen_values, frozen_provenance = _frozen_records(
            frozen=raw_frozen,
            instrument=str(row["instrument"]),
            t0=float(row["captured_ts"]),
            horizon=int(row["horizon_minutes"]),
        )
        for feature_id, record in frozen_values.items():
            values[feature_id] = record
            provenance[feature_id] = frozen_provenance[feature_id]

        historical_values, historical_provenance = feature_records_from_runtime(
            self.runtime,
            instrument=str(row["instrument"]),
            t0=float(row["captured_ts"]),
            horizon=int(row["horizon_minutes"]),
        )
        for feature_id, record in historical_values.items():
            current = values.get(feature_id)
            if current is not None and current.training_eligible:
                continue
            values[feature_id] = record
            provenance[feature_id] = historical_provenance[feature_id]
        return values, rejected, provenance

    prospective.ProspectiveFeatureAdapter._feature_values = feature_values

    previous_current_map = ai_context.canonical_current_feature_map

    def current_map(frozen: dict[str, Any], instrument: str):
        values = previous_current_map(frozen, instrument)
        raw = frozen.get("_raw_frozen") if isinstance(frozen, dict) else None
        raw = raw if isinstance(raw, dict) else frozen
        try:
            t0 = float(frozen["observation_t0"])
        except (KeyError, TypeError, ValueError):
            return values
        records, provenance = _frozen_records(
            frozen=raw if isinstance(raw, dict) else {},
            instrument=instrument, t0=t0, horizon=0)
        for feature_id, record in records.items():
            serialized = record.as_dict()
            serialized.update(provenance.get(feature_id, {}))
            serialized["available"] = serialized.get("availability") == "AVAILABLE"
            serialized["live_applicability"] = "LIVE_APPLICABLE"
            values[feature_id] = serialized
        return values

    ai_context.canonical_current_feature_map = current_map
    prospective._fomc_deterministic_ede_refinement = (
        FOMC_DETERMINISTIC_EDE_REFINEMENT_VERSION)
