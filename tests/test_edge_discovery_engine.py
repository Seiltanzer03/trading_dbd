from __future__ import annotations

import json
import sqlite3
import threading

import numpy as np
import pytest

from seiltanzer.edge_discovery import discovery
from seiltanzer.edge_discovery.candidate_registry import (
    CandidateRegistry,
    hypothesis_id,
)
from seiltanzer.edge_discovery.feature_view import causal_dynamics, feature_value
from seiltanzer.edge_discovery.filters import (
    CandidateTemplate,
    ConditionTemplate,
    candidate_templates,
    fit_rule,
    rule_mask,
)
from seiltanzer.edge_discovery.registry import feature_registry
from seiltanzer.edge_discovery.historical import aligned_cross_asset_context
from seiltanzer.edge_discovery.prospective import ProspectiveFeatureAdapter
from seiltanzer.edge_discovery.maturity import evidence_maturity, maturity_contract
from seiltanzer.edge_discovery.ablation import family_ablation
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
    by_id = {row["feature_id"]: row for row in registry["features"]}
    assert by_id["option.barrier_probability"]["live_availability"] == "LIMITED"
    assert by_id["option.rnd_geometry"]["live_availability"] == "UNAVAILABLE"


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


def test_ready_prospective_features_replace_templates_without_expanding_search_space():
    ready = {"option.iv", "option.skew", "option_dynamics.gex_velocity", "regime.macro"}
    templates = candidate_templates(ready)
    assert len(templates) == 248
    assert max(template.complexity for template in templates) <= 3
    features = {condition.feature_id for template in templates
                for condition in template.conditions}
    assert ready <= features


def test_all_available_g1s_features_enter_bounded_search_space():
    registry = feature_registry()
    ready = {
        row["feature_id"] for row in registry["features"]
        if row["research_scope"] == "G1S" and row["training_eligibility"]
        and row["feature_id"] != "price.ret_5m"
    }
    templates = candidate_templates(ready)
    represented = {condition.feature_id for template in templates
                   for condition in template.conditions}
    assert len(templates) <= 248
    assert max(template.complexity for template in templates) <= 3
    assert ready <= represented


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
    assert [event["event"] for event in restored.events()] == [
        "HYPOTHESIS_CREATED", "EVALUATION_RECORDED"]


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


def test_signal_is_compared_to_global_ret5_and_sanity_baselines():
    rows = [_row(float(index+1)) for index in range(20)]
    predictions = discovery._predictions(rows, rows)
    assert "signal_ret5_persistence" in predictions
    assert set(predictions) == {
        "signal_ret5_persistence", "conditional_ret5_persistence",
        "global_ret5_persistence", "constant_0_5", "causal_base_rate",
        "ret15_momentum"}


def test_global_ret5_uses_full_train_and_scores_same_filtered_subset():
    global_train = [_row(float(index+1), instrument="NAS100") for index in range(40)]
    for index, row in enumerate(global_train):
        row["direction_label"] = "UP" if index < 30 else "DOWN"
        row["features"]["ret_5m"] = 1.0 if index % 2 else -1.0
    conditional_train = global_train[:10]
    test = global_train[-6:]
    result = discovery._predictions(
        global_train, test, conditional_train=conditional_train)
    reference = discovery._predictions(global_train, test)
    assert np.array_equal(
        result["global_ret5_persistence"], reference["global_ret5_persistence"])
    assert len(result["conditional_ret5_persistence"]) == len(test)
    assert len(result["global_ret5_persistence"]) == len(test)
    assert not np.array_equal(
        result["conditional_ret5_persistence"], result["global_ret5_persistence"])


