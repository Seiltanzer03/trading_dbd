from __future__ import annotations

import json

import numpy as np
import pytest

from seiltanzer.edge_discovery import discovery
from seiltanzer.edge_discovery.candidate_registry import CandidateRegistry
from seiltanzer.edge_discovery.feature_view import causal_dynamics, feature_value
from seiltanzer.edge_discovery.filters import (
    CandidateTemplate,
    ConditionTemplate,
    candidate_templates,
    fit_rule,
    rule_mask,
)
from seiltanzer.edge_discovery.registry import feature_registry
from seiltanzer.edge_discovery.scoring import benjamini_hochberg
from seiltanzer.edge_discovery.validation import LiveShadowLedger
from seiltanzer.g1_short_horizon_historical_wf import _weights


def _row(ts: float, *, value: float = 0.5, instrument: str = "NAS100") -> dict:
    return {
        "instrument": instrument,
        "captured_ts": ts,
        "target_ts": ts+900,
        "horizon_minutes": 15,
        "direction_label": "UP" if int(ts) % 2 else "DOWN",
        "terminal_log_return": 0.001,
        "features": {"ret_5m": 0.001, "ret_15m": 0.002},
        "ede_features": {
            "asset": instrument, "asset_family": "EQUITY_INDICES",
            "session_utc": "US", "rv15_over_rv60": value,
            "trend_efficiency_60": value, "cross_confirmation": "SAME",
            "family_breadth": value, "market_breadth": value,
        },
    }


def _candidate(status: str = "HISTORICAL_CANDIDATE") -> dict:
    return {
        "candidate_id": "ede-candidate-test", "signal": "ret5_persistence",
        "conditions": [{"feature_id": "session_utc", "state": "US"}],
        "horizon_minutes": 15, "complexity": 1, "coverage": 0.2,
        "raw_n": 1200, "effective_n": 500,
        "improvement": {"brier": 0.01, "logloss": 0.01},
        "folds": [{"fold_index": i} for i in range(1, 5)],
        "assets": ["NAS100"], "status": status,
    }


def test_registry_is_machine_readable_inventory_and_missing_is_not_zero():
    registry = feature_registry()
    assert registry["feature_count"] >= 40
    assert registry["family_count"] >= 7
    assert len({row["feature_id"] for row in registry["features"]}) == registry["feature_count"]
    assert registry["missing_is_not_zero"] is True
    assert registry["provider_outage_is_not_market_signal"] is True
    assert registry["production_authority"] is False
    assert "option.iv" in registry["historically_unavailable"]
    assert "seiltanzer/metric_contracts.py" in registry["inventory_sources"]
    assert "seiltanzer/web/js/gex.js" in registry["inventory_sources"]


def test_missing_is_unavailable_not_zero_and_stale_is_marked():
    missing = feature_value(
        instrument="NAS100", t0=100, horizon=15, feature_id="option.iv",
        value=None, asof=None, training_eligible=True)
    assert missing.availability == "UNAVAILABLE"
    assert missing.value is None
    assert missing.training_eligible is False
    stale = feature_value(
        instrument="NAS100", t0=1000, horizon=15, feature_id="cross.correlation",
        value=0.7, asof=100, stale_after_seconds=300, training_eligible=True)
    assert stale.stale is True
    assert stale.training_eligible is False
    with pytest.raises(ValueError, match="after T0"):
        feature_value(
            instrument="NAS100", t0=100, horizon=15, feature_id="price.ret_5m",
            value=0.1, asof=101)


def test_causal_dynamics_never_change_when_future_points_are_appended():
    prefix = [(100.0+i*300.0, float(i*i)) for i in range(8)]
    original = causal_dynamics(prefix, window=5)
    extended = causal_dynamics(prefix+[(2600.0, 9999.0), (2900.0, -9999.0)], window=5)
    assert extended[:len(original)] == original
    assert all(row["causal_window_end_ts"] <= row["t0"] for row in extended)
    assert all(row["future_points_used"] is False for row in extended)


def test_quantile_thresholds_are_fit_on_train_only():
    template = CandidateTemplate((ConditionTemplate(
        "rv15_over_rv60", "train_quantile", "Q80_100"),))
    train = [_row(float(i+1), value=float(i)) for i in range(100)]
    rule = fit_rule(template, train)
    assert rule is not None
    before = rule.as_dict()
    test = [_row(1000.0, value=1e9), _row(1001.0, value=-1e9)]
    assert fit_rule(template, train).as_dict() == before
    assert rule.conditions[0].train_cutoff_ts == 100.0
    assert rule_mask(test, rule).tolist() == [False, False]


def test_relative_and_sign_filter_primitives_are_supported():
    train = [_row(float(i+1), value=float(i-50)) for i in range(100)]
    relative = CandidateTemplate((ConditionTemplate(
        "rv15_over_rv60", "train_relative", "ABOVE_MEDIAN"),))
    sign = CandidateTemplate((ConditionTemplate(
        "rv15_over_rv60", "sign", "POSITIVE"),))
    relative_rule = fit_rule(relative, train)
    sign_rule = fit_rule(sign, train)
    assert relative_rule is not None and sign_rule is not None
    test = [_row(1000.0, value=-1.0), _row(1001.0, value=1.0)]
    assert rule_mask(test, relative_rule).tolist() == [False, True]
    assert rule_mask(test, sign_rule).tolist() == [False, True]


