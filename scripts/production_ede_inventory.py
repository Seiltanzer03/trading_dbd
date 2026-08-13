#!/usr/bin/env python3
"""Read-only inventory of real EDE coverage in the production G1S database."""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from pathlib import Path

from seiltanzer.edge_discovery.prospective import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.registry import FEATURES


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
    assert len(FEATURES) == 69, len(FEATURES)
    assert len(features) == len(FEATURES), len(features)
    assert {row["feature_id"] for row in features} == {
        definition.feature_id for definition in FEATURES}
    return {
        "contract_version": "g1s-ede-production-inventory-v1.2",
        "database": str(database),
        "database_open_mode": "READ_ONLY",
        "g1s_observations_total": total,
        "g1s_observations_by_horizon": by_horizon,
        "g1s_resolutions_total": resolved,
        "adapter_observation_count": audit.get("observation_count"),
        "resolved_outcome_count": audit.get("resolved_outcome_count"),
        "feature_summary": audit.get("summary"),
        "features": features,
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
    # Keep every Actions log line bounded. A single 69-feature JSON line can
    # exceed drone-ssh's scanner buffer and disappear from the audit log.
    print("EDE_INVENTORY_SUMMARY=" + json.dumps({
        key: value for key, value in report.items() if key != "features"
    }, sort_keys=True))
    for feature in report["features"]:
        print("EDE_INVENTORY_FEATURE=" + json.dumps(feature, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
