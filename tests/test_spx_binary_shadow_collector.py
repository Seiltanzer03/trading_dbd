"""Tests are offline; no Yahoo network, sqlite or production imports."""
from datetime import datetime, timezone
import gzip
import json
import math

from scripts.spx_binary_shadow_collector import (
    exact_0dte_expiry, close_ts, quote_clock_status, diagnose_mid_curve,
    assess_shadow, collect_once, persist_one, rows_from_yahoo,
)


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, fmt):
        assert fmt == "records"
        return self.rows


class Chain:
    def __init__(self):
        self.calls = Frame([{"strike": 100, "bid": 1.3, "ask": 1.5,
                             "lastPrice": 1.7, "contractSymbol": "C100"}])
        self.puts = Frame([{"strike": 100, "bid": 1.2, "ask": 1.4,
                            "lastPrice": 1.6, "contractSymbol": "P100"}])


class Ticker:
    def __init__(self, expiries):
        self.options = expiries
        self.fast_info = type("FastInfo", (), {"last_price": 101.2})()
        self.requested = []

    def option_chain(self, exp):
        self.requested.append(exp)
        return Chain()


def clock_for(day, h=15, m=0):
    from zoneinfo import ZoneInfo
    d = datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}:00")
    return d.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


def test_exact_expiry_does_not_select_wider_future_expiry():
    now = clock_for("2026-11-20")
    assert exact_0dte_expiry(["2026-11-27", "2026-12-18"], now) is None
    assert exact_0dte_expiry(["2026-11-27", "2026-11-20"], now) == "2026-11-20"
    assert exact_0dte_expiry(["2026-11-20"], clock_for("2026-11-20", 16)) is None


def test_yahoo_raw_last_trade_is_not_quote_timestamp():
    rows = rows_from_yahoo(Frame([{"strike": 100, "bid": 1, "ask": 2,
        "lastPrice": 3, "lastTradeDate": clock_for("2026-11-20")}]), "call")
    assert rows[0]["quote_ts"] is None
    assert rows[0]["last_trade_ts"] is not None
    assert quote_clock_status(rows, clock_for("2026-11-20").timestamp()) == "UNVERIFIED_BID_ASK_TIME"


def test_future_and_stale_timestamp_are_rejected():
    x = {"quote_ts": 1000, "quote_timestamp_basis": "provider_quote_timestamp"}
    assert quote_clock_status([x], 1121) == "STALE_OR_FUTURE_QUOTE"
    assert quote_clock_status([x], 999) == "STALE_OR_FUTURE_QUOTE"
    assert quote_clock_status([x], 1050) == "VERIFIED"


def test_yahoo_snapshot_never_emits_probability_or_strict_asof():
    now = clock_for("2026-11-20")
    ticker = Ticker(["2026-11-20", "2026-11-27"])
    row = collect_once(ticker, clock=lambda: now)
    assert ticker.requested == ["2026-11-20"]
    assert row["status"] == "RAW_CAPTURED"
    assert row["assessment"]["strict_asof"] is False
    assert row["trading_probability"] is None
    assert row["spot_time_verified"] is False


def test_no_0dte_records_no_data_and_never_requests_far_expiry():
    now = clock_for("2026-11-20")
    ticker = Ticker(["2026-12-18"])
    row = collect_once(ticker, clock=lambda: now)
    assert ticker.requested == []
    assert row["status"] == "NO_SAME_DAY_EXPIRY"


def test_convexity_and_monotonicity_gates_reject_corrupt_curve():
    rows = [{"type": "call", "strike": k, "bid": c, "ask": c + 0.02}
            for k, c in zip(range(97, 104), [4, 3.1, 2.4, 1.4, 0.95, 0.75, 0.65])]
    assert diagnose_mid_curve(rows, 100)["status"] == "CALL_CONVEXITY_VIOLATION"
    rows[2]["bid"] = rows[2]["ask"] = 4.3
    assert diagnose_mid_curve(rows, 100)["status"] == "CALL_MONOTONICITY_OR_VERTICAL_ARBITRAGE"


def test_forward_mismatch_gate_rejects_even_normalized_density():
    from statistics import NormalDist
    nd = NormalDist()
    ks = [90 + i * 0.2 for i in range(101)]
    sigma = 2
    calls = []
    for k in ks:
        z = (k - 100) / sigma
        c = sigma * math.exp(-z*z/2) / math.sqrt(2*math.pi) + (100-k) * (1-nd.cdf(z))
        calls.append({"type": "call", "strike": k, "bid": max(c-0.00001, 1e-7),
                      "ask": c+0.00001})
    good = diagnose_mid_curve(calls, 100)
    assert good["status"] == "DIAGNOSTIC_CURVE_ACCEPTED", good
    bad = diagnose_mid_curve(calls, 120)
    assert bad["status"] == "RND_REJECTED_FORWARD_MISMATCH", bad


def test_missing_forward_blocks_strict_asof_even_with_quote_timestamps():
    received = clock_for("2026-11-20").timestamp()
    rows = [{"type": "call", "strike": 100, "bid": 1, "ask": 2,
             "quote_ts": received-5, "quote_timestamp_basis": "provider_quote_timestamp"}]
    snap = {"status": "RAW_CAPTURED", "received_at_ts": received, "calls": rows, "puts": []}
    assert assess_shadow(snap)["status"] == "SPOT_QUOTE_TIME_UNVERIFIED"
    assert assess_shadow(snap)["strict_asof"] is False


def test_atomic_snapshot_and_post_write_availability(tmp_path):
    now = clock_for("2026-11-20")
    row = collect_once(Ticker(["2026-11-20"]), clock=lambda: now)
    dest, manifest = persist_one(tmp_path / "shadow", row,
        clock=lambda: datetime.fromtimestamp(now.timestamp()+1, timezone.utc))
    assert dest.exists()
    assert manifest["persisted_available_at_ts"] >= row["received_at_ts"]
    assert manifest["strict_asof"] is False
    assert json.loads(gzip.decompress(dest.read_bytes()))["schema"] == row["schema"]
    assert len((tmp_path / "shadow" / "manifest.jsonl").read_text().splitlines()) == 1


def test_dst_correct_expiry_close():
    winter = close_ts("2026-11-20")
    summer = close_ts("2026-10-23")
    assert datetime.fromtimestamp(winter, timezone.utc).hour == 21
    assert datetime.fromtimestamp(summer, timezone.utc).hour == 20


def test_strict_needs_independent_spot_forward_and_calendar_clocks():
    ts = clock_for("2026-11-20").timestamp()
    row = {"type": "call", "strike": 100, "bid": 1, "ask": 2,
           "quote_ts": ts-5, "quote_timestamp_basis": "provider_quote_timestamp"}
    snap = {"status": "RAW_CAPTURED", "calls": [row], "puts": [],
            "received_at_ts": ts, "spot_time_verified": True}
    assert assess_shadow(snap)["status"] == "MARKET_CALENDAR_UNVERIFIED"
    snap["market_calendar_verified"] = True
    assert assess_shadow(snap)["status"] == "FORWARD_ASOF_UNVERIFIED"
    snap["forward_asof_verified"] = True
    assert assess_shadow(snap)["status"] == "FORWARD_UNVERIFIED"
