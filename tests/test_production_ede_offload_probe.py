from __future__ import annotations

import importlib.util
import pathlib

import pytest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "production_ede_offload.py"
SPEC = importlib.util.spec_from_file_location("production_ede_offload_probe_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_probe_retries_transient_timeout_without_relaxing_three_second_budget(monkeypatch):
    calls: list[str] = []
    sleeps: list[float] = []

    def flaky_exec(_client, command: str, *, timeout=None):
        calls.append(command)
        if len(calls) < 3:
            raise RuntimeError("curl: (28) Operation timed out after 3002 milliseconds")
        return ""

    monkeypatch.setattr(MODULE, "_exec", flaky_exec)
    monkeypatch.setattr(MODULE.time, "sleep", lambda seconds: sleeps.append(float(seconds)))

    MODULE._probe_api(object(), attempts=3, retry_delay=0.25)

    assert MODULE.API_PROBE_MAX_TIME_SECONDS == 3
    assert len(calls) == 3
    assert all("--max-time 3" in command for command in calls)
    assert sleeps == [0.25, 0.25]


def test_probe_still_fails_closed_after_bounded_retries(monkeypatch):
    calls: list[str] = []

    def always_slow(_client, command: str, *, timeout=None):
        calls.append(command)
        raise RuntimeError("curl timed out")

    monkeypatch.setattr(MODULE, "_exec", always_slow)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="failed after 2 attempts"):
        MODULE._probe_api(object(), attempts=2, retry_delay=0)

    assert len(calls) == 2
    assert all("--max-time 3" in command for command in calls)


def test_probe_rejects_zero_attempts():
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        MODULE._probe_api(object(), attempts=0)