def test_outer_global_and_conditional_use_identical_filtered_test_rows(monkeypatch):
    template = CandidateTemplate((ConditionTemplate(
        "session_utc", "categorical", "US"),))
    train = [_row(float(index+1)) for index in range(200)]
    test = [_row(float(index+1000)) for index in range(40)]
    for index, row in enumerate(train):
        row["ede_features"]["session_utc"] = "US" if index < 120 else "ASIA"
    for index, row in enumerate(test):
        row["ede_features"]["session_utc"] = "US" if index < 25 else "ASIA"
    calls = []
    original = discovery._predictions

    def observed(global_train, selected_test, *, conditional_train=None):
        calls.append((list(global_train), list(conditional_train or []), list(selected_test)))
        return original(global_train, selected_test, conditional_train=conditional_train)

    monkeypatch.setattr(discovery, "_predictions", observed)
    evaluation = discovery._outer_evaluation(
        {"primary_baseline_name": "GLOBAL_RET5_PERSISTENCE"},
        template, train, test)
    assert evaluation is not None
    global_train, conditional_train, filtered_test = calls[-1]
    assert len(global_train) == 200
    assert len(conditional_train) == 120
    assert len(filtered_test) == 25
    assert evaluation["rows"] == filtered_test
    assert len(evaluation["model_prediction"]) == len(evaluation["baseline_prediction"]) == 25


def test_registry_stable_hypothesis_new_dataset_evaluation_and_immutable_rejection(tmp_path):
    path = tmp_path/"registry.jsonl"
    registry = CandidateRegistry(path)
    candidate = _candidate("REJECTED") | {
        "template_id": "ede-template-stable",
        "template": [{"feature_id": "session_utc", "kind": "categorical", "state": "US"}],
        "q_value": 0.4, "reason_rejected": "INNER_FDR_Q_GT_0_10",
    }
    first = registry.register_evaluation(
        candidate, dataset_sha256="dataset-a", research_run="run-1",
        measurement_contract="v1.1", created_ts=1.0)
    same = registry.register_evaluation(
        candidate, dataset_sha256="dataset-a", research_run="run-2",
        measurement_contract="v1.1", created_ts=2.0)
    second = registry.register_evaluation(
        candidate, dataset_sha256="dataset-b", research_run="run-3",
        measurement_contract="v1.1", created_ts=3.0)
    assert first == same
    assert first != second
    assert hypothesis_id(candidate) == registry.evaluation(first)["hypothesis_id"]
    assert registry.evaluation(first)["dataset_source_sha256"] == "dataset-a"
    assert registry.evaluation(second)["dataset_source_sha256"] == "dataset-b"
    changed = json.loads(json.dumps(candidate))
    changed["q_value"] = 0.01
    with pytest.raises(ValueError, match="immutable"):
        registry.register_evaluation(
            changed, dataset_sha256="dataset-a", research_run="run-4",
            measurement_contract="v1.1", created_ts=4.0)


