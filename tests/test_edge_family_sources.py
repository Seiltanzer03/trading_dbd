from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlparse
from urllib.error import HTTPError

import pytest

from seiltanzer.edge_family_adapters import FAMILIES, build_edge_family_evidence
from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.edge_family_sources import (
    CRYPTO, NYSE_HOLIDAYS_2026, SourceBudget, build_bundle as _build_bundle, fetch_public, parse_coinbase_book,
    parse_coinbase_closes, parse_coinbase_tape, parse_cot, parse_nyse_calendar,
    parse_binance_book, parse_binance_tape,
)

T0 = datetime(2026, 10, 1, 15, tzinfo=timezone.utc).timestamp()


def build_bundle(**kwargs):
    # Unit fixtures do not spend wall time on the real public-request limiter.
    kwargs.setdefault("budget", SourceBudget(min_interval=0))
    return _build_bundle(**kwargs)


def encoded(value):
    return json.dumps(value).encode()


def isotime(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def book(ts=T0 - 1, sequence=2):
    return encoded({"time": isotime(ts), "sequence": sequence,
                    "bids": [["100", "2", 3]], "asks": [["101", "4", 9]]})


def tape():
    return encoded([{"time": isotime(T0 - 1), "trade_id": 2, "price": "100", "size": "3", "side": "sell"},
                    {"time": isotime(T0 - 10), "trade_id": 1, "price": "100", "size": "1", "side": "buy"}])


def cot():
    return encoded([{"report_date_as_yyyy_mm_dd": "2026-09-29T00:00:00.000", "cftc_contract_market_code": "088691",
                     "noncomm_positions_long_all": "100", "noncomm_positions_short_all": "30", "open_interest_all": "240"},
                    {"report_date_as_yyyy_mm_dd": "2026-09-22T00:00:00.000", "cftc_contract_market_code": "088691",
                     "noncomm_positions_long_all": "80", "noncomm_positions_short_all": "40", "open_interest_all": "200"}])


def calendar():
    rows = "".join("<tr><td>Holiday</td><td>" + datetime.strptime(day, "%Y-%m-%d").strftime("%B %d").replace(" 0", " ")
                   + "</td><td>different 2027 date</td></tr>" for day in sorted(NYSE_HOLIDAYS_2026))
    return ("<table><tr><th>Holiday</th><th>2026</th><th>2027</th></tr>" + rows + "</table>"
            "Core Trading Session: 9:30 a.m. to 4:00 p.m. "
            "Early 1:00 p.m. on Friday, November 27, 2026 and Thursday, December 24, 2026").encode()


def candles():
    return encoded([[T0 - 60 * n, 90, 110, 99, 100 + n, 5] for n in range(12)])


def test_tape_inverts_maker_side_and_rejects_future_stale_unknown():
    record = parse_coinbase_tape(tape(), product="BTC-USD", receipt=T0, source_id="tape")
    assert record["aggressive_buy_volume"] == 3
    assert record["aggressive_sell_volume"] == 1
    assert record["window_complete"] is False
    for shift in (-10, 120):
        with pytest.raises(ValueError):
            parse_coinbase_tape(tape(), product="BTC-USD", receipt=T0 + shift, source_id="tape")
    malformed = json.loads(tape())
    malformed[0]["side"] = "unknown"
    with pytest.raises(ValueError):
        parse_coinbase_tape(encoded(malformed), product="BTC-USD", receipt=T0, source_id="tape")


def test_real_book_uses_aggregate_size_not_order_count_and_requires_previous():
    prior = parse_coinbase_book(book(T0 - 2, 1), product="BTC-USD", receipt=T0 - 1, source_id="prior")
    record = parse_coinbase_book(book(), product="BTC-USD", receipt=T0, source_id="now", previous=prior)
    assert record["current_top"]["bid_size"] == 2
    assert record["previous_top"]["ask_size"] == 4
    assert "previous_top" not in parse_coinbase_book(book(), product="BTC-USD", receipt=T0, source_id="now")
    assert "previous_top" not in parse_coinbase_book(book(), product="BTC-USD", receipt=T0, source_id="now", previous=record)
    auction = json.loads(book())
    auction["auction_mode"] = True
    with pytest.raises(ValueError, match="AUCTION"):
        parse_coinbase_book(encoded(auction), product="BTC-USD", receipt=T0, source_id="now")


def test_cot_report_date_is_not_invented_publication_or_historical_availability():
    record = parse_cot(cot(), contract="088691", receipt=T0, source_id="cot")
    assert record["report_ts"] < record["published_at"] == record["available_at"] == T0
    assert record["historical_positions"][0]["available_at"] == T0
    assert record["net_position"] == 70
    result = build_edge_family_evidence({"instrument": "CFTC088691", "captured_ts": T0 - 1,
                                         "edge_family_sources": {"positioning": record}})
    assert result["families"]["positioning"]["available"] is False
    with pytest.raises(ValueError, match="MARKET"):
        parse_cot(cot(), contract="099741", receipt=T0, source_id="cot")


def test_candles_exclude_in_progress_and_detect_conflicting_duplicates():
    closes = parse_coinbase_closes(candles(), receipt=T0)
    assert max(closes) == T0
    rows = json.loads(candles())
    rows.append([T0 - 60, 90, 110, 99, 500, 5])
    with pytest.raises(ValueError, match="CONFLICTING"):
        parse_coinbase_closes(encoded(rows), receipt=T0)


def test_candles_strict_response_cap_and_requested_completed_window():
    rows = [[T0 - 60 * n, 90, 110, 99, 100, 5] for n in range(302)]
    with pytest.raises(ValueError, match="CANDLE_ROWS_INVALID:type=list:n=302"):
        parse_coinbase_closes(encoded(rows), receipt=T0, start_ts=T0 - 1800, end_ts=T0)
    closes = parse_coinbase_closes(encoded(rows[:40]), receipt=T0 + 120,
                                  start_ts=T0 - 1800, end_ts=T0)
    assert len(closes) == 30
    assert min(closes) == T0 - 1740
    assert max(closes) == T0
    with pytest.raises(ValueError, match="WINDOW_INCOMPLETE"):
        parse_coinbase_closes(candles(), receipt=T0, start_ts=T0 - 1800)
    with pytest.raises(ValueError, match="WINDOW_INVALID"):
        parse_coinbase_closes(candles(), receipt=T0, start_ts=T0 - 1800, end_ts=T0 + 60)


def test_cash_calendar_holiday_early_close_and_dst_not_cfd_authority():
    for date, close_hour in (("2026-11-27T15:00:00+00:00", 18), ("2026-10-01T15:00:00+00:00", 20)):
        stamp = datetime.fromisoformat(date).timestamp()
        record = parse_nyse_calendar(calendar(), receipt=stamp, source_id="calendar")
        assert datetime.fromtimestamp(record["session_close_ts"], timezone.utc).hour == close_hour
        assert record["instrument"] == "NYSECASH"
    holiday = datetime(2026, 7, 3, 15, tzinfo=timezone.utc).timestamp()
    record = parse_nyse_calendar(calendar(), receipt=holiday, source_id="calendar")
    assert record["session_id"] == "NYSE:2026-07-06"
    assert record["session_open_ts"] > holiday
    with pytest.raises(ValueError, match="TABLE_CHANGED"):
        parse_nyse_calendar(calendar().replace(b"<th>2026</th>", b"<th>2027</th>"), receipt=T0, source_id="calendar")


def test_fetch_is_allowlisted_before_network():
    with pytest.raises(ValueError, match="ALLOWLISTED"):
        fetch_public("https://example.com/")


def test_redirect_cannot_drain_unbudgeted_body_or_issue_another_request(monkeypatch):
    import io
    from email.message import Message
    from urllib.request import BaseHandler, build_opener
    from urllib.response import addinfourl
    from seiltanzer import edge_family_sources as module

    downloaded = []
    requests = []
    class Stream(io.BytesIO):
        def read(self, size=-1):
            body = super().read(size)
            downloaded.append(len(body))
            return body
        def read1(self, size=-1):
            return self.read(size)
    class Transport(BaseHandler):
        handler_order = 0
        def https_open(self, request):
            requests.append(request.full_url)
            first = len(requests) == 1
            headers = Message()
            if first:
                headers['Location'] = module.COINBASE + '/end'
            response = addinfourl(Stream(b'x' * 1500 if first else b'ok'),
                                  headers, request.full_url, 302 if first else 200)
            response.msg = 'Found' if first else 'OK'
            return response
    opener = build_opener(module._SourceRedirects(), Transport())
    monkeypatch.setattr(module, 'build_opener', lambda *args: opener)
    monkeypatch.setattr(module, 'MAX_TOTAL_BYTES', 1000)
    budget = SourceBudget(min_interval=0)
    budget.reserve('api.exchange.coinbase.com')
    with pytest.raises(ValueError, match='SOURCE_REDIRECT_DISABLED'):
        fetch_public(module.COINBASE + '/start', budget=budget)
    assert requests == [module.COINBASE + '/start']
    assert sum(downloaded) == budget.body_bytes == 0


def test_streaming_global_body_budget_bounds_actual_reads(monkeypatch):
    from seiltanzer import edge_family_sources as module
    class Response:
        def __init__(self): self.remaining = 4_000_000
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return "https://api.exchange.coinbase.com/products/BTC-USD/book?level=1"
        def read1(self, size):
            n = min(size, self.remaining)
            self.remaining -= n
            downloaded[0] += n
            return b"x" * n
    downloaded = [0]
    opener = type("Opener", (), {"open": lambda self, *args, **kwargs: Response()})()
    monkeypatch.setattr(module, "build_opener", lambda *args: opener)
    limit = SourceBudget(min_interval=0)
    for _ in range(16):
        try:
            limit.reserve("api.exchange.coinbase.com")
            module.fetch_public("https://api.exchange.coinbase.com/products/BTC-USD/book?level=1", budget=limit)
        except ValueError:
            pass
    assert downloaded[0] <= module.MAX_TOTAL_BYTES
    assert limit.body_bytes == downloaded[0]
    assert limit.requests <= 5


def test_bundle_has_all_eight_honest_rows_actual_hashes_and_no_model_authority():
    calls = []
    def fetch(url):
        calls.append(url)
        if "cftc.gov" in url:
            return cot()
        if "nyse.com" in url:
            return calendar()
        if "book?" in url:
            return book()
        if "trades?" in url:
            return tape()
        return candles()
    bundle = build_bundle(instruments=("XAU", "BTCUSD", "NAS100"), fetch=fetch, clock=lambda: T0)
    assert bundle["production_authority"] is False
    assert bundle["models_produced"] == 0
    assert len(calls) == 12
    assert not bundle["errors"]
    for code, entry in bundle["instruments"].items():
        assert set(entry["readiness"]) == set(FAMILIES)
        assert all(row["forecast_available"] is False and row["voting_weight"] == 0 for row in entry["readiness"].values())
        assert entry["readiness"]["intermarket"]["available"] is True
        assert entry["readiness"]["event"]["available"] is False
        assert not entry["edge_family_sources"]["event"]
    for code, family in (("XAU", "positioning"), ("BTCUSD", "order_flow"), ("NAS100", "session")):
        row = bundle["instruments"][code]["readiness"][family]
        assert row["available"] is False
        assert any(item["reason"] == "INSTRUMENT_OR_PROXY_MAPPING_UNVALIDATED" for item in row["rejected_sources"])
    assert all(len(meta["body_sha256"]) == 64 for meta in bundle["raw_sources"].values())
    links = bundle["instruments"]["NAS100"]["edge_family_sources"]["intermarket"][0]["linked_returns"]
    assert len({(row["start_ts"], row["end_ts"]) for row in links}) == 1
    assert all(row["end_ts"] - row["start_ts"] == 300 for row in links)


def test_shared_explicit_crypto_request_bounds_and_immutable_source_lineage():
    calls, bodies = [], {}
    def fetch(url):
        calls.append(url)
        body = candles() if "/candles?" in url else book() if "/book?" in url else tape()
        bodies[url] = body
        if "nyse.com" in url:
            return calendar()
        return body
    # A non-minute wall clock must not admit a provider's unfinished or
    # out-of-window candle even if the HTTP response arrives later.
    bundle = build_bundle(instruments=("BTCUSD", "ETHUSD", "SOLUSD"),
                          fetch=fetch, clock=lambda: T0 + 29)
    urls = [url for url in calls if "/candles?" in url]
    assert len(urls) == 3
    queries = [parse_qs(urlparse(url).query) for url in urls]
    assert all(query == queries[0] for query in queries)
    query = queries[0]
    start = datetime.fromisoformat(query["start"][0]).timestamp()
    end = datetime.fromisoformat(query["end"][0]).timestamp()
    assert end == T0 and end - start == 1800
    assert query["granularity"] == ["60"]
    import hashlib
    for code, entry in bundle["instruments"].items():
        record = entry["edge_family_sources"]["intermarket"][0]
        assert record["collection_window_start_ts"] == start
        assert record["collection_window_end_ts"] == end
        assert all(start <= link["start_ts"] < link["end_ts"] <= end for link in record["linked_returns"])
        for leader in CRYPTO:
            raw = bundle["raw_sources"][f"coinbase:{leader}:candles"]
            digest = hashlib.sha256(bodies[raw["official_url"]]).hexdigest()
            assert raw["body_sha256"] == digest
            assert raw["source_id"] in record["supporting_source_ids"]
            assert raw["requested_window_end_ts"] == end
            assert raw["received_ts"] <= bundle["captured_ts"]


def test_all_unavailable_sources_remain_explicit_no_fill_forward_or_zero_carry():
    def unavailable(url):
        raise TimeoutError("blocked")
    bundle = build_bundle(instruments=("XAU", "BTCUSD"), fetch=unavailable, clock=lambda: T0)
    assert len(bundle["errors"]) == bundle["collection_limits"]["requests"]
    for entry in bundle["instruments"].values():
        assert all(not records for records in entry["edge_family_sources"].values())
        assert all(not row["available"] for row in entry["readiness"].values())


def test_default_rows_include_every_actual_configured_instrument():
    bundle = build_bundle(fetch=lambda url: (_ for _ in ()).throw(TimeoutError()), clock=lambda: T0)
    assert set(bundle["instruments"]) == set(ALL_INSTRUMENTS)
    assert "USDCAD" in bundle["instruments"]
    assert all(set(row["readiness"]) == set(FAMILIES) for row in bundle["instruments"].values())


def test_existing_context_never_imports_fitted_models_or_grants_new_votes():
    existing = {"instruments": {"XAU": {"edge_family_models": {"macro": {"validated": True}},
                "edge_family_sources": {"macro": [{"source_id": "actual-local", "source_verified": True,
                "observed_ts": T0 - 1, "available_at": T0, "global_context": True,
                "features": {"macro.rate": 3.5}}]}}}}
    bundle = build_bundle(instruments=("XAU",), fetch=lambda url: (_ for _ in ()).throw(TimeoutError()),
                          clock=lambda: T0, existing=existing)
    entry = bundle["instruments"]["XAU"]
    assert entry["readiness"]["macro"]["available"] is True
    assert entry["readiness"]["macro"]["forecast_available"] is False
    assert "edge_family_models" not in entry
