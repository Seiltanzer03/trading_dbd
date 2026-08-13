from __future__ import annotations

import math
import sqlite3
import threading

import numpy as np
import pytest

from seiltanzer.g1_short_horizon_champion_runtime import DIRECTION_TARGET
from seiltanzer.g1_short_horizon_p2_regime_research import (
    CROSS_FEATURES,
    FEATURE_FAMILIES,
    FOLD_JOINT_NON_DEGRADE_REQUIRED,
    P2_CONTRACT_VERSION,
    P2_EVIDENCE_LABEL,
    _build_contexts,
    _cross_features,
    _inner_split,
    _learn_zero_alpha,
    _source_context,
    _winner_gate,
    p2_status,
)


def _bars(n=300, *, start=1_700_000_000.0, drift=0.0002,
          gap_after=None, gap_seconds=0.0):
    rows=[]; shift=0.0
    previous=100.0
    for i in range(n):
        if gap_after is not None and i > gap_after:
            shift=gap_seconds
        start_ts=start+i*300.0+shift
        close=100.0*math.exp(drift*i+0.001*math.sin(i/9.0))
        rows.append({
            "bar_start_ts":start_ts,"bar_end_ts":start_ts+300.0,
            "open":previous,"high":max(previous,close)*1.0003,
            "low":min(previous,close)*0.9997,"close":close,"volume":1000+i,
        })
        previous=close
    return rows


def _source(code, bars):
    return {"instrument":code,"ticker":code,"source_id":f"src-{code}","bars":bars}


def test_source_context_is_completed_bar_only_and_does_not_bridge_overnight_gap():
    bars=_bars(260,gap_after=129,gap_seconds=8*3600)
    context=_source_context(_source("NAS100",bars))
    assert context
    before=bars[129]["bar_end_ts"]
    after=bars[130]["bar_end_ts"]
    assert after-before > 7*3600
    # A fresh 60m regime context cannot exist immediately after an 8h gap.
    assert not any(after <= ts < after+60*60 for ts in context)
    later=min(ts for ts in context if ts >= after+60*60)
    values=context[later]
    assert set(("ret_5m","ret_15m","ret_60m","realized_vol_15m","realized_vol_60m")) <= set(values)
    assert all(math.isfinite(v) for v in values.values())


def test_cross_asset_context_requires_exact_same_t0_not_stale_peer_carry():
    target=_source_context(_source("NAS100",_bars(180,start=1_700_000_000.0)))
    peer=_source_context(_source("SP500",_bars(180,start=1_700_000_000.0,drift=0.00015)))
    contexts={"NAS100":target,"SP500":peer}
    times={code:sorted(rows) for code,rows in contexts.items()}
    ts=sorted(target)[-1]
    own=target[ts]
    full=_cross_features("NAS100",ts,own,contexts,times)
    assert full["cross_peer_fraction"] == pytest.approx(1.0)
    assert math.isfinite(full["cross_mean_corr_60"])

    # Remove only this peer T0. Earlier peer bars still exist, but must not be
    # carried forward and called contemporaneous cross-asset evidence.
    peer_without=dict(peer); peer_without.pop(ts,None)
    contexts2={"NAS100":target,"SP500":peer_without}
    times2={code:sorted(rows) for code,rows in contexts2.items()}
    missing=_cross_features("NAS100",ts,own,contexts2,times2)
    assert missing["cross_peer_fraction"] == 0.0
    assert missing["cross_breadth_ret15"] == 0.0
    assert missing["cross_mean_corr_60"] == 0.0


def _wf_rows(n=1000,horizon=60):
    start=1_700_000_000.0
    rows=[]
    for i in range(n):
        captured=start+i*300.0
        target=captured+horizon*60.0
        r=0.001*math.sin(i/11.0)
        rows.append({
            "instrument":"NAS100" if i%2==0 else "SP500",
            "captured_ts":captured,"target_ts":target,"horizon_minutes":horizon,
            "direction_label":"UP" if r>0 else "DOWN",
            "terminal_log_return":r,
            "features":{"ret_5m":0.0001*math.sin(i/5.0),"ret_15m":0.0002*math.sin(i/7.0)},
            "p2_features":{},
        })
    return rows


def test_inner_selection_split_is_purged_and_nested_inside_outer_train():
    rows=_wf_rows()
    train,validation=_inner_split(rows,60)
    assert train and validation
    validation_start=min(float(row["captured_ts"]) for row in validation)
    assert max(float(row["target_ts"]) for row in train) < validation_start-300.0
    assert max(float(row["captured_ts"]) for row in train) < validation_start


def test_zero_anchor_alpha_is_analytic_clipped_and_can_choose_zero():
    y=np.asarray([1.0,2.0,3.0]); p=np.asarray([1.0,2.0,3.0]); w=np.ones(3)
    assert _learn_zero_alpha(y,p,w) == pytest.approx(1.0)
    assert _learn_zero_alpha(-y,p,w) == 0.0
    assert 0.0 <= _learn_zero_alpha(y,p*10,w) <= 1.0


def test_winner_gate_requires_both_metrics_and_three_joint_outer_folds():
    evaluation={
        "fold_count":4,"fold_joint_non_degrade_n":FOLD_JOINT_NON_DEGRADE_REQUIRED,
        "model":{"brier":0.240,"logloss":0.670},
        "baselines":{
            "ret5_persistence":{"brier":0.250,"logloss":0.690},
            "constant_0_5":{"brier":0.251,"logloss":0.693},
        },
    }
    gate=_winner_gate(DIRECTION_TARGET,evaluation,5000,1200)
    assert gate["historical_winner"] is True
    assert gate["robustness_gate"] is True
    evaluation["fold_joint_non_degrade_n"]=2
    assert _winner_gate(DIRECTION_TARGET,evaluation,5000,1200)["historical_winner"] is False
    evaluation["fold_joint_non_degrade_n"]=4
    evaluation["model"]["brier"]=0.2495  # only 0.2% better, below fixed 0.5% gate
    assert _winner_gate(DIRECTION_TARGET,evaluation,5000,1200)["historical_winner"] is False


def test_feature_families_are_predeclared_and_cross_family_has_explicit_availability():
    assert tuple(FEATURE_FAMILIES) == ("BASE_P2","REGIME_P2","CROSS_P2","REGIME_CROSS_P2")
    assert "cross_peer_fraction" in CROSS_FEATURES
    assert "family_peer_fraction" in CROSS_FEATURES
    assert set(FEATURE_FAMILIES["BASE_P2"]) < set(FEATURE_FAMILIES["REGIME_CROSS_P2"])


class _Runtime:
    def __init__(self):
        self._conn=sqlite3.connect(":memory:",check_same_thread=False)
        self._conn.row_factory=sqlite3.Row
        self._lock=threading.RLock()


def test_status_has_no_live_parity_promotion_or_authority():
    rt=_Runtime()
    status=p2_status(rt)
    assert status["contract_version"] == P2_CONTRACT_VERSION
    assert status["evidence_label"] == P2_EVIDENCE_LABEL
    assert status["outer_test_used_for_selection"] is False
    assert status["historical_options_used"] is False
    assert status["live_parity_ready"] is False
    assert status["auto_promotion"] is False
    assert status["production_authority"] is False
