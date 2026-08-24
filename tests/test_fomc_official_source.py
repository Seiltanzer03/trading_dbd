import sqlite3
import threading
import time

from seiltanzer.fomc_official_source import (
    discover_statement_urls,
    extract_statement_text,
    refresh_latest_fomc,
)
from seiltanzer.macro_data_factory import MacroDataFactory


class FakeRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row


INDEX_HTML = """
<html><body>
<a href="/newsevents/pressreleases/monetary20260617a.htm">Federal Reserve issues FOMC statement</a>
<a href="/newsevents/pressreleases/monetary20260729a.htm">Federal Reserve issues FOMC statement</a>
<a href="/newsevents/pressreleases/monetary20260729b.htm">Other monetary release</a>
<a href="https://evil.example/monetary20260801a.htm">Not official</a>
</body></html>
"""

STATEMENT_HTML = """
<html><head><style>.x{display:none}</style><script>ignore()</script></head><body>
<h1>Federal Reserve issues FOMC statement</h1>
<p>For release at 2:00 p.m. EDT</p>
<p>The Federal Open Market Committee approved the following statement for release by a 9 – 3 vote:</p>
<p>The Committee decided to maintain the target range for the federal funds rate. Economic activity
is expanding at a solid pace, while inflation remains elevated relative to the Committee's goal.</p>
<p>The Committee will carefully assess incoming data and the balance of risks.</p>
<p>For media inquiries, please contact the Federal Reserve.</p>
<p>Implementation Note issued today</p>
</body></html>
"""


def test_fomc_index_discovery_is_official_statement_a_only_and_newest_first():
    rows = discover_statement_urls(INDEX_HTML)
    assert [row[0] for row in rows] == ["20260729", "20260617"]
    assert all(url.startswith("https://www.federalreserve.gov/") for _, url in rows)
    assert all(url.endswith("a.htm") for _, url in rows)


def test_statement_parser_extracts_policy_body_without_scripts_or_media_footer():
    text = extract_statement_text(STATEMENT_HTML)
    assert text.startswith("The Federal Open Market Committee approved")
    assert "target range" in text
    assert "ignore()" not in text
    assert "For media inquiries" not in text
    assert "Implementation Note" not in text


def test_statement_parser_keeps_conditions_paragraph_before_policy_decision():
    page = """
    <html><body><h1>Federal Reserve issues FOMC statement</h1>
    <p>For release at 2:00 p.m. EDT</p>
    <p>Recent indicators suggest that economic activity moderated.</p>
    <p>In support of its goals, the Committee decided to maintain the target
    range for the federal funds rate at 4-1/4 to 4-1/2 percent.</p>
    <p>For media inquiries, call the Board.</p></body></html>
    """
    text = extract_statement_text(page)
    assert text.startswith("Recent indicators suggest")
    assert "Committee decided to maintain" in text
    assert "Federal Reserve issues" not in text


def test_refresh_seeds_previous_text_without_second_llm_call_and_extracts_latest_once():
    runtime = FakeRuntime()
    factory = MacroDataFactory(runtime)
    now = time.time()
    older_text = (
        "The Federal Open Market Committee approved the following statement. The Committee decided "
        "to maintain the target range. Inflation remains elevated and activity remains solid. "
        "The Committee will continue to assess incoming information and the balance of risks."
    )
    newer_text = older_text + " The vote and forward-guidance wording changed at this meeting."
    docs = [
        {
            "family": "FOMC_STATEMENT", "source": "Federal Reserve Board",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm",
            "published_at": now - 100, "fetched_at": now - 10,
            "text": newer_text, "numeric": {}, "official_source_verified": True,
        },
        {
            "family": "FOMC_STATEMENT", "source": "Federal Reserve Board",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm",
            "published_at": now - 10000, "fetched_at": now - 10,
            "text": older_text, "numeric": {}, "official_source_verified": True,
        },
    ]
    calls = []

    def fetcher(*, limit):
        assert limit == 2
        return docs

    def extractor(current, previous, model):
        calls.append((current, previous, model))
        assert "forward-guidance wording changed" in current
        assert previous is not None and "balance of risks" in previous
        return {
            "policy_tone": 0.3,
            "policy_shift": 0.2,
            "inflation_concern": 0.7,
            "growth_concern": 0.2,
            "forward_guidance_shift": 0.15,
            "uncertainty": 0.4,
        }

    result = refresh_latest_fomc(factory, fetcher=fetcher, extractor=extractor)

    assert result["status"] == "VALID"
    assert result["official_source_verified"] is True
    assert result["previous_statement_seeded_as_reference_only"] is True
    assert result["semantic"]["policy_shift"] == 0.2
    assert len(calls) == 1
    with runtime._lock:
        document_n = runtime._conn.execute("SELECT COUNT(*) FROM macro_documents").fetchone()[0]
        extraction_n = runtime._conn.execute("SELECT COUNT(*) FROM macro_extractions").fetchone()[0]
    assert document_n == 2
    assert extraction_n == 1
