from __future__ import annotations

import threading
import time

from seiltanzer.config import Settings
from seiltanzer.data.cache import DiskCache
from seiltanzer.g1_q_audit_scalability import _ORIGINAL_Q_AUDIT
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


def test_q_audit_read_snapshot_preserves_original_semantics_and_indexes(tmp_path):
    runtime, passive, cache = _runtime(tmp_path)
    now = 1_800_000_000.0
    try:
        with passive._lock, passive._conn:
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
                    "audit-scalability-test", now - 10.0, "background_collector", "XAU",
                    None, "NONE", None, None, None, 0, 0, 1, 0, 0, 0, 0, 0,
                    None, "NO_Q_SOURCE_CONFIGURED", 1.0,
                    "cap-v1", "attempt-v1", "policy-v1", "q-v1", "clock-v1",
                    "measurement-v1", "{}", now - 10.0,
                ),
            )

        original = _ORIGINAL_Q_AUDIT(runtime, now=now, limit=5000)
        bounded = runtime.q_audit(now=now, limit=5000)
        assert bounded == original

        q_indexes = {
            row[1] for row in passive._conn.execute(
                "PRAGMA index_list('g1_q_capture_attempts')"
            ).fetchall()
        }
        bar_indexes = {
            row[1] for row in passive._conn.execute(
                "PRAGMA index_list('passive_market_bars')"
            ).fetchall()
        }
        assert "ix_g1_q_attempt_ts" in q_indexes
        assert "ix_passive_bar_instrument_end" in bar_indexes
    finally:
        passive.close()
        cache.close()
