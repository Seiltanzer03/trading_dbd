from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_readiness_check.py"
SPEC = importlib.util.spec_from_file_location("production_readiness_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


def test_post_restore_stability_requires_consecutive_healthy_samples(monkeypatch):
    samples = iter([
        (200, {"ok": True}, 100.0),
        TimeoutError("busy"),
        (200, {"ok": True}, 90.0),
        (200, {"ok": True}, 80.0),
        (200, {"ok": True}, 70.0),
    ])
    calls = 0

    def fake_request(path, *, method="GET", timeout=readiness.FAST_TIMEOUT):
        nonlocal calls
        calls += 1
        sample = next(samples)
        if isinstance(sample, BaseException):
            raise sample
        return sample

    monkeypatch.setattr(readiness, "request", fake_request)
    monkeypatch.setattr(readiness.time, "sleep", lambda _seconds: None)

    readiness.wait_route_stable("/api/state", budget_ms=3000.0,
                                consecutive=3, attempts=5)
    assert calls == 5


def test_post_restore_stability_resets_on_latency_overrun(monkeypatch):
    samples = iter([
        (200, {}, 100.0),
        (200, {}, 3001.0),
        (200, {}, 90.0),
        (200, {}, 80.0),
    ])
    calls = 0

    def fake_request(path, *, method="GET", timeout=readiness.FAST_TIMEOUT):
        nonlocal calls
        calls += 1
        return next(samples)

    monkeypatch.setattr(readiness, "request", fake_request)
    monkeypatch.setattr(readiness.time, "sleep", lambda _seconds: None)

    readiness.wait_route_stable("/api/state", budget_ms=3000.0,
                                consecutive=2, attempts=4)
    assert calls == 4


def test_post_restore_stability_fails_bounded_when_transport_never_recovers(monkeypatch):
    calls = 0

    def fake_request(path, *, method="GET", timeout=readiness.FAST_TIMEOUT):
        nonlocal calls
        calls += 1
        raise TimeoutError("still busy")

    monkeypatch.setattr(readiness, "request", fake_request)
    monkeypatch.setattr(readiness.time, "sleep", lambda _seconds: None)

    with pytest.raises(AssertionError, match="post-restore stability not reached"):
        readiness.wait_route_stable("/api/state", budget_ms=3000.0,
                                    consecutive=2, attempts=3)
    assert calls == 3


def test_post_restore_stability_does_not_retry_http_failure(monkeypatch):
    calls = 0

    def fake_request(path, *, method="GET", timeout=readiness.FAST_TIMEOUT):
        nonlocal calls
        calls += 1
        return 503, {"detail": "not ready"}, 10.0

    monkeypatch.setattr(readiness, "request", fake_request)
    monkeypatch.setattr(readiness.time, "sleep", lambda _seconds: None)

    with pytest.raises(AssertionError):
        readiness.wait_route_stable("/api/state", budget_ms=3000.0,
                                    consecutive=2, attempts=5)
    assert calls == 1
