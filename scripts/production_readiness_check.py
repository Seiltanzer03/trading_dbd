#!/usr/bin/env python3
"""Authoritative localhost production-readiness verification.

Executed over SSH by a GitHub-hosted workflow. It deliberately runs *on* the VPS
so localhost-only durability checks remain private, while deployment orchestration
no longer depends on the self-hosted GitHub runner process surviving heavy load.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
FAST_TIMEOUT = 5.0


def sh(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def request(path: str, *, method: str = "GET", timeout: float = FAST_TIMEOUT):
    started = time.monotonic()
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        code = int(exc.code)
    elapsed_ms = (time.monotonic() - started) * 1000.0
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


def assert_fast(path: str, *, budget_ms: float | None = None):
    code, body, elapsed = request(path, timeout=FAST_TIMEOUT)
    print(f"{path}: {code} {elapsed:.0f}ms")
    assert code == 200, (path, code, body)
    if budget_ms is not None:
        assert elapsed < budget_ms, (path, elapsed, budget_ms)
    return body


def assert_authority_off(authority: dict, label: str) -> None:
    assert authority.get("production_authority") is False, (label, authority)
    assert authority.get("edge_claim_allowed", False) is False, (label, authority)
    if "auto_execution_allowed" in authority:
        assert authority.get("auto_execution_allowed") is False, (label, authority)
    if "policy_promotion_allowed" in authority:
        assert authority.get("policy_promotion_allowed") is False, (label, authority)


def wait_fast_resolved() -> dict:
    latest = {}
    for attempt in range(1, 31):
        latest = assert_fast("/api/research/g1s/status", budget_ms=3000)
        by = {int(row["horizon_minutes"]): row for row in latest.get("horizons", [])}
        ready = all(int((by.get(h) or {}).get("raw_resolved") or 0) > 0 for h in (15, 30, 60))
        print("g1s resolved attempt=", attempt, {h: (by.get(h) or {}).get("raw_resolved") for h in (15,30,60)})
        if ready:
            return latest
        time.sleep(3)
    raise AssertionError("H15/H30/H60 did not materialize resolved evidence within 90 seconds")


def verify(expected_sha: str) -> None:
    wait_core(expected_sha)

    fast_paths = [
        ("/api/state", 3000),
        ("/api/validation", None),
        ("/api/system/database-authority", 3000),
        ("/api/system/storage/status", 3000),
        ("/api/system/storage/backups", None),
        ("/api/research/runtime/status", 3000),
        ("/api/research/runtime/materializers", None),
        ("/api/research/g1s/status", 3000),
        ("/api/research/g1s/horizons", None),
        ("/api/research/g1s/barriers", None),
        ("/api/research/g1s/cuts", None),
        ("/api/research/g1s/oos", None),
        ("/api/research/g1s/ablation", None),
        ("/api/research/g1s/trade-relevance", None),
        ("/api/research/g1/q/audit?limit=5000", None),
        ("/api/research/g1/intelligence/status", None),
        ("/api/research/g1/management/status", 3000),
        ("/api/research/g1/management/local-status", 3000),
    ]
    bodies = {path: assert_fast(path, budget_ms=budget) for path, budget in fast_paths}

    # True SQLite quick_check is intentionally separate from routine status latency.
    code, integrity, elapsed = request("/api/system/storage/integrity?full=false", timeout=45.0)
    print(f"/api/system/storage/integrity: {code} {elapsed:.0f}ms")
    assert code == 200 and integrity.get("ok") is True, integrity
    assert integrity.get("check_kind") == "quick_check", integrity

    g1s = wait_fast_resolved()
    storage = bodies["/api/system/storage/status"]
    backups = bodies["/api/system/storage/backups"]
    db_auth = bodies["/api/system/database-authority"]
    runtime = bodies["/api/research/runtime/status"]
    q = bodies["/api/research/g1/q/audit?limit=5000"]
    intel = bodies["/api/research/g1/intelligence/status"]
    g1m = bodies["/api/research/g1/management/status"]
    g1ml = bodies["/api/research/g1/management/local-status"]

    assert db_auth.get("authoritative_database_path") == "/opt/seiltanzer/data/trades.db", db_auth
    assert storage.get("research_health_decoupled") is True, storage
    assert storage.get("request_time_integrity_scan") is False, storage
    assert storage.get("health") in {"HEALTHY", "LOCAL_BACKUP_ONLY", "DISASTER_RECOVERY_DEGRADED"}, storage

    worker = runtime.get("worker") or {}
    assert runtime.get("market_collection_separate_from_research") is True, runtime
    assert worker.get("contract_version") == "g1-research-worker-v1", worker
    assert worker.get("running") is True, worker

    local = backups.get("local") or []
    assert local and local[0].get("verified") is True, backups
    assert float(storage.get("last_local_backup_age_sec") or 1e99) <= 1800.0, storage
    counts = local[0].get("critical_table_counts") or {}
    for table in (
        "trades", "passive_market_observations", "g1_q_capture_attempts",
        "g1m_management_observations", "g1m_resolutions", "g1m_policy_outcomes",
        "g1s_observations", "g1s_resolutions", "g1s_models", "g1s_shadow_predictions",
        "g1s_trade_links", "g1s_barrier_outcomes", "g1s_training_cuts", "g1s_model_cut_links",
        "g1m_local_windows", "g1m_local_outcomes", "g1m_local_policy_outcomes",
        "research_materialization_state",
    ):
        assert table in counts and counts[table] is not None, (table, counts.get(table))

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
    assert drill.get("live_database_replaced") is False, drill
    assert not (drill.get("critical_table_mismatches") or {}), drill

    by = {int(row["horizon_minutes"]): row for row in g1s.get("horizons", [])}
    print("G1S", json.dumps({h: {
        "raw": by[h].get("raw_resolved"),
        "effective": by[h].get("effective_n"),
        "state": by[h].get("state"),
    } for h in sorted(by)}, sort_keys=True))
    print("Q_AUDIT", json.dumps(q.get("counts") or {}, sort_keys=True))
    print("G1M_LOCAL", json.dumps({k: g1ml.get(k) for k in (
        "windows", "resolved", "evidence_eligible", "eligible_resolved")}, sort_keys=True))
    print("RESEARCH_WORKER", json.dumps({k: worker.get(k) for k in (
        "running", "last_duration_ms", "last_error", "scalability_refinement_version")}, sort_keys=True))
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
