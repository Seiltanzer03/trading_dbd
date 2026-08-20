"""Read-only historical ISM PMI overlay for EDE v1.3.

The four headline ISM IDs already exist for prospective T0 capture. This module
only makes the exact same IDs historically available from dated official roundup
reproductions. The canonical ID universe never changes; only historical-source
metadata is upgraded when this refinement is installed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .macro_fomc_deterministic_ede_refinement import (
    install_fomc_deterministic_ede_refinement,
)
from .macro_ism_historical_bootstrap import FEATURE_IDS, feature_records_from_runtime
from .macro_ism_historical_parser_refinement import (
    install_ism_historical_parser_refinement,
)


ISM_HISTORICAL_EDE_REFINEMENT_VERSION = "ism-historical-ede-overlay-v3"
ISM_FEATURE_IDS = frozenset(
    feature_id for mapping in FEATURE_IDS.values() for feature_id in mapping.values()
)


def _historical_ism_registry(definitions):
    output = []
    for item in definitions:
        if item.feature_id in ISM_FEATURE_IDS:
            output.append(replace(
                item,
                source="macro_ism_historical_bootstrap.macro_ism_historical_releases",
                historical_availability="AVAILABLE",
                notes=(
                    "official dated ISM roundup post-release reproduction; "
                    "10:00 ET release-day asof; release_id is dependence unit"
                ),
            ))
        else:
            output.append(item)
    return tuple(output)


def install_ism_historical_ede_refinement() -> None:
    """Add dated-roundup ISM history without mutating old T0 observations."""
    install_ism_historical_parser_refinement()
    install_fomc_deterministic_ede_refinement()
    from .edge_discovery import ai_context, filters, prospective, registry, selective
    from .macro_bls_historical_ede_refinement import _refresh_selective_registry

    if getattr(prospective, "_ism_historical_ede_refinement", None) == (
            ISM_HISTORICAL_EDE_REFINEMENT_VERSION):
        return

    definitions = _historical_ism_registry(registry.FEATURES)
    registry.FEATURES = definitions
    prospective.FEATURES = definitions
    filters.FEATURES = definitions
    ai_context.FEATURES = definitions
    _refresh_selective_registry(selective, definitions)
    prospective.DERIVED_IMPLEMENTED_IDS = set(prospective.DERIVED_IMPLEMENTED_IDS) | set(
        ISM_FEATURE_IDS)
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
        historical_values, historical_provenance = feature_records_from_runtime(
            self.runtime,
            instrument=str(row["instrument"]),
            t0=float(row["captured_ts"]),
            horizon=int(row["horizon_minutes"]),
        )
        for feature_id, record in historical_values.items():
            current = values.get(feature_id)
            # A value actually frozen prospectively at this T0 is stronger
            # provenance and always wins over the historical read overlay.
            if current is not None and current.training_eligible:
                continue
            values[feature_id] = record
            provenance[feature_id] = historical_provenance[feature_id]
        return values, rejected, provenance

    prospective.ProspectiveFeatureAdapter._feature_values = feature_values
    prospective._ism_historical_ede_refinement = ISM_HISTORICAL_EDE_REFINEMENT_VERSION
