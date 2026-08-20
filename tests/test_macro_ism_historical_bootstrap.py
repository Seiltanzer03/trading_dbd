import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from seiltanzer.macro_ism_historical_bootstrap import (
    ISMHistoricalReleaseStore,
    _candidate_roundup_urls,
    feature_records_from_runtime,
    parse_ism_historical_roundup,
)
from seiltanzer.macro_ism_historical_ede_refinement import (
    install_ism_historical_ede_refinement,
)


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.RLock()


JUNE_MFG_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog/2025/2025-07/"
    "report-on-business-roundup-june-manufacturing-pmi/"
)
JULY_MFG_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog/2025/2025-08/"
    "report-on-business-roundup-july-2025-manufacturing-pmi/"
)
JUNE_SERVICES_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog/2025/2025-07/"
    "report-on-business-roundup-june-2025-services-pmi/"
)
JULY_SERVICES_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "news-publications/inside-supply-management-magazine/blog/2025/2025-08/"
    "report-on-business-roundup-july-2025-services-pmi/"
)

JUNE_MFG_HTML = """
<html><body>
<h1>Report On Business® Roundup: June Manufacturing PMI®</h1>
<div>July 01, 2025</div>
<p>The Manufacturing PMI® for June registered 49 percent as the sector remained in contraction.</p>
</body></html>
"""

JULY_MFG_HTML = """
<html><body>
<h1>Report On Business® Roundup: July Manufacturing PMI®</h1>
<div>August 01, 2025</div>
<p>When the Manufacturing PMI® for July registered 48 percent, the reading was below expectations.</p>
</body></html>
"""

JUNE_SERVICES_HTML = """
<html><body>
<h1>Report On Business® Roundup: June Services PMI®</h1>
<div>July 03, 2025</div>
<p>The Services PMI® registered 50.8 percent in June, returning the sector to expansion.</p>
</body></html>
"""

JULY_SERVICES_HTML = """
<html><body>
<h1>Report On Business® Roundup: July Services PMI®</h1>
<div>August 05, 2025</div>
<p>The Services PMI® registered 50.1 percent on Tuesday, coming in below expectations.</p>
</body></html>
"""


def _parse(html, *, family, period, url):
    return parse_ism_historical_roundup(
        html, family=family, period=period, source_url=url)


def test_candidate_urls_cover_pre_and_post_rebrand_official_slugs():
    urls = _candidate_roundup_urls("ISM_MANUFACTURING", "2025-07")
    assert any("ism-pmi-reports-roundup-july-2025-manufacturing" in url for url in urls)
    assert any("report-on-business-roundup-july-2025-manufacturing-pmi" in url for url in urls)
    assert all("/2025/2025-08/" in url for url in urls)


