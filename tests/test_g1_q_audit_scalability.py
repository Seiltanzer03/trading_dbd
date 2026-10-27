from __future__ import annotations

import math
import threading
import time

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_q_audit_scalability import (
    Q_AUDIT_CANDIDATE_BATCH_SIZE,
    _ORIGINAL_Q_AUDIT,
    _terminal_candidate_batch_snapshot,
)
from seiltanzer.g1_short_horizon_runtime import ShortHorizonRuntime
from seiltanzer.passive_learning import PassiveLearningEngine


class _Engine:
    def __init__(self, passive):
        self.passive = passive


def _runtime(tmp_path):
    cache = DiskCache(str(tmp_path / "cache.db"))
    passive = PassiveLearningEngine(
        str(tmp_path / "trades.db"),
        Settings(demo=False, data_dir=str(tmp_path)),
        cache,
    )
    runtime = ShortHorizonRuntime(_Engine(passive))
    return runtime, passive, cache


def _insert_observation(
    passive,
    *,
    observation_id: str,
    instrument: str,
    target_ts: float,
    status: str = "pending",
):
    passive._conn.execute(
        """
        INSERT INTO passive_market_observations(
            observation_id,anchor_group_id,captured_ts,target_ts,instrument,
            horizon_minutes,trigger_reason,market_price,feature_contract_version,
            forecast_model_version,calibrator_version,scenario_version,
            features_json,forecast_json,evidence_eligible,resolution_status,created_ts
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            observation_id,
            f"anchor-{observation_id}",
            target_ts - 3600.0,
            target_ts,
            instrument,
            60,
            "test",
            100.0,
            "features-v1",
            "forecast-v1",
            "cal-v1",
            "scenario-v1",
            "{}",
            "{}",
            1,
            status,
            target_ts - 3600.0,
        ),
    )


def _insert_attempt(
    passive,
    *,
    attempt_id: str,
    attempt_ts: float,
    instrument: str,
    observation_id: str | None,
    target_ts: float | None,
    blocker_code: str | None = None,
):
    passive._conn.execute(
        """
        INSERT INTO g1_q_capture_attempts(
            attempt_id,attempt_ts,attempt_origin,target_instrument,
            q_source_instrument,relation,proxy_transform,provider,
            requested_expiry_ts,source_available,source_fresh,
            target_price_available,source_price_available,chain_available,
            distribution_built,distribution_valid,observation_created,
            created_observation_id,blocker_code,latency_ms,
            capability_contract_version,attempt_contract_version,
            capture_policy_version,option_q_contract_version,
            expiry_clock_version,measurement_runtime_version,detail_json,created_ts
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            attempt_id,
            attempt_ts,
            "background_collector",
            instrument,
            None,
            "NONE",
            None,
            None,
            target_ts,
            1 if observation_id else 0,
            1 if observation_id else 0,
            1,
            1 if observation_id else 0,
            0,
            0,
            0,
            1 if observation_id else 0,
            observation_id,
            blocker_code,
            1.0,
            "cap-v1",
            "attempt-v1",
            "policy-v1",
            "q-v1",
            "clock-v1",
            "measurement-v1",
            "{}",
            attempt_ts,
        ),
    )


def test_q_audit_does_not_wait_for_passive_worker_lock(tmp_path):
    runtime, passive, cache = _runtime(tmp_path)
    lock_held = threading.Event()
    release_lock = threading.Event()

    def holder():
        with passive._lock:
            lock_held.set()
            release_lock.wait(timeout=3.0)

    holder_thread = threading.Thread(target=holder, daemon=True)
    holder_thread.start()
    assert lock_held.wait(timeout=1.0)

    result = {}
    error = {}

    def audit():
        try:
            result.update(runtime.q_audit(now=1_800_000_000.0, limit=5000))
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            error["exc"] = exc

    audit_thread = threading.Thread(target=audit, daemon=True)
    started = time.perf_counter()
    audit_thread.start()
    audit_thread.join(timeout=0.75)
    elapsed = time.perf_counter() - started
    completed_while_lock_held = not audit_thread.is_alive()

    release_lock.set()
    holder_thread.join(timeout=1.0)
    audit_thread.join(timeout=1.0)
    try:
        assert not error, error
        assert completed_while_lock_held, "Q audit still waits for passive worker lock"
        assert elapsed < 0.75
        assert result["slow_q_semantics_unchanged"] is True
    finally:
        passive.close()
        cache.close()


