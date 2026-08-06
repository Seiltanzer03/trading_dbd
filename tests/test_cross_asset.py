from seiltanzer.core.cross_asset import compute_correlation_graph


def test_cross_asset_empty_is_honest_no_data():
    res = compute_correlation_graph()
    assert res["available"] is False
    assert res["nodes"] == []
    assert res["links"] == []
    assert res["summary"]["authority"] == "correlation_family"
    assert res["summary"]["independent_vote"] is False


def test_cross_asset_uses_actual_assets_and_break_alert():
    corr = {
        "assets": ["NAS", "VXN"],
        "asof": 10_000.0,
        "matrix_baseline": [[1.0, -0.75], [-0.75, 1.0]],
        "matrix_short": [[1.0, -0.20], [-0.20, 1.0]],
        "matrix_delta": [[0.0, 0.55], [0.55, 0.0]],
    }
    res = compute_correlation_graph(corr)
    assert res["available"] is True
    assert {n["id"] for n in res["nodes"]} == {"NAS", "VXN"}
    assert len(res["links"]) == 1
    link = res["links"][0]
    assert link["source"] == "NAS"
    assert link["target"] == "VXN"
    assert link["correlation"] == -0.2
    assert link["baseline"] == -0.75
    assert link["delta_baseline"] == 0.55
    assert link["delta_5m"] is None
    assert res["summary"]["active_breaks_count"] == 1
    assert res["summary"]["regime"] == "CORRELATION BREAKDOWN"


def test_cross_asset_velocity_requires_real_previous_sample():
    previous = {
        "assets": ["NAS", "SP500"],
        "asof": 10_000.0,
        "matrix_short": [[1.0, 0.90], [0.90, 1.0]],
    }
    current = {
        "assets": ["NAS", "SP500"],
        "asof": 10_300.0,
        "matrix_baseline": [[1.0, 0.85], [0.85, 1.0]],
        "matrix_short": [[1.0, 0.72], [0.72, 1.0]],
        "matrix_delta": [[0.0, -0.13], [-0.13, 0.0]],
    }
    res = compute_correlation_graph(current, history=[previous, current])
    assert res["available"] is True
    assert res["links"][0]["delta_5m"] == -0.18
    assert res["summary"]["velocity_ready"] is True
    # No fabricated BTC/GOLD/etc. nodes may appear.
    assert {n["id"] for n in res["nodes"]} == {"NAS", "SP500"}
