from __future__ import annotations

from types import SimpleNamespace

from seiltanzer.g1_short_horizon_refinement import (
    ATR_CONTRACT_VERSION, FEATURE_SETS, _frozen_atr,
)


def test_atr_is_computed_only_from_already_available_daily_bars():
    closes=[100+i*0.2 for i in range(20)]
    highs=[c+1.0 for c in closes]
    lows=[c-0.8 for c in closes]
    feed=SimpleNamespace(daily={"bars":{"highs":highs,"lows":lows,"closes":closes}})
    atr=_frozen_atr(feed, market_price=closes[-1])
    assert atr["contract_version"] == ATR_CONTRACT_VERSION
    assert atr["available"] is True
    assert atr["atr_price"] > 0
    assert atr["atr_fraction"] > 0
    assert atr["source"] == "daily_bars_available_before_t0"


def test_atr_is_unavailable_not_hindsight_filled_when_daily_history_missing():
    feed=SimpleNamespace(daily={"bars":{"highs":[1,2],"lows":[.5,1.5],"closes":[.8,1.8]}})
    atr=_frozen_atr(feed, market_price=2.0)
    assert atr["available"] is False
    assert atr["reason"] == "INSUFFICIENT_DAILY_BARS"


def test_ablation_contract_contains_price_regime_options_and_full_families():
    assert set(FEATURE_SETS) >= {
        "PRICE_ONLY_V1", "PRICE_REGIME_V1", "PRICE_OPTIONS_V1", "FULL_V1"
    }
    assert "option_skew" not in FEATURE_SETS["PRICE_ONLY_V1"]
    assert "option_skew" not in FEATURE_SETS["PRICE_REGIME_V1"]
    assert "option_skew" in FEATURE_SETS["PRICE_OPTIONS_V1"]
    assert "option_skew" in FEATURE_SETS["FULL_V1"]
