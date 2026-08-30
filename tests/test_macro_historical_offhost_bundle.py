from __future__ import annotations

import sqlite3
import threading
from copy import deepcopy

import pytest

from seiltanzer import macro_historical_offhost_bundle as offhost
from seiltanzer.macro_bls_historical_bootstrap import (
    BLSHistoricalReleaseStore,
    BLSReleaseSpec,
    parse_bls_archive_spec,
    parse_cpi_archive,
    parse_nfp_archive,
)
from seiltanzer.macro_bls_alfred_vintage import (
    SERIES as ALFRED_SERIES,
    SOURCE_KIND as ALFRED_SOURCE_KIND,
    _canonical as alfred_canonical,
    _sha as alfred_sha,
    alfred_series_url,
    build_vintage_payload,
    conservative_available_at,
    fred_calendar_url,
    parse_alfred_csv,
    parse_fred_release_calendar,
    parse_vintage_evidence,
)
from seiltanzer.macro_ism_historical_bootstrap import (
    ISM_RELEASE_CALENDAR_URL,
    ISMHistoricalReleaseStore,
    parse_ism_historical_direct_report,
    parse_ism_historical_roundup,
)
from seiltanzer.macro_fomc_deterministic_bootstrap import (
    INDEX_TEMPLATE,
    deterministic_statement_payload,
    extract_statement_text,
    parse_fomc_index,
)
from seiltanzer.macro_fomc_deterministic_store_refinement import (
    StrictFOMCDeterministicReleaseStore,
)


