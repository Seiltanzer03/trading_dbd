from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from seiltanzer import ai_provider_guard


def test_provider_timeout_configuration_is_bounded(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "0.1")
    assert ai_provider_guard.provider_timeout_sec() == 3.0
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "999")
    assert ai_provider_guard.provider_timeout_sec() == 8.0
    monkeypatch.setenv("AI_PROVIDER_TIMEOUT_SEC", "bad")
    assert ai_provider_guard.provider_timeout_sec() == ai_provider_guard.DEFAULT_PROVIDER_TIMEOUT_SEC
    monkeypatch.delenv("AI_PROVIDER_TIMEOUT_SEC", raising=False)
    assert ai_provider_guard.provider_timeout_sec() == 6.0


def test_bounded_provider_call_returns_fast_result():
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = ai_provider_guard.bounded_provider_call(
            lambda snapshot: {"verdict": snapshot["value"]},
            {"value": "ok"},
            timeout_sec=0.2,
            executor=executor,
        )
    assert result == {"verdict": "ok"}


def test_provider_worker_preserves_request_trace_without_leaking_to_next_call():
    from seiltanzer.ai_request_trace import AIRequestTrace, current_trace
    trace = AIRequestTrace()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def worker(_snapshot):
            active = current_trace()
            return active.request_id if active is not None else None
        with trace.activate():
            correlated = ai_provider_guard.bounded_provider_call(worker, {}, executor=executor)
        uncorrelated = ai_provider_guard.bounded_provider_call(worker, {}, executor=executor)
    assert correlated == trace.request_id
    assert uncorrelated is None


def test_bounded_provider_call_times_out_without_waiting_for_provider():
    executor = ThreadPoolExecutor(max_workers=1)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="provider_timeout_after"):
            ai_provider_guard.bounded_provider_call(
                lambda snapshot: (time.sleep(0.15) or {"verdict": "late"}),
                {},
                timeout_sec=0.02,
                executor=executor,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 0.10
    finally:
        executor.shutdown(wait=True)


def test_production_timeout_opens_circuit_and_next_call_fails_fast(monkeypatch):
    # Isolate the module-global production worker/circuit from other tests while
    # preserving the exact production code path (executor=None).
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(ai_provider_guard, "_EXECUTOR", executor)
    monkeypatch.setattr(ai_provider_guard, "_CIRCUIT_OPEN_UNTIL", 0.0)
    monkeypatch.setattr(ai_provider_guard, "provider_circuit_sec", lambda: 0.20)
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeError, match="provider_timeout_after"):
            ai_provider_guard.bounded_provider_call(
                lambda snapshot: (time.sleep(0.12) or {"verdict": "late"}),
                {}, timeout_sec=0.02,
            )
        first_elapsed = time.monotonic() - started
        assert first_elapsed < 0.10

        second_started = time.monotonic()
        with pytest.raises(RuntimeError, match="provider_circuit_open"):
            ai_provider_guard.bounded_provider_call(
                lambda snapshot: {"verdict": "must-not-queue"}, {}, timeout_sec=0.02)
        assert time.monotonic() - second_started < 0.03
    finally:
        executor.shutdown(wait=True)
        ai_provider_guard._close_circuit()

@pytest.mark.parametrize('elapsed,expected',[(0.,6.),(3.86,4.14),(7.,1.)])
def test_provider_reserves_four_seconds_for_authoritative_completion(monkeypatch,elapsed,expected):
    from seiltanzer.ai_request_trace import AIRequestTrace
    from concurrent.futures import Future
    calls=[]
    class RecordedFuture(Future):
        def result(self,timeout=None):
            calls.append(timeout)
            return {'verdict':'fixture'}
    class Pool:
        def submit(self,*a,**k):return RecordedFuture()
    trace=AIRequestTrace();trace.started=100.
    monkeypatch.setattr(ai_provider_guard.time,'monotonic',lambda:100.+elapsed)
    with trace.activate():
        result=ai_provider_guard.bounded_provider_call(lambda s:s,{},executor=Pool())
    assert result=={'verdict':'fixture'}
    assert calls==[pytest.approx(expected)]


def test_exhausted_request_budget_never_starts_an_optional_provider(monkeypatch):
    from seiltanzer.ai_request_trace import AIRequestTrace
    class Pool:
        def submit(self,*a,**k):pytest.fail('provider must not start')
    trace=AIRequestTrace();trace.started=100.
    monkeypatch.setattr(ai_provider_guard.time,'monotonic',lambda:108.)
    with trace.activate(),pytest.raises(RuntimeError,match='provider_request_budget_exhausted'):
        ai_provider_guard.bounded_provider_call(lambda s:s,{},executor=Pool())
