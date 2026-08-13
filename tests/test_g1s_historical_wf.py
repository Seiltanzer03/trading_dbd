from __future__ import annotations

import json
import math
import sqlite3
import threading

import numpy as np
import pytest

from seiltanzer.g1_short_horizon_historical_wf import (
    BAR_SECONDS,
    DIRECTION_TARGET,
    HISTORICAL_EVIDENCE_LABEL,
    HISTORICAL_FEATURE_SET,
    HISTORICAL_PROB_MODEL_FAMILY,
    HISTORICAL_WF_CONTRACT_VERSION,
    LIVE_EVIDENCE_LABEL,
    _beats_probability_baselines,
    _beats_return_baselines,
    _build_horizon_rows,
    _dependency_key,
    _ensure_tables,
    _historical_folds,
    _historical_status,
    _live_feature_vector,
    _register_live_cohort,
    _weights,
)


def _bars(n=500, start=1_700_000_000.0, gap_after=None, gap_seconds=0.0):
    out=[]
    shift=0.0
    price=100.0
    for i in range(n):
        if gap_after is not None and i > gap_after:
            shift=gap_seconds
        ts=start+i*BAR_SECONDS+shift
        previous=price
        price=100.0*math.exp(0.00015*i + 0.001*math.sin(i/11.0))
        out.append({
            "bar_start_ts": ts,
            "bar_end_ts": ts+BAR_SECONDS,
            "open": previous,
            "high": max(previous,price)*1.0005,
            "low": min(previous,price)*0.9995,
            "close": price,
            "volume": 1000.0+i,
        })
    return out


def _source(bars):
    return {
        "source_id":"src-1", "instrument":"NAS100", "ticker":"^NDX",
        "bars":bars, "source_sha256":"x", "bar_count":len(bars),
        "first_bar_end_ts":bars[0]["bar_end_ts"],
        "last_bar_end_ts":bars[-1]["bar_end_ts"],
        "calendar_span_days":(bars[-1]["bar_end_ts"]-bars[0]["bar_end_ts"])/86400.0,
    }


def test_historical_rows_use_completed_t0_bar_and_future_target_only():
    bars=_bars(700)
    rows=_build_horizon_rows(_source(bars), 60)
    assert rows
    row=rows[20]
    assert row["evidence_label"] == HISTORICAL_EVIDENCE_LABEL
    assert row["target_ts"] > row["captured_ts"]
    assert abs((row["target_ts"]-row["captured_ts"])-3600.0) <= BAR_SECONDS*1.5
    assert set(row["features"]) == {
        "ret_5m","ret_15m","ret_60m","realized_vol_15m","realized_vol_60m"}
    # All features are backward-looking; terminal outcome is independently future-looking.
    index=next(i for i,b in enumerate(bars) if b["bar_end_ts"] == row["captured_ts"])
    expected_ret5=math.log(bars[index]["close"]/bars[index-1]["close"])
    expected_ret15=math.log(bars[index]["close"]/bars[index-3]["close"])
    expected_ret60=math.log(bars[index]["close"]/bars[index-12]["close"])
    assert row["features"]["ret_5m"] == pytest.approx(expected_ret5)
    assert row["features"]["ret_15m"] == pytest.approx(expected_ret15)
    assert row["features"]["ret_60m"] == pytest.approx(expected_ret60)
    assert row["terminal_log_return"] == pytest.approx(
        math.log(next(b["close"] for b in bars if b["bar_end_ts"] == row["target_ts"])
                 /bars[index]["close"])
    )


def test_historical_rows_do_not_bridge_market_session_gap():
    # An 8h gap simulates overnight/session closure. No 15m target may jump it.
    bars=_bars(300, gap_after=149, gap_seconds=8*3600)
    rows=_build_horizon_rows(_source(bars), 15)
    assert rows
    gap_left=bars[149]["bar_end_ts"]
    gap_right=bars[150]["bar_end_ts"]
    assert gap_right-gap_left > 7*3600
    assert not any(
        row["captured_ts"] <= gap_left and row["target_ts"] >= gap_right
        for row in rows
    )
    # Pre-T0 60m feature window also cannot bridge the same gap.
    assert not any(
        row["captured_ts"] >= gap_right
        and row["captured_ts"]-60*60 < gap_left
        for row in rows
    )


def _wf_rows(n=1200, horizon=60):
    start=1_700_000_000.0
    rows=[]
    for i in range(n):
        captured=start+i*BAR_SECONDS
        target=captured+horizon*60.0
        r=0.001*math.sin(i/7.0)+0.0002
        rows.append({
            "instrument":"NAS100" if i%2==0 else "SP500",
            "captured_ts":captured,"target_ts":target,
            "horizon_minutes":horizon,
            "features":{
                "ret_5m":0.0001*math.sin(i/3),
                "ret_15m":0.0002*math.sin(i/5),
                "ret_60m":0.0003*math.sin(i/9),
                "realized_vol_15m":0.001+0.0001*(i%3),
                "realized_vol_60m":0.002+0.0001*(i%7),
            },
            "terminal_log_return":r,
            "direction_label":"UP" if r>0 else "DOWN",
        })
    return rows


def test_walkforward_is_expanding_and_purges_target_overlap():
    rows=_wf_rows()
    folds=_historical_folds(rows,60)
    assert len(folds)==4
    previous_train_n=0
    for fold in folds:
        assert len(fold["train"]) > previous_train_n
        previous_train_n=len(fold["train"])
        assert max(r["target_ts"] for r in fold["train"]) < fold["purge_boundary_ts"]
        assert min(r["captured_ts"] for r in fold["test"]) == fold["test_start_ts"]
        assert fold["train_target_max_ts"] < fold["test_start_ts"]