NOW = 1_756_000_000.0
SHA = "c" * 40
RUN_ID = "32377010025"
ATOM_CPI = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Consumer Price Index</title><id>https://www.bls.gov/feed/cpi.rss</id>
<updated>2025-08-12T07:51:00-04:00</updated>
<entry><title>Consumer Price Index - July 2025</title>
<id>https://www.bls.gov/news.release/archives/cpi_08122025.htm</id>
<published>2025-08-12T07:51:00-04:00</published>
<link rel="alternate" href="https://www.bls.gov/news.release/archives/cpi_08122025.htm" />
</entry></feed>
"""
ATOM_NFP = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Employment Situation</title><id>https://www.bls.gov/feed/empsit.rss</id>
<updated>2025-08-01T07:50:00-04:00</updated>
<entry><title>Employment Situation - July 2025</title>
<id>https://www.bls.gov/news.release/archives/empsit_08012025.htm</id>
<published>2025-08-01T07:50:00-04:00</published>
<link rel="alternate" href="https://www.bls.gov/news.release/archives/empsit_08012025.htm" />
</entry></feed>
"""
ARCHIVE_INDEX_CPI = """
<html><body><h1>Consumer Price Index Archived News Releases</h1>
<p>Data in archived news releases may have been revised in subsequent releases.</p>
<a href="/news.release/archives/cpi_08122025.htm">July 2025 CPI</a>
</body></html>
"""
ARCHIVE_INDEX_NFP = """
<html><body><h1>Employment Situation Archived News Releases</h1>
<p>Links to archive copies of The Employment Situation news releases.</p>
<a href="/news.release/archives/empsit_08012025.htm">July 2025 Employment Situation</a>
</body></html>
"""
CPI = """
<html><body><p>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Tuesday, August 12, 2025</p>
<h1>CONSUMER PRICE INDEX - JULY 2025</h1><table>
<tr><th>Item</th><th>May</th><th>Jun</th><th>Jul</th><th>12 mos.</th></tr>
<tr><td>All items</td><td>0.1</td><td>0.3</td><td>0.2</td><td>2.7</td></tr>
<tr><td>All items less food and energy</td><td>0.1</td><td>0.2</td><td>0.3</td><td>3.1</td></tr>
</table></body></html>
"""
NFP = """
<html><body><p>Transmission of material in this release is embargoed until
8:30 a.m. (ET) Friday, August 1, 2025</p>
<h1>THE EMPLOYMENT SITUATION -- JULY 2025</h1><table>
<tr><td>Total nonfarm</td><td>100</td><td>110</td><td>115</td><td>120</td></tr>
<tr><td>Unemployment rate</td><td>4.1</td><td>4.0</td><td>4.1</td><td>4.2</td><td>0.1</td></tr>
<tr><td>Average hourly earnings</td><td>30</td><td>31</td><td>32</td><td>33</td></tr>
</table></body></html>
"""
ISM_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog/2025/2025-07/"
    "report-on-business-roundup-june-manufacturing-pmi/"
)
ISM = """
<html><body><h1>Report On Business Roundup: June Manufacturing PMI</h1>
<div>July 01, 2025</div>
<p>The Manufacturing PMI for June registered 49 percent as the sector remained
in contraction and the official report described current business conditions.</p>
</body></html>
"""
ISM_SERVICES_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog/2025/2025-08/"
    "report-on-business-roundup-july-2025-services-pmi/"
)
ISM_SERVICES = """
<html><body><h1>Report On Business® Roundup: July Services PMI®</h1>
<div>August 05, 2025</div>
<p>The Services PMI® registered 50.1 percent in July, indicating expansion.
The official report described resilient business activity and continuing demand
across service industries while employment conditions remained mixed.</p>
</body></html>
"""
ISM_CALENDAR_2025 = """
<html><body><h1>Release Dates for the ISM Manufacturing and Services PMI Reports</h1>
<h2>2025 ISM PMI Reports Release Dates</h2><table>
<tr><th>Month</th><th>Manufacturing PMI</th><th>Services PMI</th></tr>
<tr><td>July 2025</td><td>1</td><td>3</td></tr>
<tr><td>August 2025</td><td>1</td><td>5</td></tr>
</table><p>Reports are issued by the official ISM business survey panels.</p>
</body></html>
"""
ISM_DIRECT_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/ism-pmi-reports/pmi/june/"
)
ISM_DIRECT = """
<html><body><h1>June 2025 ISM Manufacturing PMI Report</h1>
<p>The report was issued today by the Institute for Supply Management.</p><table>
<tr><th>Index</th><th>Jun</th><th>May</th><th>Change</th></tr>
<tr><td>Manufacturing PMI</td><td>49.0</td><td>48.5</td><td>0.5</td></tr>
<tr><td>New Orders</td><td>50.0</td><td>49.0</td><td>1.0</td></tr>
<tr><td>Production</td><td>51.0</td><td>50.0</td><td>1.0</td></tr>
</table></body></html>
"""
FOMC_INDEX = """
<html><body><h1>2025 FOMC press releases</h1>
<p>Official Federal Reserve Board archive of dated monetary policy statements.</p>
<a href="/newsevents/pressreleases/monetary20250618a.htm">June statement</a>
<a href="/newsevents/pressreleases/monetary20250730a.htm">July statement</a>
<p>This archive is maintained by the Federal Reserve Board for public research.</p>
</body></html>
"""
FOMC_PREVIOUS = """
<html><body><div>For release at 2:00 p.m. EDT</div>
<p>The Federal Open Market Committee decided to maintain the target range for
the federal funds rate at 4-1/4 to 4-1/2 percent.</p>
<p>Voting for the monetary policy action were Alice Alpha; Bob Beta; Carol Gamma;
and Dan Delta. The decision was unanimous.</p>
<p>For media inquiries, call the Board.</p></body></html>
"""
FOMC_CURRENT = """
<html><body><div>For release at 2:00 p.m. EDT</div>
<p>The Federal Open Market Committee decided to lower the target range for the
federal funds rate at 4 to 4-1/4 percent.</p>
<p>Voting for the monetary policy action were Alice Alpha; Bob Beta; Carol Gamma;
and Dan Delta. Voting against this action was Eve Epsilon.</p>
<p>For media inquiries, call the Board.</p></body></html>
"""


class Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.execute(
            "CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY, "
            "captured_ts REAL, frozen_features_json TEXT)"
        )


def _record(value):
    return offhost._record(value)


