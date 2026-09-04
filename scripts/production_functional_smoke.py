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
AI_VERDICT_MAX_MS = 12_000.0
PASSIVE_STATUS_TIMEOUT_SEC = 20.0
AI_VERDICT_TRANSPORT_TIMEOUT_SEC = 14.0
AI_MATERIALIZER_WAIT_SEC = 150.0
EDGE_RESEARCHER_MAX_MS = 250.0
EDGE_RESEARCHER_WAIT_SEC = 75.0
FOMC_WAIT_SEC = 45.0
MACRO_NUMERIC_REFRESH_WAIT_SEC = 30.0
MACRO_NUMERIC_REFRESH_POLL_SEC = 1.0
FOMC_PROMPT_VERSION = "fomc-semantic-v2-json-schema"
FOMC_SEMANTIC_KEYS = {
    "policy_tone", "policy_shift", "inflation_concern", "growth_concern",
    "forward_guidance_shift", "uncertainty",
}


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


def assert_route(path: str, *, timeout: float = 5.0) -> dict | list | None:
    for attempt in range(1, TRANSIENT_ATTEMPTS + 1):
        try:
            code, body, elapsed = request(path, timeout=timeout)
        except Exception as exc:
            if not _is_transient_transport_error(exc) or attempt >= TRANSIENT_ATTEMPTS:
                raise
            print(f"{path}: transient {type(exc).__name__} "
                  f"attempt={attempt}/{TRANSIENT_ATTEMPTS}; retrying")
            time.sleep(TRANSIENT_RETRY_DELAY_SEC)
            continue
        print(f"{path}: {code} {elapsed:.0f}ms attempt={attempt}/{TRANSIENT_ATTEMPTS}")
        assert code == 200, (path, code, body)
        return body
    raise AssertionError((path, "retry loop exhausted"))


def verify_universe_routes() -> None:
    rates = assert_route("/api/visual/rates-orbit", timeout=15.0)
    assert isinstance(rates, dict), rates
    assert rates.get("production_authority") is False, rates
    semantics = rates.get("semantics") or {}
    assert semantics.get("synthetic_fallback") is False, rates
    assert semantics.get("interpolation") is False, rates
    assert isinstance(rates.get("series"), list), rates

    edge = assert_route("/api/visual/edge-universe", timeout=15.0)
    assert isinstance(edge, dict), edge
    assert edge.get("production_authority") is False, edge
    assert edge.get("visualization_only") is True, edge
    weight = edge.get("production_weight") or {}
    assert weight.get("hard_risk_override") is False, edge
    assert weight.get("cvar_override") is False, edge
    assert weight.get("may_widen_stop") is False, edge
    assert weight.get("automatic_execution") is False, edge
    assert isinstance((edge.get("canonical_features") or {}).get("items"), dict), edge
    assert isinstance(edge.get("cross_asset"), dict), edge
    active = edge.get("active_edge") or {}
    assert "directional_matched_signal_n" in active, active
    assert "non_directional_matched_signal_n" in active, active
    assert "directional_matched_group_n" in active, active
    assert "directional_weight_reason" in active, active


def verify_edge_researcher() -> None:
    deadline = time.monotonic() + EDGE_RESEARCHER_WAIT_SEC
    lifecycle = None
    while time.monotonic() < deadline:
        code, body, elapsed = request(
            "/api/research/g1s/edge-researcher/lifecycle", timeout=2.0)
        print(
            "/api/research/g1s/edge-researcher/lifecycle: "
            f"{code} {elapsed:.0f}ms gate<{EDGE_RESEARCHER_MAX_MS:.0f}ms"
        )
        assert code == 200, (code, body)
        assert elapsed < EDGE_RESEARCHER_MAX_MS, (elapsed, body)
        assert isinstance(body, dict), body
        lifecycle = body
        if body.get("pr_c_contract_version") == "llm-edge-researcher-v1.3-pr-c":
            break
        time.sleep(2.0)
    assert isinstance(lifecycle, dict), lifecycle
    assert lifecycle.get("pr_c_contract_version") == "llm-edge-researcher-v1.3-pr-c", lifecycle
    assert lifecycle.get("request_time_history_scan") is False, lifecycle
    assert lifecycle.get("production_authority") is False, lifecycle
    automation = lifecycle.get("automation") or {}
    assert automation.get("manual_post_only") is False, lifecycle
    assert int(automation.get("required_new_resolved_t0") or 0) == 100, lifecycle
    assert int(automation.get("minimum_provider_interval_sec") or 0) == 43_200, lifecycle
    assert int(automation.get("max_automatic_hypotheses") or 0) == 5, lifecycle
    assert int(automation.get("heavy_evaluation_concurrency") or 0) == 1, lifecycle
    quality = lifecycle.get("research_quality") or {}
    assert "llm_discovery_to_prospective_survival_rate" in quality, lifecycle
    assert quality.get("production_authority") is False, lifecycle

    code, status, elapsed = request(
        "/api/research/g1s/edge-researcher/status", timeout=2.0)
    print(
        "/api/research/g1s/edge-researcher/status: "
        f"{code} {elapsed:.0f}ms gate<{EDGE_RESEARCHER_MAX_MS:.0f}ms"
    )
    assert code == 200, (code, status)
    assert elapsed < EDGE_RESEARCHER_MAX_MS, (elapsed, status)
    assert isinstance(status, dict), status
    assert status.get("request_time_history_scan") is False, status
    assert status.get("production_authority") is False, status
    assert (status.get("automation") or {}).get("manual_post_only") is False, status


