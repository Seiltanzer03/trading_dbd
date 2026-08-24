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


INVENTORY_CONTRACT_VERSION = "g1s-ede-production-inventory-v1.5.0"
HORIZONS = (15, 30, 60, 120, 240)
KNOWN_DATA_MATURITY = frozenset({
    "INSUFFICIENT_DATA",
    "DATA_READY_EARLY",
    "DATA_READY_RESEARCH",
    "DATA_READY_PROVISIONAL",
    "DATA_READY_ROBUST",
})


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


def _normalized_data_maturity(value: Any) -> str:
    maturity = str(value or "INSUFFICIENT_DATA")
    return maturity if maturity in KNOWN_DATA_MATURITY else "INSUFFICIENT_DATA"


def _missing_coverage_state(definition: Any, row: dict[str, Any]) -> str:
    diagnosis = row.get("zero_coverage_diagnosis") or {}
    if bool(diagnosis.get("causal_backfill")):
        return "CAUSAL_BACKFILL_NO_COVERAGE"
    if str(definition.live_availability) == "UNAVAILABLE":
        return "SOURCE_MISSING"
    if str(definition.historical_availability) == "UNAVAILABLE":
        return "HISTORICAL_DATA_MISSING"
    return "NO_REAL_OBSERVATIONS"


def _coverage_state(definition: Any, row: dict[str, Any]) -> str:
    scope = str(definition.research_scope)
    if scope == "G1M_ONLY":
        return "G1M_ONLY"
    if scope == "QUALITY_ONLY":
        return "QUALITY_ONLY"

    real_n = int(row.get("real_observations") or 0)
    if real_n <= 0:
        return _missing_coverage_state(definition, row)

    # Fail closed: an upstream boolean alone is not sufficient evidence.  The
    # canonical adapter must also emit a recognized non-insufficient maturity.
    maturity = _normalized_data_maturity(row.get("data_maturity"))
    if bool(row.get("usable_for_ede")) and maturity != "INSUFFICIENT_DATA":
        return "DATA_READY"
    return "INSUFFICIENT_INDEPENDENT_EVIDENCE"


def _horizon_bucket(row: dict[str, Any], horizon: int) -> dict[str, Any]:
    buckets = row.get("by_horizon") or {}
    if not isinstance(buckets, dict):
        return {}
    bucket = buckets.get(str(int(horizon))) or {}
    return bucket if isinstance(bucket, dict) else {}


def _horizon_coverage_state(
    definition: Any, row: dict[str, Any], horizon: int
) -> str:
    scope = str(definition.research_scope)
    if scope == "G1M_ONLY":
        return "G1M_ONLY"
    if scope == "QUALITY_ONLY":
        return "QUALITY_ONLY"

    bucket = _horizon_bucket(row, horizon)
    raw_n = int(bucket.get("raw") or 0)
    if raw_n <= 0:
        return _missing_coverage_state(definition, row)

    maturity = _normalized_data_maturity(bucket.get("data_maturity"))
    if maturity == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_INDEPENDENT_EVIDENCE"
    return "DATA_READY"


def _enrich_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = {definition.feature_id: definition for definition in FEATURES}
    output: list[dict[str, Any]] = []
    for row in features:
        feature_id = str(row["feature_id"])
        definition = definitions[feature_id]
        diagnosis = row.get("zero_coverage_diagnosis") or {}
        coverage_state_by_horizon = {
            str(horizon): _horizon_coverage_state(definition, row, horizon)
            for horizon in HORIZONS
        }
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
            "coverage_state_by_horizon": coverage_state_by_horizon,
        }
        output.append(enriched)
    return output


def _family_horizon_summary(
    rows: list[dict[str, Any]], horizon: int
) -> dict[str, Any]:
    buckets = [_horizon_bucket(row, horizon) for row in rows]
    states = Counter(
        str((row.get("coverage_state_by_horizon") or {}).get(
            str(horizon), "INSUFFICIENT_INDEPENDENT_EVIDENCE"))
        for row in rows
    )
    maturity = Counter(
        _normalized_data_maturity(bucket.get("data_maturity"))
        for bucket in buckets
    )
    temporal_blocks = Counter(int(bucket.get("temporal_blocks") or 0)
                              for bucket in buckets)
    coverage = [float(bucket.get("coverage_pct") or 0.0) for bucket in buckets]

    return {
        "feature_count": len(rows),
        "with_training_eligible_observations": sum(
            int(bucket.get("raw") or 0) > 0 for bucket in buckets),
        "with_resolved_observations": sum(
            int(bucket.get("resolved") or 0) > 0 for bucket in buckets),
        "data_ready_features": int(states.get("DATA_READY", 0)),
        "zero_coverage_features": sum(
            int(bucket.get("raw") or 0) == 0 for bucket in buckets),
        # These are feature-observation totals, not independent market T0 rows.
        "raw_feature_observations": sum(
            int(bucket.get("raw") or 0) for bucket in buckets),
        "effective_feature_observations": sum(
            int(bucket.get("effective") or 0) for bucket in buckets),
        "resolved_feature_observations": sum(
            int(bucket.get("resolved") or 0) for bucket in buckets),
        "temporal_block_counts": {
            str(key): value for key, value in sorted(temporal_blocks.items())
        },
        "coverage_pct_min": min(coverage) if coverage else 0.0,
        "coverage_pct_mean": (
            sum(coverage) / len(coverage) if coverage else 0.0),
        "coverage_pct_max": max(coverage) if coverage else 0.0,
        "data_maturity_counts": dict(sorted(maturity.items())),
        "coverage_state_counts": dict(sorted(states.items())),
        "production_authority": False,
    }


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
            "usable_for_ede": int(states.get("DATA_READY", 0)),
            "zero_coverage": sum(
                int(row.get("real_observations") or 0) == 0 for row in rows),
            "coverage_state_counts": dict(sorted(states.items())),
            "by_horizon": {
                str(horizon): _family_horizon_summary(rows, horizon)
                for horizon in HORIZONS
            },
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
        "family_horizon_classification": True,
        "unknown_data_maturity_fails_closed": True,
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