def _bundle():
    spec = parse_bls_archive_spec(
        CPI, family="CPI",
        source_url="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    cpi_payload = parse_cpi_archive(CPI, expected_period="2025-07")
    nfp_spec = parse_bls_archive_spec(
        NFP, family="NFP",
        source_url="https://www.bls.gov/news.release/archives/empsit_08012025.htm",
    )
    nfp_payload = parse_nfp_archive(NFP, expected_period="2025-07")
    ism_payload = parse_ism_historical_roundup(
        ISM, family="ISM_MANUFACTURING", period="2025-06", source_url=ISM_URL
    )
    ism_services_payload = parse_ism_historical_roundup(
        ISM_SERVICES, family="ISM_SERVICES", period="2025-07",
        source_url=ISM_SERVICES_URL,
    )
    fomc_specs = parse_fomc_index(FOMC_INDEX)
    previous_body = extract_statement_text(FOMC_PREVIOUS)
    fomc_html = [FOMC_PREVIOUS, FOMC_CURRENT]
    fomc_payloads = [
        deterministic_statement_payload(previous_body),
        deterministic_statement_payload(
            extract_statement_text(FOMC_CURRENT), previous_body=previous_body),
    ]
    bundle = {
        "contract_version": offhost.CONTRACT_VERSION,
        "expected_sha": SHA,
        "acceptance_run_id": RUN_ID,
        "created_at": NOW - 10.0,
        "window": {"start_ts": NOW - 120 * 86400.0, "end_ts": NOW, "days": 120},
        "bls_schedules": {
            "CPI": _record({
                "format": "HTML_ARCHIVE_INDEX", "family": "CPI",
                "source_url": "https://www.bls.gov/bls/news-release/cpi.htm",
                "content": ARCHIVE_INDEX_CPI,
                "source_sha256": offhost._sha256(ARCHIVE_INDEX_CPI),
            }),
            "NFP": _record({
                "format": "HTML_ARCHIVE_INDEX", "family": "NFP",
                "source_url": "https://www.bls.gov/bls/news-release/empsit.htm",
                "content": ARCHIVE_INDEX_NFP,
                "source_sha256": offhost._sha256(ARCHIVE_INDEX_NFP),
            }),
        },
        "bls_records": [
            _record({
                "spec": {
                    "family": spec.family, "period": spec.period,
                    "published_at": spec.published_at, "source_url": spec.source_url,
                },
                "fetched_at": NOW - 10.0, "html": CPI,
                "source_sha256": offhost._sha256(CPI), "payload": cpi_payload,
            }),
            _record({
                "spec": {
                    "family": nfp_spec.family, "period": nfp_spec.period,
                    "published_at": nfp_spec.published_at,
                    "source_url": nfp_spec.source_url,
                },
                "fetched_at": NOW - 10.0, "html": NFP,
                "source_sha256": offhost._sha256(NFP), "payload": nfp_payload,
            }),
        ],
        "ism_schedules": {},
        "ism_records": [
            _record({
                "family": "ISM_MANUFACTURING", "period": "2025-06",
                "source_url": ISM_URL, "fetched_at": NOW - 10.0,
                "html": ISM, "source_sha256": offhost._sha256(ISM),
                "payload": ism_payload,
            }),
            _record({
                "family": "ISM_SERVICES", "period": "2025-07",
                "source_url": ISM_SERVICES_URL, "fetched_at": NOW - 10.0,
                "html": ISM_SERVICES,
                "source_sha256": offhost._sha256(ISM_SERVICES),
                "payload": ism_services_payload,
            }),
        ],
        "fomc_window": {
            "start_ts": NOW - offhost.FOMC_WINDOW_DAYS * 86400.0,
            "context_start_ts": NOW - offhost.FOMC_WINDOW_DAYS * 86400.0,
            "end_ts": NOW,
            "days": offhost.FOMC_WINDOW_DAYS,
        },
        "fomc_schedules": {"2025": _record({
            "format": "HTML", "year": 2025,
            "source_url": INDEX_TEMPLATE.format(year=2025),
            "content": FOMC_INDEX,
            "source_sha256": offhost._sha256(FOMC_INDEX),
        })},
        "fomc_records": [
            _record({
                "spec": {
                    "date_code": spec.date_code,
                    "source_url": spec.source_url,
                },
                "previous_source_url": (
                    fomc_specs[index - 1].source_url if index else None
                ),
                "fetched_at": NOW - 10.0,
                "html": fomc_html[index],
                "source_sha256": offhost._sha256(fomc_html[index]),
                "payload": fomc_payloads[index],
            })
            for index, spec in enumerate(fomc_specs)
        ],
        "errors": {},
        "historical_availability": {
            "CPI": "VERIFIED_REAL_HISTORY",
            "NFP": "VERIFIED_REAL_HISTORY",
            "ISM_MANUFACTURING": "VERIFIED_REAL_HISTORY",
            "ISM_SERVICES": "VERIFIED_REAL_HISTORY",
            "FOMC_STATEMENT_DETERMINISTIC": "VERIFIED_REAL_HISTORY",
        },
        "missing_is_zero": False,
        "official_sources_only": True,
        "canonical_parsers_only": True, "synthetic_data_used": False,
        "no_placeholders": True, "research_only": True,
        "production_authority": False,
    }
    bundle["bundle_sha256"] = offhost._sha256(bundle)
    return bundle


def test_historical_bundle_reparses_every_official_page_and_binds_owner(monkeypatch):
    monkeypatch.delenv("MACRO_HISTORICAL_OFFHOST_MAX_AGE_SEC", raising=False)
    bundle = _bundle()
    assert offhost.validate_bundle(
        bundle, expected_sha=SHA, acceptance_run_id=RUN_ID, now=NOW
    ) is bundle
    changed = deepcopy(bundle)
    changed["bls_records"][0]["payload"]["headline_mom_pct"] = 99.0
    changed["bls_records"][0]["record_sha256"] = offhost._sha256(
        offhost._without(changed["bls_records"][0], "record_sha256")
    )
    changed["bundle_sha256"] = offhost._sha256(
        offhost._without(changed, "bundle_sha256")
    )
    with pytest.raises(ValueError, match="BLS_PAYLOAD_MISMATCH"):
        offhost.validate_bundle(changed, expected_sha=SHA, now=NOW)

    changed = deepcopy(bundle)
    changed["fomc_records"][1]["payload"]["target_change_bp"] = 999.0
    changed["fomc_records"][1]["record_sha256"] = offhost._sha256(
        offhost._without(changed["fomc_records"][1], "record_sha256")
    )
    changed["bundle_sha256"] = offhost._sha256(
        offhost._without(changed, "bundle_sha256")
    )
    with pytest.raises(ValueError, match="FOMC_PAYLOAD_MISMATCH"):
        offhost.validate_bundle(changed, expected_sha=SHA, now=NOW)


def test_historical_bundle_accepts_direct_ism_only_with_hashed_official_calendar():
    bundle = _bundle()
    payload = parse_ism_historical_direct_report(
        ISM_DIRECT,
        family="ISM_MANUFACTURING",
        period="2025-06",
        source_url=ISM_DIRECT_URL,
        calendar_html=ISM_CALENDAR_2025,
    )
    bundle["ism_schedules"] = {"2025": _record({
        "format": "HTML_ISM_RELEASE_CALENDAR",
        "year": 2025,
        "source_url": ISM_RELEASE_CALENDAR_URL,
        "content": ISM_CALENDAR_2025,
        "source_sha256": offhost._sha256(ISM_CALENDAR_2025),
    })}
    bundle["ism_records"][0] = _record({
        "family": "ISM_MANUFACTURING",
        "period": "2025-06",
        "source_url": ISM_DIRECT_URL,
        "fetched_at": NOW - 10.0,
        "html": ISM_DIRECT,
        "source_sha256": offhost._sha256(ISM_DIRECT),
        "payload": payload,
    })
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256")
    )
    assert offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW) is bundle

    changed = deepcopy(bundle)
    calendar = changed["ism_schedules"]["2025"]
    calendar["content"] = calendar["content"].replace(
        "<td>1</td><td>3</td>", "<td>2</td><td>3</td>"
    )
    calendar["source_sha256"] = offhost._sha256(calendar["content"])
    calendar["record_sha256"] = offhost._sha256(
        offhost._without(calendar, "record_sha256")
    )
    changed["bundle_sha256"] = offhost._sha256(
        offhost._without(changed, "bundle_sha256")
    )
    with pytest.raises(ValueError, match="ISM_PAYLOAD_MISMATCH"):
        offhost.validate_bundle(changed, expected_sha=SHA, now=NOW)


