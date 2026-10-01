#!/usr/bin/env python3
"""Bounded production functional smoke executed over SSH on localhost."""
from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
TRANSIENT_ATTEMPTS = 3
TRANSIENT_RETRY_DELAY_SEC = 1.0
AI_VERDICT_MAX_MS = 12_000.0
# This research-only aggregate scans the 9+ GiB production SQLite database and
# currently returns a valid materialized status in roughly 39-43 seconds. Keep
# its transport allowance separate from the strict live trading/API gates.
PASSIVE_STATUS_TIMEOUT_SEC = 50.0
PASSIVE_EDGE_TIMEOUT_SEC = 30.0
CALIBRATOR_STATUS_TIMEOUT_SEC = 50.0
MATERIALIZED_STATUS_TIMEOUT_SEC = 15.0
MATERIALIZED_STATUS_PATHS = frozenset({
    "/api/research/g1s/status",
    "/api/research/g1/q/audit",
    "/api/research/g1/management/status",
    "/api/research/g1/management/local-status",
    "/api/system/storage/status",
    "/api/system/database-authority",
    "/api/analytics/gex-migration",
    "/api/analytics/regime-phase",
    "/api/analytics/wavelet",
    "/api/analytics/correlation-graph",
})
AI_VERDICT_TRANSPORT_TIMEOUT_SEC = 14.0
AI_MATERIALIZER_WAIT_SEC = 150.0
EDGE_RESEARCHER_MAX_MS = 250.0
EDGE_RESEARCHER_WAIT_SEC = 75.0
FOMC_WAIT_SEC = 45.0
MACRO_NUMERIC_REFRESH_WAIT_SEC = 120.0
MACRO_NUMERIC_REFRESH_POLL_SEC = 1.0
MACRO_LATEST_TIMEOUT_SEC = 20.0
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


def wait_for_live_state(*, wait_sec: float = 45.0) -> dict:
    """Price-source changes can briefly invalidate the materialized live tick."""
    deadline = time.monotonic() + wait_sec
    while True:
        code, body, elapsed = request("/api/state", timeout=5.0)
        if code == 200:
            print(f"/api/state: 200 {elapsed:.0f}ms")
            assert isinstance(body, dict), body
            return body
        if (code != 503 or not isinstance(body, dict)
                or body.get("detail") != "live state snapshot is warming"
                or time.monotonic() >= deadline):
            raise AssertionError(("/api/state", code, body))
        time.sleep(1.0)


def broker_bybit_observation(active: dict, quote: dict) -> dict:
    """Report a simultaneous broker/perpetual ratio without equating their units."""
    if (active.get("instrument_type") != "broker_cfd"
            or "OANDA:NAS100USD" not in str(active.get("source") or "")
            or active.get("status") != "live"):
        return {"status": "UNAVAILABLE", "reason": "direct_broker_quote_missing"}
    if quote.get("symbol") != "QQQUSDT" or quote.get("status") not in {"live", "delayed"}:
        return {"status": "UNAVAILABLE", "reason": "bybit_quote_missing"}
    try:
        broker, perp = float(active["value"]), float(quote["value"])
        broker_ts, perp_ts = float(active["ts"]), float(quote["ts"])
    except (KeyError, TypeError, ValueError):
        return {"status": "UNAVAILABLE", "reason": "invalid_quote_value_or_timestamp"}
    if (not all(math.isfinite(x) for x in (broker, perp, broker_ts, perp_ts))
            or min(broker, perp, broker_ts, perp_ts) <= 0):
        return {"status": "UNAVAILABLE", "reason": "invalid_quote_value_or_timestamp"}
    now = time.time()
    if any(not -5 <= now - ts <= 30 for ts in (broker_ts, perp_ts)):
        return {"status": "UNAVAILABLE", "reason": "quote_stale"}
    skew = abs(broker_ts - perp_ts)
    if skew > 15:
        return {"status": "UNAVAILABLE", "reason": "quotes_not_simultaneous",
                "timestamp_skew_sec": round(skew, 2)}
    return {"status": "PAIRED", "broker_value": broker, "broker_ts": broker_ts,
            "bybit_perp_value": perp, "bybit_ts": perp_ts,
            "broker_to_perp_ratio": round(broker / perp, 8),
            "timestamp_skew_sec": round(skew, 2),
            "units": "NAS100 broker points / QQQUSDT; ratio is an anchor, not a spread"}


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


