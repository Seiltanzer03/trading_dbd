from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

from seiltanzer.edge_regime import (AUTHORITY_CONTRACT, build_edge_regime_context,
                                    classify_edge_regime, refine_regime_with_events)

T0 = 1_800_000_000.


def _bars(returns=None):
    returns = returns if returns is not None else [.00003] * 60
    price = 1.2
    closes = [price]
    for value in returns:
        closes.append(closes[-1] * math.exp(value))
    return [(T0 - (61 - i) * 60, value, value, value, value, 0.)
            for i, value in enumerate(closes)]


def _authority(**updates):
    return {"contract_version": AUTHORITY_CONTRACT, "source_id": "Yahoo:EURUSD=X:1m",
            "source_instrument": "EURUSD", "observed_ts": T0, "available_at": T0,
            "interval_sec": 60, "direct_source": True, "source_verified": True,
            "derived": False, "proxy": False, "quality": .75, **updates}


def _classify(bars=None, authority=None, **kwargs):
    return classify_edge_regime(_bars() if bars is None else bars,
                                _authority() if authority is None else authority,
                                instrument="EURUSD", captured_ts=T0, **kwargs)


def test_actual_trend_and_no_predictive_authority():
    result = _classify()
    assert result["regime"] == "TREND"
    assert result["feature_values"]["trend_efficiency_60m"] == pytest.approx(1.)
    assert result["causal_clocks"]["latest_bar_end_ts"] == T0
    assert result["quality"] == .75
    assert not any(result[key] for key in ("profit_proof", "training_proof", "independent_evidence_vote", "hard_risk_modified", "automatic_execution", "dynamic_weight_authority"))


@pytest.mark.parametrize("returns", [[.00003, -.00003] * 30, [0.] * 60])
def test_range_including_constant_real_prices(returns):
    result = _classify(_bars(returns))
    assert result["regime"] == "RANGE"
    assert result["feature_values"]["constant_price_window"] == (not any(returns))


def test_relative_volatility_stress_overrides_trend():
    result = _classify(_bars([.000001] * 45 + [.00003, -.00003] * 7 + [.00003]))
    assert result["regime"] == "STRESS"
    assert result["feature_values"]["rv15_over_prior45"] > 20


def test_last_actual_jump_stress():
    assert _classify(_bars([.00001] * 59 + [.0003]))["regime"] == "STRESS"


@pytest.mark.parametrize("updates,reason", [
    ({"source_instrument": "GBPUSD"}, "SOURCE_INSTRUMENT_MISMATCH"),
    ({"proxy": True}, "SOURCE_AUTHORITY_UNAVAILABLE_OR_NOT_DIRECT"),
    ({"derived": True}, "SOURCE_AUTHORITY_UNAVAILABLE_OR_NOT_DIRECT"),
    ({"source_verified": False}, "SOURCE_AUTHORITY_UNAVAILABLE_OR_NOT_DIRECT"),
    ({"available_at": T0 + 1}, "SOURCE_CLOCK_MISSING_OR_AFTER_T0"),
    ({"observed_ts": T0 + 1}, "SOURCE_CLOCK_MISSING_OR_AFTER_T0"),
    ({"observed_ts": T0 - 1}, "BAR_END_AFTER_SOURCE_OBSERVATION"),
    ({"quality": 0}, "SOURCE_QUALITY_INVALID"),
])
def test_unknown_for_missing_causal_authority(updates, reason):
    result = _classify(authority=_authority(**updates))
    assert result["regime"] == "UNKNOWN"
    assert result["reason"] == reason
    assert result["quality"] == 0


def test_future_and_uncompleted_bars_cannot_change_features_or_regime():
    past = _bars()
    future = [(T0, -1, 1e12, -100, -99, 100), (T0 + 60, 1, 2, 1, 2, 0)]
    result = _classify(past + future)
    assert result == _classify(past)