def test_bls_manifest_falls_back_to_official_atom_and_validates():
    class Source:
        @staticmethod
        def archive_index_manifest(_family):
            raise PermissionError("403 Forbidden")

        @staticmethod
        def atom_manifest(family):
            assert family == "CPI"
            return ATOM_CPI, [
                "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
            ]

    manifest, links, error = offhost._fetch_bls_manifest(
        Source(),
        family="CPI",
        archive_index_url="https://www.bls.gov/bls/news-release/cpi.htm",
        atom_url="https://www.bls.gov/feed/cpi.rss",
    )
    assert error is None
    assert links == [
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm"
    ]
    assert manifest["format"] == "ATOM_ARCHIVE_MANIFEST"

    bundle = _bundle()
    bundle["bls_schedules"]["CPI"] = manifest
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256")
    )
    assert offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW) is bundle


def test_bls_atom_manifest_rejects_foreign_archive_link():
    bundle = _bundle()
    foreign = ATOM_CPI.replace(
        "https://www.bls.gov/news.release/archives/cpi_08122025.htm",
        "https://example.com/news.release/archives/cpi_08122025.htm",
    )
    bundle["bls_schedules"]["CPI"] = _record({
        "format": "ATOM_ARCHIVE_MANIFEST", "family": "CPI",
        "source_url": "https://www.bls.gov/feed/cpi.rss",
        "content": foreign,
        "source_sha256": offhost._sha256(foreign),
    })
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256")
    )
    with pytest.raises(ValueError, match="BLS_ATOM_ARCHIVE_LINK_INVALID"):
        offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW)


