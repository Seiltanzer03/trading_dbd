#!/usr/bin/env python3
"""Authoritative localhost production-readiness verification."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
FAST_TIMEOUT = 5.0
TRANSIENT_ATTEMPTS = 3
TRANSIENT_RETRY_DELAY_SEC = 1.0
SCHEMA_BACKUP_MAX_AGE_SEC = 15 * 60.0
EVIDENCE_REPORTS = {
    "probability_oos", "continuous_oos", "calibration_oos",
    "ablation", "trade_relevance", "final_report",
}
REQUIRED_BACKUP_TABLES = (
    "trades", "passive_market_observations", "g1_q_capture_attempts",
    "g1m_management_observations", "g1m_resolutions", "g1m_policy_outcomes",
    "g1s_observations", "g1s_resolutions", "g1s_models", "g1s_shadow_predictions",
    "g1s_trade_links", "g1s_barrier_outcomes", "g1s_training_cuts", "g1s_model_cut_links",
    "g1s_path_metrics", "g1s_dependency_groups",
    "g1s_return_models", "g1s_return_predictions",
    "g1s_probability_calibrators", "g1s_calibrated_predictions",
    "g1s_validation_cohorts", "g1s_champion_prediction_links",
    "g1s_historical_sources", "g1s_historical_wf_runs",
    "g1m_local_windows", "g1m_local_outcomes", "g1m_local_policy_outcomes",
    "g1m_local_contract_errors", "research_materialization_state",
)


def sh(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def request(path: str, *, method: str = "GET", timeout: float = FAST_TIMEOUT):
    started = time.monotonic()
    req = urllib.request.Request(BASE+path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(); code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read(); code = int(exc.code)
    elapsed_ms = (time.monotonic()-started)*1000.0
    body = json.loads(raw.decode("utf-8")) if raw else None
    return code, body, elapsed_ms


def wait_core(expected_sha: str) -> None:
    for attempt in range(1, 31):
        server_sha = sh("git", "-C", "/opt/seiltanzer", "rev-parse", "HEAD")
        active = sh("systemctl", "is-active", "seiltanzer")
        try:
            code, _, elapsed = request("/api/state", timeout=2.0)
        except Exception:
            code, elapsed = 0, 0.0
        print(f"startup attempt={attempt} sha={server_sha} active={active} http={code} {elapsed:.0f}ms")
        if server_sha == expected_sha and active == "active" and code == 200:
            return
        time.sleep(3)
    raise AssertionError("production did not become ready within 90 seconds")


def _is_transient_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout, ConnectionError))


def assert_fast(path: str, *, budget_ms: float | None = None,
                attempts: int = TRANSIENT_ATTEMPTS):
    """Verify one bounded route, retrying transport contention only.

    A research materializer can briefly hold the shared SQLite/runtime lock.
    Retrying a socket timeout does not relax the response-time assertion: the
    successful attempt must still satisfy the original per-route budget.
    HTTP errors, malformed bodies and assertion failures remain immediate.
    """
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            code, body, elapsed = request(path, timeout=FAST_TIMEOUT)
        except Exception as exc:
            if not _is_transient_transport_error(exc) or attempt >= attempts:
                raise
            print(f"{path}: transient {type(exc).__name__} "
                  f"attempt={attempt}/{attempts}; retrying")
            time.sleep(TRANSIENT_RETRY_DELAY_SEC)
            continue
        print(f"{path}: {code} {elapsed:.0f}ms attempt={attempt}/{attempts}")
        assert code == 200, (path, code, body)
        if budget_ms is not None:
            assert elapsed < budget_ms, (path, elapsed, budget_ms)
        return body
    raise AssertionError((path, "retry loop exhausted"))


def assert_authority_off(authority: dict, label: str) -> None:
    assert authority.get("production_authority") is False, (label, authority)
    assert authority.get("edge_claim_allowed", False) is False, (label, authority)
    if "auto_execution_allowed" in authority:
        assert authority.get("auto_execution_allowed") is False, (label, authority)
    if "policy_promotion_allowed" in authority:
        assert authority.get("policy_promotion_allowed") is False, (label, authority)


def schema_complete_backup(backups: dict) -> dict:
    """Newest verified manifest satisfying the current schema contract."""
    for manifest in backups.get("local") or []:
        if manifest.get("verified") is not True:
            continue
        counts = manifest.get("critical_table_counts") or {}
        if all(table in counts and counts.get(table) is not None for table in REQUIRED_BACKUP_TABLES):
            return manifest
    raise AssertionError("no verified schema-complete local backup for current G1S contract")


def wait_fast_resolved() -> dict:
    latest = {}
    for attempt in range(1, 31):
        latest = assert_fast("/api/research/g1s/status", budget_ms=3000)
        by = {int(row["horizon_minutes"]): row for row in latest.get("horizons", [])}
        ready = all(int((by.get(h) or {}).get("raw_resolved") or 0) > 0 for h in (15,30,60))
        print("g1s resolved attempt=", attempt,
              {h: (by.get(h) or {}).get("raw_resolved") for h in (15,30,60)})
        if ready:
            return latest
        time.sleep(3)
    raise AssertionError("H15/H30/H60 did not materialize resolved evidence within 90 seconds")


def wait_evidence_materialized() -> tuple[dict, dict]:
    """Wait for worker-owned frozen evidence; never request a live full-history scan."""
    latest_status: dict = {}
    latest_final: dict = {}
    for attempt in range(1, 31):
        latest_status = assert_fast("/api/research/g1s/evidence-materialization", budget_ms=3000)
        reports = latest_status.get("reports") or []
        names = {str(row.get("report_name")) for row in reports}
        complete = EVIDENCE_REPORTS.issubset(names)
        latest_final = assert_fast("/api/research/g1s/final-report", budget_ms=3000)
        final_ready = latest_final.get("status") != "BUILDING"
        print("evidence attempt=", attempt, "reports=", sorted(names),
              "final=", latest_final.get("does_model_beat_baseline_oos") or latest_final.get("status"))
        if complete and final_ready:
            return latest_status, latest_final
        time.sleep(3)
    raise AssertionError("worker did not materialize complete G1S evidence within 90 seconds")


def verify_historical_contract(hist: dict) -> None:
    assert hist.get("contract_version") == "g1s-historical-wf-real-bars-v1", hist
    assert hist.get("evidence_label") == "HISTORICAL_WALK_FORWARD", hist
    assert hist.get("live_validation_label") == "LIVE_PROSPECTIVE_OOS", hist
    assert hist.get("interval") == "5m", hist
    assert hist.get("requested_period") == "60d", hist
    assert hist.get("historical_option_features") == "UNAVAILABLE_NOT_SYNTHESIZED", hist
    assert hist.get("synthetic_option_history") is False, hist
    assert hist.get("expanding_chronological_walk_forward") is True, hist
    assert hist.get("purge_embargo") is True, hist
    assert hist.get("shuffle") is False, hist
    assert hist.get("dependency_group_total_weight_one") is True, hist
    assert hist.get("historical_fold_outcomes_count_as_live_oos") is False, hist
    assert hist.get("provisional_artifact_starts_separate_live_oos") is True, hist
    assert hist.get("request_time_network_fetch") is False, hist
    assert hist.get("request_time_full_history_scan") is False, hist
    assert hist.get("auto_promotion") is False, hist
    assert hist.get("production_authority") is False, hist
    assert hist.get("state") in {"PENDING", "RUNNING", "COMPLETE", "ERROR"}, hist
    if hist.get("state") == "COMPLETE":
        assert int(hist.get("source_count") or 0) == 10, hist
        assert int(hist.get("run_count") or 0) == 10, hist
        sources = hist.get("sources") or []
        assert len(sources) == 10, sources
        assert all(int(row.get("bar_count") or 0) >= 1000 for row in sources), sources
        runs = hist.get("runs") or []
        assert len(runs) == 10, runs
        assert {int(row.get("horizon_minutes")) for row in runs} == {15,30,60,120,240}, runs
        assert {str(row.get("target")) for row in runs} == {"direction_up","terminal_log_return"}, runs
        for row in runs:
            assert int(row.get("fold_count") or 0) == 4, row
            assert row.get("verdict") in {"PROVISIONAL_LEARNED","HISTORICAL_BASELINE_NOT_BEATEN"}, row
            if row.get("historical_winner"):
                assert row.get("provisional_model_id"), row


def verify(expected_sha: str) -> None:
    wait_core(expected_sha)
    fast_paths = [
        ("/api/state", 3000), ("/api/validation", None),
        ("/api/system/database-authority", 3000), ("/api/system/storage/status", 3000),
        ("/api/system/storage/backups", None), ("/api/research/runtime/status", 3000),
        ("/api/research/runtime/materializers", None), ("/api/research/g1s/status", 3000),
        ("/api/research/g1s/historical-wf", 3000),
        ("/api/research/g1s/horizons", None), ("/api/research/g1s/barriers", None),
        ("/api/research/g1s/cuts", None), ("/api/research/g1s/oos", 3000),
        ("/api/research/g1s/continuous-oos", 3000), ("/api/research/g1s/calibration-oos", 3000),
        ("/api/research/g1s/ablation", 3000), ("/api/research/g1s/trade-relevance", 3000),
        ("/api/research/g1s/evidence-materialization", 3000),
        ("/api/research/g1s/final-report", 3000),
        ("/api/research/g1/q/audit?limit=5000", None),
        ("/api/research/g1/intelligence/status", None),
        ("/api/research/g1/management/status", 3000),
        ("/api/research/g1/management/local-status", 3000),
    ]
    bodies = {path: assert_fast(path, budget_ms=budget) for path, budget in fast_paths}

    code, integrity, elapsed = request("/api/system/storage/integrity?full=false", timeout=45.0)
    print(f"/api/system/storage/integrity: {code} {elapsed:.0f}ms")
    assert code == 200 and integrity.get("ok") is True, integrity
    assert integrity.get("check_kind") == "quick_check", integrity

    g1s = wait_fast_resolved()
    evidence_status, final_report = wait_evidence_materialized()
    storage = bodies["/api/system/storage/status"]
    backups = bodies["/api/system/storage/backups"]
    db_auth = bodies["/api/system/database-authority"]
    runtime = bodies["/api/research/runtime/status"]
    historical = bodies["/api/research/g1s/historical-wf"]
    q = bodies["/api/research/g1/q/audit?limit=5000"]
    intel = bodies["/api/research/g1/intelligence/status"]
    g1m = bodies["/api/research/g1/management/status"]
    g1ml = bodies["/api/research/g1/management/local-status"]

    assert db_auth.get("authoritative_database_path") == "/opt/seiltanzer/data/trades.db", db_auth
    assert storage.get("research_health_decoupled") is True, storage
    assert storage.get("request_time_integrity_scan") is False, storage
    assert storage.get("health") in {"HEALTHY", "LOCAL_BACKUP_ONLY", "DISASTER_RECOVERY_DEGRADED"}, storage
    assert float(storage.get("rpo_target_sec") or 1e9) <= SCHEMA_BACKUP_MAX_AGE_SEC, storage

    worker = runtime.get("worker") or {}
    assert runtime.get("market_collection_separate_from_research") is True, runtime
    assert runtime.get("request_time_full_history_evidence_scan") is False, runtime
    assert runtime.get("request_time_historical_network_fetch") is False, runtime
    assert worker.get("contract_version") == "g1-research-worker-v1", worker
    assert worker.get("scalability_refinement_version") == "g1-research-worker-bounded-v4", worker
    assert worker.get("evidence_reports_request_time_scan") is False, worker
    assert worker.get("historical_walkforward_runs_on_research_worker") is True, worker
    assert worker.get("historical_walkforward_request_time_network_fetch") is False, worker
    assert worker.get("running") is True, worker
    assert float(worker.get("startup_grace_sec") or 0.0) >= 60.0, worker
    assert worker.get("first_cycle_not_before_ts") is not None, worker
    assert evidence_status.get("request_time_full_history_scan") is False, evidence_status
    assert {str(row.get("report_name")) for row in evidence_status.get("reports") or []} >= EVIDENCE_REPORTS
    verify_historical_contract(historical)

    assert final_report.get("production_authority") is False, final_report
    assert final_report.get("production_authority_changed") is False, final_report
    assert final_report.get("edge_claim_allowed") is False, final_report
    assert final_report.get("auto_promotion_allowed") is False, final_report
    assert final_report.get("does_model_beat_baseline_oos") in {"YES", "NO", "INSUFFICIENT"}, final_report

    champion = g1s.get("champion_validation") or {}
    champion_items = champion.get("items") or []
    assert champion.get("champion_frozen") is True, champion
    assert champion.get("challenger_can_stop_champion_stream") is False, champion
    assert champion.get("champion_can_train_on_own_oos") is False, champion
    assert champion.get("prediction_must_precede_target") is True, champion
    assert champion.get("auto_promotion") is False, champion
    assert champion.get("production_authority") is False, champion
    assert champion_items, champion
    for item in champion_items:
        assert item.get("champion_is_frozen") is True, item
        assert item.get("challenger_does_not_replace_champion") is True, item
        assert item.get("champion_training_excludes_live_oos") is True, item
        assert float(item.get("training_cutoff_ts") or 0.0) < float(item.get("oos_start_ts") or 0.0), item
        assert item.get("production_authority") is False, item

    if historical.get("state") == "COMPLETE":
        provisional_ids = {str(row.get("provisional_model_id")) for row in historical.get("runs") or []
                           if row.get("historical_winner") and row.get("provisional_model_id")}
        historical_cohorts = [row for row in champion_items
                              if row.get("source") == "HISTORICAL_WALK_FORWARD"]
        assert {str(row.get("champion_model_id")) for row in historical_cohorts} == provisional_ids, (
            provisional_ids, historical_cohorts)
        for item in historical_cohorts:
            assert item.get("status") == "LIVE_VALIDATING", item
            assert item.get("production_authority") is False, item

    local = backups.get("local") or []
    assert local and local[0].get("verified") is True, backups
    selected = schema_complete_backup(backups)
    selected_age = max(0.0, time.time()-float(selected.get("created_ts") or 0.0))
    assert selected_age <= SCHEMA_BACKUP_MAX_AGE_SEC, (selected.get("backup_id"), selected_age)
    counts = selected.get("critical_table_counts") or {}
    for table in REQUIRED_BACKUP_TABLES:
        assert table in counts and counts[table] is not None, (table, counts.get(table))
    print("SCHEMA_BACKUP", selected.get("backup_id"), f"age={selected_age:.0f}s", "PASS")

    assert_authority_off(g1s.get("authority") or {}, "G1S")
    assert_authority_off(g1m.get("authority") or {}, "G1M")
    assert_authority_off(g1ml.get("authority") or {}, "G1M_LOCAL")
    ia = intel.get("authority") or {}
    assert ia.get("research_only") is True and ia.get("production_authority") is False, ia
    assert ia.get("promotion_allowed") is False and ia.get("shadow_p_used_for_trading") is False, ia

    overdue = int((q.get("counts") or {}).get("DUE_BUT_NOT_RESOLVED") or 0)
    assert overdue == 0, q

    code, drill, elapsed = request("/api/system/storage/restore-drill", method="POST", timeout=90.0)
    print(f"restore-drill: {code} {elapsed:.0f}ms")
    assert code == 200 and drill.get("ok") is True, drill
    assert drill.get("schema_complete_current_contract") is True, drill
    assert drill.get("live_database_replaced") is False, drill
    assert not (drill.get("critical_table_mismatches") or {}), drill

    by = {int(row["horizon_minutes"]): row for row in g1s.get("horizons", [])}
    print("G1S", json.dumps({h: {"raw": by[h].get("raw_resolved"),
        "effective": by[h].get("effective_n"), "state": by[h].get("state")}
        for h in sorted(by)}, sort_keys=True))
    print("CHAMPION", json.dumps({"n": len(champion_items),
        "linked": sum(int(x.get("linked_prediction_n") or 0) for x in champion_items),
        "authority": champion.get("production_authority")}, sort_keys=True))
    print("HISTORICAL_WF", json.dumps({
        "state": historical.get("state"), "sources": historical.get("source_count"),
        "runs": historical.get("run_count"), "provisional": historical.get("provisional_count"),
        "authority": historical.get("production_authority")}, sort_keys=True))
    print("EDGE_VERDICT", final_report.get("does_model_beat_baseline_oos"))
    print("Q_AUDIT", json.dumps(q.get("counts") or {}, sort_keys=True))
    print("G1M_LOCAL", json.dumps({k: g1ml.get(k) for k in
        ("windows", "resolved", "evidence_eligible", "eligible_resolved")}, sort_keys=True))
    print("RESEARCH_WORKER", json.dumps({k: worker.get(k) for k in
        ("running", "last_duration_ms", "last_error", "scalability_refinement_version")}, sort_keys=True))
    print("RESTORE", drill.get("backup_id"), "PASS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    verify(args.expected_sha)
    print("PRODUCTION READINESS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
