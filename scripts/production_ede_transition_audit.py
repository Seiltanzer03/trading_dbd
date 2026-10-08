#!/usr/bin/env python3
"""Read-only production audit for already-frozen macro/wavelet transition state."""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from production_ede_v13_audit import ReadOnlyRuntime, immutable_snapshot
from seiltanzer.edge_discovery.baseline_rows import baseline_eligible_rows
from seiltanzer.edge_discovery.dataset_fingerprint import (
    DATASET_FINGERPRINT_CONTRACT_VERSION,
    research_dataset_fingerprint,
)
from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.transition_search import (
    TRANSITION_FEATURES,
    TRANSITION_HORIZONS,
    augment_rows_from_frozen_v3,
    run_transition_search,
)


# Canonical non-transition fields that the transition search may use only in
# explicitly predeclared interactions. Keep this set aligned with
# run_transition_search; including the actual available universe in the
# dataset fingerprint makes source identity change when the usable transition
# research surface changes.
_TRANSITION_INTERACTION_FEATURE_IDS = (
    "regime.wavelet_phase",
    "option_dynamics.gex_velocity",
    "option_dynamics.iv_velocity",
    "option.iv_rv_ratio",
    "cross.confirmation",
    "regime.trend",
)


def _transition_available_feature_ids(rows: list[dict[str, Any]]) -> set[str]:
    candidates = set(TRANSITION_FEATURES) | set(_TRANSITION_INTERACTION_FEATURE_IDS)
    return {
        feature_id
        for feature_id in candidates
        if any((row.get("ede_features") or {}).get(feature_id) is not None for row in rows)
    }


def _load_transition_inputs(runtime, adapter):
    """Keep unresolved context through transforms, retain only eligible rows."""
    rows, coverage, combined_gate = [], None, None
    for horizon in TRANSITION_HORIZONS:
        print(f"EDE_TRANSITION_LOAD_START horizon={horizon}", flush=True)
        horizon_rows = adapter.rows(resolved_only=False, strict=False,
                                    horizon_minutes=horizon)
        current_coverage = augment_rows_from_frozen_v3(runtime, horizon_rows)
        resolved = [row for row in horizon_rows if row.get("outcome_available")]
        eligible, gate = baseline_eligible_rows(resolved)
        if coverage is None:
            coverage = current_coverage
        else:
            coverage["coverage_by_horizon"][str(horizon)] = (
                current_coverage["coverage_by_horizon"][str(horizon)])
        if combined_gate is None:
            combined_gate = gate
        else:
            for key in ("input_rows", "eligible_rows", "excluded_rows", "invalid_direction_rows"):
                combined_gate[key] += gate[key]
            for feature, count in gate["missing_by_feature"].items():
                combined_gate["missing_by_feature"][feature] += count
        rows.extend(eligible)
        print(f"EDE_TRANSITION_LOAD_DONE horizon={horizon} rows={len(horizon_rows)} "
              f"resolved={len(resolved)} eligible={len(eligible)}", flush=True)
        del horizon_rows, resolved, eligible
    rows.sort(key=lambda row: (float(row["captured_ts"]), str(row["instrument"]),
                               int(row["horizon_minutes"])))
    return rows, coverage, combined_gate


def audit(database: Path, *, verified_immutable_input: bool = False) -> dict:
    started = time.time()
    temporary = None
    if verified_immutable_input:
        if any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise RuntimeError("verified immutable input has mutable SQLite sidecars")
        snapshot = database
    else:
        temporary = tempfile.TemporaryDirectory(prefix="ede-transition-production-")
        snapshot = Path(temporary.name) / "immutable-production-copy.sqlite3"
        immutable_snapshot(database, snapshot)
    runtime = ReadOnlyRuntime(snapshot)
    try:
        adapter = ProspectiveFeatureAdapter(runtime)
        rows, transition_coverage, baseline_gate = _load_transition_inputs(runtime, adapter)
    finally:
        runtime.close()
        if temporary is not None:
            temporary.cleanup()

    available_feature_ids = _transition_available_feature_ids(rows)
    source_sha = research_dataset_fingerprint(
        rows,
        eligible_feature_ids=available_feature_ids,
    )
    print(f"EDE_TRANSITION_SEARCH_START eligible={len(rows)}", flush=True)
    transition = run_transition_search(rows, source_set_sha256=source_sha)
    print("EDE_TRANSITION_SEARCH_DONE", flush=True)
    # discover_horizon publishes the actual inner search count under
    # inner_hypotheses_tested. Keep the transition summary truthful instead of
    # reporting zero while sample/FDR counters are nonzero.
    transition["hypotheses_tested"] = sum(
        int(horizon.get("inner_hypotheses_tested") or 0)
        for horizon in transition.get("horizons") or []
    )
    return {
        "contract_version": "g1s-ede-production-regime-transition-v1.3.7",
        "source_database": str(database),
        "source_database_open_mode": "READ_ONLY_SNAPSHOT",
        "dataset_sha256": source_sha,
        "dataset_fingerprint_contract_version": DATASET_FINGERPRINT_CONTRACT_VERSION,
        "available_transition_feature_ids": sorted(available_feature_ids),
        "resolved_before_baseline_gate": baseline_gate["input_rows"],
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
    parser.add_argument("--verified-immutable-input", action="store_true")
    args = parser.parse_args(argv)
    report = audit(args.database, verified_immutable_input=args.verified_immutable_input)
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
        "dataset_fingerprint_contract_version": report["dataset_fingerprint_contract_version"],
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
