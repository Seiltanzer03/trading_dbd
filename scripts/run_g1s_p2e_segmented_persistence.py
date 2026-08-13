#!/usr/bin/env python3
"""Run the one-shot P2E real 5m/60d experiment into a JSON artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import threading
from pathlib import Path

from seiltanzer.g1_short_horizon_historical_wf import (
    _ensure_tables,
    _fetch_sources,
    _json,
    _sha,
)
from seiltanzer.g1_short_horizon_p2e_segmented_persistence import (
    dumps_report,
    run_from_runtime,
    run_segmented_persistence_experiment,
)


class _TemporaryRuntime:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="p2e-segmented-persistence-report.json")
    parser.add_argument(
        "--database",
        help="Read an existing immutable P1B source set instead of fetching Yahoo",
    )
    args = parser.parse_args()
    output = Path(args.output).resolve()
    with tempfile.TemporaryDirectory(prefix="seiltanzer-p2e-") as temporary:
        database = Path(args.database).resolve() if args.database else Path(temporary) / "research.sqlite3"
        runtime = _TemporaryRuntime(database)
        try:
            if args.database:
                report = run_from_runtime(runtime)
                report["source_fetch"] = {
                    "provider": "immutable P1B source set from verified production backup",
                    "interval": "5m", "requested_period": "60d",
                    "database_authority": "offline_read_only_copy",
                    "fresh_fetch_errors": {},
                }
            else:
                _ensure_tables(runtime)
                sources, fetch_errors = _fetch_sources(runtime)
                if fetch_errors:
                    raise RuntimeError(
                        "fresh real source fetch incomplete: "
                        + json.dumps(fetch_errors, sort_keys=True))
                source_set = _sha(_json(sorted(
                    (source["instrument"], source["source_id"], source["source_sha256"])
                    for source in sources)))
                report = run_segmented_persistence_experiment(
                    sources, source_set_sha256=source_set)
                report["source_fetch"] = {
                    "provider": "Yahoo Finance via yfinance",
                    "interval": "5m", "requested_period": "60d",
                    "instrument_count": len(sources),
                    "source_sha256": {
                        str(source["instrument"]): str(source["source_sha256"])
                        for source in sources
                    },
                    "fresh_fetch_errors": fetch_errors,
                }
            raw = dumps_report(report)
            output.write_text(raw + "\n", encoding="utf-8")
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            print(json.dumps({
                "output": str(output), "report_sha256": digest,
                "verdict": report["verdict"],
                "winner_count": report["winner_count"],
                "duration_ms": report["duration_ms"],
                "results": [{
                    key: row.get(key) for key in (
                        "horizon_minutes", "verdict", "raw_n", "effective_n",
                        "brier_relative_improvement", "logloss_relative_improvement",
                        "fold_joint_non_degrade_n")
                } for row in report["results"]],
            }, sort_keys=True))
        finally:
            runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
