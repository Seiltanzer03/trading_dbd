#!/usr/bin/env python3
"""Verify production after the bounded research worker core has completed."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:8790"


def sh(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def request(path: str, *, method: str = "GET", timeout: float = 5.0):
    started = time.monotonic()
    req = urllib.request.Request(BASE+path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(); code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read(); code = int(exc.code)
    elapsed = (time.monotonic()-started)*1000.0
    return code, json.loads(raw.decode()) if raw else None, elapsed


def _transient(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)) or (
        isinstance(exc, urllib.error.URLError)
        and isinstance(exc.reason, (TimeoutError, socket.timeout, ConnectionError)))


def assert_route(path: str, budget_ms: float = 3000.0):
    for attempt in range(1, 4):
        try:
            code, body, elapsed = request(path)
        except Exception as exc:
            if not _transient(exc) or attempt == 3:
                raise
            time.sleep(1); continue
        print(f"{path}: {code} {elapsed:.0f}ms")
        assert code == 200, (path, code, body)
        assert elapsed < budget_ms, (path, elapsed, budget_ms)
        return body
    raise AssertionError(path)


def _latest_attempt_finished(worker: dict) -> bool:
    started = worker.get("last_started_ts")
    finished = worker.get("last_finished_ts")
    return (
        started is not None
        and finished is not None
        and float(finished) >= float(started)
    )


def _cycle_finished(worker: dict, result: object) -> bool:
    if not _latest_attempt_finished(worker) or not isinstance(result, dict):
        return False
    # If acceptance was acquired while an optional maintenance phase was already
    # running, wait for that phase to finish. A new phase cannot start once the
    # gate is observed after the bounded core.
    return (
        worker.get("maintenance_running") is False
        and worker.get("acceptance_pause_active") is True
        and worker.get("current_phase") == "acceptance_pause"
    )


def verify(expected_sha: str) -> None:
    assert sh("git", "-C", "/opt/seiltanzer", "rev-parse", "HEAD") == expected_sha
    assert sh("systemctl", "is-active", "seiltanzer") == "active"
    worker = None
    result = None
    for attempt in range(1, 73):
        lifecycle = assert_route("/api/research/runtime/worker-status")
        assert lifecycle.get("sqlite_access") is False, lifecycle
        worker = lifecycle.get("worker") or {}
        result = lifecycle.get("last_result")
        print("worker cycle", attempt, {
            "first_cycle_not_before_ts": worker.get("first_cycle_not_before_ts"),
            "current_phase": worker.get("current_phase"),
            "last_started_ts": worker.get("last_started_ts"),
            "last_finished_ts": worker.get("last_finished_ts"),
            "last_duration_ms": worker.get("last_duration_ms"),
            "last_error": worker.get("last_error"),
            "maintenance_running": worker.get("maintenance_running"),
            "maintenance_phase": worker.get("maintenance_phase"),
            "last_maintenance_error": worker.get("last_maintenance_error"),
            "acceptance_pause_active": worker.get("acceptance_pause_active"),
            "acceptance_gate_run_id": worker.get("acceptance_gate_run_id"),
        })
        if _latest_attempt_finished(worker) and worker.get("last_error") is not None:
            raise AssertionError(f"research worker core failed: {worker.get('last_error')}")
        if _cycle_finished(worker, result):
            break
        time.sleep(10)
    else:
        raise AssertionError("bounded research worker core did not complete")
    assert worker is not None
    assert worker.get("running") is True, worker
    assert worker.get("last_error") is None, worker
    assert float(worker["last_started_ts"]) >= float(worker["first_cycle_not_before_ts"])-1.0
    result = result or {}
    assert isinstance(result.get("g1s"), dict) and isinstance(result.get("g1m_local"), dict), result
    assert int((result["g1s"] or {}).get("batch_limit") or 0) > 0, result

    assert_route("/api/research/runtime/status")
    assert_route("/api/state")
    assert_route("/api/analytics/gex-migration")
    assert_route("/api/analytics/regime-phase")
    assert_route("/api/analytics/wavelet")
    assert_route("/api/analytics/correlation-graph")
    code, body, elapsed = request("/api/ai/verdict", method="POST", timeout=65.0)
    print(f"/api/ai/verdict: {code} {elapsed:.0f}ms")
    assert code in {200, 400, 429}, (code, body)
    assert isinstance((body or {}).get("ok"), bool), body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    verify(args.expected_sha)
    print("POST-RESEARCH PRODUCTION CHECK PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