def test_stale_and_gapped_and_duplicate_bars_are_unknown():
    old = [(row[0] - 240, *row[1:]) for row in _bars()]
    assert _classify(old)["reason"] == "COMPLETED_DIRECT_PRICE_STALE"
    gap = _bars()
    gap.insert(0, (gap[0][0] - 120, *gap[0][1:]))
    del gap[20]
    assert _classify(gap)["reason"] == "COMPLETED_DIRECT_MINUTE_CONTINUITY_MISSING"
    assert _classify(_bars() + [_bars()[-1]])["reason"] == "DIRECT_BAR_CLOCK_DUPLICATE"


def _snapshot_event(**updates):
    release = {"status": "VALID", "official_source_verified": True,
               "release_id": "actual-cpi", "published_at": T0 - 600,
               "available_at": T0 - 590, **updates}
    return {"macro_context_v1": {"numeric_macro": {"releases": {"CPI": release}}}}


def test_observed_official_event_retains_underlying_price_regime():
    result = _classify(snapshot=_snapshot_event())
    assert result["regime"] == "EVENT"
    assert result["price_regime"] == "TREND"
    assert result["observed_events"][0]["source_id"] == "actual-cpi"


@pytest.mark.parametrize("updates", [{"published_at": T0 + 1}, {"available_at": T0 + 1},
                                     {"official_source_verified": False},
                                     {"published_at": T0 - 1801}, {"status": "INVALID"}])
def test_future_unverified_and_old_events_never_create_event_regime(updates):
    assert _classify(snapshot=_snapshot_event(**updates))["regime"] == "TREND"


def test_verified_event_does_not_rescue_missing_price_authority():
    assert _classify(authority={}, snapshot=_snapshot_event())["regime"] == "UNKNOWN"


def test_later_event_refinement_uses_exact_frozen_price_not_new_feed():
    frozen = _classify()
    snapshot = {**_snapshot_event(), "captured_ts": T0,
                "strategy": {"instrument": "EURUSD"}, "edge_regime": frozen}
    result = refine_regime_with_events(snapshot)
    assert result["regime"] == snapshot["market_regime"] == "EVENT"
    assert result["feature_values"] == frozen["feature_values"]
    assert result["causal_clocks"] == frozen["causal_clocks"]
    snapshot["macro_context_v1"] = {}
    assert refine_regime_with_events(snapshot)["regime"] == "TREND"
    snapshot["captured_ts"] = T0 + 1
    assert refine_regime_with_events(snapshot)["captured_ts"] == T0


def test_engine_mismatch_demo_and_offset_cannot_use_series_authority():
    snapshot = {"captured_ts": T0, "strategy": {"instrument": "EURUSD"}}
    for updates in ({"instrument_code": "NAS100"}, {"demo": True}, {"intraday_is_offset": True}):
        feed = SimpleNamespace(instrument_code="EURUSD", demo=False, intraday_is_offset=False,
                               intraday_source_authority=_authority(), intraday_ohlcv=_bars())
        for key, value in updates.items():
            setattr(feed, key, value)
        assert build_edge_regime_context(SimpleNamespace(market=feed), snapshot)["regime"] == "UNKNOWN"


def test_builder_copies_bars_and_authority_together_under_lock(monkeypatch):
    from seiltanzer import edge_regime
    feed = SimpleNamespace(instrument_code="EURUSD", demo=False, intraday_is_offset=False,
                           intraday_source_authority=_authority(),
                           intraday_ohlcv=[list(row) for row in _bars()])
    class Guard:
        locked = False
        def __enter__(self):
            self.locked = True
        def __exit__(self, *args):
            self.locked = False
            # Mimic the next provider publication immediately after releasing
            # the lock. Neither new prices nor new receipt clocks belong to T0.
            feed.intraday_ohlcv[-1][4] *= 10
            feed.intraday_source_authority["available_at"] = T0 + 100
    guard = Guard()
    feed._intraday_lock = guard
    original = edge_regime.classify_edge_regime
    def checked(raw, authority, **kwargs):
        assert guard.locked is False
        assert isinstance(raw[-1], tuple)
        assert authority["available_at"] == T0
        return original(raw, authority, **kwargs)
    monkeypatch.setattr(edge_regime, "classify_edge_regime", checked)
    result = build_edge_regime_context(SimpleNamespace(market=feed),
                                       {"captured_ts": T0, "strategy": {"instrument": "EURUSD"}})
    assert result["regime"] == "TREND"
    assert result["feature_values"]["log_return_60m"] == pytest.approx(.0018)