def test_q_audit_batch_preserves_refined_v2_semantics_for_mixed_candidates(tmp_path):
    runtime, passive, cache = _runtime(tmp_path)
    now = 1_800_000_000.0
    overdue_tie = now - 1200.0
    blocked_target = now - 2400.0
    future_target = now + 1200.0
    resolved_target = now - 3600.0
    try:
        with passive._lock, passive._conn:
            _insert_observation(
                passive,
                observation_id="obs-overdue-tie",
                instrument="XAU",
                target_ts=overdue_tie,
                status="pending",
            )
            _insert_observation(
                passive,
                observation_id="obs-blocked",
                instrument="XAU",
                target_ts=blocked_target,
                status="blocked",
            )
            _insert_observation(
                passive,
                observation_id="obs-future",
                instrument="NAS100",
                target_ts=future_target,
                status="pending",
            )
            _insert_observation(
                passive,
                observation_id="obs-resolved",
                instrument="NAS100",
                target_ts=resolved_target,
                status="resolved",
            )

            # Equal terminal timestamps must keep the legacy/v2 bar-first tie rule.
            passive._conn.execute(
                """
                INSERT INTO passive_market_bars(
                    instrument,bar_start_ts,bar_end_ts,open,high,low,close,
                    source,quality,kind,created_ts
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "XAU", overdue_tie - 60.0, overdue_tie,
                    100.0, 101.0, 99.0, 100.5,
                    "test-bar", 1.0, "direct", overdue_tie,
                ),
            )
            passive._conn.execute(
                """
                INSERT INTO passive_market_path(instrument,ts,price,source,quality,kind)
                VALUES(?,?,?,?,?,?)
                """,
                ("XAU", overdue_tie, 100.5, "test-path", 1.0, "direct"),
            )
            # For the already-blocked observation, path is newer than bar and
            # therefore must be exposed as the diagnostic terminal candidate.
            passive._conn.execute(
                """
                INSERT INTO passive_market_bars(
                    instrument,bar_start_ts,bar_end_ts,open,high,low,close,
                    source,quality,kind,created_ts
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "XAU", blocked_target - 80.0, blocked_target - 20.0,
                    100.0, 101.0, 99.0, 100.5,
                    "test-bar-old", 1.0, "direct", blocked_target - 20.0,
                ),
            )
            passive._conn.execute(
                """
                INSERT INTO passive_market_path(instrument,ts,price,source,quality,kind)
                VALUES(?,?,?,?,?,?)
                """,
                ("XAU", blocked_target - 10.0, 100.4, "test-path-new", 1.0, "direct"),
            )

            _insert_attempt(
                passive,
                attempt_id="attempt-overdue-tie",
                attempt_ts=now - 10.0,
                instrument="XAU",
                observation_id="obs-overdue-tie",
                target_ts=overdue_tie,
            )
            _insert_attempt(
                passive,
                attempt_id="attempt-blocked",
                attempt_ts=now - 20.0,
                instrument="XAU",
                observation_id="obs-blocked",
                target_ts=blocked_target,
            )
            _insert_attempt(
                passive,
                attempt_id="attempt-future",
                attempt_ts=now - 30.0,
                instrument="NAS100",
                observation_id="obs-future",
                target_ts=future_target,
            )
            _insert_attempt(
                passive,
                attempt_id="attempt-resolved",
                attempt_ts=now - 40.0,
                instrument="NAS100",
                observation_id="obs-resolved",
                target_ts=resolved_target,
            )
            _insert_attempt(
                passive,
                attempt_id="attempt-capture-blocked",
                attempt_ts=now - 50.0,
                instrument="XAU",
                observation_id=None,
                target_ts=None,
                blocker_code="NO_Q_SOURCE_CONFIGURED",
            )

        original = _ORIGINAL_Q_AUDIT(runtime, now=now, limit=5000)
        bounded = runtime.q_audit(now=now, limit=5000)
        assert bounded == original

        by_attempt = {item["attempt_id"]: item for item in bounded["items"]}
        assert by_attempt["attempt-overdue-tie"]["audit_state"] == "DUE_BUT_NOT_RESOLVED"
        assert by_attempt["attempt-overdue-tie"]["terminal_candidate_source"] == "direct_1m_bar"
        assert by_attempt["attempt-blocked"]["audit_state"] == "RESOLUTION_BLOCKED"
        assert by_attempt["attempt-blocked"]["terminal_candidate_source"] == "direct_path_point"
        assert bounded["capture_blocked_n"] == 1
        assert bounded["capture_blockers"] == {"NO_Q_SOURCE_CONFIGURED": 1}

        q_indexes = {
            row[1] for row in passive._conn.execute(
                "PRAGMA index_list('g1_q_capture_attempts')"
            ).fetchall()
        }
        path_indexes = {
            row[1] for row in passive._conn.execute(
                "PRAGMA index_list('passive_market_path')"
            ).fetchall()
        }
        bar_indexes = {
            row[1] for row in passive._conn.execute(
                "PRAGMA index_list('passive_market_bars')"
            ).fetchall()
        }
        assert "ix_g1_q_attempt_ts" in q_indexes
        assert "ix_passive_bar_instrument_end" in bar_indexes
        assert "ix_passive_direct_path_terminal" in path_indexes
        assert "ix_passive_direct_bar_terminal" in bar_indexes
    finally:
        passive.close()
        cache.close()


