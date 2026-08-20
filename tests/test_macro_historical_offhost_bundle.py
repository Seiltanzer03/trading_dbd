from __future__ import annotations

import sqlite3
import threading
from copy import deepcopy

import pytest

from seiltanzer import macro_historical_offhost_bundle as offhost
from seiltanzer.macro_bls_historical_bootstrap import (
    BLSHistoricalReleaseStore,
    parse_bls_ical,
    parse_cpi_archive,
)
from seiltanzer.macro_ism_historical_bootstrap import (
    ISMHistoricalReleaseStore,
    parse_ism_historical_roundup,
)


NOW = 1_756_000_000.0
SHA = "c" * 40
RUN_ID = "32377010025"
SCHEDULE_ICAL = """BEGIN:VCALENDAR
PRODID:-//Department of Labor//Bureau of Labor Statistics//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:BLS Economic News Release Calendar
BEGIN:VEVENT
UID:official-cpi-20250812@bls.gov
DTSTART;TZID=US-Eastern:20250812T083000
SUMMARY:Consumer Price Index
LOCATION:Washington, DC
CATEGORIES:IMPORTANT, BLS
END:VEVENT
END:VCALENDAR
"""
CPI = """
<html><body><h1>CONSUMER PRICE INDEX - JULY 2025</h1><table>
<tr><th>Item</th><th>May</th><th>Jun</th><th>Jul</th><th>12 mos.</th></tr>
<tr><td>All items</td><td>0.1</td><td>0.3</td><td>0.2</td><td>2.7</td></tr>
<tr><td>All items less food and energy</td><td>0.1</td><td>0.2</td><td>0.3</td><td>3.1</td></tr>
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
    spec = parse_bls_ical(SCHEDULE_ICAL)[0]
    cpi_payload = parse_cpi_archive(CPI, expected_period="2025-07")
    ism_payload = parse_ism_historical_roundup(
        ISM, family="ISM_MANUFACTURING", period="2025-06", source_url=ISM_URL
    )
    bundle = {
        "contract_version": offhost.CONTRACT_VERSION,
        "expected_sha": SHA,
        "acceptance_run_id": RUN_ID,
        "created_at": NOW - 10.0,
        "window": {"start_ts": NOW - 120 * 86400.0, "end_ts": NOW, "days": 120},
        "bls_schedules": {"official_ical": _record({
            "format": "ICAL",
            "source_url": "https://www.bls.gov/schedule/news_release/bls.ics",
            "content": SCHEDULE_ICAL,
            "source_sha256": offhost._sha256(SCHEDULE_ICAL),
        })},
        "bls_records": [_record({
            "spec": {
                "family": spec.family, "period": spec.period,
                "published_at": spec.published_at, "source_url": spec.source_url,
            },
            "fetched_at": NOW - 10.0, "html": CPI,
            "source_sha256": offhost._sha256(CPI), "payload": cpi_payload,
        })],
        "ism_records": [_record({
            "family": "ISM_MANUFACTURING", "period": "2025-06",
            "source_url": ISM_URL, "fetched_at": NOW - 10.0,
            "html": ISM, "source_sha256": offhost._sha256(ISM),
            "payload": ism_payload,
        })],
        "errors": {}, "official_sources_only": True,
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


def test_historical_offhost_materializes_real_rows_without_network(monkeypatch):
    bundle = _bundle()
    monkeypatch.setattr(offhost, "load_verified_bundle", lambda _runtime: bundle)

    bls_runtime = Runtime()
    spec = parse_bls_ical(SCHEDULE_ICAL)[0]
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
