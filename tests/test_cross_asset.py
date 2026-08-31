from seiltanzer.core.cross_asset import compute_correlation_graph, relationship_state
from seiltanzer.config import INSTRUMENTS
from seiltanzer.data.feeds import CORRELATION_CORE_ASSETS, CORRELATION_SERIES


def test_cross_asset_empty_is_honest_no_data():
    res = compute_correlation_graph()
    assert res["available"] is False
    assert res["nodes"] == []
    assert res["links"] == []
    assert res["summary"]["authority"] == "correlation_family"
    assert res["summary"]["independent_vote"] is False


def test_correlation_universe_covers_every_traded_instrument_once():
    names = {name for name, _ in CORRELATION_SERIES}
    aliases = {"NAS100": "NAS", "XAU": "GOLD", "XAG": "XAGUSD"}
    assert {aliases.get(code, code) for code in INSTRUMENTS} <= names
    assert "USDCAD" in names
    assert "CADUSD" not in names  # inverse duplicate would manufacture a -1 pair
    assert CORRELATION_CORE_ASSETS == (
        "NAS", "VXN", "SP500", "VIX", "GOLD", "GVZ", "OIL", "OVX")


def test_extended_topology_preserves_core_aggregate_and_marks_missing_assets():
    corr = {
        "assets": ["NAS", "VXN", "EURUSD", "USDCAD"],
        "core_assets": ["NAS", "VXN"],
        "observations_short": [96, 96, 96, 0],
        "matrix_short": [
            [1.0, -0.50, 0.90, None],
            [-0.50, 1.0, -0.10, None],
            [0.90, -0.10, 1.0, None],
            [None, None, None, None],
        ],
    }
    res = compute_correlation_graph(corr)
    nodes = {node["id"]: node for node in res["nodes"]}
    assert res["summary"]["systemic_coupling"] == 0.5
    assert nodes["USDCAD"]["data_available"] is False
    assert nodes["USDCAD"]["observed_pair_count"] == 0
    assert res["summary"]["assets_without_pairs"] == ["USDCAD"]
    assert res["summary"]["inverse_aliases"] == {"CADUSD": "USDCAD"}


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
    assert link["tension"] > 0
    assert res["summary"]["active_breaks_count"] == 1
    assert res["summary"]["regime"] == "CORRELATION BREAKDOWN"
    assert 0 <= res["summary"]["fragmentation"] <= 1
    assert res["summary"]["dominant_stress_node"] in {"NAS", "VXN"}
    for node in res["nodes"]:
        assert "coupling" in node
        assert "stress_pressure" in node
        assert "stress_normalized" in node


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
    assert res["links"][0]["velocity_magnitude"] == 0.18
    assert res["summary"]["velocity_ready"] is True
    assert res["summary"]["history_span_minutes"] == 5.0
    assert {n["id"] for n in res["nodes"]} == {"NAS", "SP500"}


def test_cross_asset_separates_relationship_level_from_measured_change():
    assert relationship_state(0.72, 0.70, 0.01, False) == "STABLE_HIGH_COUPLING"
    assert relationship_state(0.08, 0.10, 0.01, False) == "STABLE_DECOUPLED"
    assert relationship_state(-0.35, 0.40, 0.20, True) == "CORRELATION_REVERSAL"
    assert relationship_state(0.70, 0.40, 0.05, False) == "SYSTEMIC_RECOUPLING"
    assert relationship_state(0.45, 0.70, 0.05, True) == "CORRELATION_BREAK"


def test_cross_asset_full_finite_matrix_exposes_every_observed_pair():
    assets = ["NAS", "SP500", "VXN", "GOLD", "DXY", "OIL", "BTC", "US30"]
    n = len(assets)
    matrix = [
        [1.0 if i == j else round((i - j) / (n + 1), 3) for j in range(n)]
        for i in range(n)
    ]
    # Correlation matrices are symmetric; values can be weak but remain real
    # observed links and must never disappear from the FULL topology.
    for i in range(n):
        for j in range(i + 1, n):
            matrix[j][i] = matrix[i][j]
    res = compute_correlation_graph({"assets": assets, "matrix_short": matrix, "asof": 10_000.0})
    expected = n * (n - 1) // 2
    assert len(res["links"]) == expected
    assert res["summary"]["observed_pairs"] == expected
    assert res["summary"]["possible_pairs"] == expected
    assert res["summary"]["complete_topology"] is True
