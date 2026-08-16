#!/usr/bin/env python3
"""Run strategy-agnostic active structured edge discovery off production."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.active_edge_policy import (
    ACTIVE_EDGE_POLICY_VERSION,
    run_active_structured_discovery,
)
from seiltanzer.edge_discovery.historical import load_p1b_sources
from seiltanzer.edge_discovery.rates import build_rates_states, fetch_treasury_daily_rates
from seiltanzer.edge_discovery.universal_structured_discovery import UNIVERSAL_HORIZONS
from seiltanzer.edge_discovery.universal_target_scoring import BASELINE_METHOD
from seiltanzer.g1_short_horizon_historical_wf import _ensure_tables, _fetch_sources


class SQLiteRuntime:
    def __init__(self, path: Path, *, read_only: bool):
        if read_only:
            uri = f"file:{path.as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        else:
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False), encoding="utf-8")


def _source_set_sha(sources: list[dict[str, Any]]) -> str:
    payload = sorted(
        (str(source["instrument"]), str(source["source_id"]), str(source["source_sha256"]))
        for source in sources
    )
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _fresh_off_host_sources() -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="universal-ede-") as directory:
        runtime = SQLiteRuntime(Path(directory) / "research.sqlite3", read_only=False)
        try:
            _ensure_tables(runtime)  # type: ignore[arg-type]
            sources, errors = _fetch_sources(runtime)  # type: ignore[arg-type]
            detached = [dict(source) for source in sources]
            return detached, _source_set_sha(detached), dict(errors)
        finally:
            runtime.close()


def _immutable_database_sources(path: Path) -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    runtime = SQLiteRuntime(path, read_only=True)
    try:
        sources, source_set = load_p1b_sources(runtime)
        return sources, source_set, {}
    finally:
        runtime.close()


def _parse_horizons(raw: str) -> tuple[int, ...]:
    values = tuple(dict.fromkeys(
        int(item.strip()) for item in str(raw).split(",") if item.strip()))
    if not values:
        raise ValueError("--horizons must contain at least one horizon")
    unsupported = [value for value in values if value not in UNIVERSAL_HORIZONS]
    if unsupported:
        raise ValueError(f"unsupported horizons: {unsupported}")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database")
    parser.add_argument("--output", default="universal-structured-edge-report.json")
    parser.add_argument(
        "--horizons", default=",".join(str(value) for value in UNIVERSAL_HORIZONS))
    parser.add_argument("--skip-rates", action="store_true")
    args = parser.parse_args()
    horizons = _parse_horizons(args.horizons)

    if args.database:
        sources, source_set, source_errors = _immutable_database_sources(
            Path(args.database).resolve())
        source_mode = "IMMUTABLE_OFFLINE_DATABASE"
    else:
        sources, source_set, source_errors = _fresh_off_host_sources()
        source_mode = "EPHEMERAL_REAL_YAHOO_5M_60D"

    first = min(float(source["bars"][0]["bar_end_ts"])
                for source in sources if source.get("bars"))
    last = max(float(source["bars"][-1]["bar_end_ts"])
               for source in sources if source.get("bars"))
    rates_states = ()
    rates_metadata: dict[str, Any] = {
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
        except Exception as exc:
            rates_metadata["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"

    report = run_active_structured_discovery(
        sources,
        source_set_sha256=source_set,
        rates_states=rates_states,
        horizons=horizons,
    )
    report["baseline_method"] = BASELINE_METHOD
    report["rates"] = rates_metadata
    report["source_mode"] = source_mode
    report["source_fetch_errors"] = source_errors
    report["database_authority"] = "OFF_HOST_RESEARCH_ONLY"
    report["production_authority"] = False
    report["auto_promotion"] = False
    _write(Path(args.output).resolve(), report)
    print(json.dumps({
        "verdict": report["verdict"],
        "edge_policy": report.get("edge_policy", ACTIVE_EDGE_POLICY_VERSION),
        "requested_horizons": report["requested_horizons"],
        "baseline_method": report["baseline_method"],
        "hypotheses_tested_inner": report["hypotheses_tested_inner"],
        "sample_gate_passed_inner": report["sample_gate_passed_inner"],
        "fdr_passed_inner": report["fdr_passed_inner"],
        "discovery_signal_count": report["discovery_signal_count"],
        "rates_available": rates_metadata["available"],
        "source_mode": source_mode,
        "production_authority": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