def _bounded_edge_research_route(path: str) -> dict:
    # A single request can land behind a scheduled research task. Gate the
    # median of three independent reads: one outlier is tolerated, while
    # sustained >250 ms latency still fails production acceptance.
    observations = []
    for _ in range(3):
        code, body, elapsed = request(path, timeout=2.0)
        print(f"{path}: {code} {elapsed:.0f}ms gate p50<{EDGE_RESEARCHER_MAX_MS:.0f}ms")
        assert code == 200 and isinstance(body, dict), (code, body)
        observations.append((elapsed, body))
    median_ms = sorted(item[0] for item in observations)[1]
    assert median_ms < EDGE_RESEARCHER_MAX_MS, (
        [round(item[0], 1) for item in observations], observations[-1][1])
    return observations[-1][1]


def verify_edge_researcher() -> None:
    deadline = time.monotonic() + EDGE_RESEARCHER_WAIT_SEC
    lifecycle = None
    while time.monotonic() < deadline:
        lifecycle = _bounded_edge_research_route("/api/research/g1s/edge-researcher/lifecycle")
        if lifecycle.get("pr_c_contract_version") == "llm-edge-researcher-v1.3-pr-c":
            break
        time.sleep(2.0)
    assert isinstance(lifecycle, dict), lifecycle
    assert lifecycle.get("pr_c_contract_version") == "llm-edge-researcher-v1.3-pr-c", lifecycle
    assert lifecycle.get("request_time_history_scan") is False, lifecycle
    assert lifecycle.get("production_authority") is False, lifecycle
    automation = lifecycle.get("automation") or {}
    assert automation.get("manual_post_only") is False, lifecycle
    assert int(automation.get("required_new_resolved_t0") or 0) in {1, 100}, lifecycle
    assert int(automation.get("minimum_provider_interval_sec") or 0) == 43_200, lifecycle
    assert int(automation.get("max_automatic_hypotheses") or 0) == 5, lifecycle
    assert int(automation.get("heavy_evaluation_concurrency") or 0) == 1, lifecycle
    quality = lifecycle.get("research_quality") or {}
    assert "llm_discovery_to_prospective_survival_rate" in quality, lifecycle
    assert quality.get("production_authority") is False, lifecycle

    status = _bounded_edge_research_route("/api/research/g1s/edge-researcher/status")
    assert status.get("request_time_history_scan") is False, status
    assert status.get("production_authority") is False, status
    assert (status.get("automation") or {}).get("manual_post_only") is False, status


def wait_for_ai_snapshot_ready() -> dict:
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
        assert status.get("building") is not True, status
    return status


def verify_management_calculation_audit(body: dict) -> None:
    audit = body.get("management_calculation_audit") or {}
    assert audit.get("version") == "management-calculation-audit-v1", audit
    assert audit.get("status") == "AVAILABLE", audit
    if audit["status"] == "AVAILABLE":
        rows = audit["policies"]
        costs = audit["execution_cost_model"]
        assert set(rows) == {"HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"}, rows
        for row in rows.values():
            assert row["outcomes_include_execution_costs"] is True
            assert abs(row["gross_expected_final_r"] - row["execution_cost_r"]
                       - row["expected_final_r_net"]) < .00021, row
        assert abs(rows["EXIT"]["expected_final_r_net"] -
                   (audit["model_current_r"] - costs["immediate_full_close_r"])) < .00011, audit
        print("MANAGEMENT_NET_COST_AUDIT success")
    candidates = body.get("active_management_candidates")
    if candidates:
        assert len(candidates) == 7, candidates
        for row in candidates:
            assert row["status"] in {"blocked", "eligible", "already_armed"}, row
            if row["status"] == "eligible":
                assert row["paired_delta_ci95_lower_r"] > row["materiality_band_r"], row
                assert row["worst_seed_cvar10_net_r"] >= row["hard_net_floor_r"] - 1e-8, row
                assert row["automatic_execution_allowed"] is False, row
        print("MANAGEMENT_ALL_ACTIONS_AUDIT success")