class _ProspectiveRuntime:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE g1s_observations(
                observation_id TEXT PRIMARY KEY, captured_ts REAL NOT NULL,
                target_ts REAL NOT NULL, instrument TEXT NOT NULL,
                horizon_minutes INTEGER NOT NULL, frozen_features_json TEXT NOT NULL)
        """)
        self._conn.execute("""
            CREATE TABLE g1s_resolutions(
                observation_id TEXT PRIMARY KEY, resolved_ts REAL NOT NULL,
                terminal_log_return REAL NOT NULL, direction_label TEXT NOT NULL,
                mfe_log_return REAL, mae_log_return REAL, path_quality_status TEXT)
        """)


def _prospective_features(t0: float, *, option_asof: float | None = None,
                          include_options: bool = True) -> str:
    option = ({
        "available": True, "iv": 0.22, "skew": -0.04,
        "quality": {"source_ts": option_asof, "source_quality": 0.9, "stale": False},
    } if include_options else {
        "available": False, "reason": "missing",
        "quality": {"source_ts": None, "source_quality": 0.0, "stale": False},
    })
    return json.dumps({"g1s_evidence_v3": {
        "price_volatility": {
            "available": True, "ret_5m": 0.01, "ret_15m": 0.02,
            "realized_vol_15m": 0.01, "realized_vol_60m": 0.02,
            "quality": {"source_ts": t0, "source_quality": 1.0, "stale": False},
        },
        "option_static": option,
    }}, sort_keys=True, separators=(",", ":"))


def test_prospective_adapter_rejects_option_asof_after_t0():
    runtime = _ProspectiveRuntime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
        ("o1", 100.0, 200.0, "NAS100", 15,
         _prospective_features(100.0, option_asof=101.0)))
    runtime._conn.commit()
    with pytest.raises(ValueError, match="option.iv rejected.*after T0"):
        ProspectiveFeatureAdapter(runtime, available_asof=150.0).rows(
            resolved_only=False, strict=True)


def test_prospective_outcome_hidden_until_target_and_missing_options_stay_missing():
    runtime = _ProspectiveRuntime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
        ("o1", 100.0, 200.0, "NAS100", 15,
         _prospective_features(100.0, include_options=False)))
    runtime._conn.commit()
    adapter = ProspectiveFeatureAdapter(runtime, available_asof=150.0)
    rows = adapter.rows(resolved_only=False)
    assert rows[0]["outcome_available"] is False
    assert rows[0]["direction_label"] is None
    assert rows[0]["feature_values"]["option.iv"]["availability"] == "UNAVAILABLE"
    assert rows[0]["feature_values"]["option.iv"]["value"] is None
    assert adapter.rows(resolved_only=True) == []


def test_nearest_causal_cross_join_accepts_past_and_rejects_future_and_stale():
    sp = _row(98.0, instrument="SP500")
    nas = _row(100.0, instrument="NAS100")
    aligned_cross_asset_context([nas, sp], max_staleness_seconds=5.0)
    meta = nas["ede_features"]["cross_join_metadata"]
    assert meta["peer_asof"] == 98.0
    assert meta["peer_age_sec"] == 2.0
    assert meta["future_peer_used"] is False
    assert sp["ede_features"]["cross_peer_count"] == 0  # NAS=100 is future at SP=98

    stale_peer = _row(50.0, instrument="SP500")
    later = _row(100.0, instrument="NAS100")
    aligned_cross_asset_context([stale_peer, later], max_staleness_seconds=20.0)
    assert later["ede_features"]["cross_peer_count"] == 0
    assert later["ede_features"]["cross_stale"] is True


def test_causal_cross_correlation_and_leave_one_out_become_available():
    rows = []
    for index in range(12):
        sp = _row(1000.0+index*10.0, instrument="SP500")
        nas = _row(1002.0+index*10.0, instrument="NAS100")
        sp["features"]["ret_5m"] = 0.001*(index+1)
        nas["features"]["ret_5m"] = 0.002*(index+1)
        rows.extend((sp, nas))
    aligned_cross_asset_context(rows, max_staleness_seconds=30.0)
    latest = rows[-1]
    assert latest["ede_features"]["cross_correlation"] == pytest.approx(1.0)
    assert latest["ede_features"]["market_breadth_peer_count"] == 1
    assert "NAS100" not in latest["ede_features"]["cross_join_metadata"]["external_instruments"]


def test_causal_bar_recomputation_marks_provenance_and_ignores_future_points():
    runtime = _ProspectiveRuntime()
    runtime._conn.execute("""
        CREATE TABLE passive_market_bars(
            instrument TEXT, bar_start_ts REAL, bar_end_ts REAL,
            open REAL, high REAL, low REAL, close REAL,
            source TEXT, quality REAL, kind TEXT, created_ts REAL)
    """)
    t0 = 10_000.0
    bars = []
    for index in range(61):
        end = t0-3600.0+index*60.0
        close = 100.0+index*0.1
        bars.append(("NAS100", end-60.0, end, close-0.1, close+0.2,
                     close-0.2, close, "direct", 1.0, "direct", t0-1.0))
    # This extreme bar exists after T0 and must not affect the recomputation.
    bars.append(("NAS100", t0, t0+60.0, 9999.0, 10000.0, 1.0, 9999.0,
                 "direct", 1.0, "direct", t0-1.0))
    runtime._conn.executemany(
        "INSERT INTO passive_market_bars VALUES(?,?,?,?,?,?,?,?,?,?,?)", bars)
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
        ("bar-o1", t0, t0+900.0, "NAS100", 15,
         _prospective_features(t0, include_options=False)))
    runtime._conn.commit()
    row = ProspectiveFeatureAdapter(runtime, available_asof=t0).rows(
        resolved_only=False)[0]
    for feature_id in (
            "price.trend_efficiency_60", "price.range_60",
            "price.drawdown_60", "price.drawup_60", "regime.trend",
            "regime.volatility"):
        value = row["feature_values"][feature_id]
        assert value["availability"] == "AVAILABLE"
        assert value["provenance"] == "CAUSAL_RECOMPUTED"
        assert value["future_points_used"] is False
        assert value["source_window_end_ts"] <= t0


def test_frozen_raw_rr_skew_mapping_repairs_missing_adapter_field():
    runtime = _ProspectiveRuntime()
    t0 = 100.0
    frozen = json.loads(_prospective_features(t0, option_asof=t0))
    frozen["g1s_evidence_v3"]["option_static"].pop("skew", None)
    frozen["option_distribution"] = {"skew": {"rr": -0.075}}
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
        ("skew-o1", t0, 200.0, "NAS100", 15, json.dumps(frozen)))
    runtime._conn.commit()
    row = ProspectiveFeatureAdapter(runtime, available_asof=150.0).rows(
        resolved_only=False)[0]
    assert row["feature_values"]["option.skew"]["value"] == pytest.approx(-0.075)
    assert row["feature_values"]["option.skew"]["asof"] == t0


def test_recovered_skew_builds_causal_velocity_without_chain_backfill():
    runtime = _ProspectiveRuntime()
    for index, t0 in enumerate((100.0, 110.0, 120.0)):
        frozen = json.loads(_prospective_features(t0, option_asof=t0))
        frozen["g1s_evidence_v3"]["option_static"].pop("skew", None)
        frozen["option_distribution"] = {"skew": {"rr": -0.08+index*0.01}}
        runtime._conn.execute(
            "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
            (f"skew-{index}", t0, t0+900.0, "NAS100", 15, json.dumps(frozen)))
    runtime._conn.commit()
    rows = ProspectiveFeatureAdapter(runtime, available_asof=120.0).rows(
        resolved_only=False)
    velocity = rows[-1]["feature_values"]["option_dynamics.skew_velocity"]
    assert velocity["availability"] == "AVAILABLE"
    assert velocity["value"] == pytest.approx(0.001)
    assert velocity["future_points_used"] is False


def test_maturity_tiers_are_predeclared_and_fdr_caps_provisional_claims():
    assert evidence_maturity(raw_n=100, effective_n=50, temporal_blocks=2) == \
        "EARLY_CONTEXT"
    assert evidence_maturity(
        raw_n=250, effective_n=100, temporal_blocks=2,
        brier_improvement=.01, logloss_improvement=.01) == "RESEARCH_SIGNAL"
    assert evidence_maturity(
        raw_n=600, effective_n=250, temporal_blocks=3,
        brier_improvement=.01, logloss_improvement=.01, q_value=.01,
        folds_evaluated=3, folds_positive=2, inner_fdr_passed=False) == \
        "RESEARCH_SIGNAL"
    assert evidence_maturity(
        raw_n=600, effective_n=250, temporal_blocks=3,
        brier_improvement=.01, logloss_improvement=.01, q_value=.01,
        folds_evaluated=3, folds_positive=2, inner_fdr_passed=True) == \
        "PROVISIONAL_EDGE"
    assert maturity_contract()["one_early_metric_may_trigger_exit_or_close"] is False


def test_g1m_and_quality_features_are_not_counted_as_g1s_predictor_failures():
    by_id = {row["feature_id"]: row for row in feature_registry()["features"]}
    assert by_id["option.barrier_probability"]["research_scope"] == "G1M_ONLY"
    assert by_id["option.rnd_geometry"]["research_scope"] == "G1M_ONLY"
    assert by_id["quality.availability"]["research_scope"] == "QUALITY_ONLY"
    assert by_id["quality.staleness"]["training_eligibility"] is False


def test_prospective_adapter_rejects_resolution_written_before_target():
    runtime = _ProspectiveRuntime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
        ("o1", 100.0, 200.0, "NAS100", 15,
         _prospective_features(100.0, include_options=False)))
    runtime._conn.execute(
        "INSERT INTO g1s_resolutions VALUES(?,?,?,?,?,?,?)",
        ("o1", 199.0, 0.01, "UP", 0.02, -0.01, "COMPLETE"))
    runtime._conn.commit()
    with pytest.raises(ValueError, match="before target_ts"):
        ProspectiveFeatureAdapter(runtime, available_asof=300.0).rows(
            resolved_only=False)


def test_prospective_adapter_joins_real_outcome_only_after_target():
    runtime = _ProspectiveRuntime()
    runtime._conn.execute(
        "INSERT INTO g1s_observations VALUES(?,?,?,?,?,?)",
        ("o1", 100.0, 200.0, "NAS100", 15,
         _prospective_features(100.0, option_asof=99.0)))
    runtime._conn.execute(
        "INSERT INTO g1s_resolutions VALUES(?,?,?,?,?,?,?)",
        ("o1", 201.0, 0.01, "UP", 0.02, -0.01, "COMPLETE"))
    runtime._conn.commit()
    hidden = ProspectiveFeatureAdapter(runtime, available_asof=199.0).rows(
        resolved_only=False)
    assert hidden[0]["outcome_available"] is False
    assert hidden[0]["direction_label"] is None
    resolved = ProspectiveFeatureAdapter(runtime, available_asof=201.0).rows(
        resolved_only=True)
    assert resolved[0]["direction_label"] == "UP"
    assert resolved[0]["terminal_log_return"] == pytest.approx(0.01)
    assert resolved[0]["mfe_log_return"] == pytest.approx(0.02)
    assert resolved[0]["retrospective_options_reconstruction"] is False


def test_leave_one_out_breadth_excludes_current_instrument():
    nas = _row(100.0, instrument="NAS100")
    spx = _row(100.0, instrument="SP500")
    nas["features"]["ret_5m"] = 0.01
    spx["features"]["ret_5m"] = -0.01
    aligned_cross_asset_context([nas, spx])
    assert nas["ede_features"]["market_breadth"] == 0.0
    assert spx["ede_features"]["market_breadth"] == 1.0
    assert nas["ede_features"]["market_breadth_peer_count"] == 1
    single = _row(200.0, instrument="NAS100")
    aligned_cross_asset_context([single])
    assert single["ede_features"]["market_breadth"] is None
    assert single["ede_features"]["cross_confirmation"] == "NEUTRAL"


def test_inner_fdr_failure_cannot_reach_outer_selection(monkeypatch):
    template = candidate_templates()[0]
    train = [_row(float(index+1)) for index in range(500)]
    validation = [_row(float(index+1000)) for index in range(200)]
    monkeypatch.setattr(discovery, "_inner_split", lambda rows, horizon: (train, validation))
    fitted = fit_rule(template, train)
    assert fitted is not None
    monkeypatch.setattr(discovery, "_evaluate_inner", lambda *args: {
        "template_id": template.template_id, "complexity": template.complexity,
        "rule": fitted, "primary_baseline_name": "GLOBAL_RET5_PERSISTENCE",
        "conditional_ret5": {"brier": 0.2, "logloss": 0.6},
        "global_ret5": {"brier": 0.21, "logloss": 0.61},
        "global_ret5_comparison": {
            "global_ret5_brier": .21, "conditional_ret5_brier": .2,
            "brier_delta": .01, "global_ret5_logloss": .61,
            "conditional_ret5_logloss": .6, "logloss_delta": .01},
        "sanity_baselines": {}, "improvement": {"brier": .01, "logloss": .01},
        "p_value": 0.5, "inner_score": .01,
    })
    result = discovery._inner_discovery(train+validation, 15, (template,))
    assert result["sample_gate_passed"] == 1
    assert result["fdr_passed"] == 0
    assert result["selected"] == []
    assert result["diagnostics"][0]["q_value"] > 0.10
