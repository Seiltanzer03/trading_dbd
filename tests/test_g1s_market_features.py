from __future__ import annotations

import math
from types import SimpleNamespace

from seiltanzer.g1_short_horizon_market_features import (
    CONTRACT_VERSION, FEATURE_SETS, _intraday_state,
)


def _bar(ts, close):
    return (float(ts), close-0.2, close+0.5, close-0.5, close, 1000.0)


def test_intraday_features_ignore_bars_ending_after_t0():
    t0=10_000.0
    past=[_bar(t0-3600+i*60,100+i*0.01) for i in range(60)]
    future=[_bar(t0+i*60,500+i) for i in range(1,10)]
    feed=SimpleNamespace(intraday_ohlcv=past+future)
    a=_intraday_state(feed,t0,101.0)
    b=_intraday_state(SimpleNamespace(intraday_ohlcv=past),t0,101.0)
    assert a == b
    assert a["contract_version"] == CONTRACT_VERSION
    assert a["available"] is True
    assert a["future_bars_used"] is False
    assert a["latest_admissible_bar_end_ts"] <= t0 + 1e-9


def test_price_feature_family_contains_local_momentum_and_availability_flag():
    price=FEATURE_SETS["PRICE_ONLY_V1"]
    assert "intraday_feature_available" in price
    assert {"ret_15m","ret_30m","ret_60m","realized_vol_15m","realized_vol_60m"} <= set(price)
    assert "option_skew" not in price
    assert "option_skew" in FEATURE_SETS["PRICE_OPTIONS_V1"]