@pytest.mark.parametrize("code,symbol,direct", [("EURUSD", "EURUSD=X", True),
                                                ("USDCAD", "CAD=X", True),
                                                ("NAS100", "^NDX", False),
                                                ("XAU", "GC=F", False),
                                                ("EURUSD", "GBPUSD=X", False)])
def test_actual_provider_success_captures_independent_bar_authority(tmp_path, monkeypatch, code, symbol, direct):
    import pandas as pd
    from seiltanzer.config import Settings
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.data.feeds import MarketData
    frame = pd.DataFrame(_bars(), columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    frame.index = pd.to_datetime(frame.pop("ts"), unit="s", utc=True)
    symbols = []
    class Ticker:
        def __init__(self, requested):
            symbols.append(requested)
        def history(self, **kwargs):
            return frame
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker))
    monkeypatch.setattr("seiltanzer.data.feeds.time.time", lambda: T0)
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        feed = MarketData(Settings(stream=False, data_dir=str(tmp_path)), cache)
        feed.set_instrument(code)
        feed.price = {"value": 1.2, "source": "deliberately unrelated price authority"}
        if symbol != feed.instrument.yahoo:
            monkeypatch.setattr(type(feed), "instrument", property(lambda self: SimpleNamespace(
                yahoo=symbol, asset_class="fx", swissquote_pair=None, tradingview_symbol=None)))
        feed.refresh_intraday()
        authority = feed.intraday_source_authority
        assert symbols == [symbol]
        assert authority["direct_source"] is direct
        assert authority["proxy"] is not direct
        assert authority["source_id"] == "Yahoo:" + symbol + ":1m"
        assert authority["observed_ts"] == authority["available_at"] == T0
        assert authority["broker_execution_bars"] is False
        if direct:
            assert classify_edge_regime(feed.intraday_ohlcv, authority, instrument=code,
                                        captured_ts=T0)["regime"] == "TREND"
        feed.set_instrument("BTCUSD")
        assert not feed.intraday_source_authority
        assert not feed.intraday_ohlcv
    finally:
        cache._conn.close()


def test_provider_failure_clears_previous_authority(tmp_path, monkeypatch):
    from seiltanzer.config import Settings
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.data.feeds import MarketData
    class Ticker:
        def __init__(self, symbol):
            raise RuntimeError("actual source unavailable")
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker))
    cache = DiskCache(str(tmp_path / "cache.db"))
    try:
        feed = MarketData(Settings(stream=False, data_dir=str(tmp_path)), cache)
        feed.set_instrument("EURUSD")
        feed.intraday_source_authority = _authority()
        feed.refresh_intraday()
        assert feed.intraday_source_authority == {}
    finally:
        cache._conn.close()


def test_returned_history_for_old_instrument_is_discarded(tmp_path, monkeypatch):
    import pandas as pd
    from seiltanzer.config import Settings
    from seiltanzer.data.cache import DiskCache
    from seiltanzer.data.feeds import MarketData
    frame = pd.DataFrame(_bars(), columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    frame.index = pd.to_datetime(frame.pop("ts"), unit="s", utc=True)
    cache = DiskCache(str(tmp_path / "cache.db"))
    feed = MarketData(Settings(stream=False, data_dir=str(tmp_path)), cache)
    feed.set_instrument("EURUSD")
    class Ticker:
        def __init__(self, symbol):
            assert symbol == "EURUSD=X"
        def history(self, **kwargs):
            feed.set_instrument("USDCAD")
            return frame
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=Ticker))
    try:
        feed.refresh_intraday()
        assert feed.instrument_code == "USDCAD"
        assert feed.intraday_source_authority == {}
        assert feed.intraday_ohlcv == []
    finally:
        cache._conn.close()
