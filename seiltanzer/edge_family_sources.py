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
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
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
BINANCE_SYMBOLS = {code: ALL_INSTRUMENTS[code].binance_symbol for code in CRYPTO}
MAX_REQUESTS = 16
MAX_TOTAL_BYTES = 16_000_000
COLLECTION_SECONDS = 120.
MIN_REQUEST_INTERVAL = .2
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


def _allowed_url(url: str) -> bool:
    return any(url == base or url.startswith(base + "/") or url.startswith(base + "?")
               for base in (COINBASE, CFTC, NYSE))


class _SourceRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib drains redirect bodies and retries outside our shared budget.
        # Even allowlisted redirects must not silently bypass those bounds.
        raise ValueError("SOURCE_REDIRECT_DISABLED")


class SourceBudget:
    """One shared request/rate/byte/deadline budget; denial never means retry."""
    def __init__(self, *, monotonic=time.monotonic, pause=time.sleep,
                 min_interval=MIN_REQUEST_INTERVAL, seconds=COLLECTION_SECONDS):
        self.monotonic, self.pause = monotonic, pause
        self.min_interval, self.deadline = min_interval, monotonic() + seconds
        self.lock, self.requests, self.body_bytes = Lock(), 0, 0
        self.body_reserved = 0
        self.last_start, self.stopped_hosts = None, set()

    def remaining(self):
        return max(0., self.deadline - self.monotonic())

    def reserve(self, host):
        with self.lock:
            if host in self.stopped_hosts:
                raise ValueError("SOURCE_HOST_STOPPED_AFTER_DENIAL")
            if self.body_bytes >= MAX_TOTAL_BYTES:
                raise ValueError("SOURCE_GLOBAL_BODY_BOUND")
            if self.requests >= MAX_REQUESTS or self.remaining() <= 0:
                raise ValueError("SOURCE_GLOBAL_REQUEST_OR_TIME_BOUND")
            delay = 0 if self.last_start is None else max(0., self.last_start + self.min_interval - self.monotonic())
            if delay >= self.remaining():
                raise ValueError("SOURCE_GLOBAL_REQUEST_OR_TIME_BOUND")
            if delay:
                self.pause(delay)
            if self.remaining() <= 0:
                raise ValueError("SOURCE_GLOBAL_REQUEST_OR_TIME_BOUND")
            self.last_start = self.monotonic()
            self.requests += 1

    def accept_body(self, size):
        with self.lock:
            self.body_bytes += size
            if self.body_bytes > MAX_TOTAL_BYTES:
                raise ValueError("SOURCE_GLOBAL_BODY_BOUND")

    def read_chunk(self, response, maximum):
        """Reserve bytes before socket reads, across all concurrent workers."""
        with self.lock:
            count = min(maximum, MAX_TOTAL_BYTES - self.body_bytes - self.body_reserved)
            if count <= 0:
                raise ValueError("SOURCE_GLOBAL_BODY_BOUND")
            self.body_reserved += count
        chunk = b""
        try:
            chunk = response.read1(count)
            return chunk
        finally:
            with self.lock:
                self.body_reserved -= count
                self.body_bytes += len(chunk)

    def stop_host(self, host):
        with self.lock:
            self.stopped_hosts.add(host)


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


def fetch_public(url: str, *, timeout: float = 12., max_bytes: int = 4_000_000,
                 budget: SourceBudget | None = None) -> bytes:
    """Allowlisted GET, capped response, no credentials and no retries."""
    if not _allowed_url(url):
        raise ValueError("SOURCE_URL_NOT_ALLOWLISTED")
    timeout = min(12., max(.01, timeout))
    deadline = time.monotonic() + timeout
    request = Request(url, headers={"User-Agent": "trading-dbd-source-audit/1", "Accept": "application/json,text/html"})
    with build_opener(_SourceRedirects()).open(request, timeout=timeout) as response:
        final_url = response.geturl()
        if not _allowed_url(final_url):
            raise ValueError("SOURCE_REDIRECT_NOT_ALLOWLISTED")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("SOURCE_RESPONSE_DEADLINE")
            # HTTPResponse.read1 performs one buffered/socket read rather than
            # waiting to fill a chunk indefinitely from a trickling response.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(remaining)
            count = min(65536, max_bytes + 1 - size)
            chunk = budget.read_chunk(response, count) if budget is not None else response.read1(count)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("SOURCE_BODY_TOO_LARGE")
        body = b"".join(chunks)
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


