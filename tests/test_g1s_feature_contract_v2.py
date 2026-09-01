from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from seiltanzer import g1_short_horizon_feature_contract_v2 as v2
from seiltanzer.g1_short_horizon_runtime import FEATURE_SETS, ShortHorizonRuntime
from seiltanzer.passive_learning import _walk_timestamps


class _Cache:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)

    def chain_snapshots(self, ticker, limit=60):
        assert ticker == "QQQ"
        return self.snapshots[-limit:]


class _Engine:
    def __init__(self, snapshots):
        self.cache = _Cache(snapshots)


def _cross_features(asof: float):
    assets = ["NAS", "VXN", "SP500", "VIX", "GOLD", "GVZ", "OIL", "OVX"]
    n = len(assets)
    short = np.eye(n).tolist()
    delta = np.zeros((n, n)).tolist()
    short[0][2] = short[2][0] = 0.82
    short[0][1] = short[1][0] = -0.61
    short[4][5] = short[5][4] = -0.44
    delta[0][2] = delta[2][0] = 0.12
    delta[0][1] = delta[1][0] = -0.08
    delta[4][5] = delta[5][4] = 0.05
    return {
        "cross_asset": {
            "available": True,
            "data": {"value": {"assets": assets, "matrix_short": short,
                                "matrix_delta": delta, "asof": asof},
                     "ts": asof, "status": "delayed"},
        }
    }


def test_v2_sets_are_additive_and_do_not_mutate_legacy_feature_registry():
    assert "MARKET_V2" not in FEATURE_SETS
    assert "MARKET_OPTION_V2" not in FEATURE_SETS
    assert "FULL_V2" not in FEATURE_SETS
    assert set(v2.V2_FEATURE_SETS) == {"MARKET_V2", "MARKET_OPTION_V2", "FULL_V2"}


def test_window_stats_excludes_bar_ending_after_t0():
    rows = []
    for i in range(70):
        rows.append({"bar_end_ts": 1000.0+i*60.0, "close": 100.0+i*0.1})
    rows.append({"bar_end_ts": 1000.0+70*60.0, "close": 9999.0})
    t0 = 1000.0+69*60.0
    result = v2._window_stats(rows, t0)
    assert result["source_last_bar_end_ts"] <= t0
    expected = math.log((100.0+69*0.1)/(100.0+64*0.1))
    assert result["ret_5m"] == pytest.approx(expected)
    assert result["ret_5m"] < 0.1


def test_option_snapshot_selects_latest_strictly_pre_t0():
    engine = _Engine([
        {"ts": 90.0, "proxy": "QQQ", "implied_move": {"sigma_annual": 0.2}},
        {"ts": 110.0, "proxy": "QQQ", "implied_move": {"sigma_annual": 0.9}},
    ])
    features = {"option_distribution": {"proxy": "QQQ"}}
    chosen = v2._latest_option_snapshot(engine, features, 100.0)
    assert chosen["available"] is True
    assert chosen["source_ts"] == pytest.approx(90.0)
    assert chosen["metrics"]["implied_move"]["sigma_annual"] == pytest.approx(0.2)


def test_production_option_snapshot_rejects_demo_cache_row():
    engine = _Engine([
        {"ts": 90.0, "demo": False, "proxy": "QQQ",
         "implied_move": {"sigma_annual": 0.2}},
        {"ts": 95.0, "demo": True, "proxy": "QQQ",
         "implied_move": {"sigma_annual": 0.9}},
    ])
    engine.settings = SimpleNamespace(demo=False)
    chosen = v2._latest_option_snapshot(
        engine, {"option_distribution": {"proxy": "QQQ"}}, 100.0)
    assert chosen["available"] is True
    assert chosen["source_ts"] == pytest.approx(90.0)
    assert chosen["metrics"]["demo"] is False


def test_future_cross_asset_is_rejected_without_admitted_future_source_timestamp():
    result = v2._cross_asset(_cross_features(110.0), 100.0, "NAS100")
    assert result["available"] is False
    assert result["reason"] == "cross_asset_after_t0"
    assert "source_ts" not in result
    assert result["rejected_asof"] == pytest.approx(110.0)
    # Generic T0 validator must see no future timestamp in rejected evidence.
    assert not list(_walk_timestamps({"g1s_evidence_v2": {"cross_asset": result}}))


def test_gold_cross_asset_uses_gold_gvz_pair():
    result = v2._cross_asset(_cross_features(90.0), 100.0, "XAU")
    assert result["available"] is True
    assert result["risk_pair"] == ["GOLD", "GVZ"]
    assert result["risk_corr"] == pytest.approx(-0.44)


def test_bs_greek_context_is_finite_and_never_claims_dealer_inventory_or_physical_p():
    spot = 100.0
    strikes = np.asarray([90.0, 100.0, 110.0])
    raw = {
        "strikes": strikes,
        "call_iv": np.asarray([0.22, 0.20, 0.21]),
        "put_iv": np.asarray([0.24, 0.22, 0.21]),
        "call_oi": np.asarray([100.0, 300.0, 150.0]),
        "put_oi": np.asarray([150.0, 350.0, 100.0]),
        "t_years": 30.0/365.0,
    }
    result = v2._bs_greek_context(raw, spot)
    assert result["available"] is True
    for key in ("net_delta_oi_weighted", "vega_per_spot_oi_weighted",
                "vanna_oi_weighted", "charm_per_day_oi_weighted"):
        assert math.isfinite(float(result[key]))
    assert -1.0 <= result["net_delta_oi_weighted"] <= 1.0
    assert result["vega_per_spot_oi_weighted"] > 0.0
    assert result["dealer_inventory_assumption"] is False
    assert result["dealer_positioning_claim"] is False
    assert result["physical_probability_semantics"] is False


def test_v2_feature_vector_has_explicit_missingness_and_stable_dimension():
    row = {
        "instrument": "NAS100", "price_quality": 0.98, "option_quality": 0.7,
        "frozen_features_json": '{"g1s_evidence_v2":{"contract_version":"g1s-t0-evidence-v2",'
                                '"intraday":{"available":false},"wavelet":{"available":false},'
                                '"option_context":{"available":false},"cross_asset":{"available":false}}}',
        "frozen_forecast_json": "{}",
    }
    vector, values = ShortHorizonRuntime._feature_vector(row, "FULL_V2")
    expected = len(v2.V2_FEATURE_SETS["FULL_V2"]) + 9  # 10 instruments -> 9 dummies
    assert len(vector) == expected
    assert values["market_intraday_available"] == 0.0
    assert values["option_context_available"] == 0.0
    assert values["cross_asset_available"] == 0.0
