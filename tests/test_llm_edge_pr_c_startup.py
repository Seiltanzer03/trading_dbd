from __future__ import annotations

import json
import sqlite3
import threading
import time

import seiltanzer.llm_edge_lifecycle as lifecycle
import seiltanzer.llm_edge_pr_c as prc
from seiltanzer.llm_edge_pr_c_startup import (
    STARTUP_CONTRACT_VERSION,
    initialize_pr_c_materialized_state,
)
from seiltanzer.llm_edge_prospective_journal import initialize_journal_storage


class Runtime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("""CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY,
                instrument TEXT NOT NULL,
                captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL,
                horizon_minutes INTEGER NOT NULL,
                frozen_features_json TEXT NOT NULL DEFAULT '{}',
                created_ts REAL NOT NULL
            )""")
            self._conn.execute("""CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY,
                resolved_ts REAL NOT NULL,
                terminal_log_return REAL,
                direction_label TEXT,
                mfe_log_return REAL,
                mae_log_return REAL,
                path_quality_status TEXT
            )""")


def test_startup_upgrade_preserves_materialized_truth_without_research(monkeypatch):
    runtime = Runtime()
    initialize_journal_storage(runtime)
    previous = {
        "contract_version": "llm-edge-lifecycle-v1.3-pr-b",
        "status": "OK",
        "researcher": {
            "proposal_runs": 14,
            "hypotheses": 61,
            "discovery_signals": 7,
            "frozen_prospective": 5,
            "collecting": 3,
            "underpowered": 1,
            "prospective_pass": 1,
            "prospective_fail": 1,
            "active_edge": 1,
            "strict_reference": 0,
            "rejected": 48,
        },
        "prospective_journal": {
            "opportunities_total": 123,
            "eligible_opportunities": 100,
            "matched_opportunities": 47,
            "unavailable_opportunities": 23,
            "resolved_outcomes": 41,
        },
        "candidates": [{
            "candidate_id": "llm-edge-candidate-existing",
            "state": "LIVE_VALIDATING",
            "prospective": {"matched_n": 47, "next_checkpoint": 48},
        }],
        "production_authority": False,
        "request_time_history_scan": False,
        "updated_ts": 100.0,
    }
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "INSERT INTO llm_edge_lifecycle_materialized VALUES(1,?,?)",
            (json.dumps(previous), 100.0),
        )

    # Startup contract upgrade must not invoke any research reconstruction.
    monkeypatch.setattr(
        prc, "materialize_lifecycle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("full lifecycle materialization must not run at startup")
        ),
    )
    monkeypatch.setattr(
        prc._evaluator, "evaluate_edge_research_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("evaluator must not run at startup")
        ),
    )
    monkeypatch.setattr(
        prc, "propose_edge_hypotheses",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider/proposer must not run at startup")
        ),
    )

    upgraded = initialize_pr_c_materialized_state(runtime, now=200.0)
    reread = lifecycle.read_materialized_lifecycle(runtime)

    assert upgraded["researcher"] == previous["researcher"]
    assert upgraded["candidates"] == previous["candidates"]
    assert upgraded["prospective_journal"] == previous["prospective_journal"]
    assert upgraded["pr_c_contract_version"] == prc.PR_C_CONTRACT_VERSION
    assert upgraded["pr_c_startup_contract_version"] == STARTUP_CONTRACT_VERSION
    assert upgraded["request_time_history_scan"] is False
    assert upgraded["production_authority"] is False
    assert upgraded["automation"]["manual_post_only"] is False
    assert upgraded["automation"]["required_new_resolved_t0"] == 100
    assert upgraded["automation"]["minimum_provider_interval_sec"] == 43_200
    assert upgraded["automation"]["max_automatic_hypotheses"] == 5
    assert upgraded["automation"]["heavy_evaluation_concurrency"] == 1
    assert upgraded["automation"]["new_resolved_t0_since_last_run"] is None
    assert "llm_discovery_to_prospective_survival_rate" in upgraded["research_quality"]
    assert reread == upgraded


def test_startup_upgrade_is_idempotent_and_never_downgrades_counts():
    runtime = Runtime()
    initialize_journal_storage(runtime)

    first = initialize_pr_c_materialized_state(runtime, now=100.0)
    first["researcher"]["hypotheses"] = 9
    first["candidates"] = [{"candidate_id": "persist-me"}]
    with runtime._lock, runtime._conn:
        runtime._conn.execute(
            "UPDATE llm_edge_lifecycle_materialized SET payload_json=?,updated_ts=? WHERE singleton_id=1",
            (json.dumps(first), 110.0),
        )

    second = initialize_pr_c_materialized_state(runtime, now=120.0)
    assert second["researcher"]["hypotheses"] == 9
    assert second["candidates"] == [{"candidate_id": "persist-me"}]
    assert second["pr_c_contract_version"] == prc.PR_C_CONTRACT_VERSION
    assert second["updated_ts"] == 120.0
    assert lifecycle.read_cached_materialized_lifecycle(runtime) == second


def test_cached_materialized_read_never_waits_for_worker_lock():
    runtime = Runtime()
    initialize_journal_storage(runtime)
    upgraded = initialize_pr_c_materialized_state(runtime, now=200.0)
    acquired = threading.Event()
    release = threading.Event()

    def hold_worker_lock():
        with runtime._lock:
            acquired.set()
            release.wait(timeout=1.0)

    thread = threading.Thread(target=hold_worker_lock, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    try:
        started = time.monotonic()
        reread = lifecycle.read_cached_materialized_lifecycle(runtime)
        assert time.monotonic() - started < 0.25
        assert reread == upgraded
    finally:
        release.set()
        thread.join(timeout=1.0)


def test_missing_cached_lifecycle_fails_closed_without_worker_lock():
    runtime = Runtime()
    setattr(runtime, lifecycle._MATERIALIZED_CACHE_ATTR, "{broken")
    acquired = threading.Event()
    release = threading.Event()

    def hold_worker_lock():
        with runtime._lock:
            acquired.set()
            release.wait(timeout=1.0)

    thread = threading.Thread(target=hold_worker_lock, daemon=True)
    thread.start()
    assert acquired.wait(timeout=1.0)
    try:
        started = time.monotonic()
        payload = lifecycle.read_cached_materialized_lifecycle(runtime)
        assert time.monotonic() - started < 0.25
        assert payload["status"] == "INITIALIZING"
        assert payload["production_authority"] is False
    finally:
        release.set()
        thread.join(timeout=1.0)
