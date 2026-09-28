import threading
from types import SimpleNamespace

import pytest

from seiltanzer.macro_numeric_data import NumericMacroRuntime


def test_refresh_returns_before_source_finishes_and_is_single_flight(monkeypatch):
    runtime = NumericMacroRuntime(SimpleNamespace(status=lambda: {}))
    entered, release = threading.Event(), threading.Event()
    workers = []

    def ingest():
        workers.append(threading.current_thread())
        entered.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(runtime, "_ingest_bls", ingest)
    monkeypatch.setattr(runtime, "_ingest_ism", lambda: [])
    runtime.last_result = {"status": "OK", "old": True}
    try:
        assert runtime.request_refresh()["status"] == "IN_PROGRESS"
        assert entered.wait(1)
        assert runtime.running is True
        assert runtime.progress()["running"] is True
        assert runtime.last_result is None
        assert runtime.last_finished_at is None
        assert runtime.request_refresh()["status"] == "IN_PROGRESS"
        assert runtime.refresh()["status"] == "IN_PROGRESS"
        assert len(workers) == 1
    finally:
        release.set()
        for worker in workers:
            worker.join(3)
    assert runtime.running is False
    assert runtime.last_result["status"] == "OK"
    assert "old" not in runtime.last_result


@pytest.mark.parametrize("error, expected", [(ValueError("bad source"), "PARTIAL"),
                                            (RuntimeError("storage failure"), "FAILED")])
def test_refresh_failure_cannot_reuse_old_success(monkeypatch, error, expected):
    runtime = NumericMacroRuntime(SimpleNamespace(status=lambda: {}))
    runtime.last_result = {"status": "OK", "old": True}

    def fail():
        raise error

    monkeypatch.setattr(runtime, "_ingest_bls", fail)
    monkeypatch.setattr(runtime, "_ingest_ism", lambda: [])
    assert runtime.refresh()["status"] == expected
    assert runtime.last_error
    assert runtime.running is False
    monkeypatch.setattr(runtime, "_ingest_bls", lambda: [])
    assert runtime.refresh()["status"] == "OK"
    assert runtime.last_error is None


def test_progress_does_not_query_busy_storage():
    def unavailable_store():
        raise AssertionError("progress must not query storage")

    runtime = NumericMacroRuntime(SimpleNamespace(status=unavailable_store))
    assert runtime._claim_refresh() is True
    progress = runtime.progress()
    assert progress["running"] is True
    assert progress["last_result"] is None
    assert "store" not in progress


def test_thread_start_failure_releases_reservation(monkeypatch):
    runtime = NumericMacroRuntime(SimpleNamespace(status=lambda: {}))

    def fail_start(_thread):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="cannot start thread"):
        runtime.request_refresh()
    assert runtime.running is False
    assert runtime.last_result["status"] == "FAILED"
    assert runtime.last_error
