import pytest

from seiltanzer.macro_ism_parser_refinement import install_ism_roundup_parser_refinement
from seiltanzer.macro_ism_resilience import parse_ism_roundup


install_ism_roundup_parser_refinement()


PREVIOUS_MFG = {
    "period": "2026-06",
    "metrics": {
        "pmi": {"current": 53.3},
        "new_orders": {"current": 56.0},
        "production": {"current": 52.2},
        "employment": {"current": 49.7},
        "prices": {"current": 73.0},
    },
}

PREVIOUS_SVC = {
    "period": "2026-06",
    "metrics": {
        "pmi": {"current": 54.0},
        "business_activity": {"current": 55.4},
        "new_orders": {"current": 55.1},
        "employment": {"current": 51.2},
        "supplier_deliveries": {"current": 54.4},
        "prices": {"current": 67.7},
    },
}


def test_manufacturing_roundup_joins_only_previous_official_values():
    html = """
    <html><h1>ISM® PMI® Reports Roundup: July Manufacturing</h1>
    <p>The ISM Manufacturing PMI Report for July showed firm activity.</p>
    <p>The composite PMI registered 55.6 percent.</p>
    <p>The July data confirmed demand: The New Orders (56.7 percent) index increased.</p>
    <p>The Production Index registered 58.5 percent.</p>
    <p>The Employment Index registered 52.8 percent.</p>
    <p>The Prices Index showed continuing cooling, down 1.9 percentage points to 71.1 percent.</p>
    </html>
    """
    report = parse_ism_roundup(
        html,
        "ISM_MANUFACTURING",
        "https://www.ismworld.org/supply-management-news-and-reports/news-publications/inside-supply-management-magazine/blog/2026/2026-08/ism-pmi-reports-roundup-july-2026-manufacturing/",
        year=2026,
        month=7,
        previous_report=PREVIOUS_MFG,
    )
    assert report["period"] == "2026-07"
    assert report["metrics"]["pmi"] == {
        "current": 55.6, "previous": 53.3, "change_pp": pytest.approx(2.3)
    }
    assert report["metrics"]["production"]["change_pp"] == pytest.approx(6.3)
    assert report["metrics"]["employment"]["change_pp"] == pytest.approx(3.1)
    assert report["metrics"]["prices"]["change_pp"] == pytest.approx(-1.9)
    assert report["consensus_available"] is False
    assert report["surprise_computed"] is False


def test_services_roundup_extracts_current_official_prose_and_previous_join():
    html = """
    <html><h1>ISM® PMI® Reports Roundup: July Services</h1>
    <p>The ISM Services PMI Report for July remained resilient.</p>
    <p>The composite PMI reading was steady, increasing 0.1 percentage point to 54.1 percent.</p>
    <p>The Supplier Deliveries Index decreased to 52.8 percent.</p>
    <p>The Employment Index (47.4 percent) reentered contraction.</p>
    <p>The Prices Index (70.3 percent) remained elevated.</p>
    <p>The Business Activity Index increased to 59.1 percent, and the New Orders Index elevated to 57.2 percent.</p>
    </html>
    """
    report = parse_ism_roundup(
        html,
        "ISM_SERVICES",
        "https://www.ismworld.org/supply-management-news-and-reports/news-publications/inside-supply-management-magazine/blog/2026/2026-08/ism-pmi-reports-roundup-july-2026-services/",
        year=2026,
        month=7,
        previous_report=PREVIOUS_SVC,
    )
    assert report["metrics"]["pmi"]["current"] == 54.1
    assert report["metrics"]["supplier_deliveries"]["change_pp"] == pytest.approx(-1.6)
    assert report["metrics"]["business_activity"]["change_pp"] == pytest.approx(3.7)
    assert report["metrics"]["new_orders"]["change_pp"] == pytest.approx(2.1)
    assert report["metrics"]["employment"]["change_pp"] == pytest.approx(-3.8)


def test_roundup_rejects_wrong_period_or_unofficial_host():
    html = """
    <h1>ISM PMI Reports Roundup: July Manufacturing</h1>
    <p>The ISM Manufacturing PMI Report for July.</p>
    <p>The composite PMI registered 55.6 percent.</p>
    <p>The New Orders (56.7 percent).</p><p>The Production Index registered 58.5 percent.</p>
    <p>The Employment Index registered 52.8 percent.</p>
    """
    with pytest.raises(ValueError):
        parse_ism_roundup(
            html, "ISM_MANUFACTURING",
            "https://example.com/ism-pmi-reports-roundup-july-2026-manufacturing/",
            year=2026, month=7, previous_report=PREVIOUS_MFG,
        )
    with pytest.raises(ValueError):
        parse_ism_roundup(
            html, "ISM_MANUFACTURING",
            "https://www.ismworld.org/supply-management-news-and-reports/news-publications/inside-supply-management-magazine/blog/2026/2026-08/ism-pmi-reports-roundup-june-2026-manufacturing/",
            year=2026, month=7, previous_report=PREVIOUS_MFG,
        )