def verify_ai_verdict() -> None:
    status = assert_route("/api/ai/snapshot/status")
    assert isinstance(status, dict), status
    assert status.get("periodic_heavy_recompute") is False, status
    assert status.get("request_path_heavy_build") is False, status
    assert float(status.get("review_delta_r") or 0.0) == 0.15, status
    assert float(status.get("failure_backoff_sec") or 0.0) >= 5.0, status

    if status.get("current_trade_id") is not None:
        deadline = time.monotonic() + AI_MATERIALIZER_WAIT_SEC
        while not status.get("ready") and time.monotonic() < deadline:
            print(
                "/api/ai/snapshot/status: warming "
                f"building={status.get('building')} reason={status.get('invalidated_reason')} "
                f"retry={status.get('failure_retry_in_sec')}"
            )
            time.sleep(2.0)
            status = assert_route("/api/ai/snapshot/status")
        assert status.get("ready") is True, status

    # Every individual POST must remain below the reverse-proxy budget. If the
    # market crosses a review trigger between the status read and POST, a fast
    # JSON 503 is correct; wait for the background deterministic rebuild and retry
    # through a new short request instead of keeping one HTTP request open.
    deadline = time.monotonic() + AI_MATERIALIZER_WAIT_SEC
    while True:
        code, body, elapsed = request(
            "/api/ai/verdict", method="POST", timeout=AI_VERDICT_TRANSPORT_TIMEOUT_SEC)
        print(f"/api/ai/verdict: {code} {elapsed:.0f}ms gate<{AI_VERDICT_MAX_MS:.0f}ms")
        assert elapsed < AI_VERDICT_MAX_MS, (elapsed, AI_VERDICT_MAX_MS, code, body)
        assert code != 504, body
        if code == 503 and ((body or {}).get("error") or {}).get("code") == "ai_snapshot_warming":
            assert time.monotonic() < deadline, body
            time.sleep(min(3.0, max(1.0, float((body or {}).get("retry_after_sec") or 2.0))))
            continue
        assert code in {200, 400, 429}, (code, body)
        assert isinstance(body, dict), body
        assert isinstance(body.get("ok"), bool), body
        if body["ok"]:
            assert body.get("mode") in {"llm", "deterministic_fallback"}, body
            assert isinstance(body.get("verdict"), str) and body["verdict"], body
        else:
            assert (body.get("error") or {}).get("code") in {
                "no_active_trade", "ai_rate_limited", "ai_request_in_progress"
            }, body
        break


def _verify_fomc_semantic() -> None:
    deadline = time.monotonic() + FOMC_WAIT_SEC
    row = None
    while time.monotonic() < deadline:
        row = assert_route("/api/research/macro/latest?family=FOMC_STATEMENT")
        if isinstance(row, dict) and row.get("status") == "VALID":
            break
        runtime = assert_route("/api/research/macro/status")
        detail = (runtime or {}).get("fomc_runtime") or {}
        print(
            "FOMC v2 waiting "
            f"running={detail.get('running')} last_error={detail.get('last_error')} "
            f"last_status={(detail.get('last_result') or {}).get('status')}"
        )
        time.sleep(3.0)
    assert isinstance(row, dict), row
    assert row.get("status") == "VALID", row
    assert row.get("family") == "FOMC_STATEMENT", row
    assert row.get("source") == "Federal Reserve Board", row
    assert str(row.get("source_url") or "").startswith("https://www.federalreserve.gov/"), row
    assert row.get("prompt_version") == FOMC_PROMPT_VERSION, row
    assert float(row.get("available_at") or 0.0) > 0.0, row
    semantic = row.get("semantic") or {}
    assert set(semantic) == FOMC_SEMANTIC_KEYS, row
    assert all(value is not None for value in semantic.values()), row
    assert row.get("production_authority") is False, row


