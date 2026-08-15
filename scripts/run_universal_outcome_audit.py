#!/usr/bin/env python3
"""Read-only coverage audit for strategy-agnostic universal market outcomes."""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.universal_outcome_adapter import (
    ProspectiveUniversalOutcomeAdapter,
)
from seiltanzer.edge_discovery.universal_outcome_audit import (
    summarize_universal_outcomes,
)


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


def audit(database: Path) -> dict[str, Any]:
    runtime = ReadOnlyRuntime(database)
    try:
        rows = ProspectiveFeatureAdapter(runtime).rows(resolved_only=False, strict=False)
        attached = ProspectiveUniversalOutcomeAdapter(runtime).attach(rows)
        return summarize_universal_outcomes(attached)
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
