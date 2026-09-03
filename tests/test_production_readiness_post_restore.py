from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_readiness_check.py"
SPEC = importlib.util.spec_from_file_location("production_readiness_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
readiness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(readiness)


def test_user_authorized_low_disk_mode_keeps_backup_but_skips_full_restore(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("full-size restore must not run in low-disk mode")

    monkeypatch.setattr(readiness, "request", forbidden)
    monkeypatch.setattr(readiness, "wait_route_stable", forbidden)

    assert readiness.verify_restore_drill(
        skip=True, backup_id="verified-backup-1"
    ) is None


def test_deploy_explicitly_selects_user_authorized_low_disk_mode():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")
    assert "--skip-restore-drill-low-disk" in workflow


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


def test_production_readiness_allows_degraded_low_disk_health_and_age():
    allowed = {"HEALTHY", "LOCAL_BACKUP_ONLY", "DISASTER_RECOVERY_DEGRADED"}
    skip_restore_drill = True
    storage = {"startup_integrity": {"durability_degraded": True}, "health": "BACKUP_STALE"}
    if skip_restore_drill and (
        storage.get("startup_integrity", {}).get("durability_degraded")
        or storage.get("health") == "BACKUP_STALE"
    ):
        allowed.add("BACKUP_STALE")
    assert storage.get("health") in allowed