def test_manufacturing_roundup_uses_official_release_day_at_10_et():
    parsed = _parse(
        JULY_MFG_HTML, family="ISM_MANUFACTURING", period="2025-07",
        url=JULY_MFG_URL)
    expected = datetime(
        2025, 8, 1, 10, 0, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    assert parsed["pmi"] == 48.0
    assert parsed["published_at"] == expected
    assert parsed["publication_date"] == "2025-08-01"
    assert parsed["source_kind"] == "OFFICIAL_DATED_ROUNDUP_POST_RELEASE_REPRODUCTION"
    assert parsed["source_vintage_guarantee"] == "OFFICIAL_DATED_PAGE_NOT_FIRST_REPORT_HTML"
    assert parsed["llm_used"] is False


def test_services_roundup_rejects_wrong_period_or_family():
    with pytest.raises(ValueError, match="TITLE_PERIOD_FAMILY_MISMATCH"):
        _parse(
            JULY_SERVICES_HTML, family="ISM_SERVICES", period="2025-06",
            url=JULY_SERVICES_URL)
    with pytest.raises(ValueError):
        _parse(
            JULY_SERVICES_HTML, family="ISM_MANUFACTURING", period="2025-07",
            url=JULY_SERVICES_URL)


def test_store_computes_change_only_from_immediately_previous_official_roundup():
    runtime = _Runtime()
    runtime._conn.execute(
        "CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY, captured_ts REAL, "
        "frozen_features_json TEXT)")
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-1',200.0,'{\"original\":true}')")
    store = ISMHistoricalReleaseStore(runtime)

    june = store.ingest(
        _parse(JUNE_MFG_HTML, family="ISM_MANUFACTURING", period="2025-06",
               url=JUNE_MFG_URL),
        html=JUNE_MFG_HTML, fetched_at=2_000_000_000.0,
        require_previous=False,
    )
    july = store.ingest(
        _parse(JULY_MFG_HTML, family="ISM_MANUFACTURING", period="2025-07",
               url=JULY_MFG_URL),
        html=JULY_MFG_HTML, fetched_at=2_000_000_000.0,
        require_previous=True,
    )
    assert june["change_pp"] is None
    assert july["change_pp"] == -1.0

    current = store.latest_admissible("ISM_MANUFACTURING", july["published_at"])
    assert current["release_id"] == july["release_id"]
    assert current["pmi"] == 48.0
    assert current["change_pp"] == -1.0
    assert current["previous_release_id"] == june["release_id"]

    frozen = runtime._conn.execute(
        "SELECT frozen_features_json FROM g1s_observations WHERE observation_id='obs-1'"
    ).fetchone()[0]
    assert frozen == '{"original":true}'
    with pytest.raises(sqlite3.DatabaseError):
        runtime._conn.execute(
            "UPDATE macro_ism_historical_releases SET period='2099-01'")


def test_store_rejects_gap_before_immutable_change_derivative():
    runtime = _Runtime()
    store = ISMHistoricalReleaseStore(runtime)
    with pytest.raises(ValueError, match="ISM_HISTORICAL_PREVIOUS_RELEASE_MISSING"):
        store.ingest(
            _parse(JULY_SERVICES_HTML, family="ISM_SERVICES", period="2025-07",
                   url=JULY_SERVICES_URL),
            html=JULY_SERVICES_HTML, fetched_at=2_000_000_000.0,
            require_previous=True,
        )
    assert runtime._conn.execute(
        "SELECT COUNT(*) FROM macro_ism_historical_releases"
    ).fetchone()[0] == 0


def test_repeated_market_t0s_reuse_one_ism_release_id_and_never_backfill_current_page():
    runtime = _Runtime()
    store = ISMHistoricalReleaseStore(runtime)
    june = store.ingest(
        _parse(JUNE_SERVICES_HTML, family="ISM_SERVICES", period="2025-06",
               url=JUNE_SERVICES_URL),
        html=JUNE_SERVICES_HTML, fetched_at=2_000_000_000.0,
        require_previous=False,
    )
    july = store.ingest(
        _parse(JULY_SERVICES_HTML, family="ISM_SERVICES", period="2025-07",
               url=JULY_SERVICES_URL),
        html=JULY_SERVICES_HTML, fetched_at=2_000_000_000.0,
        require_previous=True,
    )
    t0 = july["published_at"] + 1.0

    first, first_prov = feature_records_from_runtime(
        runtime, instrument="NAS100", t0=t0, horizon=30)
    later, later_prov = feature_records_from_runtime(
        runtime, instrument="NAS100", t0=t0+3600.0, horizon=30)
    feature_id = "macro.ism_services_pmi_change_pp"
    assert first[feature_id].value == pytest.approx(-0.7)
    assert first[feature_id].dependency_group == "macro_release:ISM_SERVICES"
    assert first_prov[feature_id]["release_id"] == july["release_id"]
    assert later_prov[feature_id]["release_id"] == july["release_id"]
    assert first_prov[feature_id]["current_mutable_report_backfill"] is False
    assert first_prov[feature_id]["future_points_used"] is False
    assert first_prov[feature_id]["llm_used"] is False


def test_ism_registry_ids_become_historically_available_without_changing_id_universe():
    from seiltanzer.edge_discovery import registry

    before_ids = {item.feature_id for item in registry.FEATURES}
    install_ism_historical_ede_refinement()
    after_ids = {item.feature_id for item in registry.FEATURES}
    definitions = {item.feature_id: item for item in registry.FEATURES}

    assert after_ids == before_ids
    assert definitions["macro.ism_manufacturing_pmi"].historical_availability == "AVAILABLE"
    assert definitions["macro.ism_services_pmi_change_pp"].historical_availability == "AVAILABLE"
    assert "macro.ism_manufacturing_new_orders" not in after_ids
