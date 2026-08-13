from __future__ import annotations

import math

import numpy as np
import pytest

from seiltanzer.g1_short_horizon_p3_path_geometry import (
    EPS,
    P3_CONTRACT_VERSION,
    P3_EVIDENCE_LABEL,
    P3_FEATURE_CONTRACT,
    P3_MODEL_FAMILY,
    TARGET_FUTURE_RV,
    TARGET_MAE,
    TARGET_MFE,
    _anchor_prediction,
    _fit_anchor_factor,
    _predict_model,
    _pre_t0_context,
    _target_row,
    build_rows,
    winner_gate,
)


def _bars(n=500, *, start=1_700_000_000.0, gap_after=None, gap_seconds=0.0):
    rows=[]; previous=100.0; shift=0.0
    for i in range(n):
        if gap_after is not None and i > gap_after:
            shift=gap_seconds
        ts=start+i*300.0+shift
        close=100.0*math.exp(0.00012*i+0.0015*math.sin(i/9.0))
        high=max(previous,close)*(1.0005+0.0001*(i%3))
        low=min(previous,close)*(0.9995-0.0001*(i%2))
        rows.append({
            "bar_start_ts":ts,"bar_end_ts":ts+300.0,
            "open":previous,"high":high,"low":low,"close":close,"volume":1000+i,
        })
        previous=close
    return rows


def _source(bars):
    return {"instrument":"NAS100","ticker":"^NDX","source_id":"s1","bars":bars}


def test_pre_t0_context_does_not_bridge_overnight_gap():
    bars=_bars(320,gap_after=149,gap_seconds=8*3600)
    contexts=_pre_t0_context(_source(bars))
    before=bars[149]["bar_end_ts"]
    after=bars[150]["bar_end_ts"]
    assert after-before > 7*3600
    assert not any(after <= ts < after+60*60 for ts in contexts)
    assert any(ts >= after+60*60 for ts in contexts)


def test_future_targets_use_strictly_post_t0_high_low_and_5m_close_returns():
    bars=_bars(300)
    source=_source(bars)
    contexts=_pre_t0_context(source)
    captured=sorted(contexts)[80]
    context=contexts[captured]
    row=_target_row(source,context,15)
    assert row is not None
    index=next(i for i,b in enumerate(bars) if b["bar_end_ts"]==captured)
    target_index=next(i for i,b in enumerate(bars) if b["bar_end_ts"]==row["target_ts"])
    future=bars[index+1:target_index+1]
    current=bars[index]["close"]
    expected_mfe=max(0.0,math.log(max([current]+[b["high"] for b in future])/current))
    expected_mae=max(0.0,-math.log(min([current]+[b["low"] for b in future])/current))
    closes=np.asarray([current]+[b["close"] for b in future],dtype=float)
    expected_rv=float(np.std(np.diff(np.log(closes)),ddof=0))
    assert row[TARGET_MFE]==pytest.approx(expected_mfe)
    assert row[TARGET_MAE]==pytest.approx(expected_mae)
    assert row[TARGET_FUTURE_RV]==pytest.approx(expected_rv)
    assert row["path_source"]=="real_yahoo_5m_ohlc"
    assert row["historical_sampling_interval_sec"]==300
    assert row["target_ts"] > row["captured_ts"]


def test_horizon_target_never_jumps_session_gap():
    bars=_bars(320,gap_after=149,gap_seconds=8*3600)
    rows=build_rows([_source(bars)],60)
    before=bars[149]["bar_end_ts"]
    after=bars[150]["bar_end_ts"]
    assert rows
    assert not any(row["captured_ts"] <= before and row["target_ts"] >= after for row in rows)


def _model_rows(n=400,horizon=60):
    rows=[]
    for i in range(n):
        rv60=0.002+0.0001*(i%5)
        steps=horizon//5
        rows.append({
            "instrument":"NAS100" if i%2 else "SP500",
            "captured_ts":1_700_000_000.0+i*300,
            "target_ts":1_700_000_000.0+i*300+horizon*60,
            "horizon_minutes":horizon,"future_steps_5m":steps,
            "current_realized_volatility_5m_60m":rv60,
            "current_realized_volatility_5m_15m":rv60*1.05,
            TARGET_FUTURE_RV:rv60*1.1,
            TARGET_MFE:rv60*math.sqrt(steps)*0.8,
            TARGET_MAE:rv60*math.sqrt(steps)*0.7,
            "log_current_rv60_5m":math.log(rv60+EPS),
            "log_current_rv15_5m":math.log(rv60*1.05+EPS),
            "ret5_over_rv60":0.1,"ret15_over_rv60":0.2,"ret60_over_rv60":0.3,
            "rv15_over_rv60":1.05,"range60_over_rv60":2.0,
            "drawup60_over_rv60":1.0,"drawdown60_over_rv60":1.0,
            "trend_agreement_5_15":1.0,"trend_agreement_15_60":1.0,
            "utc_sin":0.0,"utc_cos":1.0,
        })
    return rows


def test_zero_log_residual_coefficients_recover_causal_anchor_exactly():
    train=_model_rows(300)
    test=_model_rows(30)
    for target in (TARGET_FUTURE_RV,TARGET_MFE,TARGET_MAE):
        factor=_fit_anchor_factor(train,target)
        anchor=_anchor_prediction(test,target,factor)
        feature_count=13+9
        artifact={
            "target":target,"anchor_factor":factor,
            "feature_mean":[0.0]*feature_count,"feature_std":[1.0]*feature_count,
            "log_residual_intercept_and_coefficients":[0.0]*(feature_count+1),
        }
        prediction=_predict_model(test,artifact)
        assert np.max(np.abs(prediction-anchor)) < 1e-12


def test_winner_gate_requires_both_primary_metrics_and_robust_folds():
    evaluation={
        "fold_count":4,"fold_joint_non_degrade_n":3,
        "model":{"mae":0.0090,"rmse":0.0120,"bias":0.0},
        "baselines":{
            "causal_vol_anchor":{"mae":0.0100,"rmse":0.0130,"bias":0.0},
            "zero":{"mae":0.0110,"rmse":0.0140,"bias":0.0},
        },
    }
    assert winner_gate(evaluation,5000,1200)["historical_winner"] is True
    evaluation["fold_joint_non_degrade_n"]=2
    assert winner_gate(evaluation,5000,1200)["historical_winner"] is False
    evaluation["fold_joint_non_degrade_n"]=4
    evaluation["model"]["mae"]=0.00997
    assert winner_gate(evaluation,5000,1200)["historical_winner"] is False


def test_contract_names_do_not_mislabel_5m_volatility_as_live_1m_metric():
    assert P3_CONTRACT_VERSION.endswith("v1")
    assert P3_FEATURE_CONTRACT.endswith("v1")
    assert P3_EVIDENCE_LABEL=="HISTORICAL_WALK_FORWARD_5M_PATH"
    assert P3_MODEL_FAMILY=="CAUSAL_ANCHOR_LOG_RESIDUAL_RIDGE_V1"
    assert TARGET_FUTURE_RV=="future_realized_volatility_5m"
    assert "1m" not in TARGET_FUTURE_RV
