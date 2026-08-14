#!/usr/bin/env python3
"""Read-only production audit for already-frozen macro/wavelet transition state."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from production_ede_v13_audit import ReadOnlyRuntime, _source_sha, immutable_snapshot
from seiltanzer.edge_discovery.baseline_rows import baseline_eligible_rows
from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.transition_search import (
    TRANSITION_HORIZONS,
    augment_rows_from_frozen_v3,
    run_transition_search,
)


def audit(database: Path) -> dict:
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="ede-transition-production-") as temporary:
        snapshot = Path(temporary) / "immutable-production-copy.sqlite3"
        immutable_snapshot(database, snapshot)
        runtime = ReadOnlyRuntime(snapshot)
        try:
            adapter = ProspectiveFeatureAdapter(runtime)
            all_rows = adapter.rows(resolved_only=False, strict=False)
            transition_coverage = augment_rows_from_frozen_v3(runtime, all_rows)
        finally:
            runtime.close()

    resolved_all = [
        row for row in all_rows
        if row.get("outcome_available")
        and int(row["horizon_minutes"]) in TRANSITION_HORIZONS
    ]
    rows, baseline_gate = baseline_eligible_rows(resolved_all)
    source_sha = _source_sha(rows)
    transition = run_transition_search(rows, source_set_sha256=source_sha)
    # discover_horizon publishes the actual inner search count under
    # inner_hypotheses_tested. Keep the transition summary truthful instead of
    # reporting zero while sample/FDR counters are nonzero.
    transition["hypotheses_tested"] = sum(
        int(horizon.get("inner_hypotheses_tested") or 0)
        for horizon in transition.get("horizons") or []
    )
    return {
        "contract_version": "g1s-ede-production-regime-transition-v1.3.6",
        "source_database": str(database),
        "source_database_open_mode": "READ_ONLY_SNAPSHOT",
        "dataset_sha256": source_sha,
        "resolved_before_baseline_gate": len(resolved_all),
        "resolved_after_baseline_gate": len(rows),
        "baseline_row_gate": baseline_gate,
        "transition_feature_coverage": transition_coverage,
        "transition_search": transition,
        "runtime_seconds": time.time() - started,
        "causal_baseline_adapter": "prospective_v13",
        "existing_frozen_v3_fields_only": True,
        "synthetic_data_used": False,
        "retrospective_reconstruction": False,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("/opt/seiltanzer/data/trades.db"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = audit(args.database)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ), encoding="utf-8")
        temporary.replace(args.output)
    transition = report["transition_search"]
    print("EDE_TRANSITION_SUMMARY=" + json.dumps({
        "dataset_sha256": report["dataset_sha256"],
        "resolved_rows": report["resolved_after_baseline_gate"],
        "search_budget": transition["search_budget"],
        "hypotheses_tested": transition["hypotheses_tested"],
        "sample_gate_passed": transition["sample_gate_passed"],
        "fdr_passed": transition["fdr_passed"],
        "stability_gate_passed": transition["stability_gate_passed"],
        "edge_maturity_counts": transition["edge_maturity_counts"],
        "verdict": transition["verdict"],
        "runtime_seconds": report["runtime_seconds"],
    }, sort_keys=True))
    for candidate in transition["top_20"]:
        print("EDE_TRANSITION_TOP=" + json.dumps(candidate, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