def _binance_identity(code: str, symbol: str) -> None:
    configured = ALL_INSTRUMENTS.get(code)
    if (configured is None or configured.binance_symbol != symbol
            or configured.tradingview_symbol != "BINANCE:" + symbol):
        raise ValueError("BINANCE_SYMBOL_NOT_EXACT_CONFIGURED_VENUE")


def parse_binance_book(body: bytes, *, code: str, symbol: str, receipt: float,
                       source_id: str, previous: dict | None = None) -> dict:
    """Two sampled actual REST books, not a fabricated full incremental stream.

    Spot depth REST has no exchange event timestamp. Its observation time is the
    local completed response receipt, explicitly NOT a claimed exchange clock.
    """
    _binance_identity(code, symbol)
    root = json.loads(body)
    sequence = _number(root["lastUpdateId"])
    if sequence < 0 or not sequence.is_integer():
        raise ValueError("BINANCE_BOOK_SEQUENCE_INVALID")
    bids, asks = root["bids"], root["asks"]
    if not (isinstance(bids, list) and isinstance(asks, list)
            and 0 < len(bids) <= 5 and 0 < len(asks) <= 5):
        raise ValueError("BINANCE_BOOK_DEPTH_BOUND")
    values = [_number(v) for v in (bids[0][0], bids[0][1], asks[0][0], asks[0][1])]
    if min(values) <= 0 or values[0] >= values[2]:
        raise ValueError("BINANCE_BOOK_PRICES_OR_SIZES_INVALID")
    current = dict(zip(("bid_price", "bid_size", "ask_price", "ask_size"), values), ts=receipt)
    record = {**_meta(source_id, code, receipt, receipt), "kind": "exchange_order_book",
              "venue": "BINANCE_SPOT", "venue_instrument": symbol, "quote_currency": "USDT",
              "exact_configured_venue": True, "sequence": int(sequence), "current_top": current,
              "clock_basis": "LOCAL_RESPONSE_OBSERVATION_NOT_EXCHANGE_EVENT_TIMESTAMP",
              "flow_scope": "SAMPLED_TWO_TOP_OBSERVATIONS_NOT_FULL_INCREMENTAL_OFI",
              "max_age_sec": 60., "dependency_group": "binance:" + symbol + ":book_tape"}
    if (isinstance(previous, dict) and previous.get("source_verified") is True
            and previous.get("instrument") == code and previous.get("venue_instrument") == symbol
            and previous.get("venue") == "BINANCE_SPOT"
            and previous.get("observed_ts") == previous.get("received_ts")
            and previous.get("current_top", {}).get("ts") == previous.get("observed_ts")
            and previous["observed_ts"] < receipt <= previous["observed_ts"] + 60
            and previous.get("sequence", int(sequence)) < int(sequence)):
        record["previous_top"] = previous["current_top"]
        record["previous_source_id"] = previous["source_id"]
        record["supporting_source_ids"] = [previous["source_id"], source_id]
        record["pair_status"] = "TWO_ACTUAL_ADVANCING_RESPONSES_WITHIN_60S"
    else:
        record["pair_status"] = "NO_CAUSAL_ADVANCING_PREVIOUS_RESPONSE"
    return record


