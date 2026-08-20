import math
import sqlite3
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from seiltanzer.macro_fomc_deterministic_bootstrap import (
    FOMCStatementSpec,
    deterministic_statement_payload,
    feature_records_from_runtime,
    parse_fomc_index,
    parse_release_timestamp,
)
from seiltanzer.macro_fomc_deterministic_store_refinement import (
    StrictFOMCDeterministicReleaseStore,
)
from seiltanzer.macro_t0_context import build_macro_t0_context


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.RLock()


INDEX_HTML = """
<html><body>
<a href="/newsevents/pressreleases/monetary20260318a.htm">March statement</a>
<a href="/newsevents/pressreleases/monetary20260429a.htm">April statement</a>
</body></html>
"""

PREVIOUS_HTML = """
<html><body>
<div>For release at 2:00 p.m. EDT</div>
<p>The Federal Open Market Committee decided to maintain the target range for the federal funds rate at 4-1/2 to 4-3/4 percent.</p>
<p>Voting for the monetary policy action were Alice Alpha; Bob Beta; Carol Gamma; and Dan Delta.</p>
<p>For media inquiries, call the Board.</p>
</body></html>
"""

CURRENT_HTML = """
<html><body>
<div>For release at 2:00 p.m. EDT</div>
<p>The Federal Open Market Committee decided to lower the target range for the federal funds rate at 4-1/4 to 4-1/2 percent.</p>
<p>Voting for the monetary policy action were Alice Alpha; Bob Beta; Carol Gamma; and Dan Delta. Voting against this action was Eve Epsilon.</p>
<p>For media inquiries, call the Board.</p>
</body></html>
"""


def _spec(date_code):
    return FOMCStatementSpec(
        date_code=date_code,
        source_url=(
            "https://www.federalreserve.gov/newsevents/pressreleases/"
            f"monetary{date_code}a.htm"
        ),
    )


def test_index_discovers_official_statement_urls_oldest_first():
    specs = parse_fomc_index(INDEX_HTML)
    assert [item.date_code for item in specs] == ["20260318", "20260429"]
    assert all(item.source_url.startswith("https://www.federalreserve.gov/") for item in specs)


