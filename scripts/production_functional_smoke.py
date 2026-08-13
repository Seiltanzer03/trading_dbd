#!/usr/bin/env python3
"""Bounded production functional smoke executed over SSH on localhost."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
TRANSIENT_ATTEMPTS = 3
TRANSIENT_RETRY_DELAY_SEC = 1.0


def sh(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def request(path: str, *, method: str = "GET", timeout: float = 5.0):
    started = time.monotonic()
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(); code = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read(); code = int(exc.code)
    elapsed = (time.monotonic()-started)*1000.0
    body = json.loads(raw.decode("utf-8")) if raw else None
    return code, body, elapsed


def _is_transient_transport_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(
        exc.reason, (TimeoutError, socket.timeout, ConnectionError))


def assert_route(path: str) -> dict | list | None:
    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            code, body, elapsed = request(path, timeout=5.0)
        except Exception as exc:
            if not _is_transient_transport_error(exc) or attempt >= TRANSIENT_ATTEMPTS:
                raise
            print(f"{path}: transient {type(exc).__name__} "
                  f"attempt={attempt}/{TRANSIENT_ATTEMPTS}; retrying")
            time.sleep(TRANSIENT_RETRY_DELAY_SEC)
            continue
        print(f"{path}: {code} {elapsed:.0f}ms attempt={attempt}/{TRANSIENT_ATTEMPTS}")
        assert code == 200, (path, code)
        return body
    raise AssertionError((path, "retry loop exhausted"))


def verify(expected_sha: str) -> None:
    actual = sh("git", "-C", "/opt/seiltanzer", "rev-parse", "HEAD")
    assert actual == expected_sha, (actual, expected_sha)
    assert sh("systemctl", "is-active", "seiltanzer") == "active"

    paths = (
        "/api/state", "/api/validation", "/api/research/counterfactual",
        "/api/research/passive/status", "/api/research/passive/calibration",
        "/api/research/passive/edge", "/api/research/g1/intelligence/status",
        "/api/research/g1/calibrators/status", "/api/research/g1s/status",
        "/api/research/g1/q/audit", "/api/research/g1/management/status",
        "/api/research/g1/management/local-status", "/api/system/storage/status",
        "/api/system/database-authority", "/api/analytics/gex-migration",
        "/api/analytics/regime-phase", "/api/analytics/wavelet",
        "/api/analytics/correlation-graph",
    )
    for path in paths:
        assert_route(path)

    code, body, elapsed = request("/api/ai/verdict", method="POST", timeout=65.0)
    print(f"/api/ai/verdict: {code} {elapsed:.0f}ms")
    assert code in {200, 400, 429}, (code, body)
    assert isinstance(body.get("ok"), bool), body
    if body["ok"]:
        assert body.get("mode") in {"llm", "deterministic_fallback"}, body
        assert isinstance(body.get("verdict"), str) and body["verdict"], body
    else:
        assert (body.get("error") or {}).get("code") in {
            "no_active_trade", "ai_rate_limited", "ai_request_in_progress"
        }, body


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    verify(args.expected_sha)
    print("PRODUCTION FUNCTIONAL SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