def verify_unified_management_contract(body: dict) -> None:
    audit = body.get("unified_edge_ensemble") or {}
    assert audit.get("contract_version") == "unified-edge-ensemble-v1", audit
    candidates = audit.get("candidates") or []
    from seiltanzer.llm_decision_shadow import VALID_POLICIES
    assert {row.get("policy") for row in candidates} == set(VALID_POLICIES), audit
    assert len(audit.get("components") or []) == 5, audit
    assert len(audit.get("counterfactuals") or []) == 5, audit
    assert len(audit.get("scheme_comparisons") or []) == 4, audit
    assert audit.get("automatic_execution_allowed") is False, audit
    assert audit.get("hard_risk_override") is False, audit
    if audit.get("applied"):
        winner = next(row for row in candidates
                      if row["candidate_id"] == audit["selected_candidate_id"])
        assert winner.get("eligible") is True, winner
    print("UNIFIED_MANAGEMENT_12_ACTIONS success applied=" + str(audit.get("applied")))


def verify_management_ack_guard_contract() -> None:
    """Check the deployed production guard without acknowledging a real trade."""
    import inspect
    from seiltanzer.position_state import PositionLedger
    from seiltanzer import strategy_terminal_guard as guard
    preview, acknowledge, installed = (
        PositionLedger.preview_decision, PositionLedger.acknowledge, guard._INSTALLED)
    try:
        guard.install_strategy_terminal_guard()
        parameter = inspect.signature(PositionLedger.acknowledge).parameters.get(
            "execution_price_source")
        assert parameter is not None, "production ACK guard rejects broker fill provenance"
        assert parameter.default == "unspecified", parameter
        print("MANAGEMENT_ACK_GUARD_CONTRACT success")
    finally:
        PositionLedger.preview_decision = preview
        PositionLedger.acknowledge = acknowledge
        guard._INSTALLED = installed


def verify_trade_settlement_contract() -> None:
    """Check installed accounting on a temporary DB, never on a user position."""
    import tempfile
    from pathlib import Path
    from seiltanzer.journal import Journal
    from seiltanzer.position_state import PositionLedger
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / 'settlement.sqlite3')
        journal, position = Journal(path), PositionLedger(path)
        try:
            trade = journal.open_trade(3, 'NAS100', 'long', 30500, 30396, 30770)
            position.open_trade(trade)
            position.record_manual_fill(trade, request_id='smoke-partial',
                                        close_fraction_current=.5, execution_price=30396, execution_r=-1)
            position.terminal_exit(trade, execution_price=30833, execution_r=333 / 104,
                                   execution_price_source='user_supplied_broker_fill')
            closed = journal.get_trade(trade['id'])
            assert closed['status'] == 'closed' and journal.active_trade() is None, closed
            assert abs(closed['result_r'] - (-.5 + .5 * 333 / 104)) < 1e-7, closed
            assert journal.list_trades()[0]['management_summary']['fill_count'] == 2
            print('TRADE_SETTLEMENT_CONTRACT success')
        finally:
            position.close(); journal.close()


def verify_ai_verdict() -> None:
    verify_management_ack_guard_contract()
    verify_trade_settlement_contract()
    wait_for_ai_snapshot_ready()

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
            verify_management_calculation_audit(body)
            verify_unified_management_contract(body)
        else:
            assert (body.get("error") or {}).get("code") in {
                "no_active_trade", "ai_rate_limited", "ai_request_in_progress"
            }, body
        break

    # A fresh option chain can invalidate the snapshot while the verdict POST is
    # in flight. That background rebuild shares the runtime store with macro
    # status. Wait again so macro acceptance never races a newly-started build.
    wait_for_ai_snapshot_ready()


