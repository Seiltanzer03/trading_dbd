#!/usr/bin/env python3
"""Read-only coverage audit for strategy-agnostic universal market outcomes."""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.universal_outcome_adapter import (
    ProspectiveUniversalOutcomeAdapter,
)
from seiltanzer.edge_discovery.universal_outcomes import (
    UNIVERSAL_OUTCOME_CONTRACT_VERSION,
)


AUDIT_VERSION = "g1s-universal-outcome-coverage-audit-v1"


class ReadOnlyRuntime:
    def __init__(self, database: Path):
        self._conn = sqlite3.connect(
            f"file:{database.resolve()}?mode=ro", uri=True,
            check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA query_only=ON")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[int, dict[str, Any]] = {}
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["horizon_minutes"])].append(row)
    for horizon, items in sorted(grouped.items()):
        resolved = [item for item in items if item.get("outcome_available")]
        available = [
            item for item in resolved
            if isinstance(item.get("universal_outcome"), dict)
            and item["universal_outcome"].get("available")
        ]
        complete = [item for item in available if item["universal_outcome"].get("path_complete")]
        clean_barrier = Counter()
        all_barrier = Counter()
        for item in available:
            for barrier_id, label in (item["universal_outcome"].get("barriers") or {}).items():
                all_barrier[f"{barrier_id}:{label.get('label')}"] += 1
                if label.get("clean_label"):
                    clean_barrier[f"{barrier_id}:{label.get('label')}"] += 1
        by_horizon[horizon] = {
            "rows": len(items),
            "resolved_rows": len(resolved),
            "universal_outcome_available": len(available),
            "complete_ohlc_outcomes": len(complete),
            "available_pct_of_resolved": 100.0*len(available)/max(1, len(resolved)),
            "complete_pct_of_available": 100.0*len(complete)/max(1, len(available)),
            "all_barrier_labels": dict(sorted(all_barrier.items())),
            "clean_barrier_labels": dict(sorted(clean_barrier.items())),
        }
    reasons = Counter(
        str(row.get("universal_outcome_reason") or "AVAILABLE") for row in rows
    )
    return {
        "audit_version": AUDIT_VERSION,
        "outcome_contract_version": UNIVERSAL_OUTCOME_CONTRACT_VERSION,
        "rows": len(rows),
        "by_horizon": {str(key): value for key, value in by_horizon.items()},
        "availability_reasons": dict(sorted(reasons.items())),
        "strategy_agnostic": True,
        "production_authority": False,
        "auto_promotion": False,
    }


def audit(database: Path) -> dict[str, Any]:
    runtime = ReadOnlyRuntime(database)
    try:
        rows = ProspectiveFeatureAdapter(runtime).rows(resolved_only=False, strict=False)
        attached = ProspectiveUniversalOutcomeAdapter(runtime).attach(rows)
        return _summarize(attached)
    finally:
        runtime.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path,
                        default=Path("/opt/seiltanzer/data/trades.db"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = audit(args.database)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print("UNIVERSAL_OUTCOME_AUDIT=" + payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
