from __future__ import annotations

import pytest

from scripts import production_readiness_check as readiness


def test_assert_fast_retries_one_transient_budget_overrun(monkeypatch):
    calls = iter([
        (200, {"ok": True, "attempt": 1}, 3547.0),
        (200, {"ok": True, "attempt": 2}, 15.0),
    ])
    sleeps: list[float] = []

    monkeypatch.setattr(readiness, "request", lambda *args, **kwargs: next(calls))
    monkeypatch.setattr(readiness.time, "sleep", sleeps.append)

    body = readiness.assert_fast("/api/test", budget_ms=3000, attempts=3)

    assert body == {"ok": True, "attempt": 2}
    assert sleeps == [readiness.TRANSIENT_RETRY_DELAY_SEC]


def test_assert_fast_keeps_original_budget_after_all_attempts(monkeypatch):
    calls = iter([
        (200, {"attempt": 1}, 3500.0),
        (200, {"attempt": 2}, 3200.0),
        (200, {"attempt": 3}, 3100.0),
    ])

    monkeypatch.setattr(readiness, "request", lambda *args, **kwargs: next(calls))
    monkeypatch.setattr(readiness.time, "sleep", lambda *_: None)

    with pytest.raises(AssertionError) as exc:
        readiness.assert_fast("/api/test", budget_ms=3000, attempts=3)

    assert exc.value.args[0] == ("/api/test", 3100.0, 3000)


def test_assert_fast_http_error_still_fails_without_retry(monkeypatch):
    calls = 0

    def fake_request(*args, **kwargs):
        nonlocal calls
        calls += 1
        return 503, {"error": "not ready"}, 10.0

    monkeypatch.setattr(readiness, "request", fake_request)
    monkeypatch.setattr(readiness.time, "sleep", lambda *_: None)

    with pytest.raises(AssertionError):
        readiness.assert_fast("/api/test", budget_ms=3000, attempts=3)

    assert calls == 1
