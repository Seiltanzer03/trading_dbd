#!/usr/bin/env python3
"""Run strategy-agnostic structured edge discovery on an immutable DB copy."""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from pathlib import Path

from seiltanzer.edge_discovery.historical import load_p1b_sources
from seiltanzer.edge_discovery.rates import build_rates_states, fetch_treasury_daily_rates
from seiltanzer.edge_discovery.universal_structured_discovery import (
    run_universal_structured_discovery,
)


class ReadOnlyRuntime:
    def __init__(self, path: Path):
        uri = f"file:{path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True,
                        help="immutable offline production/P1B SQLite copy")
    parser.add_argument("--output", default="universal-structured-edge-report.json")
    parser.add_argument("--skip-rates", action="store_true",
                        help="run without official Treasury daily context")
    args = parser.parse_args()

    runtime = ReadOnlyRuntime(Path(args.database).resolve())
    try:
        sources, source_set = load_p1b_sources(runtime)
    finally:
        runtime.close()

    first = min(float(source["bars"][0]["bar_end_ts"]) for source in sources if source.get("bars"))
    last = max(float(source["bars"][-1]["bar_end_ts"]) for source in sources if source.get("bars"))
    rates_states = ()
    rates_metadata = {
        "requested": not bool(args.skip_rates),
        "available": False,
        "source": "U.S. Treasury Daily Treasury Par Yield Curve Rates",
        "intraday_velocity_claim": False,
        "prospective_confirmation": False,
        "error": None,
    }
    if not args.skip_rates:
        try:
            observations = fetch_treasury_daily_rates(first, last)
            rates_states = tuple(build_rates_states(observations))
            rates_metadata.update({
                "available": bool(rates_states),
                "daily_observations": len(observations),
                "states": len(rates_states),
            })
        except Exception as exc:  # discovery remains useful without optional macro context
            rates_metadata["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"

    report = run_universal_structured_discovery(
        sources, source_set_sha256=source_set, rates_states=rates_states)
    report["rates"] = rates_metadata
    report["database_authority"] = "OFFLINE_READ_ONLY_IMMUTABLE_COPY"
    report["production_authority"] = False
    report["auto_promotion"] = False
    _write(Path(args.output).resolve(), report)
    print(json.dumps({
        "verdict": report["verdict"],
        "hypotheses_tested_inner": report["hypotheses_tested_inner"],
        "sample_gate_passed_inner": report["sample_gate_passed_inner"],
        "fdr_passed_inner": report["fdr_passed_inner"],
        "discovery_signal_count": report["discovery_signal_count"],
        "rates_available": rates_metadata["available"],
        "production_authority": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
