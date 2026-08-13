from __future__ import annotations

import math

import pytest

from seiltanzer.g1_short_horizon_p3_path_geometry import (
    TARGET_FUTURE_RV,
    TARGET_MAE,
    TARGET_MFE,
    _pre_t0_context,
    _target_row,
)
from seiltanzer.g1_short_horizon_p3_fast import (
    P3_FAST_CONTRACT,
    _precompute_sources,
    _target_row_fast,
    build_rows_fast,
)


def _source(n=260):
    rows=[]; previous=100.0; start=1_700_000_000.0
    for i in range(n):
        ts=start+i*300.0
        close=100.0*math.exp(0.0001*i+0.0012*math.sin(i/8.0))
        rows.append({
            "bar_start_ts":ts,"bar_end_ts":ts+300.0,"open":previous,
            "high":max(previous,close)*1.0005,
            "low":min(previous,close)*0.9995,
            "close":close,"volume":1000+i,
        })
        previous=close
    return {"instrument":"NAS100","ticker":"^NDX","source_id":"s1","bars":rows}


def test_fast_target_row_matches_reference_exact_semantics():
    source=_source()
    contexts=_pre_t0_context(source)
    captured=sorted(contexts)[100]
    reference=_target_row(source,contexts[captured],60)
    times=[float(bar["bar_end_ts"]) for bar in source["bars"]]
    index={ts:i for i,ts in enumerate(times)}[captured]
    fast=_target_row_fast(source,contexts[captured],60,times,index)
    assert reference is not None and fast is not None
    for key in ("target_ts","future_steps_5m",TARGET_FUTURE_RV,TARGET_MFE,TARGET_MAE):
        assert fast[key] == pytest.approx(reference[key],abs=1e-14)
    assert fast["path_source"] == reference["path_source"]
    assert fast["historical_sampling_interval_sec"] == 300


def test_fast_builder_reuses_precomputed_context_and_matches_row_count():
    source=_source()
    precomputed=_precompute_sources([source])
    assert P3_FAST_CONTRACT.endswith("v1")
    rows=build_rows_fast(precomputed,30)
    assert rows
    assert all(row["horizon_minutes"]==30 for row in rows)
    assert all(row["target_ts"]>row["captured_ts"] for row in rows)
    assert list(precomputed)==["NAS100"]
    assert len(precomputed["NAS100"]["contexts"]) >= len(rows)