def parse_binance_tape(body: bytes, *, code: str, symbol: str, receipt: float,
                       source_id: str) -> dict:
    _binance_identity(code, symbol)
    rows = json.loads(body)
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        raise ValueError("BINANCE_TAPE_ROWS_INVALID")
    seen, trades = set(), []
    for row in rows:
        identity = _number(row["a"])
        observed, quantity, price = _number(row["T"]) / 1000., _number(row["q"]), _number(row["p"])
        if not identity.is_integer() or identity < 0 or not isinstance(row["m"], bool):
            raise ValueError("BINANCE_AGG_TRADE_ID_OR_MAKER_SIDE_INVALID")
        if observed > receipt or min(quantity, price) <= 0:
            raise ValueError("BINANCE_TAPE_CLOCK_OR_VALUE_INVALID")
        if identity in seen:
            raise ValueError("BINANCE_TAPE_DUPLICATE_AGG_TRADE")
        seen.add(identity)
        trades.append((observed, quantity, row["m"]))
    latest = max(row[0] for row in trades)
    if receipt - latest > 60:
        raise ValueError("BINANCE_TAPE_STALE")
    window = [row for row in trades if latest - 60 <= row[0] <= latest]
    first = min(row[0] for row in window)
    if first >= latest:
        raise ValueError("BINANCE_TAPE_WINDOW_MISSING")
    return {**_meta(source_id, code, latest, receipt), "kind": "exchange_trade_tape",
            "venue": "BINANCE_SPOT", "venue_instrument": symbol, "quote_currency": "USDT",
            "exact_configured_venue": True, "window_start_ts": first, "window_end_ts": latest,
            "aggressive_buy_volume": sum(qty for _, qty, buyer_maker in window if not buyer_maker),
            "aggressive_sell_volume": sum(qty for _, qty, buyer_maker in window if buyer_maker),
            "window_complete": False, "coverage": "LATEST_LIMITED_AGGREGATE_PAGE_NOT_COMPLETE_60S_TAPE",
            "aggregate_trade_count": len(window), "size_unit": "BASE_ASSET", "buyer_maker_inverted": True,
            "max_age_sec": 60., "dependency_group": "binance:" + symbol + ":book_tape"}


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
    result = {**_meta(source_id, "CFTC" + contract, latest["report_ts"], receipt),
            "kind": "cot_report", "published_at": receipt, "report_ts": latest["report_ts"],
            "net_position": latest["net_position"], "historical_positions": history[:-1],
            "publication_clock_basis": "FIRST_SEEN_UPPER_BOUND_NOT_EXACT_PUBLICATION",
            "historical_availability_basis": "FIRST_SEEN_NOW_NOT_BACKDATED",
            "position_category": "NONCOMMERCIAL_LONG_MINUS_SHORT_FUTURES_ONLY",
            "market_name": str(rows[0].get("market_and_exchange_names", "")),
            "dependency_group": "cftc:legacy-futures:" + contract}
    if len(history) > 1:
        from .edge_family_history import POSITION_CONTRACT
        identity = dict(series_id='CFTC' + contract, kind='cot_report', unit='contracts',
            category=result['position_category'], venue='CFTC', body_sha256=hashlib.sha256(body).hexdigest())
        result.update(position_history_contract=POSITION_CONTRACT, position_series=identity,
            position_change_history=[dict(history[-2], provenance=dict(identity, source_id=source_id,
                received_ts=receipt, published_at=receipt, source_verified=True))])
    return result