def test_release_time_comes_from_statement_page_and_respects_dst():
    actual = parse_release_timestamp(CURRENT_HTML, date_code="20260429")
    expected = datetime(
        2026, 4, 29, 14, 0, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    assert actual == expected

    wrong_zone = CURRENT_HTML.replace("EDT", "EST")
    with pytest.raises(ValueError, match="FOMC_RELEASE_TIMEZONE_MISMATCH"):
        parse_release_timestamp(wrong_zone, date_code="20260429")


def test_deterministic_payload_uses_only_current_and_previous_statement():
    previous = (
        "The Federal Open Market Committee decided to maintain the target range "
        "for the federal funds rate at 4-1/2 to 4-3/4 percent. "
        "Voting for the monetary policy action were Alice; Bob; Carol; and Dan."
    )
    current = (
        "The Federal Open Market Committee decided to lower the target range "
        "for the federal funds rate at 4-1/4 to 4-1/2 percent. "
        "Voting for the monetary policy action were Alice; Bob; Carol; and Dan. "
        "Voting against this action was Eve."
    )
    payload = deterministic_statement_payload(current, previous_body=previous)

    assert payload["target_lower_pct"] == 4.25
    assert payload["target_upper_pct"] == 4.5
    assert payload["target_mid_pct"] == 4.375
    assert payload["target_width_bp"] == 25.0
    assert payload["target_change_bp"] == -25.0
    assert payload["dissent_share"] == 0.2
    assert 0.0 < payload["statement_change"] < 1.0
    assert payload["llm_used"] is False
    assert payload["market_data_used"] is False
    assert payload["future_document_used"] is False


def test_current_release_is_not_immutably_stored_without_known_previous():
    runtime = _Runtime()
    store = StrictFOMCDeterministicReleaseStore(runtime)

    with pytest.raises(ValueError, match="FOMC_PREVIOUS_RELEASE_MISSING"):
        store.ingest(
            _spec("20260429"), html=CURRENT_HTML,
            previous_source_url=_spec("20260318").source_url,
            fetched_at=2_000_000_000.0,
        )

    count = runtime._conn.execute(
        "SELECT COUNT(*) FROM macro_fomc_deterministic_releases"
    ).fetchone()[0]
    assert count == 0


def test_store_is_causal_immutable_and_derivatives_use_exact_previous_release():
    runtime = _Runtime()
    runtime._conn.execute(
        "CREATE TABLE g1s_observations(observation_id TEXT PRIMARY KEY, captured_ts REAL, "
        "frozen_features_json TEXT)")
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES('obs-1',200.0,'{\"original\":true}')")
    store = StrictFOMCDeterministicReleaseStore(runtime)

    first = store.ingest(
        _spec("20260318"), html=PREVIOUS_HTML, fetched_at=2_000_000_000.0)
    second = store.ingest(
        _spec("20260429"), html=CURRENT_HTML,
        previous_source_url=_spec("20260318").source_url,
        fetched_at=2_000_000_000.0,
    )
    assert first["status"] == "STORED"
    assert second["previous_release_id"] == first["release_id"]

    published = second["published_at"]
    assert store.latest_admissible(published-0.001)["release_id"] == first["release_id"]
    current = store.latest_admissible(published)
    assert current["release_id"] == second["release_id"]
    assert current["payload"]["target_change_bp"] == -25.0
    assert current["payload"]["dissent_share"] == 0.2
    assert current["payload"]["llm_used"] is False

    frozen = runtime._conn.execute(
        "SELECT frozen_features_json FROM g1s_observations WHERE observation_id='obs-1'"
    ).fetchone()[0]
    assert frozen == '{"original":true}'
    with pytest.raises(sqlite3.DatabaseError):
        runtime._conn.execute(
            "UPDATE macro_fomc_deterministic_releases SET date_code='20990101'")


def test_repeated_t0_rows_reuse_same_fomc_release_id():
    runtime = _Runtime()
    store = StrictFOMCDeterministicReleaseStore(runtime)
    first = store.ingest(
        _spec("20260318"), html=PREVIOUS_HTML, fetched_at=2_000_000_000.0)
    second = store.ingest(
        _spec("20260429"), html=CURRENT_HTML,
        previous_source_url=_spec("20260318").source_url,
        fetched_at=2_000_000_000.0,
    )
    t0 = second["published_at"] + 1.0

    values_a, provenance_a = feature_records_from_runtime(
        runtime, instrument="NAS100", t0=t0, horizon=30)
    values_b, provenance_b = feature_records_from_runtime(
        runtime, instrument="NAS100", t0=t0+3600.0, horizon=30)

    feature_id = "macro.fomc_target_change_bp"
    assert values_a[feature_id].value == -25.0
    assert values_a[feature_id].dependency_group == "macro_release:FOMC_STATEMENT"
    assert provenance_a[feature_id]["release_id"] == second["release_id"]
    assert provenance_b[feature_id]["release_id"] == second["release_id"]
    assert provenance_a[feature_id]["llm_used"] is False


def test_macro_t0_context_freezes_deterministic_fomc_without_historical_llm_backfill():
    release_ts = 100.0

    class _Store:
        def latest_admissible(self, captured_ts):
            assert captured_ts == 101.0
            return {
                "status": "VALID",
                "release_id": "fomc-det-1",
                "date_code": "20260429",
                "source": "Federal Reserve Board",
                "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260429a.htm",
                "official_source_verified": True,
                "published_at": release_ts,
                "available_at": release_ts,
                "body_sha256": "abc",
                "previous_release_id": "fomc-det-0",
                "payload": {
                    "target_mid_pct": 4.375,
                    "target_width_bp": 25.0,
                    "target_change_bp": -25.0,
                    "dissent_share": 0.2,
                    "statement_change": 0.15,
                },
                "historical_reconstruction": True,
                "source_vintage_guarantee": "OFFICIAL_DATED_PAGE_NOT_VERSIONED",
            }

    class _Factory:
        numeric_release_store = None
        fomc_deterministic_store = _Store()

        def latest_admissible(self, captured_ts, *, family):
            # No prospective LLM semantic observation exists at this T0.
            return {"status": "UNAVAILABLE", "reason": "NO_LLM_SEMANTIC"}

    context = build_macro_t0_context(_Factory(), 101.0)

    assert context["fomc"]["available"] is False
    assert context["fomc_deterministic"]["available"] is True
    assert context["candidate_vector"]["macro.fomc_target_change_bp"] == -25.0
    assert context["candidate_vector"]["macro.fomc_dissent_share"] == 0.2
    assert "macro.fomc_policy_tone" not in context["candidate_vector"]
    assert context["fomc_deterministic"]["llm_used"] is False
    assert context["historical_backfill_allowed"] is False