def test_outer_test_is_never_passed_to_inner_selection(monkeypatch):
    train = [{"partition": "train"}]
    test = [{"partition": "outer_test"}]
    monkeypatch.setattr(discovery, "build_discovery_rows", lambda sources, horizon: train+test)
    monkeypatch.setattr(discovery, "_historical_folds", lambda rows, horizon: [{
        "fold_index": 1, "train": train, "test": test,
        "test_start_ts": 10.0, "test_end_ts": 20.0,
        "train_target_max_ts": 1.0, "purge_boundary_ts": 2.0,
    }])

    def inner(rows, horizon, templates):
        assert rows is train
        assert all(row["partition"] != "outer_test" for row in rows)
        return {"tested": 1, "sample_gate_passed": 0, "selected": []}

    monkeypatch.setattr(discovery, "_inner_discovery", inner)
    result = discovery.discover_horizon([], 15, candidate_templates()[:1])
    assert result["folds"][0]["outer_test_used_for_selection"] is False


def test_search_space_is_bounded_and_has_no_more_than_three_conditions():
    templates = candidate_templates()
    assert len(templates) == 248
    assert max(template.complexity for template in templates) == 3
    assert min(template.complexity for template in templates) == 1


def test_dependency_group_weights_sum_to_one():
    rows = [_row(100.0, instrument="NAS100"), _row(200.0, instrument="NAS100"),
            _row(1000.0, instrument="NAS100")]
    weights, effective = _weights(rows)
    assert effective == 2
    assert np.isclose(weights[:2].sum(), 1.0)
    assert np.isclose(weights[2], 1.0)


def test_bh_false_discovery_correction_is_monotone_and_conservative():
    adjusted = benjamini_hochberg([0.001, 0.02, 0.2, 0.8])
    assert adjusted == sorted(adjusted)
    assert all(q >= p for p, q in zip([0.001, 0.02, 0.2, 0.8], adjusted))
    assert adjusted[-1] <= 1.0


def test_rejected_candidate_is_preserved_append_only(tmp_path):
    path = tmp_path/"candidates.jsonl"
    registry = CandidateRegistry(path)
    registry.register(_candidate("REJECTED"), created_ts=1.0)
    restored = CandidateRegistry(path)
    assert restored.current("ede-candidate-test")["status"] == "REJECTED"
    with pytest.raises(ValueError, match="invalid transition"):
        restored.transition("ede-candidate-test", "HISTORICAL_CANDIDATE")
    assert len(path.read_text().splitlines()) == 1


def test_candidate_is_immutable_after_selection_and_freeze(tmp_path):
    path = tmp_path/"candidates.jsonl"
    registry = CandidateRegistry(path)
    candidate = _candidate()
    registry.register(candidate, created_ts=1.0)
    registry.transition(candidate["candidate_id"], "FROZEN_FOR_VALIDATION",
                        artifact=candidate, event_ts=2.0)
    changed = json.loads(json.dumps(candidate))
    changed["conditions"][0]["state"] = "ASIA"
    with pytest.raises(ValueError, match="immutable"):
        registry.transition(candidate["candidate_id"], "LIVE_VALIDATING",
                            artifact=changed, event_ts=3.0)
    assert registry.current(candidate["candidate_id"])["status"] == "FROZEN_FOR_VALIDATION"


def test_live_validation_writes_prediction_before_future_outcome(tmp_path):
    ledger = LiveShadowLedger(tmp_path/"live.jsonl")
    frozen = _candidate("FROZEN_FOR_VALIDATION")
    with pytest.raises(ValueError, match="future feature"):
        ledger.record_prediction(
            candidate=frozen, t0=100.0, target_ts=200.0, qualified=True,
            signal=1.0, prediction=0.6,
            feature_values={"price.ret_5m": {"value": 0.1, "asof": 101.0}})
    record_id = ledger.record_prediction(
        candidate=frozen, t0=100.0, target_ts=200.0, qualified=True,
        signal=1.0, prediction=0.6,
        feature_values={"price.ret_5m": {"value": 0.1, "asof": 100.0}},
        recorded_ts=100.0)
    with pytest.raises(ValueError, match="before target"):
        ledger.resolve(record_id, outcome=1.0, observed_ts=199.0)
    ledger.resolve(record_id, outcome=1.0, observed_ts=200.0)
    events = ledger.events()
    assert [event["event"] for event in events] == ["PREDICTION", "OUTCOME"]
    assert all(event["production_authority"] is False for event in events)


def test_signal_is_compared_to_non_signal_causal_baselines():
    rows = [_row(float(index+1)) for index in range(20)]
    predictions = discovery._predictions(rows, rows)
    assert "signal_ret5_persistence" in predictions
    assert set(predictions)-{"signal_ret5_persistence"} == {
        "constant_0_5", "causal_base_rate", "ret15_momentum"}
