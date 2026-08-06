import math

from seiltanzer.core.macro_regime import compute_macro_regime


def _prices(n=180):
    return [
        {
            "ts": 1_000 + i * 300,
            "price": 100.0 * math.exp(0.00045 * i + 0.006 * math.sin(i / 8.0)),
        }
        for i in range(n)
    ]


def test_macro_regime_empty():
    res = compute_macro_regime([])
    assert res["available"] is False
    assert res["summary"]["authority"] == "strategy_context"
    assert res["summary"]["independent_vote"] is False


def test_macro_regime_trajectory_is_rolling_not_origin_interpolation():
    prices = _prices()
    vols = {"vxn": {"value": 24.0, "status": "delayed"}}
    corr = {"matrix_delta": [[0.0, 0.15], [0.15, 0.0]]}
    res = compute_macro_regime(
        prices, vols, corr, instrument_code="NAS100",
        source_meta={"source": "fixture 5m history"},
    )
    assert res["available"] is True
    traj = res["trajectory_24h"]
    assert len(traj) > 8
    # A real rolling trajectory must contain multiple independently calculated
    # state changes; the old prototype was just a straight ratio from origin.
    xy = {(round(p["x"], 2), round(p["y"], 2)) for p in traj}
    assert len(xy) > 5
    assert res["summary"]["points"] == len(prices)
    assert res["summary"]["source"]["source"] == "fixture 5m history"
    assert res["summary"]["authority"] == "strategy_context"
    assert res["summary"]["independent_vote"] is False


def test_macro_regime_accepts_actual_status_dict_vol_feed():
    prices = _prices()
    low = compute_macro_regime(
        prices, {"vxn": {"value": 18.0}}, instrument_code="NAS100")
    high = compute_macro_regime(
        prices, {"vxn": {"value": 42.0}}, instrument_code="NAS100")
    assert low["available"] and high["available"]
    assert high["current"]["y_vol"] > low["current"]["y_vol"]


def test_macro_regime_vol_shock_from_real_stress_context():
    prices = _prices()
    corr = {"matrix_delta": [[0.0, 0.75], [0.75, 0.0]]}
    res = compute_macro_regime(
        prices,
        {"vxn": {"value": 45.0}},
        corr,
        instrument_code="NAS100",
    )
    assert res["available"] is True
    assert res["current"]["regime"] == "VOL SHOCK"
    assert res["current"]["z_stress"] > 1.6