def parse_coinbase_closes(body: bytes, *, receipt: float,
                          start_ts: float | None = None,
                          end_ts: float | None = None) -> dict[float, float]:
    rows = json.loads(body)
    if not isinstance(rows, list) or len(rows) > 300:
        raise ValueError("CANDLE_ROWS_INVALID:type=" + type(rows).__name__
                         + ":n=" + str(len(rows) if isinstance(rows, (list, dict)) else "unknown"))
    if (start_ts is None) != (end_ts is None):
        raise ValueError("CANDLE_REQUEST_WINDOW_INCOMPLETE")
    if start_ts is not None:
        start_ts, end_ts = _number(start_ts), _number(end_ts)
        if not start_ts < end_ts <= receipt or start_ts % 60 or end_ts % 60:
            raise ValueError("CANDLE_REQUEST_WINDOW_INVALID")
    result = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("CANDLE_FIELDS_INVALID")
        start, close = _number(row[0]), _number(row[4])
        if start % 60 or close <= 0:
            raise ValueError("CANDLE_VALUE_INVALID")
        if (start + 60 <= receipt and (start_ts is None
                or start >= start_ts and start + 60 <= end_ts)):
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
                 previous: dict | None = None, budget: SourceBudget | None = None) -> dict:
    """At most 16 GETs, four workers; unavailable endpoints become explicit errors."""
    instruments = tuple(dict.fromkeys(canonical_instrument_code(x) for x in instruments))
    if not instruments or len(instruments) > 32 or any(not x for x in instruments):
        raise ValueError("INSTRUMENTS_INVALID")
    existing = existing if isinstance(existing, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    budget = budget or SourceBudget()
    # Provider-default windows are not the bounded window requested by this
    # collector. Request one common, already-completed window for all leaders;
    # retaining the parser cap also rejects a provider that ignores these bounds.
    candle_end = math.floor(_number(clock()) / 60) * 60
    candle_start = candle_end - 30 * 60
    candle_query = urlencode({"granularity": 60,
        "start": datetime.fromtimestamp(candle_start, timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(candle_end, timezone.utc).isoformat()})
    jobs = {"nyse:calendar": NYSE}
    for code, product in CRYPTO.items():
        for kind, suffix in (("book", "book?level=1"), ("tape", "trades?limit=1000"),
                             ("candles", "candles?" + candle_query)):
            jobs[f"coinbase:{code}:{kind}"] = f"{COINBASE}/products/{product}/{suffix}"
    for code, contract in COT_MARKETS.items():
        if code in instruments:
            params = {"$where": f"cftc_contract_market_code='{contract}'",
                      "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 128}
            jobs["cftc:" + code] = CFTC + "?" + urlencode(params)
    raw, bodies, errors = {}, {}, []

    def get(item):
        key, url = item
        host = url.split("/")[2]
        try:
            budget.reserve(host)
            body = fetch(url, timeout=min(12., budget.remaining()), budget=budget) if fetch is fetch_public else fetch(url)
            receipt = _number(clock())
            if not isinstance(body, bytes) or len(body) > 4_000_000:
                raise ValueError("FETCH_BODY_INVALID")
            if fetch is not fetch_public:
                budget.accept_body(len(body))
            digest = hashlib.sha256(body).hexdigest()
            return key, body, {"official_url": url, "body_sha256": digest,
                    "source_id": key + ":" + digest,
                    "received_ts": receipt, "fetch_status": "FETCHED", "body_bytes": len(body)}, None
        except Exception as exc:
            status = exc.code if isinstance(exc, HTTPError) else None
            if status in {403, 451, 429, 418}:
                budget.stop_host(host)
            reason = "HTTP_" + str(status) if status is not None else str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            return key, None, {"official_url": url, "received_ts": _number(clock()),
                    "fetch_status": "UNAVAILABLE", "body_sha256": None,
                    "http_status": status}, reason[:160]

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
        first_book = parse(f"coinbase:{code}:book", parse_coinbase_book, product=product)
        second_key = f"coinbase:{code}:book_second"
        key, body, meta, error = get((second_key, f"{COINBASE}/products/{product}/book?level=1"))
        raw[key] = meta
        if error:
            errors.append({"source_id": key, "phase": "fetch", "reason": error})
        else:
            bodies[key] = body
        second_book = parse(second_key, parse_coinbase_book, product=product, previous=first_book)
        # Only the pair acquired in this run can supply sampled OFI. A 10/20
        # minute previous-bundle cache cannot invent a within-60s observation.
        chosen_book = second_book or first_book
        if chosen_book:
            chosen_book["flow_scope"] = "SAMPLED_TWO_TOP_OBSERVATIONS_NOT_FULL_INCREMENTAL_OFI"
            chosen_book["max_age_sec"] = 60.
            if chosen_book.get("previous_source_id"):
                chosen_book["supporting_source_ids"] = [chosen_book["previous_source_id"], chosen_book["source_id"]]
            output[code]["edge_family_sources"]["order_flow"].append(_proxy(chosen_book, code))
        record = parse(f"coinbase:{code}:tape", parse_coinbase_tape, product=product)
        if record:
            output[code]["edge_family_sources"]["order_flow"].append(_proxy(record, code))
    closes = {}
    for code in CRYPTO:
        key = f"coinbase:{code}:candles"
        if key in bodies:
            try:
                closes[code] = parse_coinbase_closes(bodies[key], receipt=raw[key]["received_ts"],
                                                   start_ts=candle_start, end_ts=candle_end)
                raw[key]["parse_status"] = "PARSED"
                raw[key]["requested_window_start_ts"] = candle_start
                raw[key]["requested_window_end_ts"] = candle_end
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
            # Retain the exact lagged window from these same received bodies.
            # A gapped series stays in the original legacy facts and diagnostics;
            # it cannot supply a reconstructed history proof.
            histories = []
            for leader, values in closes.items():
                required = [end - 360 + offset * 60 for offset in range(7)]
                if not all(stamp in values for stamp in required):
                    errors.append({'source_id': f'coinbase:{leader}:candles', 'phase': 'history',
                                   'reason': 'INTERMARKET_HISTORY_EXACT_WINDOW_MISSING'})
                    continue
                actual = raw[f'coinbase:{leader}:candles']
                histories.append(dict(source_id=actual['source_id'], provider='COINBASE', symbol=CRYPTO[leader],
                    base_currency=CRYPTO[leader].split('-')[0], quote_currency='USD', orientation='direct',
                    body_sha256=actual['body_sha256'], available_at=actual['received_ts'],
                    bars=[[stamp, values[stamp], actual['received_ts']] for stamp in required]))
            for code in instruments:
                record = {**_meta("synced-crypto:" + hashlib.sha256("|".join(ids).encode()).hexdigest(), code, end, received),
                          "linked_returns": links, "supporting_source_ids": ids,
                          "dependency_group": "intermarket:coinbase:5min-completed",
                          "context_scope": "RELATED_VENUE_RETURNS_NOT_TARGET_BROKER_PRICE",
                          "collection_window_start_ts": candle_start,
                          "collection_window_end_ts": candle_end,
                          "target_price_equivalence_asserted": False}
                if histories:
                    from .edge_family_history import INTERMARKET_CONTRACT
                    record.update(intermarket_history_contract=INTERMARKET_CONTRACT,
                                  historical_series=histories)
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
    try:
        from .fomc_official_source import fetch_recent_statements
        from .edge_family_event_novelty import build_received_event_novelty_source, _sha
        from datetime import datetime as _datetime
        from zoneinfo import ZoneInfo
        
        docs = fetch_recent_statements(limit=2, now=captured) if fetch is fetch_public else []
        if len(docs) >= 2:
            current_doc, previous_doc = docs[0], docs[1]
            def make_record(doc, prev_doc=None):
                pub = doc['published_at']
                code = _datetime.fromtimestamp(pub, ZoneInfo('America/New_York')).strftime('%Y%m%d')
                url = doc['source_url']
                body_sha = _sha(doc['text'])
                expected_id = 'macro-fomc-det-' + _sha(f'{code}|{url}|{pub:.6f}|{body_sha}')[:28]
                
                prev_id = None
                prev_url = None
                if prev_doc:
                    prev_pub = prev_doc['published_at']
                    prev_code = _datetime.fromtimestamp(prev_pub, ZoneInfo('America/New_York')).strftime('%Y%m%d')
                    prev_url = prev_doc['source_url']
                    prev_body_sha = _sha(prev_doc['text'])
                    prev_id = 'macro-fomc-det-' + _sha(f'{prev_code}|{prev_url}|{prev_pub:.6f}|{prev_body_sha}')[:28]

                return {
                    'release_id': expected_id,
                    'date_code': code,
                    'source_url': url,
                    'published_at': pub,
                    'fetched_at': captured,
                    'created_ts': captured,
                    'body_text': doc['text'],
                    'body_sha256': body_sha,
                    'previous_release_id': prev_id,
                    'previous_source_url': prev_url,
                    'contract_version': 'fomc-deterministic-point-in-time-v1'
                }
            
            pair = {
                'status': 'AVAILABLE',
                'current': make_record(current_doc, previous_doc),
                'previous': make_record(previous_doc)
            }
            novelty = build_received_event_novelty_source(pair, captured)
            if novelty.get('source'):
                for code in instruments:
                    if code in output:
                        output[code]["edge_family_sources"]["event"].append(novelty['source'])
            elif novelty.get('rejections'):
                errors.append({"source_id": "fomc:novelty", "phase": "build", "reason": str(novelty['rejections'])[:160]})
    except Exception as exc:
        errors.append({"source_id": "fomc:recent_statements", "phase": "fetch", "reason": str(exc)[:160]})
    for code, entry in output.items():
        evidence = build_edge_family_evidence({**entry, "instrument": code, "captured_ts": captured})
        entry["readiness"] = evidence["families"]
        entry["unresolved"] = {family: row["needs_data"] for family, row in evidence["families"].items()}
    return {"contract_version": CONTRACT, "captured_ts": captured,
            "production_authority": False, "edge_policy": EDGE_POLICY,
            "instruments": output, "raw_sources": raw, "errors": errors,
            "collection_limits": {"requests": budget.requests, "source_attempts": len(raw),
                "max_requests": MAX_REQUESTS, "workers": 4, "max_body_bytes": 4_000_000,
                "body_bytes_received": budget.body_bytes, "max_total_body_bytes": MAX_TOTAL_BYTES,
                "min_request_interval_sec": MIN_REQUEST_INTERVAL, "global_deadline_sec": COLLECTION_SECONDS},
            "refresh_policy": {"scheduled_interval_sec": 600, "intermarket_max_age_sec": 900,
                "order_flow_max_age_sec": 60, "order_flow_continuous_freshness_guaranteed": False,
                "binance_rest_collection": "DISABLED_AFTER_OBSERVED_GEOGRAPHIC_DENIAL_NO_BYPASS"},
            "models_produced": 0, "exact_publication_times_inferred": False}