def test_bls_manifest_dual_failure_remains_explicit_partial():
    class Source:
        @staticmethod
        def archive_index_manifest(_family):
            raise PermissionError("403 Forbidden")

        @staticmethod
        def atom_manifest(_family):
            raise ConnectionError("feed unavailable")

    manifest, links, error = offhost._fetch_bls_manifest(
        Source(),
        family="NFP",
        archive_index_url="https://www.bls.gov/bls/news-release/empsit.htm",
        atom_url="https://www.bls.gov/feed/empsit.rss",
    )
    assert manifest is None
    assert links == []
    assert "archive_index=PermissionError:403 Forbidden" in error
    assert "atom=ConnectionError:feed unavailable" in error


def test_historical_offhost_materializes_real_rows_without_network(monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(offhost, "load_verified_bundle", lambda _runtime: bundle)
    monkeypatch.setattr(offhost.time, "time", lambda: NOW)

    bls_runtime = Runtime()
    spec = parse_bls_archive_spec(
        CPI, family="CPI",
        source_url="https://www.bls.gov/news.release/archives/cpi_08122025.htm",
    )
    bls_runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-bls',?,'{}')",
        (spec.published_at + 60.0,),
    )
    bls_wrapper = type("Wrapper", (), {})()
    bls_wrapper.store = BLSHistoricalReleaseStore(bls_runtime)
    bls = offhost._bls_refresh(bls_wrapper)
    assert bls["status"] == "OK"
    assert bls_wrapper.store.status()["families"]["CPI"]["row_n"] == 1

    ism_runtime = Runtime()
    ism_published = bundle["ism_records"][0]["payload"]["published_at"]
    ism_runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-ism',?,'{}')",
        (ism_published + 60.0,),
    )
    ism_wrapper = type("Wrapper", (), {})()
    ism_wrapper.store = ISMHistoricalReleaseStore(ism_runtime)
    ism = offhost._ism_refresh(ism_wrapper)
    assert ism["status"] == "OK"
    assert ism_wrapper.store.status()["families"]["ISM_MANUFACTURING"]["row_n"] == 1
    assert all(row.get("status") in {"STORED", "CACHED"} for row in ism["stored"])

    fomc_runtime = Runtime()
    fomc_runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-fomc',?,'{}')",
        (NOW - 60.0,),
    )
    fomc_wrapper = type("Wrapper", (), {})()
    fomc_wrapper.store = StrictFOMCDeterministicReleaseStore(fomc_runtime)
    fomc = offhost._fomc_refresh(fomc_wrapper)
    assert fomc["status"] == "OK"
    assert fomc_wrapper.store.status()["row_n"] == 2
    latest = fomc_wrapper.store.latest_admissible(NOW)
    assert latest["payload"]["target_change_bp"] == -25.0
    assert latest["payload"]["llm_used"] is False


