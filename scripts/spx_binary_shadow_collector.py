#!/usr/bin/env python3
"""Opt-in SPY 0DTE raw option-chain shadow capture. Never writes live terminal DB.

Yahoo exposes lastTradeDate, NOT a trustworthy timestamp for bid/ask quotes.
Consequently all Yahoo captures are CONTEXT_ONLY and no trading probability
is emitted. The quote verifier below also supports future timestamped feeds.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as clock_time, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
MAX_QUOTE_AGE_SECONDS = 120
RESEARCH_FORWARD_TOLERANCE = 0.02


def utc_now():
    return datetime.now(timezone.utc)


def exchange_date(now):
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(NY).date().isoformat()


def close_ts(exchange_day):
    day = date.fromisoformat(exchange_day)
    return datetime.combine(day, clock_time(16, 0), tzinfo=NY).timestamp()


def exact_0dte_expiry(expiries, now):
    """Never pick a further-dated chain for better strike coverage."""
    target = exchange_date(now)
    if now.timestamp() >= close_ts(target):
        return None
    return target if target in set(map(str, expiries)) else None


def _number(value):
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _utc_string(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "to_pydatetime"):
        return _utc_string(value.to_pydatetime())
    return None


def rows_from_yahoo(frame, side):
    """Preserve Yahoo raw fields; lastTradeDate cannot certify bid/ask age."""
    result = []
    for r in frame.to_dict("records"):
        result.append({
            "type": side,
            "contract": str(r.get("contractSymbol") or ""),
            "strike": _number(r.get("strike")),
            "bid": _number(r.get("bid")),
            "ask": _number(r.get("ask")),
            "bid_size": _number(r.get("bidSize")),
            "ask_size": _number(r.get("askSize")),
            "last_price": _number(r.get("lastPrice")),
            "last_trade_ts": _utc_string(r.get("lastTradeDate")),
            "oi": _number(r.get("openInterest")),
            "volume": _number(r.get("volume")),
            "iv": _number(r.get("impliedVolatility")),
            "quote_ts": None,
            "quote_timestamp_basis": "unavailable",
            "source": "yfinance",
        })
    return result


def quote_clock_status(rows, received_at, max_quote_age=MAX_QUOTE_AGE_SECONDS):
    """A trade timestamp is never promoted to a bid/ask quote timestamp."""
    if not rows:
        return "NO_QUOTES"
    for row in rows:
        if row.get("quote_timestamp_basis") not in (
            "provider_quote_timestamp", "exchange_quote_timestamp"
        ):
            return "UNVERIFIED_BID_ASK_TIME"
        ts = row.get("quote_ts")
        if not isinstance(ts, (int, float)) or not math.isfinite(ts):
            return "UNVERIFIED_BID_ASK_TIME"
        age = received_at - ts
        if not 0 <= age <= max_quote_age:
            return "STALE_OR_FUTURE_QUOTE"
    return "VERIFIED"


def diagnose_mid_curve(rows, forward, *, tol=RESEARCH_FORWARD_TOLERANCE):
    """Conservative BL diagnostic, NOT an automatic repaired option surface.

    Does not invent missing quotes or shift a damaged density to the forward.
    Requires real two-sided call markets and an independently supplied forward.
    """
    if not isinstance(forward, (float, int)) or not math.isfinite(forward) or forward <= 0:
        return {"status": "FORWARD_UNVERIFIED"}
    calls = sorted((r for r in rows if r.get("type") == "call"),
                   key=lambda r: r.get("strike") or 0)
    values = []
    for r in calls:
        k, bid, ask = (r.get(x) for x in ("strike", "bid", "ask"))
        if not all(isinstance(x, (int, float)) and math.isfinite(x)
                   for x in (k, bid, ask)) or k <= 0 or bid <= 0 or ask < bid:
            continue
        values.append((float(k), (bid + ask) / 2))
    if len(values) < 7 or len(set(k for k, _ in values)) != len(values):
        return {"status": "INSUFFICIENT_TWO_SIDED_CALLS", "n": len(values)}
    k = [v[0] for v in values]
    c = [v[1] for v in values]
    slopes = [(c[i + 1] - c[i]) / (k[i + 1] - k[i]) for i in range(len(k) - 1)]
    if any(s > 1e-7 or s < -1.0001 for s in slopes):
        return {"status": "CALL_MONOTONICITY_OR_VERTICAL_ARBITRAGE"}
    if any(slopes[i + 1] + 1e-7 < slopes[i] for i in range(len(slopes) - 1)):
        return {"status": "CALL_CONVEXITY_VIOLATION"}
    ks = k[1:-1]
    qs = [2 * (slopes[i] - slopes[i - 1]) / (k[i + 1] - k[i - 1])
          for i in range(1, len(k) - 1)]
    area = sum((ks[i + 1] - ks[i]) * (qs[i + 1] + qs[i]) / 2
               for i in range(len(ks) - 1))
    if not 0.95 <= area <= 1.05:
        return {"status": "MISSING_TAIL_MASS", "mass": area}
    mean = sum((ks[i + 1] - ks[i]) *
               (ks[i + 1] * qs[i + 1] + ks[i] * qs[i]) / 2
               for i in range(len(ks) - 1)) / area
    error = mean / forward - 1
    if abs(error) > tol:
        return {"status": "RND_REJECTED_FORWARD_MISMATCH",
                "mean": mean, "forward": forward, "error_pct": 100 * error}
    return {"status": "DIAGNOSTIC_CURVE_ACCEPTED", "mass": area,
            "mean": mean, "forward": forward, "error_pct": 100 * error}


def assess_shadow(snapshot, *, verified_forward=None):
    """Fail closed unless EVERY required evidence item exists.

    Even a good numerical density is never a physical probability or edge.
    """
    if snapshot.get("status") != "RAW_CAPTURED":
        return {"status": snapshot.get("status") or "NO_DATA", "strict_asof": False}
    rows = snapshot["calls"] + snapshot["puts"]
    clock = quote_clock_status(rows, snapshot["received_at_ts"])
    if clock != "VERIFIED":
        return {"status": clock, "strict_asof": False}
    if snapshot.get("spot_time_verified") is not True:
        return {"status": "SPOT_QUOTE_TIME_UNVERIFIED", "strict_asof": False}
    if snapshot.get("market_calendar_verified") is not True:
        return {"status": "MARKET_CALENDAR_UNVERIFIED", "strict_asof": False}
    if snapshot.get("forward_asof_verified") is not True:
        return {"status": "FORWARD_ASOF_UNVERIFIED", "strict_asof": False}
    curve = diagnose_mid_curve(snapshot["calls"], verified_forward)
    return {"status": curve["status"],
            "strict_asof": curve["status"] == "DIAGNOSTIC_CURVE_ACCEPTED",
            "curve": curve}


def collect_once(ticker=None, *, clock=utc_now):
    """One bounded unit of work. No DB, config, socket listeners or trade API."""
    if ticker is None:
        import yfinance as yf
        ticker = yf.Ticker("SPY")
    start = clock()
    if start.tzinfo is None:
        raise ValueError("clock must be UTC-aware")
    result = {
        "schema": "spx-binary-spy-0dte-shadow-v1",
        "provider": "yfinance", "proxy": "SPY", "underlying_market": "SPX",
        "exchange_timezone": "America/New_York",
        "exchange_date": exchange_date(start),
        "request_started_at": start.isoformat(),
        "request_started_at_ts": start.timestamp(),
        "expiry": None, "spot": None, "spot_quote_ts": None,
        "spot_time_verified": False, "forward_asof_verified": False,
        "market_calendar_verified": False, "calls": [], "puts": [],
        "status": "NO_DATA", "trading_probability": None,
        "quote_time_verified": False,
    }
    if start.timestamp() >= close_ts(result["exchange_date"]):
        result["status"] = "AFTER_1600_ET"
    else:
        expiries = list(ticker.options or [])
        selected = exact_0dte_expiry(expiries, start)
        if selected is None:
            result["status"] = "NO_SAME_DAY_EXPIRY"
        else:
            result["expiry"] = selected
            result["spot"] = _number(ticker.fast_info.last_price)
            chain = ticker.option_chain(selected)
            result["calls"] = rows_from_yahoo(chain.calls, "call")
            result["puts"] = rows_from_yahoo(chain.puts, "put")
            result["status"] = "RAW_CAPTURED"
    received = clock()
    result["received_at"] = received.isoformat()
    result["received_at_ts"] = received.timestamp()
    result["assessment"] = assess_shadow(result)
    return result


def persist_one(output_dir, snapshot, *, clock=utc_now):
    """Atomic immutable gzip snapshot + post-commit manifest availability time.

    Snapshot and manifest are separate from trading DB. Manifest time is taken
    only after the snapshot is committed, never backdated to the request start.
    """
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / "snapshots").mkdir(exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    name = f"{snapshot['exchange_date']}-{int(snapshot['received_at_ts'] * 1000)}-{digest[:12]}.json.gz"
    dest = root / "snapshots" / name
    if dest.exists():
        raise FileExistsError(f"immutable snapshot exists: {dest}")
    fd, tmp = tempfile.mkstemp(dir=root / "snapshots", prefix=".capture-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as z:
                z.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
        os.chmod(dest, 0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    available = clock()
    manifest = {
        "snapshot": "snapshots/" + name,
        "sha256_uncompressed_json": digest,
        "received_at_ts": snapshot["received_at_ts"],
        "persisted_available_at": available.isoformat(),
        "persisted_available_at_ts": available.timestamp(),
        "strict_asof": bool(snapshot.get("assessment", {}).get("strict_asof")),
        "status": snapshot.get("assessment", {}).get("status"),
    }
    with open(root / "manifest.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return dest, manifest


def main(argv=None):
    p = argparse.ArgumentParser(description="Opt-in read-only SPY 0DTE shadow capture")
    p.add_argument("--output-dir", required=True, help="Dedicated sidecar directory, never trading DB")
    p.add_argument("--interval-seconds", type=int, default=0,
                   help="0=one capture, >=300=explicit continuous capture")
    args = p.parse_args(argv)
    if args.interval_seconds and args.interval_seconds < 300:
        p.error("interval must be >= 300 seconds to avoid provider pressure")
    while True:
        try:
            snapshot = collect_once()
        except Exception as exc:
            # Never fabricate data; do not store exception traces with secrets.
            now = utc_now()
            snapshot = {"schema": "spx-binary-spy-0dte-shadow-v1",
                        "provider": "yfinance", "exchange_date": exchange_date(now),
                        "received_at_ts": now.timestamp(), "received_at": now.isoformat(),
                        "status": "SOURCE_ERROR", "assessment": {
                            "status": "SOURCE_ERROR", "strict_asof": False},
                        "error_type": type(exc).__name__, "trading_probability": None}
        dest, manifest = persist_one(args.output_dir, snapshot)
        print(json.dumps({"snapshot": str(dest), "status": manifest["status"],
                          "strict_asof": manifest["strict_asof"]}))
        if not args.interval_seconds:
            break
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
