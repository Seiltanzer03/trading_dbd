import math
import sqlite3
import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from seiltanzer.edge_discovery.maturity import data_maturity
from seiltanzer.macro_bls_historical_bootstrap import (
    BLSHistoricalReleaseStore,
    BLSReleaseSpec,
    OfficialBLSArchiveSource,
    historical_feature_records_from_runtime,
    parse_bls_archive_spec,
    parse_bls_archive_index_urls,
    parse_bls_atom_archive_urls,
    parse_bls_ical,
    parse_bls_schedule,
    parse_cpi_archive,
    parse_nfp_archive,
)
from seiltanzer.macro_bls_historical_ede_refinement import (
    _recompute_macro_inventory_maturity,
)


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.RLock()


SCHEDULE_HTML = """
<table>
<tr><th>Date</th><th>Time</th><th>Release</th></tr>
<tr><td>Friday, August 7, 2026</td><td>08:30 AM</td><td>Employment Situation for July 2026</td></tr>
<tr><td>Wednesday, August 12, 2026</td><td>08:30 AM</td><td>Consumer Price Index for July 2026</td></tr>
<tr><td>Thursday, August 13, 2026</td><td>08:30 AM</td><td>Producer Price Index for July 2026</td></tr>
</table>
"""

SCHEDULE_ICAL = """BEGIN:VCALENDAR
PRODID:-//Department of Labor//Bureau of Labor Statistics//EN
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20260807T083000
SUMMARY:Employment Situation
END:VEVENT
BEGIN:VEVENT
DTSTART;TZID=US-Eastern:20260812T083000
SUMMARY:Consumer Price Index
END:VEVENT
END:VCALENDAR
"""

CPI_HTML = """
<html><body>
<p>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Tuesday, August 12, 2025</p>
<h1>CONSUMER PRICE INDEX - JULY 2025</h1>
<table>
<tr><th>Item</th><th>May</th><th>Jun</th><th>Jul</th><th>12 mos.</th></tr>
<tr><td>All items</td><td>0.1</td><td>0.3</td><td>0.2</td><td>2.7</td></tr>
<tr><td>Food</td><td>0.3</td><td>0.3</td><td>0.0</td><td>2.9</td></tr>
<tr><td>All items less food and energy</td><td>0.1</td><td>0.2</td><td>0.3</td><td>3.1</td></tr>
</table>
</body></html>
"""

