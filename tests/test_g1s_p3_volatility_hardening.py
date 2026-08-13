from __future__ import annotations

import math

import numpy as np
import pytest

from seiltanzer.g1_short_horizon_p3_path_geometry import TARGET_FUTURE_RV
from seiltanzer.g1_short_horizon_p3_volatility_hardening import (
    EWMA_LAMBDA,
    P3B_CONTRACT_VERSION,
    P3B_HAR_FAMILY,
    TRAILING_WINDOW_MINUTES,
    _ewma_volatility,
    _hardening_context,
    _predict_har,
    _strong_baselines,
)


def _source(n=420, *, gap_after=None, gap_seconds=0.0):
    rows=[]; previous=100.0; start=1_700_000_000.0; shift=0.0
    for i in range(n):
        if gap_after is not None and i > gap_after:
            shift=gap_seconds
        ts=start+i*300.0+shift
        close=100.0*math.exp(0.0001*i+0.0015*math.sin(i/10.0))
        rows.append({
            "bar_start_ts":ts,"bar_end_ts":ts+300.0,
            "open":previous,"high":max(previous,close)*1.0004,
            "low":min(previous,close)*0.9996,"close":close,"volume":1000+i,
        })
        previous=close
    return {"instrument":"NAS100","ticker":"^NDX","source_id":"s1","bars":rows}


def test_ewma_is_causal_fixed_lambda_and_finite():
    values=np.asarray([0.001,-0.002,0.003,-0.0015,0.0025],dtype=float)
    out=_ewma_volatility(values)
    assert EWMA_LAMBDA==0.94
    assert out>0 and math.isfinite(out)
    # A larger latest shock must increase the fixed-lambda estimate.
    values2=values.copy(); values2[-1]=0.02
    assert _ewma_volatility(values2)>out


def test_240m_hardening_context_does_not_carry_across_overnight_gap():
    source=_source(500,gap_after=249,gap_seconds=8*3600)
    context=_hardening_context(source)
    before=source["bars"][249]["bar_end_ts"]
    after=source["bars"][250]["bar_end_ts"]
    assert after-before>7*3600
    assert not any(after<=ts<after+240*60 for ts in context)
    later=min(ts for ts in context if ts>=after+240*60)
    assert context[later]["current_realized_volatility_5m_240m"]>=0
    assert context[later]["current_ewma_volatility_5m_240m"]>=0


def _row(i: int):
    rv60=0.002+0.0001*(i%4)
    rv15=rv60*(0.8+0.1*(i%3))
    rv240=rv60*1.1
    ewma=rv60*1.05
    future=rv60*(0.9+0.05*(i%5))
    return {
        "instrument":"NAS100" if i%2 else "SP500",
        "captured_ts":1_700_000_000.0+i*300,
        "target_ts":1_700_000_000.0+i*300+3600,
        "horizon_minutes":60,"future_steps_5m":12,
        "current_realized_volatility_5m_15m":rv15,
        "current_realized_volatility_5m_60m":rv60,
        "current_realized_volatility_5m_240m":rv240,
        "current_ewma_volatility_5m_240m":ewma,
        TARGET_FUTURE_RV:future,
        "log_current_rv60_5m":math.log(rv60+1e-9),
        "log_current_rv15_5m":math.log(rv15+1e-9),
        "ret5_over_rv60":0.1,"ret15_over_rv60":0.2,"ret60_over_rv60":0.3,
        "rv15_over_rv60":rv15/rv60,"range60_over_rv60":2.0,
        "drawup60_over_rv60":1.0,"drawdown60_over_rv60":1.0,
        "trend_agreement_5_15":1.0,"trend_agreement_15_60":1.0,
        "utc_sin":0.0,"utc_cos":1.0,
    }


def test_strong_baselines_include_har_ewma_and_multiwindow_persistence():
    train=[_row(i) for i in range(300)]
    test=[_row(i+300) for i in range(50)]
    baselines=_strong_baselines(train,test)
    expected={
        "zero","causal_historical_mean","causal_vol_anchor",
        "current_rv60_persistence","current_rv15_persistence",
        "current_rv240_persistence","ewma240_persistence",
        "causal_scaled_ewma240","har_5m_log_vol_ridge",
    }
    assert expected==set(baselines)
    assert all(len(values)==len(test) for values in baselines.values())
    assert all(np.isfinite(values).all() for values in baselines.values())
    assert all((values>=0).all() for values in baselines.values())


def test_p3b_contract_is_hardening_only_not_live_authority():
    assert P3B_CONTRACT_VERSION=="g1s-p3b-volatility-hardening-v1"
    assert P3B_HAR_FAMILY=="HAR_5M_LOG_VOL_RIDGE_V1"
    assert TRAILING_WINDOW_MINUTES==240