def test_verified_partial_bundle_materializes_fomc_and_marks_blocked_sources(monkeypatch):
    bundle = _bundle()
    bundle["bls_schedules"] = {}
    bundle["bls_records"] = []
    bundle["ism_records"] = []
    bundle["errors"] = {
        "BLS:manifest:CPI": "HTTPStatusError:403 Forbidden",
        "BLS:manifest:NFP": "HTTPStatusError:403 Forbidden",
        "ISM:ISM_MANUFACTURING:2025-06": "RuntimeError:redirect rejected",
        "ISM:ISM_SERVICES:2025-07": "RuntimeError:redirect rejected",
    }
    bundle["historical_availability"] = {
        "CPI": "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED",
        "NFP": "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED",
        "ISM_MANUFACTURING": "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED",
        "ISM_SERVICES": "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED",
        "FOMC_STATEMENT_DETERMINISTIC": "VERIFIED_REAL_HISTORY",
    }
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256")
    )
    assert offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW) is bundle

    monkeypatch.setattr(offhost, "load_verified_bundle", lambda _runtime: bundle)
    monkeypatch.setattr(offhost.time, "time", lambda: NOW)
    bls_runtime = Runtime()
    bls_runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-bls-partial',?,'{}')",
        (NOW - 60.0,),
    )
    bls_wrapper = type("Wrapper", (), {})()
    bls_wrapper.store = BLSHistoricalReleaseStore(bls_runtime)
    bls_result = offhost._bls_refresh(bls_wrapper)
    assert bls_result["status"] == "PARTIAL"
    assert bls_result["stored"] == []
    assert all(
        value == "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED"
        for value in bls_result["historical_availability"].values()
    )

    ism_runtime = Runtime()
    ism_runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-ism-partial',?,'{}')",
        (NOW - 60.0,),
    )
    ism_wrapper = type("Wrapper", (), {})()
    ism_wrapper.store = ISMHistoricalReleaseStore(ism_runtime)
    ism_result = offhost._ism_refresh(ism_wrapper)
    assert ism_result["status"] == "PARTIAL"
    assert ism_result["stored"] == []
    assert all(
        value == "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED"
        for value in ism_result["historical_availability"].values()
    )

    fomc_runtime = Runtime()
    fomc_runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-fomc-partial',?,'{}')",
        (NOW - 60.0,),
    )
    wrapper = type("Wrapper", (), {})()
    wrapper.store = StrictFOMCDeterministicReleaseStore(fomc_runtime)
    result = offhost._fomc_refresh(wrapper)
    assert result["status"] == "OK"
    assert wrapper.store.status()["row_n"] == 2


def test_partial_bundle_rejects_silent_missing_family():
    bundle = _bundle()
    bundle["bls_schedules"].pop("CPI")
    bundle["bls_records"] = [
        row for row in bundle["bls_records"]
        if row["spec"]["family"] != "CPI"
    ]
    bundle["historical_availability"]["CPI"] = (
        "OFFICIAL_SOURCE_UNAVAILABLE_PROSPECTIVE_REQUIRED"
    )
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256")
    )
    with pytest.raises(ValueError, match="BLS_SCHEDULES_MISSING"):
        offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW)


def _alfred_csv(series_id, release_date, rows):
    return "\n".join([
        f"observation_date,{series_id}_{release_date.replace('-', '')}",
        *(f"{period}-01,{value}" for period, value in rows.items()),
        "",
    ])