ATOM_CPI = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Consumer Price Index</title><id>https://www.bls.gov/feed/cpi.rss</id>
<updated>2025-08-12T07:51:00-04:00</updated>
<entry><title>Consumer Price Index - July 2025</title>
<published>2025-08-12T07:51:00-04:00</published>
<link rel="alternate" href="https://www.bls.gov/news.release/archives/cpi_08122025.htm" />
</entry></feed>
"""

ARCHIVE_INDEX_CPI = """
<html><body><h1>Consumer Price Index Archived News Releases</h1>
<p>Links to archive copies of Consumer Price Index news releases.</p>
<a href="/news.release/archives/cpi_08122025.htm">July 2025 CPI</a>
<a href="https://www.bls.gov/news.release/archives/cpi_07152025.htm">June 2025 CPI</a>
<a href="/news.release/cpi.nr0.htm">Current mutable CPI</a>
<a href="https://example.com/news.release/archives/cpi_06112025.htm">Foreign copy</a>
</body></html>
"""

NFP_HTML = """
<html><body>
<p>Transmission of material in this news release is embargoed until
USDL-26-1125 8:30 a.m. (ET) Thursday, July 2, 2026</p>
<h1>THE EMPLOYMENT SITUATION -- JUNE 2026</h1>
<table>
<tr><th>Category</th><th>Jun 2025</th><th>Apr 2026</th><th>May 2026</th><th>Jun 2026</th><th>Change</th></tr>
<tr><td>Unemployment rate</td><td>4.1</td><td>4.3</td><td>4.3</td><td>4.2</td><td>-0.1</td></tr>
</table>
<table>
<tr><th>Category</th><th>Jun 2025</th><th>Apr 2026</th><th>May 2026</th><th>Jun 2026</th></tr>
<tr><td>Total nonfarm</td><td>-20</td><td>148</td><td>129</td><td>57</td></tr>
<tr><td>Average hourly earnings</td><td>$36.36</td><td>$37.41</td><td>$37.51</td><td>$37.64</td></tr>
</table>
</body></html>
"""


def test_schedule_uses_exact_official_release_time_and_archive_date():
    specs = parse_bls_schedule(SCHEDULE_HTML, year=2026)
    assert [(item.family, item.period) for item in specs] == [
        ("NFP", "2026-07"), ("CPI", "2026-07")]
    nfp, cpi = specs
    expected_nfp = datetime(
        2026, 8, 7, 8, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()
    expected_cpi = datetime(
        2026, 8, 12, 8, 30, tzinfo=ZoneInfo("America/New_York")).timestamp()
    assert nfp.published_at == expected_nfp
    assert cpi.published_at == expected_cpi
    assert nfp.source_url.endswith("/empsit_08072026.htm")
    assert cpi.source_url.endswith("/cpi_08122026.htm")


def test_official_ical_uses_exact_release_time_and_archive_period_guard():
    specs = parse_bls_ical(SCHEDULE_ICAL)
    assert [(item.family, item.period) for item in specs] == [
        ("NFP", "2026-07"), ("CPI", "2026-07")]
    assert specs[0].published_at == datetime(
        2026, 8, 7, 8, 30, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    assert specs[1].published_at == datetime(
        2026, 8, 12, 8, 30, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    assert specs[0].source_url.endswith("/empsit_08072026.htm")
    assert specs[1].source_url.endswith("/cpi_08122026.htm")


def test_official_atom_discovers_archive_but_archive_sets_causal_time():
    links = parse_bls_atom_archive_urls(ATOM_CPI, family="CPI")
    assert links == [
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
    ]
    spec = parse_bls_archive_spec(
        CPI_HTML, family="CPI", source_url=links[0])
    assert spec.period == "2025-07"
    assert spec.published_at == datetime(
        2025, 8, 12, 8, 30, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    # The Atom entry was visible at 07:51, but it cannot move availability
    # ahead of the archive's explicit 08:30 embargo.
    assert spec.published_at > datetime(
        2025, 8, 12, 7, 51, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()


def test_historical_bls_transport_tries_direct_before_configured_proxy(monkeypatch):
    calls = []

    class Response:
        url = "https://www.bls.gov/bls/news-release/cpi.htm"
        text = ARCHIVE_INDEX_CPI

        @staticmethod
        def raise_for_status():
            return None

    class Client:
        def __init__(self, **kwargs):
            self.proxy = kwargs["proxy"]
            calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url):
            if self.proxy is None:
                raise ValueError("direct route unavailable")
            return Response()

    monkeypatch.setenv("MACRO_HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(
        "seiltanzer.macro_bls_historical_bootstrap.httpx.Client", Client)

    _content, links = OfficialBLSArchiveSource().archive_index_manifest("CPI")

    assert links
    assert [row["proxy"] for row in calls] == [
        None, "http://proxy.invalid:8080"]
    assert all(row["trust_env"] is False for row in calls)


def test_historical_bls_transport_does_not_touch_proxy_after_direct_success(
    monkeypatch,
):
    calls = []

    class Response:
        url = "https://www.bls.gov/bls/news-release/cpi.htm"
        text = ARCHIVE_INDEX_CPI

        @staticmethod
        def raise_for_status():
            return None

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs["proxy"])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def get(_url):
            return Response()

    monkeypatch.setenv("MACRO_HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setattr(
        "seiltanzer.macro_bls_historical_bootstrap.httpx.Client", Client)

    _content, links = OfficialBLSArchiveSource().archive_index_manifest("CPI")

    assert links
    assert calls == [None]


def test_historical_bls_transport_failure_does_not_leak_proxy(monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            self.proxy = kwargs["proxy"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url):
            raise ValueError(f"blocked via {self.proxy}")

    secret_proxy = "http://user:secret@proxy.invalid:8080"
    monkeypatch.setenv("MACRO_HTTP_PROXY", secret_proxy)
    monkeypatch.setattr(
        "seiltanzer.macro_bls_historical_bootstrap.httpx.Client", Client)

    with pytest.raises(ValueError) as failure:
        OfficialBLSArchiveSource().archive_index_manifest("CPI")

    message = str(failure.value)
    assert "DIRECT_OFFICIAL:ValueError" in message
    assert "CONFIGURED_PROXY:ValueError" in message
    assert secret_proxy not in message


def test_official_archive_index_is_family_bound_and_excludes_mutable_links():
    links = parse_bls_archive_index_urls(
        ARCHIVE_INDEX_CPI,
        family="CPI",
        source_url="https://www.bls.gov/bls/news-release/cpi.htm",
    )
    assert links == [
        "https://www.bls.gov/news.release/archives/cpi_07152025.htm",
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    ]
    with pytest.raises(ValueError, match="SOURCE_INVALID"):
        parse_bls_archive_index_urls(
            ARCHIVE_INDEX_CPI,
            family="NFP",
            source_url="https://www.bls.gov/bls/news-release/cpi.htm",
        )


def test_atom_and_archive_provenance_fail_closed():
    with pytest.raises(ValueError, match="ARCHIVE_LINK_INVALID"):
        parse_bls_atom_archive_urls(
            ATOM_CPI.replace("https://www.bls.gov/news.release", "https://example.com"),
            family="CPI",
        )
    with pytest.raises(ValueError, match="DATE_MISMATCH"):
        parse_bls_archive_spec(
            CPI_HTML, family="CPI",
            source_url="https://www.bls.gov/news.release/archives/cpi_08132025.htm",
        )


def test_cpi_archive_uses_values_printed_in_release_table():
    payload = parse_cpi_archive(CPI_HTML, expected_period="2025-07")
    assert payload["headline_mom_pct"] == 0.2
    assert payload["core_mom_pct"] == 0.3
    assert payload["headline_yoy_pct"] == 2.7
    assert payload["core_yoy_pct"] == 3.1
    assert round(payload["headline_mom_change_pp"], 10) == -0.1
    assert round(payload["core_mom_change_pp"], 10) == 0.1
    assert payload["surprise_computed"] is False
    assert payload["historical_archive_vintage"] is True


def test_nfp_archive_uses_summary_release_vintage_not_current_series():
    spec = parse_bls_archive_spec(
        NFP_HTML, family="NFP",
        source_url="https://www.bls.gov/news.release/archives/empsit_07022026.htm",
    )
    assert spec.period == "2026-06"
    payload = parse_nfp_archive(NFP_HTML, expected_period="2026-06")
    assert payload["payroll_change_k"] == 57.0
    assert payload["previous_payroll_change_k"] == 129.0
    assert payload["unemployment_rate_pct"] == 4.2
    assert payload["unemployment_change_pp"] == -0.1
    assert math.isclose(
        payload["average_hourly_earnings_mom_pct"],
        (37.64/37.51-1.0)*100.0)
    assert math.isclose(
        payload["average_hourly_earnings_yoy_pct"],
        (37.64/36.36-1.0)*100.0)
    assert payload["surprise_computed"] is False


def test_archive_store_is_causal_immutable_and_does_not_touch_t0_rows():
    runtime = _Runtime()
    runtime._conn.execute(
        "CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY, captured_ts REAL, "
        "frozen_features_json TEXT)")
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-1',150.0,'{\"original\":true}')")
    store = BLSHistoricalReleaseStore(runtime)
    spec = BLSReleaseSpec(
        family="CPI", period="2025-07", published_at=100.0,
        source_url="https://www.bls.gov/news.release/archives/cpi_08122025.htm")
    result = store.ingest(spec, html=CPI_HTML,
                          payload=parse_cpi_archive(CPI_HTML, expected_period="2025-07"),
                          fetched_at=200.0)
    assert result["status"] == "STORED"
    assert store.latest_admissible("CPI", 99.999)["status"] == "UNAVAILABLE"
    valid = store.latest_admissible("CPI", 100.0)
    assert valid["status"] == "VALID"
    assert valid["published_at"] == 100.0
    assert valid["historical_reconstruction"] is True
    frozen = runtime._conn.execute(
        "SELECT frozen_features_json FROM g1s_observations WHERE observation_id='obs-1'").fetchone()[0]
    assert frozen == '{"original":true}'
    with pytest.raises(sqlite3.DatabaseError):
        runtime._conn.execute(
            "UPDATE macro_bls_historical_releases SET period='2099-01'")


def test_historical_feature_overlay_reuses_release_id_across_repeated_t0():
    runtime = _Runtime()
    store = BLSHistoricalReleaseStore(runtime)
    spec = BLSReleaseSpec(
        family="NFP", period="2026-06", published_at=100.0,
        source_url="https://www.bls.gov/news.release/archives/empsit_07022026.htm")
    stored = store.ingest(
        spec, html=NFP_HTML,
        payload=parse_nfp_archive(NFP_HTML, expected_period="2026-06"),
        fetched_at=200.0)

    before, _ = historical_feature_records_from_runtime(
        runtime, instrument="NAS100", t0=99.0, horizon=30)
    assert "macro.nfp_payroll_change_k" not in before

    first, first_provenance = historical_feature_records_from_runtime(
        runtime, instrument="NAS100", t0=101.0, horizon=30)
    later, later_provenance = historical_feature_records_from_runtime(
        runtime, instrument="NAS100", t0=500.0, horizon=30)
    assert first["macro.nfp_payroll_change_k"].value == 57.0
    assert first["macro.nfp_payroll_change_k"].asof == 100.0
    assert first_provenance["macro.nfp_payroll_change_k"]["release_id"] == stored["release_id"]
    assert later_provenance["macro.nfp_payroll_change_k"]["release_id"] == stored["release_id"]
    assert first_provenance["macro.nfp_payroll_change_k"]["old_t0_row_mutated"] is False
    assert first_provenance["macro.nfp_payroll_change_k"]["current_revised_series_backfill"] is False


def test_macro_inventory_maturity_counts_one_release_not_repeated_t0_rows():
    feature_id = "macro.cpi_headline_mom_pct"
    rows = [
        {
            "horizon_minutes": 30,
            "outcome_available": True,
            "feature_values": {
                feature_id: {
                    "training_eligible": True,
                    "release_id": "cpi-release-one",
                }
            },
        }
        for _ in range(150)
    ]

    class _Adapter:
        def rows(self, *, resolved_only=False, strict=False,
                 horizon_minutes=None):
            assert resolved_only is False
            return [row for row in rows
                    if int(row["horizon_minutes"]) == int(horizon_minutes)]

    report = {
        "features": [{
            "feature_id": feature_id,
            "research_scope": "G1S",
            "by_horizon": {"30": {}},
            "status": "DATA_READY_RESEARCH",
            "data_maturity": "DATA_READY_RESEARCH",
            "usable_for_ede": True,
        }],
        "summary": {"g1s_insufficient_data": 0},
    }
    prospective = SimpleNamespace(HORIZONS=(30,), data_maturity=data_maturity)

    _recompute_macro_inventory_maturity(_Adapter(), report, prospective)

    horizon = report["features"][0]["by_horizon"]["30"]
    assert horizon["raw"] == 150
    assert horizon["effective"] == 1
    assert horizon["independent_release_n"] == 1
    assert horizon["repeated_t0_increases_effective_n"] is False
    assert report["features"][0]["status"] == "INSUFFICIENT_DATA"
    assert report["features"][0]["usable_for_ede"] is False
    assert report["summary"]["g1s_insufficient_data"] == 1