def test_terminal_candidate_sql_round_trips_scale_by_batch_not_rows(tmp_path):
    runtime, passive, cache = _runtime(tmp_path)
    statements: list[str] = []
    try:
        passive._conn.set_trace_callback(statements.append)
        request_n = Q_AUDIT_CANDIDATE_BATCH_SIZE * 2 + 37
        requests = [
            (index, "XAU" if index % 2 == 0 else "NAS100", 1_800_000_000.0 + index)
            for index in range(request_n)
        ]
        result = _terminal_candidate_batch_snapshot(passive._conn, requests)
        assert len(result) == request_n
        batch_selects = [sql for sql in statements if "WITH requested" in sql]
        assert len(batch_selects) == math.ceil(
            request_n / Q_AUDIT_CANDIDATE_BATCH_SIZE
        )
        # Regression boundary: hundreds of requested rows must never regress to
        # the former two execute() calls per audit row.
        assert len(batch_selects) < 10
    finally:
        passive._conn.set_trace_callback(None)
        passive.close()
        cache.close()


def test_terminal_candidate_queries_use_direct_partial_indexes(tmp_path):
    runtime, passive, cache = _runtime(tmp_path)
    try:
        path_plan = " ".join(
            str(row[3]) for row in passive._conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT ts FROM passive_market_path
                WHERE instrument=? AND ts<=?
                  AND kind='direct' AND COALESCE(quality,0)>=0.90
                ORDER BY ts DESC LIMIT 1
                """,
                ("XAU", 1_800_000_000.0),
            ).fetchall()
        )
        bar_plan = " ".join(
            str(row[3]) for row in passive._conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT bar_end_ts FROM passive_market_bars
                WHERE instrument=? AND bar_end_ts<=?
                  AND kind='direct' AND COALESCE(quality,0)>=0.90
                ORDER BY bar_end_ts DESC LIMIT 1
                """,
                ("XAU", 1_800_000_000.0),
            ).fetchall()
        )
        assert "ix_passive_direct_path_terminal" in path_plan
        assert "ix_passive_direct_bar_terminal" in bar_plan
    finally:
        passive.close()
        cache.close()


