"""Bounded off-host public-source collection; never grants forecasting authority.

No caller on the review path should invoke this module's collector. Availability
is first-seen receipt, not a guessed historical release time. Exchange proxies
remain unvalidated; this module neither fits models nor issues trade writes.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .canonical_market_context import canonical_instrument_code
from .config import ALL_INSTRUMENTS
from .edge_family_adapters import FAMILIES, build_edge_family_evidence

CONTRACT = "edge-family-source-bundle-v1"
EDGE_POLICY = "g1s-manual-trader-high-risk-edge-policy-v1"
DEFAULT_INSTRUMENTS = tuple(ALL_INSTRUMENTS)
COINBASE = "https://api.exchange.coinbase.com"
CFTC = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
NYSE = "https://www.nyse.com/trade/hours-calendars"
# Futures market identities, NOT validated mappings to a broker's CFD.
COT_MARKETS = {"XAU": "088691", "XAG": "084691", "EURUSD": "099741"}
CRYPTO = {"BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD"}
NYSE_HOLIDAYS_2026 = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
                      "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
                      "2026-11-26", "2026-12-25"}
NYSE_EARLY_2026 = {"2026-11-27", "2026-12-24"}


class _CalendarTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"th", "td"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _number(value) -> float:
    if isinstance(value, bool):
        raise ValueError("BOOLEAN_NOT_NUMERIC")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("NONFINITE_NUMBER")
    return result


def _ts(value: str) -> float:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()


def fetch_public(url: str, *, timeout: float = 12., max_bytes: int = 4_000_000) -> bytes:
    """Allowlisted GET, capped response, no credentials and no retries."""
    if not any(url == base or url.startswith(base + "/") or url.startswith(base + "?")
               for base in (COINBASE, CFTC, NYSE)):
        raise ValueError("SOURCE_URL_NOT_ALLOWLISTED")
    request = Request(url, headers={"User-Agent": "trading-dbd-source-audit/1", "Accept": "application/json,text/html"})
    with urlopen(request, timeout=min(30., max(1., timeout))) as response:
        final_url = response.geturl()
        if not any(final_url == base or final_url.startswith(base + "/") or final_url.startswith(base + "?")
                   for base in (COINBASE, CFTC, NYSE)):
            raise ValueError("SOURCE_REDIRECT_NOT_ALLOWLISTED")
        body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError("SOURCE_BODY_TOO_LARGE")
    return body


def _meta(source_id: str, instrument: str, observed: float, receipt: float) -> dict:
    return {"source_id": source_id, "instrument": instrument, "source_verified": True,
            "observed_ts": observed, "received_ts": receipt, "available_at": receipt,
            "quality": 1., "voting_weight": 0.}


def _proxy(record: dict, target: str) -> dict:
    return {**record, "proxy_mapping": {"source_instrument": record["instrument"],
            "target_instrument": target, "validated": False,
            "reason": "MEASURED_BROKER_VENUE_MAPPING_REQUIRED"}}


def parse_coinbase_tape(body: bytes, *, product: str, receipt: float, source_id: str) -> dict:
    """REST `side` is MAKER side; aggressive buy removes maker sell."""
    rows = json.loads(body)
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        raise ValueError("TAPE_ROWS_INVALID")
    seen, trades = set(), []
    for row in rows:
        key = str(row["trade_id"])
        if key in seen:
            continue
        seen.add(key)
        stamp, size, price = _ts(row["time"]), _number(row["size"]), _number(row["price"])
        if stamp > receipt or size <= 0 or price <= 0 or row["side"] not in {"buy", "sell"}:
            raise ValueError("TAPE_CLOCK_OR_VALUE_INVALID")
        trades.append((stamp, size, row["side"]))
    latest = max(row[0] for row in trades)
    if receipt - latest > 60:
        raise ValueError("TAPE_STALE")
    window = [row for row in trades if latest - 60 <= row[0] <= latest]
    first = min(row[0] for row in window)
    if first >= latest:
        raise ValueError("TAPE_WINDOW_MISSING")
    return {**_meta(source_id, "COINBASE" + product, latest, receipt),
            "kind": "exchange_trade_tape", "venue": "COINBASE_EXCHANGE",
            "window_start_ts": first, "window_end_ts": latest,
            "aggressive_buy_volume": sum(size for _, size, side in window if side == "sell"),
            "aggressive_sell_volume": sum(size for _, size, side in window if side == "buy"),
            "window_complete": False, "coverage": "LATEST_LIMITED_PAGE_NOT_COMPLETE_60S_TAPE",
            "trade_count": len(window), "size_unit": "BASE_ASSET", "maker_side_inverted": True,
            "dependency_group": "coinbase:" + product + ":book_tape"}


def parse_coinbase_book(body: bytes, *, product: str, receipt: float, source_id: str,
                        previous: dict | None = None) -> dict:
    root = json.loads(body)
    if root.get("auction_mode") is True:
        raise ValueError("INDICATIVE_AUCTION_BOOK_NOT_FIRM")
    observed = _ts(root["time"])
    bid, ask = root["bids"][0], root["asks"][0]
    values = [_number(v) for v in (bid[0], bid[1], ask[0], ask[1])]
    if not observed <= receipt <= observed + 60 or min(values) <= 0 or values[0] >= values[2]:
        raise ValueError("BOOK_CLOCK_OR_VALUE_INVALID")
    current = dict(zip(("bid_price", "bid_size", "ask_price", "ask_size"), values), ts=observed)
    record = {**_meta(source_id, "COINBASE" + product, observed, receipt),
              "kind": "exchange_order_book", "venue": "COINBASE_EXCHANGE",
              "current_top": current, "sequence": root["sequence"], "book_level": 1,
              "dependency_group": "coinbase:" + product + ":book_tape"}
    if (isinstance(previous, dict) and previous.get("instrument") == record["instrument"]
            and previous.get("source_verified") is True
            and previous.get("sequence", 0) < record["sequence"]
            and previous.get("current_top", {}).get("ts", observed) < observed
            and observed - previous["current_top"]["ts"] <= 60
            and previous.get("observed_ts") == previous["current_top"]["ts"]
            and previous.get("received_ts", -1) >= previous["observed_ts"]
            and previous.get("received_ts", receipt + 1) <= receipt):
        record["previous_top"] = previous["current_top"]
        record["previous_source_id"] = previous["source_id"]
    return record


def parse_cot(body: bytes, *, contract: str, receipt: float, source_id: str) -> dict:
    rows = json.loads(body)
    if not isinstance(rows, list) or not rows or len(rows) > 128:
        raise ValueError("COT_ROWS_INVALID")
    history, seen = [], set()
    for row in rows:
        if str(row["cftc_contract_market_code"]) != contract:
            raise ValueError("COT_MARKET_MISMATCH")
        stamp = _ts(row["report_date_as_yyyy_mm_dd"])
        long = _number(row["noncomm_positions_long_all"])
        short = _number(row["noncomm_positions_short_all"])
        if min(long, short) < 0 or stamp in seen:
            raise ValueError("COT_POSITION_VALUE_OR_DUPLICATE_INVALID")
        seen.add(stamp)
        net = long - short
        if stamp > receipt:
            raise ValueError("COT_REPORT_IN_FUTURE")
        history.append({"report_ts": stamp, "net_position": net, "available_at": receipt})
    history.sort(key=lambda row: row["report_ts"])
    latest = history[-1]
    # This API's economic report date is NOT publication time. We only know the
    # report was public by receipt, so conservative publication bound is NOW.
    return {**_meta(source_id, "CFTC" + contract, latest["report_ts"], receipt),
            "kind": "cot_report", "published_at": receipt, "report_ts": latest["report_ts"],
            "net_position": latest["net_position"], "historical_positions": history[:-1],
            "publication_clock_basis": "FIRST_SEEN_UPPER_BOUND_NOT_EXACT_PUBLICATION",
            "historical_availability_basis": "FIRST_SEEN_NOW_NOT_BACKDATED",
            "position_category": "NONCOMMERCIAL_LONG_MINUS_SHORT_FUTURES_ONLY",
            "market_name": str(rows[0].get("market_and_exchange_names", "")),
            "dependency_group": "cftc:legacy-futures:" + contract}


def parse_coinbase_closes(body: bytes, *, receipt: float) -> dict[float, float]:
    rows = json.loads(body)
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("CANDLE_ROWS_INVALID")
    result = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("CANDLE_FIELDS_INVALID")
        start, close = _number(row[0]), _number(row[4])
        if start % 60 or close <= 0:
            raise ValueError("CANDLE_VALUE_INVALID")
        if start + 60 <= receipt:
            if start + 60 in result and result[start + 60] != close:
                raise ValueError("CONFLICTING_CANDLE_DUPLICATES")
            result[start + 60] = close
    return result


def parse_nyse_calendar(body: bytes, *, receipt: float, source_id: str) -> dict:
    """Verified scheduled 2026 NYSE CASH core calendar, not broker CFD hours."""
    text = unescape(re.sub(r"<[^>]+>", " ", body.decode("utf-8")))
    text = " ".join(text.split())
    tables = _CalendarTables()
    tables.feed(body.decode("utf-8"))
    header = next((i for i, row in enumerate(tables.rows) if len(row) >= 2
                   and row[0] == "Holiday" and row[1] == "2026"), None)
    actual = set()
    if header is not None:
        for row in tables.rows[header + 1:header + 11]:
            if len(row) < 2:
                raise ValueError("NYSE_CALENDAR_ROW_INCOMPLETE")
            match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (\d{1,2})\b", row[1])
            if match:
                day = datetime.strptime(match.group(0) + " 2026", "%B %d %Y")
                actual.add(day.date().isoformat())
    required = ("November 27, 2026", "December 24, 2026", "1:00 p.m.", "9:30 a.m. to 4:00 p.m.")
    if actual != NYSE_HOLIDAYS_2026 or not all(token in text for token in required):
        raise ValueError("NYSE_2026_OFFICIAL_CALENDAR_TABLE_CHANGED")
    local = datetime.fromtimestamp(receipt, ZoneInfo("America/New_York"))
    if local.year != 2026:
        raise ValueError("NYSE_CALENDAR_YEAR_NOT_SUPPORTED")
    day = local.date()
    for _ in range(10):
        if day.weekday() < 5 and day.isoformat() not in NYSE_HOLIDAYS_2026:
            break
        day += timedelta(days=1)
    if day.year != 2026:
        raise ValueError("NYSE_NEXT_SESSION_YEAR_NOT_SUPPORTED")
    close_hour = 13 if day.isoformat() in NYSE_EARLY_2026 else 16
    opening = datetime(day.year, day.month, day.day, 9, 30, tzinfo=local.tzinfo)
    closing = opening.replace(hour=close_hour, minute=0)
    return {**_meta(source_id, "NYSECASH", receipt, receipt), "calendar_complete": True,
            "calendar_id": "NYSE-CASH-CORE-OFFICIAL-SCHEDULED-2026",
            "calendar_available_at": receipt, "session_id": "NYSE:" + day.isoformat(),
            "session_open_ts": opening.timestamp(), "session_close_ts": closing.timestamp(),
            "calendar_scope": "SCHEDULED_CASH_CORE_ONLY_NOT_UNSCHEDULED_CLOSURES_OR_CFD",
            "early_close": close_hour == 13, "timezone": "America/New_York",
            "dependency_group": "session:nyse-cash-core"}


def build_bundle(*, instruments=DEFAULT_INSTRUMENTS, fetch: Callable = fetch_public,
                 clock: Callable = time.time, existing: dict | None = None,
                 previous: dict | None = None) -> dict:
    """At most 13 GETs, four workers; unavailable endpoints become explicit errors."""
    instruments = tuple(dict.fromkeys(canonical_instrument_code(x) for x in instruments))
    if not instruments or len(instruments) > 32 or any(not x for x in instruments):
        raise ValueError("INSTRUMENTS_INVALID")
    existing = existing if isinstance(existing, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    jobs = {"nyse:calendar": NYSE}
    for code, product in CRYPTO.items():
        for kind, suffix in (("book", "book?level=1"), ("tape", "trades?limit=1000"),
                             ("candles", "candles?granularity=60")):
            jobs[f"coinbase:{code}:{kind}"] = f"{COINBASE}/products/{product}/{suffix}"
    for code, contract in COT_MARKETS.items():
        if code in instruments:
            params = {"$where": f"cftc_contract_market_code='{contract}'",
                      "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 128}
            jobs["cftc:" + code] = CFTC + "?" + urlencode(params)
    raw, bodies, errors = {}, {}, []

    def get(item):
        key, url = item
        try:
            body = fetch(url)
            receipt = _number(clock())
            if not isinstance(body, bytes) or len(body) > 4_000_000:
                raise ValueError("FETCH_BODY_INVALID")
            digest = hashlib.sha256(body).hexdigest()
            return key, body, {"official_url": url, "body_sha256": digest,
                    "source_id": key + ":" + digest,
                    "received_ts": receipt, "fetch_status": "FETCHED", "body_bytes": len(body)}, None
        except Exception as exc:
            return key, None, {"official_url": url, "received_ts": _number(clock()),
                    "fetch_status": "UNAVAILABLE", "body_sha256": None}, type(exc).__name__

    with ThreadPoolExecutor(max_workers=4) as executor:
        for key, body, meta, error in executor.map(get, jobs.items()):
            raw[key] = meta
            if error:
                errors.append({"source_id": key, "phase": "fetch", "reason": error})
            else:
                bodies[key] = body
    output = {code: {"edge_family_sources": {family: [] for family in FAMILIES}} for code in instruments}

    def parse(key, parser, **kwargs):
        if key not in bodies:
            return None
        meta = raw[key]
        try:
            record = parser(bodies[key], receipt=meta["received_ts"],
                            source_id=meta["source_id"], **kwargs)
            raw[key]["parse_status"] = "PARSED"
            return record
        except Exception as exc:
            errors.append({"source_id": key, "phase": "parse", "reason": str(exc)[:160]})
            raw[key]["parse_status"] = "REJECTED"
            return None

    for code in instruments:
        old = existing.get("instruments", {}).get(code, {})
        if isinstance(old.get("macro_context_v1"), dict):
            output[code]["macro_context_v1"] = old["macro_context_v1"]
        for family in FAMILIES:
            records = old.get("edge_family_sources", {}).get(family, [])
            if isinstance(records, dict):
                records = [records]
            if isinstance(records, list):
                output[code]["edge_family_sources"][family].extend(
                    record for record in records[:128] if isinstance(record, dict))
    for code, product in CRYPTO.items():
        if code not in output:
            continue
        prior = previous.get("instruments", {}).get(code, {}).get("edge_family_sources", {}).get("order_flow", [])
        prior = next((row for row in reversed(prior) if row.get("kind") == "exchange_order_book"), None)
        for kind, parser in (("book", parse_coinbase_book), ("tape", parse_coinbase_tape)):
            kwargs = {"product": product}
            if kind == "book":
                kwargs["previous"] = prior
            record = parse(f"coinbase:{code}:{kind}", parser, **kwargs)
            if record:
                output[code]["edge_family_sources"]["order_flow"].append(_proxy(record, code))
    closes = {}
    for code in CRYPTO:
        key = f"coinbase:{code}:candles"
        if key in bodies:
            try:
                closes[code] = parse_coinbase_closes(bodies[key], receipt=raw[key]["received_ts"])
                raw[key]["parse_status"] = "PARSED"
            except Exception as exc:
                raw[key]["parse_status"] = "REJECTED"
                errors.append({"source_id": key, "phase": "parse", "reason": str(exc)[:160]})
    if len(closes) >= 2:
        common = set.intersection(*(set(values) for values in closes.values()))
        endpoints = [end for end in common if end - 300 in common]
        if endpoints:
            end = max(endpoints)
            received = max(raw[f"coinbase:{code}:candles"]["received_ts"] for code in closes)
            links = [{"leader": "COINBASE" + CRYPTO[code], "start_ts": end - 300,
                      "end_ts": end, "start_price": values[end - 300], "end_price": values[end]}
                     for code, values in closes.items()]
            ids = [f"coinbase:{code}:candles:" + raw[f"coinbase:{code}:candles"]["body_sha256"] for code in closes]
            for code in instruments:
                record = {**_meta("synced-crypto:" + hashlib.sha256("|".join(ids).encode()).hexdigest(), code, end, received),
                          "linked_returns": links, "supporting_source_ids": ids,
                          "dependency_group": "intermarket:coinbase:5min-completed",
                          "context_scope": "RELATED_VENUE_RETURNS_NOT_TARGET_BROKER_PRICE",
                          "target_price_equivalence_asserted": False}
                output[code]["edge_family_sources"]["intermarket"].append(record)
    for code, contract in COT_MARKETS.items():
        if code in output:
            record = parse("cftc:" + code, parse_cot, contract=contract)
            if record:
                output[code]["edge_family_sources"]["positioning"].append(_proxy(record, code))
    calendar = parse("nyse:calendar", parse_nyse_calendar)
    if calendar:
        for code in ("NAS100", "SP500", "US30"):
            if code in output:
                output[code]["edge_family_sources"]["session"].append(_proxy(calendar, code))
    captured = max(_number(clock()), *(row["received_ts"] for row in raw.values()))
    for code, entry in output.items():
        evidence = build_edge_family_evidence({**entry, "instrument": code, "captured_ts": captured})
        entry["readiness"] = evidence["families"]
        entry["unresolved"] = {family: row["needs_data"] for family, row in evidence["families"].items()}
    return {"contract_version": CONTRACT, "captured_ts": captured,
            "production_authority": False, "edge_policy": EDGE_POLICY,
            "instruments": output, "raw_sources": raw, "errors": errors,
            "collection_limits": {"requests": len(jobs), "workers": 4, "max_body_bytes": 4_000_000},
            "models_produced": 0, "exact_publication_times_inferred": False}
