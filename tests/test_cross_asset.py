import pytest
from seiltanzer.core.cross_asset import compute_correlation_graph


def test_cross_asset_empty():
    res = compute_correlation_graph()
    assert res["available"] is True
    assert len(res["nodes"]) >= 9
    assert len(res["links"]) > 0
    assert res["summary"]["authority"] == "correlation_family"
    assert res["summary"]["independent_vote"] is False


def test_cross_asset_break_alert():
    corr = {
        "pairs": ["NAS100", "GOLD"],
        "matrix_baseline": [[1.0, 0.8], [0.8, 1.0]],
        "matrix_short": [[1.0, 0.2], [0.2, 1.0]],
        "matrix_delta": [[0.0, -0.6], [-0.6, 0.0]],  # Резкий раскорреляция (-0.6)
    }

    res = compute_correlation_graph(corr)
    assert res["available"] is True
    assert res["summary"]["active_breaks_count"] > 0
    assert len(res["break_alerts"]) > 0
    assert res["summary"]["regime"] == "CORRELATION BREAKDOWN"
