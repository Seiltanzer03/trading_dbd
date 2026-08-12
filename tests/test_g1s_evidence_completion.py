from __future__ import annotations

import time

import pytest

from seiltanzer.g1_short_horizon_evidence_completion import (
    SERIOUS_OOS_REQUIRED,
    _candidate_blockers,
    _causal_baselines,
    _evaluate_model,
)
from seiltanzer.g1_short_horizon_runtime import OOS_CANDIDATE_REQUIRED


class _Runtime:
    @staticmethod
    def _dependency_key(row):
        horizon = int(row["horizon_minutes"])
        bucket = int(float(row["captured_ts"]) // (horizon * 60.0))
        return f"{row['instrument']}|{horizon}|{bucket}"


def _row(index: int, *, captured: float, resolved: float, label: str,
         p_up: float = 0.7, regime: str = "normal"):
    return {
        "observation_id": f"obs-{index}",
        "instrument": "NAS100",
        "horizon_minutes": 15,
        "captured_ts": captured,
        "resolved_ts": resolved,
        "direction_label": label,
        "p_up": p_up,
        "market_regime": regime,
        "frozen_features_json": '{"g1s_intraday":{"ret_15m":0.001}}',
    }


def test_serious_oos_candidate_gate_matches_specification():
    assert SERIOUS_OOS_REQUIRED == {
        "raw_resolved": 1000,
        "effective_n": 400,
        "positive_n": 120,
        "negative_n": 120,
        "temporal_blocks": 20,
    }
    # The installer mutates the shared runtime dict in place, so bounded status
    # materializers cannot retain the old weaker thresholds.
    assert OOS_CANDIDATE_REQUIRED == SERIOUS_OOS_REQUIRED


def test_causal_baselines_do_not_see_label_before_resolution():
    rows = [
        _row(1, captured=100.0, resolved=500.0, label="UP"),
        _row(2, captured=200.0, resolved=600.0, label="DOWN"),
        _row(3, captured=700.0, resolved=1200.0, label="UP"),
    ]
    baselines = _causal_baselines(rows)
    # At T0=200 the first outcome has not resolved yet, so persistence is neutral.
    assert baselines["naive_resolved_persistence"][1] == pytest.approx(0.5)
    # At T0=700 both earlier outcomes are now known; the latest known is DOWN.
    assert baselines["naive_resolved_persistence"][2] == pytest.approx(0.45)
    assert baselines["chronological_base_rate"][0] == pytest.approx(0.5)
    assert baselines["chronological_base_rate"][1] == pytest.approx(0.5)


def test_candidate_gate_is_insufficient_below_raw_1000_even_with_many_groups():
    base = 1_780_000_000.0
    rows = []
    for index in range(999):
        rows.append(_row(
            index,
            captured=base + index*24*60*60,
            resolved=base + index*24*60*60 + 900,
            label="UP" if index % 2 == 0 else "DOWN",
            regime="high" if index % 3 == 0 else "normal",
        ))
    observed, blockers = _candidate_blockers(rows, effective_n=999)
    assert observed["raw_resolved"] == 999
    assert "INSUFFICIENT_RAW_RESOLVED" in blockers


def test_dependency_weighted_model_report_makes_adjusted_metrics_primary():
    rows = [
        _row(1, captured=100.0, resolved=1000.0, label="UP", p_up=0.9),
        _row(2, captured=200.0, resolved=1100.0, label="UP", p_up=0.8),
        _row(3, captured=4000.0, resolved=5000.0, label="DOWN", p_up=0.2),
    ]
    report = _evaluate_model(_Runtime(), rows)
    assert report["raw_n"] == 3
    assert report["effective_n"] == 2
    assert report["weight_sum"] == pytest.approx(2.0)
    assert report["metric_weighting"] == "dependency_group_total_weight_one"
    assert report["verdict"] == "INSUFFICIENT"
    assert "naive_resolved_persistence" in report["baselines"]
    assert report["ece"] is not None
    assert report["balanced_accuracy"] is not None
    assert report["roc_auc_secondary"] is not None
    assert report["pr_auc_secondary"] is not None
    assert report["chronological_baselines_use_only_pre_t0_resolutions"] is True
