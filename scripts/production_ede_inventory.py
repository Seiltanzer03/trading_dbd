#!/usr/bin/env python3
"""Read-only inventory of real EDE coverage in the production G1S database."""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.registry import FEATURES


INVENTORY_CONTRACT_VERSION = "g1s-ede-production-inventory-v1.4.0"


class _ReadOnlyRuntime:
    def __init__(self, database: Path):
        uri = f"file:{database.resolve()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA query_only=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def _history_strategy(definition: Any) -> str:
    historical = str(definition.historical_availability)
    live = str(definition.live_availability)
    if live == "UNAVAILABLE":
        return "NO_LIVE_CAPTURE"
    if historical == "AVAILABLE":
        return "HISTORICAL_AND_PROSPECTIVE"
    if historical == "LIMITED":
        return "LIMITED_HISTORICAL_AND_PROSPECTIVE"
    return "PROSPECTIVE_ONLY"


def _coverage_state(definition: Any, row: dict[str, Any]) -> str:
    scope = str(definition.research_scope)
    if scope == "G1M_ONLY":
        return "G1M_ONLY"
    if scope == "QUALITY_ONLY":
        return "QUALITY_ONLY"

    real_n = int(row.get("real_observations") or 0)
    if real_n <= 0:
        diagnosis = row.get("zero_coverage_diagnosis") or {}
        if bool(diagnosis.get("causal_backfill")):
            return "CAUSAL_BACKFILL_NO_COVERAGE"
        if str(definition.live_availability) == "UNAVAILABLE":
            return "SOURCE_MISSING"
        if str(definition.historical_availability) == "UNAVAILABLE":
            return "HISTORICAL_DATA_MISSING"
        return "NO_REAL_OBSERVATIONS"

    if bool(row.get("usable_for_ede")):
        return "DATA_READY"
    return "INSUFFICIENT_INDEPENDENT_EVIDENCE"


def _enrich_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = {definition.feature_id: definition for definition in FEATURES}
    output: list[dict[str, Any]] = []
    for row in features:
        feature_id = str(row["feature_id"])
        definition = definitions[feature_id]
        diagnosis = row.get("zero_coverage_diagnosis") or {}
        enriched = {
            **row,
            "family": definition.family,
            "producer": definition.source,
            "datatype": definition.datatype,
            "historical_capability": definition.historical_availability,
            "live_capability": definition.live_availability,
            "history_strategy": _history_strategy(definition),
            "dependency_family": definition.dependency_family,
            "registry_training_eligibility": bool(definition.training_eligibility),
            "registry_research_scope": definition.research_scope,
            "reconstructable_from_existing_causal_store": bool(
                diagnosis.get("causal_backfill")),
            "coverage_state": _coverage_state(definition, row),
        }
        output.append(enriched)
    return output


def _family_summary(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in features:
        grouped[str(row["family"])].append(row)

    output: list[dict[str, Any]] = []
    for family in sorted(grouped):
        rows = grouped[family]
        states = Counter(str(row["coverage_state"]) for row in rows)
        output.append({
            "family": family,
            "feature_count": len(rows),
            "with_real_observations": sum(
                int(row.get("real_observations") or 0) > 0 for row in rows),
            "usable_for_ede": sum(bool(row.get("usable_for_ede")) for row in rows),
            "zero_coverage": sum(
                int(row.get("real_observations") or 0) == 0 for row in rows),
            "coverage_state_counts": dict(sorted(states.items())),
            "production_authority": False,
        })
    return output


def inventory(database: Path) -> dict:
    runtime = _ReadOnlyRuntime(database)
    try:
        tables = {str(row[0]) for row in runtime._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "g1s_observations" not in tables:
            raise AssertionError("production DB has no g1s_observations table")
        total = int(runtime._conn.execute(
            "SELECT COUNT(*) FROM g1s_observations").fetchone()[0])
        by_horizon = {
            str(int(row[0])): int(row[1])
            for row in runtime._conn.execute(
                "SELECT horizon_minutes,COUNT(*) FROM g1s_observations "
                "GROUP BY horizon_minutes ORDER BY horizon_minutes").fetchall()
        }
        resolved = 0
        if "g1s_resolutions" in tables:
            resolved = int(runtime._conn.execute(
                "SELECT COUNT(*) FROM g1s_resolutions").fetchone()[0])
        audit = ProspectiveFeatureAdapter(runtime).feature_capture_audit()
    finally:
        runtime.close()

    features = audit.get("features") or []
    canonical_ids = {definition.feature_id for definition in FEATURES}
    assert len(features) == len(canonical_ids), (len(features), len(canonical_ids))
    assert {row["feature_id"] for row in features} == canonical_ids
    enriched_features = _enrich_features(features)
    families = _family_summary(enriched_features)
    coverage_states = Counter(
        str(row["coverage_state"]) for row in enriched_features)
    return {
        "contract_version": INVENTORY_CONTRACT_VERSION,
        "database": str(database),
        "database_open_mode": "READ_ONLY",
        "g1s_observations_total": total,
        "g1s_observations_by_horizon": by_horizon,
        "g1s_resolutions_total": resolved,
        "adapter_observation_count": audit.get("observation_count"),
        "resolved_outcome_count": audit.get("resolved_outcome_count"),
        "canonical_feature_count": len(canonical_ids),
        "feature_summary": audit.get("summary"),
        "coverage_state_counts": dict(sorted(coverage_states.items())),
        "family_count": len(families),
        "families": families,
        "features": enriched_features,
        "classification_is_data_coverage_not_edge_result": True,
        "missing_is_not_zero": True,
        "causal_baseline_price_backfill": True,
        "macro_release_independence": True,
        "retrospective_options_reconstruction": False,
        "production_authority": False,
        "auto_promotion": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path,
                        default=Path("/opt/seiltanzer/data/trades.db"))
    args = parser.parse_args(argv)
    report = inventory(args.database)
    # Keep every Actions log line bounded. Printing family/feature rows avoids a
    # large single JSON line as the canonical registry grows over time.
    print("EDE_INVENTORY_SUMMARY=" + json.dumps({
        key: value for key, value in report.items()
        if key not in {"features", "families"}
    }, sort_keys=True))
    for family in report["families"]:
        print("EDE_INVENTORY_FAMILY=" + json.dumps(family, sort_keys=True))
    for feature in report["features"]:
        print("EDE_INVENTORY_FEATURE=" + json.dumps(feature, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