def test_historical_bundle_accepts_hashed_official_alfred_vintage():
    family, release_date, period = "CPI", "2025-08-12", "2025-07"
    calendar_url = fred_calendar_url(
        family, start_date="2025-04-01", end_date="2025-08-24")
    calendar = '{"events":[{"title":"1 release","start":"2025-08-12"}]}'
    assert parse_fred_release_calendar(
        calendar, family=family, source_url=calendar_url) == [release_date]
    raw_values = {
        "CPIAUCSL": {"2025-05": 100.0, "2025-06": 101.0, "2025-07": 102.0},
        "CPILFESL": {"2025-05": 200.0, "2025-06": 202.0, "2025-07": 204.0},
        "CPIAUCNS": {"2024-07": 100.0, "2025-07": 103.0},
        "CPILFENS": {"2024-07": 200.0, "2025-07": 206.0},
    }
    series = []
    parsed_values = {}
    for series_id in ALFRED_SERIES[family]:
        content = _alfred_csv(series_id, release_date, raw_values[series_id])
        parsed_values[series_id] = parse_alfred_csv(
            content, series_id=series_id, vintage_date=release_date)
        series.append({
            "series_id": series_id,
            "source_url": alfred_series_url(
                series_id, period=period, vintage_date=release_date),
            "content": content, "source_sha256": alfred_sha(content),
        })
    raw = alfred_canonical({
        "source_kind": ALFRED_SOURCE_KIND, "family": family,
        "release_date": release_date, "series": series,
    })
    spec = BLSReleaseSpec(
        family=family, period=period,
        published_at=conservative_available_at(release_date),
        source_url=calendar_url,
    )
    payload = build_vintage_payload(
        family=family, period=period, release_date=release_date,
        values=parsed_values,
    )
    assert parse_vintage_evidence(
        raw, spec=spec, calendar_dates={release_date}) == payload

    bundle = _bundle()
    bundle["bls_schedules"][family] = _record({
        "format": "FRED_RELEASE_CALENDAR_JSON", "family": family,
        "source_url": calendar_url, "content": calendar,
        "source_sha256": offhost._sha256(calendar),
    })
    bundle["bls_records"][0] = _record({
        "source_kind": ALFRED_SOURCE_KIND,
        "spec": {
            "family": spec.family, "period": spec.period,
            "published_at": spec.published_at, "source_url": spec.source_url,
        },
        "fetched_at": NOW - 10.0, "html": raw,
        "source_sha256": offhost._sha256(raw), "payload": payload,
    })
    bundle["bundle_sha256"] = offhost._sha256(
        offhost._without(bundle, "bundle_sha256"))
    assert offhost.validate_bundle(bundle, expected_sha=SHA, now=NOW) is bundle


def test_alfred_vintage_rejects_foreign_calendar():
    calendar = '{"events":[{"title":"1 release","start":"2025-08-12"}]}'
    with pytest.raises(ValueError, match="CALENDAR_SOURCE_INVALID"):
        parse_fred_release_calendar(
            calendar, family="CPI",
            source_url="https://example.com/releases/calendar?rdc=1&rid=10"
        )


def test_alfred_nfp_payload_uses_one_historical_vintage():
    payload = build_vintage_payload(
        family="NFP", period="2026-07", release_date="2026-08-07",
        values={
            "PAYEMS": {
                "2026-05": 158_800.0, "2026-06": 158_860.0,
                "2026-07": 158_875.0,
            },
            "UNRATE": {"2026-06": 4.2, "2026-07": 4.1},
            "CES0500000003": {
                "2025-07": 36.50, "2026-06": 37.50, "2026-07": 37.60,
            },
        },
    )
    assert payload["payroll_change_k"] == 15.0
    assert payload["previous_payroll_change_k"] == 60.0
    assert payload["unemployment_rate_pct"] == 4.1
    assert payload["unemployment_change_pp"] == pytest.approx(-0.1)
    assert payload["historical_alfred_vintage"] is True
    assert payload["source_kind"] == ALFRED_SOURCE_KIND