def test_readiness_http_summary_keeps_same_cohort_and_maturity_without_items(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    runtime, passive, cache = _runtime(tmp_path)
    now = 1_800_000_000.
    try:
        for index, (status, target) in enumerate((
                ('resolved', now-1000), ('pending', now+1000),
                ('pending', now-1000), ('contract_rejected', now-2000))):
            identity = 'summary-observation-' + str(index)
            _insert_observation(passive, observation_id=identity, instrument='XAU',
                                target_ts=target, status=status)
            _insert_attempt(passive, attempt_id='summary-attempt-'+str(index),
                            attempt_ts=now-index, instrument='XAU',
                            observation_id=identity, target_ts=target)
        _insert_attempt(passive, attempt_id='summary-blocked', attempt_ts=now-10,
                        instrument='XAU', observation_id=None, target_ts=None,
                        blocker_code='SOURCE_UNAVAILABLE')
        passive._conn.commit()
        app = FastAPI()
        app.add_api_route('/api/research/g1/q/audit', runtime.q_audit, methods=['GET'])
        with TestClient(app) as client:
            full = client.get('/api/research/g1/q/audit', params={'now':now, 'limit':5000}).json()
            summary = client.get('/api/research/g1/q/audit', params={
                'now':now, 'limit':5000, 'include_items':'false'}).json()
        assert len(full['items']) == full['attempt_n'] == 5
        assert summary['items'] == []
        assert summary['items_included'] is False
        assert summary['item_count_total'] == full['attempt_n']
        for key, value in full.items():
            if key != 'items':
                assert summary[key] == value, key
    finally:
        passive.close()
        cache.close()


def test_file_audit_seeks_observation_metadata_without_wide_table_reads(tmp_path, monkeypatch):
    from seiltanzer import g1_q_audit_scalability as owner

    runtime, passive, cache = _runtime(tmp_path)
    now = 1_800_000_000.0
    statements = []
    original_open = owner._open_read_snapshot

    def traced_snapshot(instance):
        connection = original_open(instance)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(owner, '_open_read_snapshot', traced_snapshot)
    try:
        for index, status in enumerate(('resolved', 'pending')):
            identity = 'covering-observation-' + str(index)
            _insert_observation(passive, observation_id=identity, instrument='XAU',
                                target_ts=now+1200, status=status)
            _insert_attempt(passive, attempt_id='covering-attempt-'+str(index),
                            attempt_ts=now-index, instrument='XAU',
                            observation_id=identity, target_ts=now+1200)
        _insert_attempt(passive, attempt_id='covering-blocked', attempt_ts=now-2,
                        instrument='XAU', observation_id=None, target_ts=None,
                        blocker_code='SOURCE_UNAVAILABLE')
        _insert_attempt(passive, attempt_id='covering-missing', attempt_ts=now-3,
                        instrument='XAU', observation_id='missing-observation',
                        target_ts=None)
        passive._conn.commit()
        for include_items in (False, True):
            result = runtime.q_audit(now=now, limit=5000, include_items=include_items)
            assert result['attempt_n'] == 4
            assert result['captured_n'] == 3
            assert result['counts'] == {'RESOLVED': 1, 'NOT_DUE_YET': 1, 'CONTRACT_REJECTED': 1}
            assert result['capture_blockers'] == {'SOURCE_UNAVAILABLE': 1}
        queries = [sql for sql in statements if 'FROM g1_q_capture_attempts q' in sql]
        assert len(queries) == 2
        for query in queries:
            plan = [str(row[3]) for row in passive._conn.execute('EXPLAIN QUERY PLAN '+query)]
            assert any('SEARCH p USING COVERING INDEX' in row for row in plan), plan
    finally:
        passive.close()
        cache.close()


def test_metadata_lookup_preserves_tied_window_and_current_resolution(tmp_path):
    runtime, passive, cache = _runtime(tmp_path)
    now = 1_800_000_000.0
    try:
        for identity in ('z', 'b', 'y', 'a', 'x', 'c'):
            _insert_observation(passive, observation_id='tie-'+identity, instrument='XAU',
                                target_ts=now+1200, status='pending')
            _insert_attempt(passive, attempt_id=identity, attempt_ts=now,
                            instrument='XAU', observation_id='tie-'+identity,
                            target_ts=now+1200)
        passive._conn.commit()
        full = runtime.q_audit(now=now, limit=3)
        assert full == _ORIGINAL_Q_AUDIT(runtime, now=now, limit=3)
        assert [row['attempt_id'] for row in full['items']] == ['c', 'x', 'a']
        assert full['counts'] == {'NOT_DUE_YET': 3}
        passive._conn.execute("UPDATE passive_market_observations SET resolution_status='resolved' "
                              "WHERE observation_id='tie-c'")
        passive._conn.commit()
        summary = runtime.q_audit(now=now, limit=3, include_items=False)
        assert summary['attempt_n'] == 3
        assert summary['counts'] == {'RESOLVED': 1, 'NOT_DUE_YET': 2}
    finally:
        passive.close()
        cache.close()
