"""Install read-time CPI/NFP archive features on top of macro EDE refinement.

The overlay is intentionally read-only.  It fills a macro feature only when the
immutable T0 did not already carry a prospectively captured official value.  Old
``g1s_observations`` bytes are never rewritten.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .macro_bls_historical_bootstrap import historical_feature_records_from_runtime
from .macro_edge_evidence_refinement import (
    MACRO_EDGE_REFINEMENT_VERSION,
    MACRO_FEATURE_FAMILY,
    install_macro_edge_evidence_refinement,
)


BLS_HISTORICAL_EDE_REFINEMENT_VERSION = "macro-bls-historical-ede-overlay-v1"
HISTORICAL_BLS_FAMILIES = frozenset({"CPI", "NFP"})


def _historical_registry(definitions):
    output = []
    for item in definitions:
        family = MACRO_FEATURE_FAMILY.get(item.feature_id)
        if family in HISTORICAL_BLS_FAMILIES:
            output.append(replace(
                item,
                source="macro_bls_historical_bootstrap.macro_bls_historical_releases",
                historical_availability="AVAILABLE",
                notes=(
                    "official BLS archived release copy; published_at<=T0; "
                    "release_id is the macro dependency unit"
                ),
            ))
        else:
            output.append(item)
    return tuple(output)


def install_bls_historical_ede_refinement() -> None:
    """Add official archive-vintage CPI/NFP features to EDE read paths."""
    install_macro_edge_evidence_refinement()
    from .edge_discovery import ai_context, filters, prospective, registry

    if getattr(prospective, "_bls_historical_ede_refinement", None) == (
            BLS_HISTORICAL_EDE_REFINEMENT_VERSION):
        return

    extended = _historical_registry(registry.FEATURES)
    registry.FEATURES = extended
    prospective.FEATURES = extended
    filters.FEATURES = extended
    ai_context.FEATURES = extended
    prospective.DERIVED_IMPLEMENTED_IDS = set(prospective.DERIVED_IMPLEMENTED_IDS) | {
        feature_id for feature_id, family in MACRO_FEATURE_FAMILY.items()
        if family in HISTORICAL_BLS_FAMILIES
    }

    previous_feature_values = prospective.ProspectiveFeatureAdapter._feature_values

    def feature_values(self, row: dict[str, Any], *, strict: bool):
        values, rejected, provenance = previous_feature_values(self, row, strict=strict)
        historical_values, historical_provenance = historical_feature_records_from_runtime(
            self.runtime,
            instrument=str(row["instrument"]),
            t0=float(row["captured_ts"]),
            horizon=int(row["horizon_minutes"]),
        )
        for feature_id, record in historical_values.items():
            current = values.get(feature_id)
            # Prospectively frozen data always wins.  Historical archive overlay
            # only repairs a genuinely absent old-T0 feature.
            if current is not None and current.training_eligible:
                continue
            values[feature_id] = record
            provenance[feature_id] = historical_provenance[feature_id]
        return values, rejected, provenance

    prospective.ProspectiveFeatureAdapter._feature_values = feature_values
    prospective._bls_historical_ede_refinement = BLS_HISTORICAL_EDE_REFINEMENT_VERSION