def test_dependency_group_weights_sum_to_one_exactly():
    rows=_wf_rows(300,horizon=15)
    weights,effective=_weights(rows)
    grouped={}
    for row,weight in zip(rows,weights):
        grouped.setdefault(_dependency_key(row),0.0)
        grouped[_dependency_key(row)]+=float(weight)
    assert effective==len(grouped)
    assert grouped
    assert all(value == pytest.approx(1.0) for value in grouped.values())


def test_historical_winner_gate_requires_both_primary_metrics():
    probability_eval={
        "model":{"brier":0.19,"logloss":0.58},
        "baselines":{
            "constant_0_5":{"brier":0.25,"logloss":0.693},
            "momentum":{"brier":0.22,"logloss":0.62},
        },
    }
    passed,detail=_beats_probability_baselines(probability_eval)
    assert passed is True
    assert detail["best_brier_baseline"]=="momentum"
    probability_eval["model"]["logloss"]=0.63
    assert _beats_probability_baselines(probability_eval)[0] is False

    return_eval={
        "model":{"mae":0.007,"rmse":0.010},
        "baselines":{
            "zero_return":{"mae":0.009,"rmse":0.013},
            "historical_mean":{"mae":0.008,"rmse":0.012},
        },
    }
    assert _beats_return_baselines(return_eval)[0] is True
    return_eval["model"]["rmse"]=0.013
    assert _beats_return_baselines(return_eval)[0] is False


def _live_row(*, missing=False):
    intraday={
        "available":not missing,
        "ret_5m":None if missing else 0.001,
        "ret_15m":0.002,"ret_60m":0.003,
        "realized_vol_15m":0.004,"realized_vol_60m":0.005,
    }
    return {
        "instrument":"NAS100", "price_quality":1.0,"option_quality":0.0,
        "frozen_features_json":json.dumps({
            "g1s_evidence_v2":{
                "contract_version":"g1s-t0-evidence-v2",
                "intraday":intraday,"wavelet":{},"option_context":{},"cross_asset":{},
            }
        }),
    }


def test_live_bridge_uses_same_five_features_and_rejects_missingness():
    vector,values=_live_feature_vector(_live_row())
    assert vector[:5]==pytest.approx([0.001,0.002,0.003,0.004,0.005])
    assert len(vector)==5+9
    assert list(values)==[
        "ret_5m","ret_15m","ret_60m","realized_vol_15m","realized_vol_60m"]
    with pytest.raises(ValueError,match="features unavailable"):
        _live_feature_vector(_live_row(missing=True))


class _Runtime:
    def __init__(self):
        self._conn=sqlite3.connect(":memory:",check_same_thread=False)
        self._conn.row_factory=sqlite3.Row
        self._lock=threading.RLock()
        self._conn.executescript("""
            CREATE TABLE g1s_models(
                model_id TEXT PRIMARY KEY,model_family TEXT,horizon_minutes INTEGER,
                feature_set TEXT,training_cutoff_ts REAL,raw_n INTEGER,effective_n REAL,
                positive_n INTEGER,negative_n INTEGER,training_days INTEGER,
                parameters_json TEXT,artifact_sha256 TEXT,diagnostics_json TEXT,
                authority TEXT,created_ts REAL);
            CREATE TABLE g1s_return_models(
                model_id TEXT PRIMARY KEY,model_family TEXT,horizon_minutes INTEGER,
                feature_set TEXT,training_cutoff_ts REAL,raw_n INTEGER,effective_n REAL,
                training_days INTEGER,parameters_json TEXT,diagnostics_json TEXT,
                artifact_sha256 TEXT,authority TEXT,created_ts REAL);
        """)
        _ensure_tables(self)


def test_provisional_historical_artifact_gets_separate_live_oos_cohort():
    rt=_Runtime()
    cutoff=1_700_000_000.0
    frozen=cutoff+60.0
    cohort_id=_register_live_cohort(
        rt,target=DIRECTION_TARGET,horizon=240,
        model_family=HISTORICAL_PROB_MODEL_FAMILY,
        model_id="historical-model-1",training_cutoff_ts=cutoff,frozen_at=frozen)
    row=dict(rt._conn.execute(
        "SELECT * FROM g1s_validation_cohorts WHERE validation_cohort_id=?",(cohort_id,)
    ).fetchone())
    assert row["source"]==HISTORICAL_EVIDENCE_LABEL
    assert row["feature_set"]==HISTORICAL_FEATURE_SET
    assert row["training_cutoff_ts"] < row["oos_start_ts"]
    assert row["status"]=="LIVE_VALIDATING"
    assert row["auto_promotion"]==0
    assert row["production_authority"]==0
    assert LIVE_EVIDENCE_LABEL != HISTORICAL_EVIDENCE_LABEL


def test_status_explicitly_refuses_synthetic_historical_options():
    rt=_Runtime()
    status=_historical_status(rt)
    assert status["contract_version"]==HISTORICAL_WF_CONTRACT_VERSION
    assert status["evidence_label"]==HISTORICAL_EVIDENCE_LABEL
    assert status["live_validation_label"]==LIVE_EVIDENCE_LABEL
    assert status["historical_option_features"]=="UNAVAILABLE_NOT_SYNTHESIZED"
    assert status["synthetic_option_history"] is False
    assert status["request_time_network_fetch"] is False
    assert status["request_time_full_history_scan"] is False
    assert status["production_authority"] is False
