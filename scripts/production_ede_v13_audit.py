#!/usr/bin/env python3
"""Production read-only EDE v1.3 selective-edge + prospective-shadow audit."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from seiltanzer.edge_discovery.baseline_rows import baseline_eligible_rows
from seiltanzer.edge_discovery.candidate_registry import CandidateRegistry
from seiltanzer.edge_discovery.dataset_fingerprint import (
    DATASET_FINGERPRINT_CONTRACT_VERSION,
    research_dataset_fingerprint,
)
from seiltanzer.edge_discovery.evidence_ledger import (
    append_frozen_evidence,
    build_frozen_evidence,
)
from seiltanzer.edge_discovery.prospective_v13 import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.selective import (
    SELECTIVE_CONTRACT_VERSION,
    SELECTIVE_HORIZONS,
    run_selective_search,
)
from seiltanzer.edge_discovery.shadow import (
    ShadowLedger,
    create_shadow_predictions,
    resolve_shadow_predictions,
    shadow_summary,
)
from seiltanzer.edge_discovery.stratified import augment_selective_report_with_strata


EVALUATION_MEASUREMENT_CONTRACT = (
    f"{SELECTIVE_CONTRACT_VERSION}+{DATASET_FINGERPRINT_CONTRACT_VERSION}"
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


def _dataset_breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_horizon: dict[str, int] = {}
    by_instrument: dict[str, int] = {}
    for row in rows:
        horizon = str(int(row["horizon_minutes"])); instrument = str(row["instrument"])
        by_horizon[horizon] = by_horizon.get(horizon, 0)+1
        by_instrument[instrument] = by_instrument.get(instrument, 0)+1
    return {
        "resolved_rows": len(rows),
        "by_horizon": dict(sorted(by_horizon.items(), key=lambda item: int(item[0]))),
        "by_instrument": dict(sorted(by_instrument.items())),
    }


def _register_evaluations(
    path: Path, report: dict[str, Any], *, dataset_sha256: str, run_id: str,
) -> dict[str, int]:
    registry = CandidateRegistry(path)
    written = 0; existing = 0
    for horizon in report.get("horizons") or []:
        for candidate in horizon.get("hypothesis_evaluations") or []:
            before = len(registry.events())
            registry.register_evaluation(
                candidate, dataset_sha256=dataset_sha256,
                research_run=run_id,
                measurement_contract=EVALUATION_MEASUREMENT_CONTRACT)
            if len(registry.events()) > before:
                written += 1
            else:
                existing += 1
    return {
        "events_or_evaluations_written": written,
        "deduplicated_or_existing": existing,
        "ledger_events_total": len(registry.events()),
    }


def audit(
    database: Path, *, evidence_ledger: Path | None = None,
    shadow_ledger: Path | None = None, candidate_registry: Path | None = None,
) -> dict[str, Any]:
    materialized_at = time.time()
    with tempfile.TemporaryDirectory(prefix="ede-v13-production-") as temporary:
        snapshot = Path(temporary)/"immutable-production-copy.sqlite3"
        immutable_snapshot(database, snapshot)
        runtime = ReadOnlyRuntime(snapshot)
        try:
            adapter = ProspectiveFeatureAdapter(runtime)
            inventory = adapter.feature_capture_audit()
            all_rows = adapter.rows(resolved_only=False, strict=False)
            resolved_rows_all = [
                row for row in all_rows
                if row.get("outcome_available")
                and int(row["horizon_minutes"]) in SELECTIVE_HORIZONS]
            pending_rows = [
                row for row in all_rows
                if not row.get("outcome_available")
                and int(row["horizon_minutes"]) in SELECTIVE_HORIZONS]
        finally:
            runtime.close()

    # GLOBAL_RET5_PERSISTENCE is the primary comparator. Missing ret5/ret15 at
    # T0 is missing evidence, never zero. Gate before temporal folds so the
    # candidate and its baseline are trained/scored on one identical universe.
    resolved_rows, baseline_row_gate = baseline_eligible_rows(resolved_rows_all)
    eligible = {
        str(row["feature_id"]) for row in inventory["features"]
        if bool(row["usable_for_ede"])
    }
    # v2 fingerprints the values actually consumed by research, not merely row
    # identity/timestamps. Outcome resolution, causal feature backfill, adapter
    # changes, or eligibility changes therefore cannot collide with an immutable
    # historical evaluation that was produced from different inputs.
    source_sha = research_dataset_fingerprint(
        resolved_rows, eligible_feature_ids=eligible)
    selective = run_selective_search(
        prospective_rows=resolved_rows,
        source_set_sha256=source_sha,
        eligible_feature_ids=eligible)
    augment_selective_report_with_strata(selective, resolved_rows)
    evidence_cutoff = max(
        (float(row.get("resolved_ts") or row["target_ts"]) for row in resolved_rows),
        default=materialized_at)
    frozen = build_frozen_evidence(
        inventory=inventory, discovery=selective,
        dataset_sha256=source_sha, evidence_cutoff_ts=evidence_cutoff,
        frozen_at=materialized_at, prospective_rows=resolved_rows)

    evidence_append = None
    if evidence_ledger is not None:
        evidence_append = append_frozen_evidence(evidence_ledger, frozen)

    shadow_report: dict[str, Any] = {
        "enabled": shadow_ledger is not None,
        "prediction_creation": None, "resolution": None, "summary": None}
    if shadow_ledger is not None:
        ledger = ShadowLedger(shadow_ledger)
        resolution = resolve_shadow_predictions(
            ledger, resolved_rows=resolved_rows_all, asof_ts=materialized_at)
        causal_pending_rows = [
            row for row in pending_rows
            if float(row.get("captured_ts") or 0.0) >= materialized_at - 1e-6]
        prediction_creation = create_shadow_predictions(
            ledger, frozen_evidence=frozen, selective_report=selective,
            resolved_rows=resolved_rows, pending_rows=causal_pending_rows,
            created_ts=materialized_at)
        shadow_report = {
            "enabled": True,
            "prediction_creation": {
                **prediction_creation,
                "rule_must_preexist_t0": True,
                "eligible_pending_rows": len(causal_pending_rows),
            },
            "resolution": resolution,
            "summary": shadow_summary(ledger, cutoff_ts=materialized_at),
        }

    registry_report = None
    if candidate_registry is not None:
        registry_report = _register_evaluations(
            candidate_registry, selective, dataset_sha256=source_sha,
            run_id=f"production-{int(materialized_at)}-{source_sha[:12]}")

    return {
        "contract_version": "g1s-ede-production-audit-v1.3.4",
        "source_database": str(database),
        "source_database_open_mode": "READ_ONLY_SNAPSHOT",
        "dataset_sha256": source_sha,
        "dataset_fingerprint_contract_version": DATASET_FINGERPRINT_CONTRACT_VERSION,
        "evaluation_measurement_contract": EVALUATION_MEASUREMENT_CONTRACT,
        "dataset": _dataset_breakdown(resolved_rows),
        "dataset_before_baseline_gate": _dataset_breakdown(resolved_rows_all),
        "baseline_row_gate": baseline_row_gate,
        "pending_rows": len(pending_rows),
        "inventory": inventory,
        "selective_search": selective,
        "frozen_evidence": frozen,
        "evidence_ledger_appended": evidence_append,
        "candidate_registry": registry_report,
        "prospective_shadow": shadow_report,
        "synthetic_data_used": False,
        "retrospective_options_reconstruction": False,
        "production_authority": False,
        "production_directional_authority": False,
        "auto_promotion": False,
        "may_trigger_exit_or_close": False,
        "ai_authority_changed": False,
    }


def _compact_candidate(item: dict[str, Any]) -> dict[str, Any]:
    comparison = item.get("global_ret5_comparison") or {}
    return {
        "candidate_id": item.get("candidate_id"),
        "horizon": item.get("horizon_minutes"),
        "template": item.get("template"),
        "raw": item.get("raw_n"), "effective": item.get("effective_n"),
        "coverage": (item.get("practical_coverage") or {}).get("coverage_pct"),
        "delta_brier": comparison.get("brier_delta"),
        "delta_logloss": comparison.get("logloss_delta"),
        "q": item.get("q_value"),
        "folds_positive": item.get("folds_positive"),
        "folds_evaluated": item.get("folds_evaluated"),
        "asset_distribution": item.get("asset_distribution"),
        "edge_maturity": item.get("edge_maturity"),
        "research_rank": (item.get("research_rank") or {}).get("score"),
        "baseline_failure_regime": item.get("baseline_failure_regime"),
        "cross_instrument_stability": (
            (item.get("stratified_diagnostics") or {}).get("cross_instrument_stability")
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path,
                        default=Path("/opt/seiltanzer/data/trades.db"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evidence-ledger", type=Path, default=None)
    parser.add_argument("--shadow-ledger", type=Path, default=None)
    parser.add_argument("--candidate-registry", type=Path, default=None)
    args = parser.parse_args(argv)

    report = audit(
        args.database, evidence_ledger=args.evidence_ledger,
        shadow_ledger=args.shadow_ledger,
        candidate_registry=args.candidate_registry)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix+".tmp")
        temporary.write_text(json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False), encoding="utf-8")
        temporary.replace(args.output)

    selective = report["selective_search"]
    print("EDE_V13_SUMMARY=" + json.dumps({
        "dataset_sha256": report["dataset_sha256"],
        "dataset_fingerprint_contract_version": report["dataset_fingerprint_contract_version"],
        "evaluation_measurement_contract": report["evaluation_measurement_contract"],
        "dataset": report["dataset"],
        "dataset_before_baseline_gate": report["dataset_before_baseline_gate"],
        "baseline_row_gate": report["baseline_row_gate"],
        "pending_rows": report["pending_rows"],
        "search_budget": selective["search_budget"],
        "hypotheses_tested": selective["hypotheses_tested"],
        "sample_gate_passed": selective["sample_gate_passed"],
        "fdr_passed": selective["fdr_passed"],
        "stable_candidates": selective["stable_candidates"],
        "edge_maturity_counts": selective["edge_maturity_counts"],
        "verdict": selective["verdict"],
        "where_it_helps_contexts": len(selective.get("where_it_helps_contexts") or []),
        "where_it_hurts_contexts": len(selective.get("where_it_hurts_contexts") or []),
        "shadow": report["prospective_shadow"].get("summary"),
    }, sort_keys=True))
    for item in selective["top_20_research_candidates"]:
        print("EDE_V13_TOP=" + json.dumps(_compact_candidate(item), sort_keys=True))
    for item in selective["top_10_baseline_failure_regimes"]:
        print("EDE_V13_BASELINE_FAILURE=" + json.dumps(
            _compact_candidate(item), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
