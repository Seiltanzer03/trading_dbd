"""Install read-time CPI/NFP archive features on top of macro EDE refinement.

The overlay is intentionally read-only.  It fills a macro feature only when the
immutable T0 did not already carry a prospectively captured official value.  Old
``g1s_observations`` bytes are never rewritten.

Macro inventory maturity is also recomputed on the *official release* dependence
unit.  Repeating one CPI/NFP/FOMC/ISM value through hundreds of market T0 rows may
increase raw coverage, but can never make the feature research-ready by itself.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from .macro_bls_historical_bootstrap import historical_feature_records_from_runtime
from .macro_edge_evidence_refinement import (
    MACRO_FEATURE_FAMILY,
    install_macro_edge_evidence_refinement,
)


BLS_HISTORICAL_EDE_REFINEMENT_VERSION = "macro-bls-historical-ede-overlay-v3"
HISTORICAL_BLS_FAMILIES = frozenset({"CPI", "NFP"})
_MATURITY_RANK = {
    "INSUFFICIENT_DATA": 0,
    "DATA_READY_EARLY": 1,
    "DATA_READY_RESEARCH": 2,
    "DATA_READY_PROVISIONAL": 3,
    "DATA_READY_ROBUST": 4,
}


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


def _release_id(row: dict[str, Any], feature_id: str) -> str | None:
    record = (row.get("feature_values") or {}).get(feature_id) or {}
    release_id = str(record.get("release_id") or "").strip()
    return release_id or None


def _recompute_macro_inventory_maturity(adapter, report: dict[str, Any], prospective) -> None:
    """Make inventory eligibility obey the same release independence as candidates."""
    feature_rows = {
        str(item.get("feature_id")): item
        for item in report.get("features") or []
    }
    for horizon in prospective.HORIZONS:
        horizon_rows = adapter.rows(
            resolved_only=False, strict=False, horizon_minutes=int(horizon))
        for feature_id in MACRO_FEATURE_FAMILY:
            item = feature_rows.get(feature_id)
            if item is None:
                continue
            by_horizon = item.get("by_horizon") or {}
            eligible_rows = [
                row for row in horizon_rows
                if ((row.get("feature_values") or {}).get(feature_id) or {}).get(
                    "training_eligible")
                and _release_id(row, feature_id)
            ]
            resolved_rows = [row for row in eligible_rows if row.get("outcome_available")]
            resolved_release_ids = {
                _release_id(row, feature_id) for row in resolved_rows
            } - {None}
            eligible_release_ids = {
                _release_id(row, feature_id) for row in eligible_rows
            } - {None}
            independent_n = len(resolved_release_ids)
            maturity = prospective.data_maturity(
                raw_n=len(resolved_rows),
                effective_n=independent_n,
                temporal_blocks=independent_n,
            )
            target = by_horizon.setdefault(str(horizon), {})
            target.update({
                "raw": len(eligible_rows),
                "effective": independent_n,
                "resolved": len(resolved_rows),
                "temporal_blocks": independent_n,
                "coverage_pct": 100.0*len(eligible_rows)/max(1, len(horizon_rows)),
                "data_maturity": maturity,
                "edge_maturity": "INSUFFICIENT_DATA",
                "independent_release_n": independent_n,
                "available_release_n": len(eligible_release_ids),
                "dependency_unit": "OFFICIAL_MACRO_RELEASE_ID",
                "repeated_t0_increases_effective_n": False,
            })
        del eligible_rows, resolved_rows, horizon_rows

    for feature_id in MACRO_FEATURE_FAMILY:
        item = feature_rows.get(feature_id)
        if item is None:
            continue
        by_horizon = item.get("by_horizon") or {}
        best = max(
            (row.get("data_maturity", "INSUFFICIENT_DATA")
             for row in by_horizon.values()),
            key=lambda value: _MATURITY_RANK.get(value, 0),
            default="INSUFFICIENT_DATA",
        )
        item["data_maturity"] = best
        item["status"] = best
        item["usable_for_ede"] = best != "INSUFFICIENT_DATA"
        item["independent_release_n"] = max(
            (int(row.get("independent_release_n") or 0) for row in by_horizon.values()),
            default=0,
        )
        item["dependency_unit"] = "OFFICIAL_MACRO_RELEASE_ID"
        item["repeated_t0_increases_effective_n"] = False

    summary = report.get("summary") or {}
    features = report.get("features") or []
    summary["g1s_insufficient_data"] = sum(
        row.get("research_scope") == "G1S"
        and row.get("status") == "INSUFFICIENT_DATA"
        for row in features
    )
    summary["macro_release_independence_enforced"] = True
    summary["macro_repeated_t0_increases_effective_n"] = False
    report["summary"] = summary
    report["macro_maturity_dependency_unit"] = "OFFICIAL_MACRO_RELEASE_ID"


def _refresh_selective_registry(selective, definitions) -> None:
    """v1.3 caches registry maps at import; refresh them after macro IDs install."""
    selective.FEATURES = tuple(definitions)
    selective._FAMILY = {item.feature_id: item.family for item in definitions}
    selective._DEPENDENCY = {
        item.feature_id: item.dependency_family for item in definitions
    }
    selective._macro_registry_runtime_refresh = BLS_HISTORICAL_EDE_REFINEMENT_VERSION


def install_bls_historical_ede_refinement() -> None:
    """Add official archive-vintage CPI/NFP features to EDE read/search paths."""
    install_macro_edge_evidence_refinement()
    from .edge_discovery import ai_context, filters, prospective, registry, selective

    if getattr(prospective, "_bls_historical_ede_refinement", None) == (
            BLS_HISTORICAL_EDE_REFINEMENT_VERSION):
        return

    extended = _historical_registry(registry.FEATURES)
    registry.FEATURES = extended
    prospective.FEATURES = extended
    filters.FEATURES = extended
    ai_context.FEATURES = extended
    _refresh_selective_registry(selective, extended)
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
            # Prospectively frozen data always wins. Historical archive overlay
            # only repairs a genuinely absent old-T0 feature.
            if current is not None and current.training_eligible:
                continue
            values[feature_id] = record
            provenance[feature_id] = historical_provenance[feature_id]
        return values, rejected, provenance

    prospective.ProspectiveFeatureAdapter._feature_values = feature_values

    previous_capture_audit = prospective.ProspectiveFeatureAdapter.feature_capture_audit

    def feature_capture_audit(self):
        report = previous_capture_audit(self)
        _recompute_macro_inventory_maturity(self, report, prospective)
        return report

    prospective.ProspectiveFeatureAdapter.feature_capture_audit = feature_capture_audit
    prospective._bls_historical_ede_refinement = BLS_HISTORICAL_EDE_REFINEMENT_VERSION
