import sqlite3
import threading

from seiltanzer.macro_numeric_data import (
    BLS_SERIES,
    NumericMacroStore,
    ReleaseWrite,
    build_bls_releases,
    discover_ism_report_urls,
    parse_ism_report,
    research_context,
)


def _bls_item(year, month, value):
    return {
        "year": str(year),
        "period": f"M{month:02d}",
        "periodName": "x",
        "value": str(value),
        "footnotes": [{}],
    }


def _bls_payload():
    values = {
        BLS_SERIES["cpi_headline_sa"]: [
            (2025, 7, 100.0), (2026, 5, 104.0), (2026, 6, 104.2), (2026, 7, 104.5),
        ],
        BLS_SERIES["cpi_core_sa"]: [
            (2025, 7, 100.0), (2026, 5, 103.0), (2026, 6, 103.2), (2026, 7, 103.5),
        ],
        BLS_SERIES["cpi_headline_nsa"]: [
            (2025, 7, 100.0), (2026, 7, 103.0),
        ],
        BLS_SERIES["cpi_core_nsa"]: [
            (2025, 7, 100.0), (2026, 7, 102.5),
        ],
        BLS_SERIES["nfp_level_k"]: [
            (2026, 5, 160000.0), (2026, 6, 160100.0), (2026, 7, 160250.0),
        ],
        BLS_SERIES["unemployment_rate_pct"]: [
            (2026, 6, 4.1), (2026, 7, 4.2),
        ],
        BLS_SERIES["ahe_usd"]: [
            (2025, 7, 35.0), (2026, 6, 36.0), (2026, 7, 36.18),
        ],
    }
    return {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": series_id,
                    "data": [_bls_item(y, m, v) for y, m, v in rows],
                }
                for series_id, rows in values.items()
            ]
        },
    }


def test_bls_release_math_is_deterministic_and_never_calls_previous_change_surprise():
    releases = build_bls_releases(_bls_payload())
    cpi = releases["CPI"]
    nfp = releases["NFP"]

    assert cpi["period"] == "2026-07"
    assert round(cpi["headline_yoy_pct"], 6) == 3.0
    assert round(cpi["core_yoy_pct"], 6) == 2.5
    assert cpi["consensus_available"] is False
    assert cpi["surprise_computed"] is False

    assert nfp["period"] == "2026-07"
    assert nfp["payroll_change_k"] == 150.0
    assert nfp["previous_payroll_change_k"] == 100.0
    assert round(nfp["unemployment_change_pp"], 6) == 0.1
    assert round(nfp["average_hourly_earnings_mom_pct"], 6) == 0.5
    assert nfp["consensus_available"] is False
    assert nfp["surprise_computed"] is False


def test_ism_current_report_discovery_accepts_only_official_report_paths():
    html = """
    <a href='/supply-management-news-and-reports/reports/ism-pmi-reports/pmi/july/'>View Report</a>
    <a href='https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/services/july/'>View Report</a>
    """
    urls = discover_ism_report_urls(html)
    assert urls["ISM_MANUFACTURING"].startswith("https://www.ismworld.org/")
    assert "/pmi/july/" in urls["ISM_MANUFACTURING"]
    assert "/services/july/" in urls["ISM_SERVICES"]


def test_ism_table_parser_extracts_headline_and_components_without_llm():
    html = """
    <html><h1>July 2026 ISM Manufacturing PMI Report</h1><table>
      <tr><th>Index</th><th>Series Index Jul</th><th>Series Index Jun</th><th>Percentage Point Change</th></tr>
      <tr><td>Manufacturing PMI®</td><td>55.6</td><td>53.3</td><td>+2.3</td></tr>
      <tr><td>New Orders</td><td>56.7</td><td>56.0</td><td>+0.7</td></tr>
      <tr><td>Production</td><td>58.5</td><td>52.2</td><td>+6.3</td></tr>
      <tr><td>Employment</td><td>52.8</td><td>49.7</td><td>+3.1</td></tr>
      <tr><td>Prices</td><td>71.1</td><td>73.0</td><td>-1.9</td></tr>
    </table></html>
    """
    report = parse_ism_report(
        html,
        "ISM_MANUFACTURING",
        "https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/pmi/july/",
    )
    assert report["period"] == "2026-07"
    assert report["metrics"]["pmi"] == {"current": 55.6, "previous": 53.3, "change_pp": 2.3}
    assert report["metrics"]["prices"]["change_pp"] == -1.9
    assert report["metrics"]["production"]["current"] == 58.5
    assert report["surprise_computed"] is False


class _Runtime:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._lock = threading.RLock()


def test_numeric_store_is_first_seen_causal_and_never_backfills_before_fetch():
    runtime = _Runtime()
    store = NumericMacroStore(runtime)
    write = ReleaseWrite(
        family="CPI",
        period="2026-07",
        source="U.S. Bureau of Labor Statistics",
        source_url="https://api.bls.gov/publicAPI/v2/timeseries/data/",
        fetched_at=1000.0,
        payload={"period": "2026-07", "headline_mom_pct": 0.2},
    )
    stored = store.ingest(write)
    assert stored["status"] == "STORED"
    assert store.latest_admissible("CPI", 999.999)["status"] == "UNAVAILABLE"
    valid = store.latest_admissible("CPI", 1000.0)
    assert valid["status"] == "VALID"
    assert valid["available_at"] == 1000.0
    # Same official payload does not move first-seen available_at forward.
    cached = store.ingest(write)
    assert cached["status"] == "CACHED"
    assert cached["available_at"] == 1000.0


def test_research_context_exposes_candidate_vector_but_no_production_authority():
    runtime = _Runtime()
    store = NumericMacroStore(runtime)
    store.ingest(ReleaseWrite(
        family="CPI", period="2026-07", source="U.S. Bureau of Labor Statistics",
        source_url="https://api.bls.gov/publicAPI/v2/timeseries/data/", fetched_at=1000.0,
        payload={
            "period": "2026-07", "headline_mom_pct": 0.2, "core_mom_pct": 0.3,
            "headline_yoy_pct": 2.9, "core_yoy_pct": 3.1,
        },
    ))
    context = research_context(store, 1001.0)
    assert context["candidate_vector"]["macro.cpi_headline_mom_pct"] == 0.2
    assert context["candidate_vector"]["macro.cpi_core_yoy_pct"] == 3.1
    assert context["current_ml_feature_vector_reads_numeric_macro"] is False
    assert context["historical_backfill_allowed"] is False
    assert context["production_authority"] is False