def _verify_fomc_semantic() -> None:
    deadline = time.monotonic() + FOMC_WAIT_SEC
    row = None
    while time.monotonic() < deadline:
        row = assert_route(
            "/api/research/macro/latest?family=FOMC_STATEMENT",
            timeout=MACRO_LATEST_TIMEOUT_SEC,
        )
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

    The POST reserves background work and returns IN_PROGRESS. Do not turn that
    transient state into success: poll for a bounded window and validate the
    completed result. This also covers an already running startup refresh.
    """
    assert isinstance(initial_body, dict), initial_body
    if initial_body.get("status") == "OK":
        return _assert_macro_numeric_refresh_result(initial_body)
    assert initial_body.get("status") == "IN_PROGRESS", initial_body

    deadline = time.monotonic() + max(0.0, wait_sec)
    last_numeric: object = initial_body
    while time.monotonic() < deadline:
        runtime = assert_route("/api/research/macro/numeric/status", timeout=5.0)
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
        row = assert_route(
            f"/api/research/macro/latest?family={family}",
            timeout=MACRO_LATEST_TIMEOUT_SEC,
        )
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

    # The background AI materializer can still own the shared DB immediately
    # after readiness. Wait for its nonblocking status contract first, then run
    # macro before any scan-heavy research status route. A timed-out synchronous
    # status request keeps running server-side and must not precede macro writes.
    verify_ai_verdict()

    bybit = assert_route("/api/market/bybit", timeout=5.0)
    assert bybit.get("production_authority") is False, bybit
    assert isinstance(bybit.get("quote"), dict) and isinstance(bybit.get("options"), dict), bybit
    print("BYBIT PUBLIC STATUS " + json.dumps({
        "instrument": bybit.get("instrument"), "quote": bybit["quote"].get("status"),
        "quote_error": bybit["quote"].get("error"), "options": bybit["options"].get("status"),
        "options_error": bybit["options"].get("error"), "active_price": bybit.get("active_price"),
    }, ensure_ascii=False))

    # Compare like with like: QQQUSDT perpetual vs QQQ ETF. NAS100/^NDX is
    # roughly forty times QQQ and cannot be compared without a paired anchor.
    if bybit.get("instrument") == "NAS100":
        quote = bybit["quote"]
        print("BYBIT NAS100 RAW QUOTE " + json.dumps({
            "symbol": quote.get("symbol"), "value": quote.get("value"),
            "index_price": quote.get("index_price"),
            "mark_price": quote.get("mark_price"), "ts": quote.get("ts"),
        }, ensure_ascii=False))
        state = wait_for_live_state()
        feeds = (state.get("tick") or {}).get("feeds") or {}
        proxy = feeds.get("proxy_price") or {}
        active = feeds.get("price") or {}
        qqq, perp = proxy.get("value"), quote.get("value")
        comparison = {
            "perp_symbol": quote.get("symbol"), "perp_value": perp,
            "perp_index_price": quote.get("index_price"),
            "qqq_value": qqq, "qqq_source": proxy.get("source"),
            "nas100_value": active.get("value"), "nas100_source": active.get("source"),
        }
        if quote.get("symbol") == "QQQUSDT" and isinstance(qqq, (float, int)) and qqq > 0 and isinstance(perp, (float, int)):
            comparison["qqq_perp_premium_pct"] = round(100 * (perp / qqq - 1), 4)
            index = quote.get("index_price")
            if isinstance(index, (float, int)) and index > 0:
                comparison["qqq_index_premium_pct"] = round(100 * (index / qqq - 1), 4)
        print("BYBIT NAS100 PAIRED COMPARISON " + json.dumps(comparison, ensure_ascii=False))
        print("OANDA BYBIT BASIS OBSERVATION " + json.dumps(
            broker_bybit_observation(active, quote), ensure_ascii=False))

    verify_macro_runtime()

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
        if path == "/api/state":
            wait_for_live_state()
        elif path == "/api/research/passive/status":
            assert_route(path, timeout=PASSIVE_STATUS_TIMEOUT_SEC)
        elif path == "/api/research/passive/edge":
            assert_route(path, timeout=PASSIVE_EDGE_TIMEOUT_SEC)
        elif path == "/api/research/g1/calibrators/status":
            assert_route(path, timeout=CALIBRATOR_STATUS_TIMEOUT_SEC)
        elif path in MATERIALIZED_STATUS_PATHS:
            assert_route(path, timeout=MATERIALIZED_STATUS_TIMEOUT_SEC)
        else:
            assert_route(path)

    verify_universe_routes()
    verify_edge_researcher()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args(argv)
    verify(args.expected_sha)
    print("PRODUCTION FUNCTIONAL SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