def _assert_macro_numeric_refresh_result(body: object) -> dict:
    assert isinstance(body, dict), body
    assert body.get("status") == "OK", body
    assert body.get("no_placeholders") is True, body
    assert body.get("production_authority") is False, body
    assert not body.get("errors"), body
    return body


def _wait_for_macro_numeric_refresh(
    initial_body: object,
    *,
    wait_sec: float = MACRO_NUMERIC_REFRESH_WAIT_SEC,
    poll_sec: float = MACRO_NUMERIC_REFRESH_POLL_SEC,
) -> dict:
    """Accept only a completed successful official numeric refresh.

    The POST can legitimately return IN_PROGRESS when the startup worker already
    owns the refresh. Do not turn that transient state into success: poll the
    existing runtime for a short bounded window and validate its completed result.
    """
    assert isinstance(initial_body, dict), initial_body
    if initial_body.get("status") == "OK":
        return _assert_macro_numeric_refresh_result(initial_body)
    assert initial_body.get("status") == "IN_PROGRESS", initial_body

    deadline = time.monotonic() + max(0.0, wait_sec)
    last_numeric: object = initial_body
    while time.monotonic() < deadline:
        runtime = assert_route("/api/research/macro/status", timeout=5.0)
        assert isinstance(runtime, dict), runtime
        numeric = runtime.get("numeric") or {}
        assert isinstance(numeric, dict), numeric
        last_numeric = numeric
        print(
            "macro numeric refresh waiting "
            f"running={numeric.get('running')} last_error={numeric.get('last_error')} "
            f"last_status={(numeric.get('last_result') or {}).get('status')}"
        )
        if numeric.get("running") is False:
            assert not numeric.get("last_error"), numeric
            return _assert_macro_numeric_refresh_result(numeric.get("last_result") or {})
        time.sleep(max(0.0, poll_sec))

    raise AssertionError(("macro_numeric_refresh_timeout", last_numeric))


def verify_macro_runtime() -> None:
    status = assert_route("/api/research/macro/status", timeout=15.0)
    assert isinstance(status, dict), status
    assert status.get("official_sources_only") is True, status
    assert status.get("no_placeholders") is True, status
    assert status.get("consensus_feed_available") is False, status
    assert status.get("surprise_computed_without_consensus") is False, status
    assert status.get("production_authority") is False, status
    expected = {"CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES", "FOMC_STATEMENT"}
    assert expected.issubset(set(status.get("official_families") or [])), status
    transport = status.get("numeric_transport") or {}
    assert transport.get("official_source_urls_unchanged") is True, transport
    assert transport.get("payload_or_parser_fallback_added") is False, transport

    # Force one deterministic official numeric refresh on the deployed SHA. If
    # the startup worker already owns that refresh, wait only for that existing
    # bounded operation to finish and then require its real successful result.
    code, body, elapsed = request(
        "/api/research/macro/numeric/refresh", method="POST", timeout=40.0)
    print(f"/api/research/macro/numeric/refresh: {code} {elapsed:.0f}ms")
    assert code == 200, (code, body)
    body = _wait_for_macro_numeric_refresh(body)

    for family in ("CPI", "NFP", "ISM_MANUFACTURING", "ISM_SERVICES"):
        row = assert_route(f"/api/research/macro/latest?family={family}")
        assert isinstance(row, dict), row
        assert row.get("status") == "VALID", row
        assert row.get("family") == family, row
        assert row.get("official_source_verified") is True, row
        assert float(row.get("available_at") or 0.0) > 0.0, row
        payload = row.get("payload") or {}
        assert payload.get("consensus_available") is False, row
        assert payload.get("surprise_computed") is False, row

    # FOMC must also prove the new strict extraction on the production provider;
    # a green numeric refresh alone must not hide a rejected semantic v1 record.
    _verify_fomc_semantic()


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
        if path == "/api/research/passive/status":
            assert_route(path, timeout=PASSIVE_STATUS_TIMEOUT_SEC)
        else:
            assert_route(path)

    verify_universe_routes()
    verify_edge_researcher()
    verify_ai_verdict()
    verify_macro_runtime()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    verify(args.expected_sha)
    print("PRODUCTION FUNCTIONAL SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
