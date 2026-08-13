#!/usr/bin/env python3
"""Read-only production snapshot audit for EDE v1.2.2."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.ablation import family_ablation
from seiltanzer.edge_discovery.discovery import run_discovery
from seiltanzer.edge_discovery.evidence_ledger import (
    append_frozen_evidence,
    build_frozen_evidence,
)
from seiltanzer.edge_discovery.prospective import ProspectiveFeatureAdapter


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


def immutable_snapshot(source: Path, destination: Path) -> None:
    src = sqlite3.connect(
        f"file:{source.resolve()}?mode=ro", uri=True, timeout=30.0)
    src.execute("PRAGMA query_only=ON")
    src.execute("PRAGMA busy_timeout=30000")
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst, pages=1000, sleep=0.05)
        dst.commit()
    finally:
        dst.close(); src.close()
    check = sqlite3.connect(
        f"file:{destination.resolve()}?mode=ro", uri=True, timeout=30.0)
    try:
        assert check.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        check.close()


def _edge_map(discovery: dict[str, Any], inventory: dict[str, Any]) -> list[dict[str, Any]]:
    candidates_by_horizon = {
        int(item["horizon_minutes"]): item.get("candidates") or []
        for item in discovery.get("horizons") or []}
    output: list[dict[str, Any]] = []
    for feature in inventory.get("features") or []:
        feature_id = str(feature["feature_id"])
        for horizon, coverage in (feature.get("by_horizon") or {}).items():
            candidates = [
                item for item in candidates_by_horizon.get(int(horizon), [])
                if feature_id in {
                    str(condition.get("feature_id"))
                    for condition in item.get("conditions") or []}]
            candidates.sort(key=lambda item: -float(
                (item.get("edge_score") or {}).get("score") or -1e9))
            best = candidates[0] if candidates else None
            comparison = (best or {}).get("global_ret5_comparison") or {}
            output.append({
                "feature": feature_id, "horizon": int(horizon),
                "feature_raw": int(coverage.get("raw") or 0),
                "feature_effective": int(coverage.get("effective") or 0),
                "feature_resolved": int(coverage.get("resolved") or 0),
                "feature_coverage_pct": float(coverage.get("coverage_pct") or 0.0),
                "candidate_raw": int((best or {}).get("raw_n") or 0),
                "candidate_effective": int((best or {}).get("effective_n") or 0),
                "delta_brier": comparison.get("brier_delta"),
                "delta_logloss": comparison.get("logloss_delta"),
                "q": (best or {}).get("q_value"),
                "temporal_stability": ({
                    "folds_positive": best.get("folds_positive"),
                    "folds_evaluated": best.get("folds_evaluated"),
                    "temporal_blocks": best.get("temporal_blocks"),
                } if best else None),
                "assets": (best or {}).get("assets") or [],
                "data_maturity": coverage.get("data_maturity", "INSUFFICIENT_DATA"),
                "edge_maturity": ((best or {}).get("edge_maturity")
                                  or "INSUFFICIENT_DATA"),
                "status": ((best or {}).get("edge_maturity")
                           or "INSUFFICIENT_DATA"),
                "where_it_helps": bool((best or {}).get("where_it_helps")),
                "where_it_hurts": bool((best or {}).get("where_it_hurts")),
            })
    return output


def audit(database: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ede-v12-production-") as temporary:
        snapshot = Path(temporary)/"immutable-production-copy.sqlite3"
        immutable_snapshot(database, snapshot)
        runtime = ReadOnlyRuntime(snapshot)
        try:
            adapter = ProspectiveFeatureAdapter(runtime)
            inventory = adapter.feature_capture_audit()
            rows = adapter.rows(resolved_only=True, strict=True)
        finally:
            runtime.close()
        source_sha = hashlib.sha256("|".join(
            f"{row['observation_id']}:{row['captured_ts']}:{row['target_ts']}"
            for row in rows).encode()).hexdigest()
        eligible = {
            str(row["feature_id"]) for row in inventory["features"]
            if bool(row["usable_for_ede"])}
        discovery = run_discovery(
            [], source_set_sha256=source_sha, prospective_rows=rows,
            eligible_feature_ids=eligible)
        ablation = family_ablation(discovery)
        matrix = _edge_map(discovery, inventory)
    candidates = [item for horizon in discovery["horizons"]
                  for item in horizon["candidates"]]
    candidates.sort(key=lambda item: -float(
        (item.get("edge_score") or {}).get("score") or -1e9))
    materialized_at = time.time()
    return {
        "contract_version": "g1s-ede-production-audit-v1.2.2",
        "source_database": str(database),
        "source_database_open_mode": "READ_ONLY_SNAPSHOT",
        "dataset_sha256": source_sha,
        "resolved_rows": len(rows),
        "inventory": inventory,
        "discovery": discovery,
        "feature_horizon_edge_map": matrix,
        "top_15_maturity_edges": [item for item in candidates
                                  if item.get("edge_maturity") != "INSUFFICIENT_DATA"][:15],
        "where_it_helps": [item for item in candidates if item.get("where_it_helps")][:15],
        "where_it_hurts": [item for item in candidates if item.get("where_it_hurts")][:15],
        "ablation": ablation,
        "cross_asset_results": [item for item in matrix
                                if item["feature"].startswith("cross.")],
        "frozen_evidence": build_frozen_evidence(
            inventory=inventory, discovery=discovery,
            dataset_sha256=source_sha,
            evidence_cutoff_ts=max(
                (float(row.get("resolved_ts") or row["target_ts"]) for row in rows),
                default=materialized_at),
            frozen_at=materialized_at, prospective_rows=rows),
        "synthetic_data_used": False,
        "retrospective_options_reconstruction": False,
        "production_authority": False,
        "auto_promotion": False,
        "ai_authority_changed": False,
    }


def _compact_candidate(item: dict[str, Any]) -> dict[str, Any]:
    comparison = item.get("global_ret5_comparison") or {}
    return {
        "candidate_id": item.get("candidate_id"),
        "horizon": item.get("horizon_minutes"),
        "conditions": item.get("conditions"), "raw": item.get("raw_n"),
        "effective": item.get("effective_n"), "coverage": item.get("coverage"),
        "delta_brier": comparison.get("brier_delta"),
        "delta_logloss": comparison.get("logloss_delta"),
        "q": item.get("q_value"), "folds_positive": item.get("folds_positive"),
        "folds_evaluated": item.get("folds_evaluated"),
        "assets": item.get("assets"), "data_maturity": item.get("data_maturity"),
        "status": item.get("edge_maturity"),
        "discovery_status": item.get("status"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path,
                        default=Path("/opt/seiltanzer/data/trades.db"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evidence-ledger", type=Path, default=None)
    args = parser.parse_args(argv)
    report = audit(args.database)
    if args.evidence_ledger:
        appended = append_frozen_evidence(
            args.evidence_ledger, report["frozen_evidence"])
        print("EDE_V121_EVIDENCE=" + json.dumps({
            "path": str(args.evidence_ledger),
            "appended": appended,
            "frozen_at": report["frozen_evidence"]["frozen_at"],
            "evidence_cutoff_ts": report["frozen_evidence"]["evidence_cutoff_ts"],
            "edge_maturity": report["frozen_evidence"]["edge_maturity"],
            "candidate_count": len(report["frozen_evidence"]["edge_candidates"]),
        }, sort_keys=True))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary_output.write_text(json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False), encoding="utf-8")
        temporary_output.replace(args.output)
    discovery = report["discovery"]
    print("EDE_V12_SUMMARY=" + json.dumps({
        "dataset_sha256": report["dataset_sha256"],
        "resolved_rows": report["resolved_rows"],
        "observations_by_horizon": discovery["observations_by_horizon"],
        "hypotheses_tested": discovery["hypotheses_tested"],
        "sample_gate_passed": discovery["sample_gate_passed"],
        "fdr_passed": discovery["fdr_passed"],
        "data_maturity_counts": discovery["data_maturity_counts"],
        "edge_maturity_counts": discovery["edge_maturity_counts"],
        "maturity_counts": discovery["edge_maturity_counts"],
        "historical_candidate_count": discovery["historical_candidate_count"],
        "feature_summary": report["inventory"]["summary"],
    }, sort_keys=True))
    for item in report["top_15_maturity_edges"]:
        print("EDE_V12_TOP=" + json.dumps(_compact_candidate(item), sort_keys=True))
    for name, item in report["ablation"]["groups"].items():
        print("EDE_V12_ABLATION_GROUP=" + json.dumps(
            {"group": name, "result": item}, sort_keys=True))
    for item in report["cross_asset_results"]:
        print("EDE_V12_CROSS=" + json.dumps(item, sort_keys=True))
    for item in report["inventory"]["zero_feature_diagnosis"]:
        print("EDE_V12_ZERO=" + json.dumps(item, sort_keys=True))
    for item in report["feature_horizon_edge_map"]:
        print("EDE_V12_FEATURE=" + json.dumps(item, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
