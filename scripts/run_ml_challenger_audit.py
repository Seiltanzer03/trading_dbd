#!/usr/bin/env python3
"""Run the bounded PASS 6 ML challenger on fresh real data off production."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.ml_challenger import run_ml_challenger
from seiltanzer.g1_short_horizon_historical_wf import _ensure_tables, _fetch_sources


class EphemeralRuntime:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def _source_set_sha(sources: list[dict[str, Any]]) -> str:
    payload = sorted(
        (str(source["instrument"]), str(source["source_id"]),
         str(source["source_sha256"]))
        for source in sources
    )
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _fresh_sources() -> tuple[list[dict[str, Any]], str, dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="ml-challenger-") as directory:
        runtime = EphemeralRuntime(Path(directory) / "research.sqlite3")
        try:
            _ensure_tables(runtime)  # type: ignore[arg-type]
            sources, errors = _fetch_sources(runtime)  # type: ignore[arg-type]
            detached = [dict(source) for source in sources]
            return detached, _source_set_sha(detached), dict(errors)
        finally:
            runtime.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="ml-challenger-report.json")
    args = parser.parse_args()

    sources, source_set, source_errors = _fresh_sources()
    report = run_ml_challenger(
        sources, source_set_sha256=source_set)
    report["source_mode"] = "EPHEMERAL_REAL_YAHOO_5M_60D"
    report["source_fetch_errors"] = source_errors
    report["database_authority"] = "OFF_HOST_RESEARCH_ONLY"
    path = Path(args.output).resolve()
    path.write_text(json.dumps(
        report, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"],
        "hypotheses_tested": report["hypotheses_tested"],
        "discovery_signal_count": report["discovery_signal_count"],
        "model_family": report["model_family"],
        "model_library_version": report["model_library_version"],
        "baseline_method": report["baseline_method"],
        "dependency_pvalue_method": report["dependency_pvalue_method"],
        "production_authority": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
