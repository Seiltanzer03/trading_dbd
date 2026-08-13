from __future__ import annotations

import math

import numpy as np
import pytest

from seiltanzer import g1_short_horizon_p2_regime_research as p2
from seiltanzer.g1_short_horizon_p2_fast_gbt import (
    FAST_CROSS_VERSION,
    FAST_GBT_VERSION,
    build_contexts_fast,
    fit_weighted_gbt_fast,
)


def test_fast_weighted_gbt_matches_reference_fixed_candidate_search():
    rng=np.random.default_rng(7)
    x=rng.normal(size=(180,7))
    y=(0.8*x[:,0]-0.4*x[:,2]+0.2*rng.normal(size=180)>0).astype(float)
    # Deliberately non-uniform dependency-style weights.
    weights=np.asarray([1.0/(1+(i%4)) for i in range(len(y))],dtype=float)

    reference=p2._fit_weighted_gbt(x,y,weights)
    fast=fit_weighted_gbt_fast(x,y,weights)

    assert fast["compute_contract"]==FAST_GBT_VERSION
    assert fast["base_rate"]==pytest.approx(reference["base_rate"],abs=1e-12)
    assert fast["base_logit"]==pytest.approx(reference["base_logit"],abs=1e-12)
    assert len(fast["stumps"])==len(reference["stumps"])
    for left,right in zip(fast["stumps"],reference["stumps"]):
        assert left["feature_index"]==right["feature_index"]
        assert left["threshold"]==pytest.approx(right["threshold"],abs=1e-12)
        assert left["left_value"]==pytest.approx(right["left_value"],abs=1e-10)
        assert left["right_value"]==pytest.approx(right["right_value"],abs=1e-10)

    p_ref=p2._predict_weighted_gbt(x,reference)
    p_fast=p2._predict_weighted_gbt(x,fast)
    assert np.max(np.abs(p_ref-p_fast)) < 1e-9


def _source(code: str, phase: float):
    bars=[]; previous=100.0; start=1_700_000_000.0
    for i in range(180):
        ts=start+i*300.0
        close=100.0*math.exp(0.00015*i+0.0015*math.sin(i/8.0+phase))
        bars.append({
            "bar_start_ts":ts,"bar_end_ts":ts+300.0,"open":previous,
            "high":max(previous,close)*1.0003,"low":min(previous,close)*0.9997,
            "close":close,"volume":1000+i,
        })
        previous=close
    return {"instrument":code,"ticker":code,"source_id":f"src-{code}","bars":bars}


def test_fast_cross_context_matches_reference_same_t0_semantics():
    sources=[_source("NAS100",0.0),_source("SP500",0.4),_source("US30",0.8)]
    reference=p2._build_contexts(sources)
    fast=build_contexts_fast(sources)
    assert FAST_CROSS_VERSION.endswith("v1")
    assert set(reference)==set(fast)
    for instrument in reference:
        assert set(reference[instrument])==set(fast[instrument])
        # Compare the final 30 contexts, including rolling correlation values.
        for ts in sorted(reference[instrument])[-30:]:
            for name in p2.CROSS_FEATURES:
                assert fast[instrument][ts][name] == pytest.approx(
                    reference[instrument][ts][name], abs=1e-9)
