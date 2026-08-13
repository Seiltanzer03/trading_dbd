#!/usr/bin/env python3
"""Run the first bounded EDE audit against an immutable production-backup copy."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from seiltanzer.edge_discovery.candidate_registry import CandidateRegistry
from seiltanzer.edge_discovery.ablation import family_ablation
from seiltanzer.edge_discovery.discovery import run_discovery
from seiltanzer.edge_discovery.historical import load_p1b_sources, option_t0_coverage
from seiltanzer.edge_discovery.prospective import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.registry import feature_registry
from seiltanzer.g1_short_horizon_historical_wf import _ensure_tables, _fetch_sources


class ReadOnlyRuntime:
    def __init__(self, path: Path):
        uri = f"file:{path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


class TemporaryRuntime:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def close(self) -> None:
        self._conn.close()


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False), encoding="utf-8")


def _ablation(report: dict) -> dict:
    candidates = [candidate for horizon in report["horizons"]
                  for candidate in horizon["candidates"]]
    groups = {name: [] for name in
              ("PRICE_ONLY", "PRICE_VOL", "PRICE_CROSS_ASSET", "FULL_PRICE_CONTEXT")}
    for candidate in candidates:
        features = {item["feature_id"] for item in candidate["conditions"]}
        has_vol = "rv15_over_rv60" in features
        has_cross = bool(features & {"cross_confirmation", "family_breadth_state"})
        if has_vol and has_cross:
            group = "FULL_PRICE_CONTEXT"
        elif has_vol:
            group = "PRICE_VOL"
        elif has_cross:
            group = "PRICE_CROSS_ASSET"
        else:
            group = "PRICE_ONLY"
        groups[group].append(candidate)
    price_best = max((float(item["edge_score"]["score"])
                      for item in groups["PRICE_ONLY"]), default=0.0)
    output = {}
    for name, items in groups.items():
        items.sort(key=lambda item: -float(item["edge_score"]["score"]))
        best = items[0] if items else None
        best_score = float(best["edge_score"]["score"]) if best else None
        output[name] = {
            "status": "AUDITED", "candidate_count": len(items),
            "historical_candidate_count": sum(
                item["status"] == "HISTORICAL_CANDIDATE" for item in items),
            "best_candidate_id": best.get("candidate_id") if best else None,
            "best_edge_score": best_score,
            "best_brier_improvement": (best["improvement"]["brier"] if best else None),
            "best_logloss_improvement": (best["improvement"]["logloss"] if best else None),
            "score_delta_vs_best_price_only": (
                best_score-price_best if best_score is not None and name != "PRICE_ONLY" else 0.0),
            "interpretation": "bounded conditional-rule family comparison; not pristine OOS",
        }
    output.update({
        "PRICE_OPTIONS": {"status": "INSUFFICIENT_HISTORICAL_T0_DATA"},
        "PRICE_OPTIONS_DYNAMICS": {"status": "INSUFFICIENT_HISTORICAL_T0_DATA"},
        "FULL_CONTEXT_WITH_OPTIONS": {"status": "INSUFFICIENT_HISTORICAL_T0_DATA"},
    })
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        help="optional immutable offline P1B DB copy; otherwise fetch fresh real Yahoo 5m/60d bars",
    )
    parser.add_argument(
        "--prospective-database",
        help="optional immutable offline G.1S DB copy for T0 coverage and resolved prospective EDE",
    )
    parser.add_argument("--output", default="edge-discovery-report.json")
    parser.add_argument("--feature-registry", default="edge-discovery-feature-registry.json")
    parser.add_argument("--candidate-registry", default="edge-discovery-candidates.jsonl")
    parser.add_argument("--research-run", default=None)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="seiltanzer-ede-") as temporary:
        if args.database:
            runtime = ReadOnlyRuntime(Path(args.database).resolve())
            try:
                sources, source_set = load_p1b_sources(runtime)
                options = option_t0_coverage(runtime)
            finally:
                runtime.close()
            source_fetch = {
                "provider": "immutable P1B source set from supplied offline DB copy",
                "database_authority": "offline_read_only_copy", "fresh_fetch_errors": {},
            }
        else:
            runtime = TemporaryRuntime(Path(temporary)/"ede-research.sqlite3")
            try:
                _ensure_tables(runtime)
                sources, fetch_errors = _fetch_sources(runtime)
            finally:
                runtime.close()
            source_set = hashlib.sha256("|".join(
                sorted(str(source["source_sha256"]) for source in sources)
            ).encode()).hexdigest()
            options = {
                "observation_count": 0, "v2_count": 0, "v3_count": 0,
                "option_static_available": 0, "option_dynamics_available": 0,
                "eligible_for_current_p1b_discovery": False,
                "reason": "fresh real-bar source set contains no historical option snapshots",
                "synthetic_option_history_used": False,
            }
            source_fetch = {
                "provider": "Yahoo Finance via yfinance",
                "interval": "5m", "requested_period": "60d",
                "database_authority": "temporary_runner_immutable_after_fetch",
                "fresh_fetch_errors": fetch_errors,
            }
        report = run_discovery(sources, source_set_sha256=source_set)
        inventory = feature_registry()
    prospective_audit = {
        "contract_version": "g1s-ede-prospective-adapter-v1.2",
        "observation_count": 0, "resolved_outcome_count": 0,
        "features": [],
        "summary": {
            "feature_definitions": inventory["feature_count"],
            "live_capture_implemented": 0, "with_real_observations": 0,
            "with_prospective_coverage": 0, "unavailable": inventory["feature_count"],
        },
        "retrospective_options_reconstruction": False,
        "production_authority": False, "auto_promotion": False,
    }
    prospective_report = None
    prospective_source = None
    if args.prospective_database:
        prospective_runtime = ReadOnlyRuntime(Path(args.prospective_database).resolve())
        try:
            adapter = ProspectiveFeatureAdapter(prospective_runtime)
            prospective_audit = adapter.feature_capture_audit()
            prospective_rows = adapter.rows(resolved_only=True, strict=True)
        finally:
            prospective_runtime.close()
        prospective_source = hashlib.sha256("|".join(
            f"{row['observation_id']}:{row['captured_ts']}:{row['target_ts']}"
            for row in prospective_rows
        ).encode()).hexdigest()
        eligible_features = {
            str(row["feature_id"]) for row in prospective_audit["features"]
            if bool(row["usable_for_ede"])
        }
        prospective_report = run_discovery(
            [], source_set_sha256=prospective_source,
            prospective_rows=prospective_rows,
            eligible_feature_ids=eligible_features)
    option_ids = {
        "option.iv", "option.iv_rv_ratio", "option.skew", "option.term_slope",
        "option.gex_net_balance", "option.zero_gamma_distance", "option.delta",
        "option.vanna", "option.charm", "option_dynamics.iv_velocity",
        "option_dynamics.skew_velocity", "option_dynamics.gex_velocity",
        "option_dynamics.vanna_velocity", "option_dynamics.charm_velocity",
        "option_dynamics.zero_gamma_velocity",
    }
    option_coverage = [row for row in prospective_audit["features"]
                       if row["feature_id"] in option_ids]
    option_eligible = any(bool(row["usable_for_ede"]) for row in option_coverage)
    report.update({
        "feature_inventory": {
            "feature_count": inventory["feature_count"],
            "family_count": inventory["family_count"],
            "families": inventory["families"],
            "historically_unavailable": inventory["historically_unavailable"],
            "prospective_capture_audit": prospective_audit,
        },
        "options": {
            "coverage": options,
            "real_t0_feature_coverage": option_coverage,
            "incremental_edge_verdict": (
                prospective_report["verdict"] if option_eligible and prospective_report
                else "INSUFFICIENT_DATA"),
            "synthetic_history_used": False,
        },
        "prospective_discovery": prospective_report,
        "prospective_ablation": (
            family_ablation(prospective_report) if prospective_report else None),
        "source_fetch": source_fetch,
        "ablation": _ablation(report),
        "production_authority": False, "auto_promotion": False,
        "ai_trading_authority_changed": False,
    })
    output = Path(args.output).resolve()
    registry_path = Path(args.feature_registry).resolve()
    candidates_path = Path(args.candidate_registry).resolve()
    _write(output, report)
    _write(registry_path, inventory)
    registry = CandidateRegistry(candidates_path)
    research_run = str(args.research_run or os.environ.get("GITHUB_RUN_ID")
                       or f"local-{int(time.time())}")
    for evaluation in report["hypothesis_evaluations"]:
        registry.register_evaluation(
            evaluation, dataset_sha256=source_set, research_run=research_run,
            measurement_contract=report["contract_version"])
    if prospective_report is not None and prospective_source is not None:
        for evaluation in prospective_report["hypothesis_evaluations"]:
            registry.register_evaluation(
                evaluation, dataset_sha256=prospective_source,
                research_run=research_run,
                measurement_contract=prospective_report["contract_version"])
    summary = {
        "verdict": report["verdict"],
        "observations_by_horizon": report["observations_by_horizon"],
        "feature_count_used": report["feature_count_used"],
        "hypotheses_tested": report["hypotheses_tested"],
        "sample_gate_passed": report["sample_gate_passed"],
        "fdr_passed": report["fdr_passed"],
        "aggregated_sample_gate_passed": report["aggregated_sample_gate_passed"],
        "stability_gate_passed": report["stability_gate_passed"],
        "historical_candidate_count": report["historical_candidate_count"],
        "options_incremental_edge": report["options"]["incremental_edge_verdict"],
        "prospective_observations": prospective_audit["observation_count"],
        "prospective_resolved": prospective_audit["resolved_outcome_count"],
        "production_authority": False, "auto_promotion": False,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
