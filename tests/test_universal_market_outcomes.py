from __future__ import annotations

import math

from seiltanzer.edge_discovery.universal_outcomes import (
    BARRIER_PAIRS,
    UNIVERSAL_OUTCOME_CONTRACT_VERSION,
    causal_local_sigma_h,
    resolve_universal_market_outcome,
)


def _bar(minute: int, *, high: float, low: float, close: float) -> dict:
    start = 1000.0 + (minute - 1) * 60.0
    return {
        "bar_start_ts": start,
        "bar_end_ts": start + 60.0,
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
    }


def _resolve(bars, *, complete: bool = True, rv60: float = 0.02):
    return resolve_universal_market_outcome(
        start_price=100.0,
        captured_ts=1000.0,
        target_ts=1180.0,
        horizon_minutes=60,
        t0_realized_vol_60m=rv60,
        bars=bars,
        path_complete=complete,
    )


def test_causal_sigma_uses_only_frozen_t0_rv_and_horizon():
    assert causal_local_sigma_h(0.02, 60) == 0.02
    assert math.isclose(causal_local_sigma_h(0.02, 15), 0.01)
    assert causal_local_sigma_h(None, 60) is None
    assert causal_local_sigma_h(0.0, 60) is None


def test_upper_first_and_strategy_agnostic_contract():
    result = _resolve([
        _bar(1, high=101.2, low=99.8, close=100.8),
        _bar(2, high=102.2, low=100.4, close=101.9),
        _bar(3, high=102.4, low=101.0, close=102.0),
    ])
    assert result["available"] is True
    assert result["contract_version"] == UNIVERSAL_OUTCOME_CONTRACT_VERSION
    assert result["strategy_agnostic"] is True
    assert result["normalization_uses_future_data"] is False
    assert result["barriers"]["up_1s_down_1s"]["label"] == "UP_FIRST"
    assert result["barriers"]["up_1s_down_1s"]["clean_label"] is True
    assert result["forward_rv_log_return"] is not None
    assert result["mfe_log_return"] > 0
    assert result["mae_log_return"] < 0
    assert result["contains_user_entry"] is False
    assert result["contains_user_stop"] is False
    assert result["contains_user_take"] is False
    assert result["contains_user_rr"] is False


def test_lower_first_is_symmetric_market_outcome():
    result = _resolve([
        _bar(1, high=100.2, low=98.8, close=99.2),
        _bar(2, high=99.5, low=97.5, close=98.0),
        _bar(3, high=98.5, low=97.8, close=98.1),
    ])
    assert result["barriers"]["up_1s_down_1s"]["label"] == "DOWN_FIRST"
    assert result["direction_label"] == "DOWN"


def test_same_ohlc_bar_touching_both_sides_is_ambiguous_not_guessed():
    result = _resolve([
        _bar(1, high=103.0, low=97.0, close=100.5),
        _bar(2, high=101.0, low=99.5, close=100.2),
        _bar(3, high=100.8, low=99.7, close=100.1),
    ])
    label = result["barriers"]["up_1s_down_1s"]
    assert label["label"] == "AMBIGUOUS_SAME_BAR"
    assert label["ambiguous_same_bar"] is True
    assert label["clean_label"] is False


def test_complete_path_without_touch_is_no_touch():
    result = _resolve([
        _bar(1, high=100.4, low=99.7, close=100.1),
        _bar(2, high=100.6, low=99.8, close=100.2),
        _bar(3, high=100.5, low=99.9, close=100.3),
    ])
    assert result["barriers"]["up_1s_down_1s"]["label"] == "NO_TOUCH"
    assert result["barriers"]["up_1s_down_1s"]["clean_label"] is True


def test_incomplete_path_without_touch_is_censored_not_no_touch():
    result = _resolve([
        _bar(1, high=100.4, low=99.7, close=100.1),
    ], complete=False)
    assert result["barriers"]["up_1s_down_1s"]["label"] == "CENSORED"
    assert result["barriers"]["up_1s_down_1s"]["clean_label"] is False


def test_point_only_path_never_claims_clean_first_passage():
    result = resolve_universal_market_outcome(
        start_price=100.0,
        captured_ts=1000.0,
        target_ts=1120.0,
        horizon_minutes=60,
        t0_realized_vol_60m=0.02,
        points=[
            {"ts": 1060.0, "price": 102.5},
            {"ts": 1120.0, "price": 102.2},
        ],
        path_complete=True,
    )
    label = result["barriers"]["up_1s_down_1s"]
    assert label["label"] == "UP_FIRST"
    assert label["clean_label"] is False
    assert result["forward_rv_log_return"] is None


def test_barrier_set_is_bounded_and_predeclared():
    assert BARRIER_PAIRS == ((0.5, 0.5), (1.0, 0.5), (0.5, 1.0), (1.0, 1.0))
    result = _resolve([_bar(1, high=100.1, low=99.9, close=100.0)])
    assert len(result["barriers"]) == 4
