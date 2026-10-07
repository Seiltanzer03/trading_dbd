"""Admission/split tests use artificial in-memory measurements, never publish data."""
from copy import deepcopy
import hashlib
import importlib.util
import json

import pytest

from seiltanzer.edge_family_adapters import _action_models


BASE = 1_780_000_000.
FEATURE = "macro.expected_rate_change"


def dataset(rows=None, count=60):
    if rows is None:
        rows = []
        for i in range(count):
            captured = BASE + i * 120
            x = float(i % 10 - 5)
            rows.append({
                "trade_id": i + 1, "review_id": i + 1, "captured_ts": captured,
                "label_end_ts": captured + 60, "instrument": "NAS100",
                "family_id": "macro", "horizon_minutes": 1.,
                "geometry_sha256": "b" * 64, "features": {FEATURE: x},
                "feature_windows_sec": {}, "action": "CLOSE_25",
                "delta_net_r": .2 + .03 * x, "costs_verified": True,
                "synthetic": False,
                "feature_provenance": {FEATURE: {
                    "source_id": "official:rates", "dependency_group": "rates",
                    "source_verified": True, "global_context": False,
                    "max_age_sec": 60., "quality": 1., "source_instrument": "NAS100",
                    "observed_ts": captured - 2, "received_ts": captured - 1,
                    "published_at": captured - 2, "context_only": False}},
                "cost_provenance": {
                    "version": "broker-execution-cost-context-v1",
                    "reason": "VERIFIED_COMPLETE_BROKER_COSTS",
                    "source_verified": True,
                    "available": True, "complete_costs_available": True,
                    "assumed": False, "trade_id": i + 1, "source": "broker:costs",
                    "instrument": "NAS100", "direction": "long",
                    "broker_id": "fixture-broker", "account_id": "fixture-account",
                    "observed_ts": captured - 2, "received_ts": captured - 1,
                    "max_age_sec": 60., "currency": "USD", "quantity_units": 1.,
                    "risk_currency_per_unit": 100.,
                    "quantity_basis": "current_remaining_position", "horizon_minutes": 1.,
                    "document_sha256": "c" * 64, "deployment_sha": "d" * 40,
                    "coverage_start_epoch": captured, "coverage_end_epoch": captured + 60,
                    "broker_position_id": "fixture-position-" + str(i),
                    "position_evidence": {
                        "version": "broker-position-context-v1", "source_verified": True,
                        "measurement_kind": "executing_broker_position", "source_id": "fixture-position",
                        "evidence_sha256": "a" * 64, "document_sha256": "e" * 64,
                        "deployment_sha": "d" * 40, "observed_ts": captured - 2,
                        "received_ts": captured - 1, "max_age_sec": 60., "entry": 100.,
                        "original_stop": 90., "remaining_position_fraction": 1.},
                    "position_execution_units": {"currency": "USD", "quantity_units": 1.,
                        "risk_currency_per_unit": 100., "quantity_basis": "current_remaining_position"},
                    "immediate_full_close_r": .04, "deferred_full_close_r": .04,
                    "missing_components": {"immediate": [], "deferred": []},
                    "broker_rollover_schedule_sha256": "a" * 64,
                    "broker_rollover_audit": {
                        "version": "broker-rollover-economics-v1", "available": True,
                        "voting_weight": 0., "missing_quote_is_zero_carry": False,
                        "applied": True, "included_in_base_costs": False,
                        "reason": "MEASURED_REMAINING_QUANTITY_ROLLOVER",
                        "source_id": "broker:rollover", "observed_ts": captured - 2,
                        "received_ts": captured - 1, "currency": "USD", "event_n": 0,
                        "same_timestamp_rule": "fills_before_rollover"},
                    "components": {channel: {"component_provenance": {
                        name: {"cost_currency_per_unit": 1., "observed_ts": captured - 2,
                            "measurement_kind": "executing_broker_quote", "source_id": "broker:" + name,
                            "evidence_sha256": "f" * 64}
                        for name in ("spread", "commission", "slippage", "manual_latency")},
                        "components_r": {
                        "spread": .01, "commission": .01, "slippage": .01,
                        "manual_latency": .01}, "total_r": .04}
                        for channel in ("immediate", "deferred")}}})
            schedule = {"source_verified": True, "source_id": "broker:rollover", "instrument": "NAS100",
                "broker_id": "fixture-broker", "account_id": "fixture-account", "trade_id": i + 1,
                "direction": "long", "quantity_basis": "current_remaining_position", "quantity_units": 1.,
                "observed_ts": captured - 2, "received_ts": captured - 1, "max_age_sec": 60., "quality": 1.,
                "currency": "USD", "risk_currency_per_unit": 100., "charge_basis": "per_unit_of_remaining_position",
                "coverage_start_epoch": captured, "coverage_end_epoch": captured + 60,
                "included_in_base_costs": False, "same_timestamp_rule": "fills_before_rollover", "events": []}
            cost = rows[-1]["cost_provenance"]
            cost["broker_rollover_schedule"] = schedule
            cost["broker_rollover_schedule_sha256"] = hashlib.sha256(json.dumps(
                schedule, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {"version": "edge-family-dataset-v1", "dataset_sha256":
            hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=True).encode()).hexdigest(), "rows": rows}


def train(data, trained_at=BASE + 100_000):
    spec = importlib.util.find_spec("seiltanzer.edge_family_training")
    assert spec is not None, "grouped family trainer is not implemented"
    from seiltanzer.edge_family_training import train_family_models
    return train_family_models(data, trained_at=trained_at)


def retime(row, captured, end):
    row["captured_ts"], row["label_end_ts"] = captured, end
    for meta in row["feature_provenance"].values():
        meta.update(observed_ts=captured - 2, received_ts=captured - 1,
                    published_at=captured - 2)
    row["cost_provenance"].update(observed_ts=captured - 2, received_ts=captured - 1)
    row["cost_provenance"]["broker_rollover_audit"].update(
        observed_ts=captured - 2, received_ts=captured - 1)
    cost = row["cost_provenance"]
    cost.update(coverage_start_epoch=captured, coverage_end_epoch=captured + row["horizon_minutes"] * 60)
    cost["position_evidence"].update(observed_ts=captured - 2, received_ts=captured - 1)
    for channel in cost["components"].values():
        for item in channel["component_provenance"].values():
            item["observed_ts"] = captured - 2
    schedule = cost["broker_rollover_schedule"]
    schedule.update(observed_ts=captured - 2, received_ts=captured - 1,
        coverage_start_epoch=captured, coverage_end_epoch=captured + row["horizon_minutes"] * 60)
    cost["broker_rollover_schedule_sha256"] = hashlib.sha256(json.dumps(
        schedule, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def test_positive_oos_gain_produces_consumer_compatible_raw_coefficients():
    original = dataset()
    before = deepcopy(original)
    result = train(original)
    assert original == before
    assert result["available"] and len(result["models"]) == 1
    artifact = result["models"][0]
    assert artifact["geometry_sha256"] == "b" * 64
    assert artifact["validation"]["proper_score_gain"] > 0
    assert artifact["validation"]["sample_count"] == 20
    assert artifact["validation"]["fold_count"] == 2
    assert artifact["train_end_ts"] == BASE + 39 * 120
    assert artifact["validation_start_ts"] == BASE + 40 * 120
    assert artifact["validation_end_ts"] == BASE + 59 * 120 + 60
    model = artifact["action_models"]["CLOSE_25"]
    # 40 training points, var(x)=8.25, fixed standardized ridge lambda=1.
    assert model["coefficients"][FEATURE] == pytest.approx(.02926829268292683)
    assert model["intercept_r"] == pytest.approx(.19963414634146343)
    row = {"family_id": "macro", "instrument": "NAS100", "features": {FEATURE: 2.},
           "feature_provenance": {FEATURE: {"source_id": "real:source",
               "received_ts": BASE + 100_000, "observed_ts": BASE + 100_000,
               "max_age_sec": 1000., "dependency_group": "rates", "quality": 1.}}}
    component, reason = _action_models(row, artifact, BASE + 100_000, "ALL")
    assert reason == "OK"
    assert component["scores"]["HOLD"] == 0
    assert component["scores"]["CLOSE_25"] == pytest.approx(.2581707317073171)


@pytest.mark.parametrize("count", [0, 4, 19, 20, 29, 39])
def test_insufficient_distinct_groups_never_create_an_artifact(count):
    result = train(dataset(count=count))
    assert not result["available"] and result["models"] == []


def test_repeated_reviews_do_not_count_as_independent_trades():
    rows = dataset(count=30)["rows"]
    duplicated = deepcopy(rows)
    for row in duplicated:
        row["review_id"] += 100
        row["captured_ts"] += 1
        row["label_end_ts"] += 1
    result = train(dataset(rows + duplicated))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["group_count"] == 30


def test_entire_trade_group_is_purged_if_any_review_horizon_overlaps_validation():
    rows = dataset()["rows"]
    overlapping = deepcopy(rows[0])
    overlapping["review_id"] = 999
    retime(overlapping, BASE + 39 * 120, BASE + 40 * 120)
    rows.append(overlapping)
    result = train(dataset(rows))
    fold = result["diagnostics"]["cohorts"][0]["folds"][0]
    assert fold["purged_group_count"] == 1
    assert fold["train_group_count"] == 39
    assert fold["train_row_count"] == 39


def test_final_validation_trade_never_enters_final_fit_even_with_an_earlier_review():
    rows = dataset()["rows"]
    early = deepcopy(rows[50])
    early["review_id"] = 999
    retime(early, BASE + 39 * 120 + 60, BASE + 40 * 120)
    early["features"][FEATURE] = 1000.
    early["delta_net_r"] = 100.
    rows.append(early)
    result = train(dataset(rows))
    # Moving this group's first review into the first block while retaining
    # its later review in the second block makes the blocks interleaved.
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["reason"] == "VALIDATION_BLOCKS_NOT_CHRONOLOGICAL"


def test_final_holdout_labels_do_not_change_fitted_parameters():
    data = dataset()
    first = train(data)["models"][0]
    changed = deepcopy(data["rows"])
    for row in changed[50:]:
        row["delta_net_r"] += .1
    second = train(dataset(changed))["models"][0]
    assert first["action_models"] == second["action_models"]
    assert first["validation"]["proper_score_gain"] != second["validation"]["proper_score_gain"]


def test_negative_gain_against_train_only_mean_yields_no_artifact():
    rows = dataset()["rows"]
    for row in rows[40:]:
        row["delta_net_r"] = .2 - .3 * row["features"][FEATURE]
    result = train(dataset(rows))
    assert not result["available"] and result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["proper_score_gain"] < 0


def test_no_positive_gain_for_constant_labels():
    rows = dataset()["rows"]
    for row in rows:
        row["delta_net_r"] = .25
    assert train(dataset(rows))["models"] == []


def test_different_geometry_and_feature_windows_are_not_pooled():
    rows = dataset()["rows"]
    for row in rows[30:]:
        row["geometry_sha256"] = "e" * 64
    assert train(dataset(rows))["models"] == []
    rows = dataset()["rows"]
    for row in rows:
        window = 60. if row["trade_id"] <= 30 else 120.
        row["feature_windows_sec"] = {FEATURE: window}
        row["feature_provenance"][FEATURE]["window_seconds"] = window
    assert train(dataset(rows))["models"] == []


@pytest.mark.parametrize("change", ["future_label", "future_feature", "nonfinite_feature",
    "nonfinite_label", "unverified_costs", "missing_cost_provenance", "future_costs",
    "wrong_cost_trade", "missing_component", "wrong_total", "context_only", "synthetic"])
def test_invalid_rows_are_rejected_before_fitting(change):
    rows = dataset(count=40)["rows"]
    row = rows[0]
    if change == "future_label":
        row["label_end_ts"] = BASE + 100_001
    elif change == "future_feature":
        row["feature_provenance"][FEATURE]["received_ts"] = BASE + 1
    elif change == "nonfinite_feature":
        row["features"][FEATURE] = float("nan")
    elif change == "nonfinite_label":
        row["delta_net_r"] = float("inf")
    elif change == "unverified_costs":
        row["costs_verified"] = False
    elif change == "missing_cost_provenance":
        del row["cost_provenance"]
    elif change == "future_costs":
        row["cost_provenance"]["received_ts"] = BASE + 1
    elif change == "wrong_cost_trade":
        row["cost_provenance"]["trade_id"] = "other"
    elif change == "missing_component":
        del row["cost_provenance"]["components"]["immediate"]["components_r"]["slippage"]
    elif change == "wrong_total":
        row["cost_provenance"]["components"]["immediate"]["total_r"] = 0
    elif change == "context_only":
        row["feature_provenance"][FEATURE]["context_only"] = True
    else:
        row["synthetic"] = True
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] >= 1


def test_deterministic_output_under_row_reordering_and_hashes_bind_parameters():
    data = dataset()
    first = train(data)
    second = train(dataset(list(reversed(data["rows"]))))
    # Dataset identity changes with source byte order, fitting does not.
    assert first["models"][0]["action_models"] == second["models"][0]["action_models"]
    assert first == train(data)
    assert len(first["models"][0]["model_sha256"]) == 64


def test_dataset_hash_mismatch_and_synthetic_dataset_are_unavailable():
    data = dataset()
    data["dataset_sha256"] = "f" * 64
    assert train(data)["models"] == []
    data = dataset()
    data["synthetic"] = True
    assert train(data)["models"] == []


def test_more_than_32_features_is_not_silently_outcome_selected():
    rows = dataset()["rows"]
    for row in rows:
        for i in range(32):
            name = "macro.feature_" + str(i)
            row["features"][name] = float(i)
            row["feature_provenance"][name] = deepcopy(row["feature_provenance"][FEATURE])
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 60


def test_purged_sources_are_not_claimed_as_model_training_lineage():
    rows = dataset()["rows"]
    rows[0]["label_end_ts"] = BASE + 40 * 120
    rows[0]["feature_provenance"][FEATURE]["source_id"] = "purged-only"
    artifact = train(dataset(rows))["models"][0]
    assert "purged-only" not in artifact["source_ids"]


def test_features_from_other_families_are_not_hidden_training_inputs():
    rows = dataset()["rows"]
    for row in rows:
        row["features"]["option.iv"] = 1.
        row["feature_provenance"]["option.iv"] = deepcopy(row["feature_provenance"][FEATURE])
    assert train(dataset(rows))["models"] == []


def test_two_validation_blocks_evaluate_the_identical_frozen_mean_baseline():
    rows = dataset()["rows"]
    for row in rows[40:]:
        row["delta_net_r"] += .1
    artifact = train(dataset(rows))["models"][0]
    assert artifact["validation"]["baseline_mean_r"] == pytest.approx(.185)
    assert artifact["action_models"]["CLOSE_25"]["coefficients"][FEATURE] == pytest.approx(.02926829268292683)
    assert artifact["validation"]["folds"][1]["train_row_count"] == 40


def test_conflicting_review_identity_is_excluded_instead_of_double_weighted():
    rows = dataset(count=40)["rows"]
    conflict = deepcopy(rows[0])
    conflict["delta_net_r"] = 10.
    result = train(dataset(rows + [conflict]))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 2


def test_identical_review_duplicates_are_not_counted_twice():
    data = dataset()
    result = train(dataset(data["rows"] + [deepcopy(data["rows"][45])]))
    assert result["models"][0]["validation"]["sample_count"] == 20


def test_terminal_labels_do_not_bypass_the_full_forecast_horizon_gap():
    rows = dataset(count=40)["rows"]
    for row in rows:
        row["horizon_minutes"] = 3.
        row["cost_provenance"]["horizon_minutes"] = 3.
        retime(row, row["captured_ts"], row["captured_ts"] + 1)
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["train_group_count"] == 19


@pytest.mark.filterwarnings("error")
def test_finite_but_numerically_unsafe_inputs_fail_closed_without_warnings():
    rows = dataset()["rows"]
    for row in rows:
        row["features"][FEATURE] = 1e308
        row["delta_net_r"] = 1e308
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["reason"] == "RIDGE_FIT_NONFINITE_OR_FAILED"


def test_malformed_cost_and_feature_metadata_do_not_escape_validation():
    rows = dataset()["rows"]
    for row in rows:
        row["feature_provenance"][FEATURE]["max_age_sec"] = 1.
    assert train(dataset(rows))["models"] == []


@pytest.mark.parametrize("change", ["wrong_instrument", "future_mapping", "zero_quality",
    "missing_age", "oversized_age", "missing_dependency"])
def test_feature_admission_is_rechecked_instead_of_trusting_dataset_claims(change):
    rows = dataset(count=40)["rows"]
    meta = rows[0]["feature_provenance"][FEATURE]
    if change == "wrong_instrument":
        meta["source_instrument"] = "SP500"
    elif change == "future_mapping":
        meta["source_instrument"] = "SP500"
        meta["proxy_mapping"] = {"validated": True, "source_instrument": "SP500",
            "target_instrument": "NAS100", "mapping_id": "fixture-map", "validated_at": BASE + 1}
    elif change == "zero_quality":
        meta["quality"] = 0.
    elif change == "missing_age":
        del meta["max_age_sec"]
    elif change == "oversized_age":
        meta["max_age_sec"] = 100_000_000.
    else:
        del meta["dependency_group"]
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 1


@pytest.mark.parametrize("change", ["instrument", "version", "reason", "full_total",
    "missing_list", "rollover_unavailable", "rollover_future", "rollover_currency", "rollover_hash"])
def test_full_cost_contract_is_rechecked_before_net_label_admission(change):
    rows = dataset(count=40)["rows"]
    cost = rows[0]["cost_provenance"]
    if change == "instrument": cost["instrument"] = "SP500"
    elif change == "version": cost["version"] = "unknown"
    elif change == "reason": cost["reason"] = "PARTIAL_BROKER_COSTS_MISSING_COMPONENTS"
    elif change == "full_total": cost["immediate_full_close_r"] = .05
    elif change == "missing_list": cost["missing_components"]["deferred"] = ["commission"]
    elif change == "rollover_unavailable": cost["broker_rollover_audit"]["available"] = False
    elif change == "rollover_future": cost["broker_rollover_audit"]["received_ts"] = BASE + 1
    elif change == "rollover_currency": cost["broker_rollover_audit"]["currency"] = "EUR"
    else: cost["broker_rollover_schedule_sha256"] = "not-a-digest"
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 1


def test_interleaved_repeated_reviews_cannot_be_called_chronological_validation_blocks():
    rows = dataset()["rows"]
    late = deepcopy(rows[40])
    late["review_id"] = 999
    retime(late, BASE + 55 * 120, BASE + 55 * 120 + 60)
    result = train(dataset(rows + [late]))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["reason"] == "VALIDATION_BLOCKS_NOT_CHRONOLOGICAL"


def test_overlapping_validation_label_horizons_fail_closed():
    rows = dataset()["rows"]
    rows[49]["label_end_ts"] = BASE + 50 * 120
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["reason"] == "VALIDATION_BLOCKS_NOT_CHRONOLOGICAL"


def test_first_validation_block_labels_do_not_refit_the_second_block_model():
    original = train(dataset())["models"][0]
    changed = dataset()["rows"]
    for row in changed[40:50]:
        row["delta_net_r"] += .1
    artifact = train(dataset(changed))["models"][0]
    assert artifact["action_models"] == original["action_models"]
    assert artifact["validation"]["folds"][1]["model_mse"] == original["validation"]["folds"][1]["model_mse"]


@pytest.mark.parametrize("change", ["component_missing", "component_future", "component_assumed",
    "component_hash", "component_value", "coverage", "position_missing", "position_future",
    "position_sha", "position_direction", "units", "rollover_schedule", "wrong_trade_type"])
def test_retained_independent_position_and_component_evidence_is_revalidated(change):
    rows = dataset(count=40)["rows"]
    cost = rows[0]["cost_provenance"]
    item = cost["components"]["immediate"]["component_provenance"]["spread"]
    if change == "component_missing": del cost["components"]["immediate"]["component_provenance"]
    elif change == "component_future": item["observed_ts"] = BASE + 1
    elif change == "component_assumed": item["measurement_kind"] = "assumed"
    elif change == "component_hash": item["evidence_sha256"] = "broken"
    elif change == "component_value": item["cost_currency_per_unit"] = 2.
    elif change == "coverage": cost["coverage_end_epoch"] -= 1
    elif change == "position_missing": del cost["position_evidence"]
    elif change == "position_future": cost["position_evidence"]["received_ts"] = BASE + 1
    elif change == "position_sha": cost["position_evidence"]["deployment_sha"] = "e" * 40
    elif change == "position_direction": cost["position_evidence"]["original_stop"] = 110.
    elif change == "units": cost["position_execution_units"]["quantity_units"] = 2.
    elif change == "rollover_schedule": cost["broker_rollover_schedule"]["events"] = [{"scheduled_epoch": BASE + 20., "charge_currency_per_unit": 100.}]
    else: cost["trade_id"] = str(cost["trade_id"])
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 1


def test_only_explicit_verified_macro_global_sources_may_omit_instrument_mapping():
    rows = dataset()["rows"]
    for row in rows:
        row["feature_provenance"][FEATURE].update(source_instrument="", global_context=True)
    assert train(dataset(rows))["available"]
    for row in rows:
        row["feature_provenance"][FEATURE]["global_context"] = False
    assert train(dataset(rows))["models"] == []


def test_verified_global_event_releases_are_admitted_with_causal_consensus():
    rows = dataset()["rows"]
    name = "event.cpi.surprise"
    for row in rows:
        row["family_id"] = "event"
        row["features"] = {name: row["features"][FEATURE]}
        meta = row["feature_provenance"].pop(FEATURE)
        meta.update(source_instrument="", global_context=True,
            release_id="cpi-release-" + str(row["trade_id"]), period="2026-09", unit="pct",
            consensus_source_id="official:consensus", supporting_source_ids=["official:consensus"],
            consensus_received_ts=row["captured_ts"] - 3)
        meta["consensus_provenance"] = {**deepcopy(meta), "source_id": "official:consensus",
            "observed_ts": row["captured_ts"] - 4, "received_ts": row["captured_ts"] - 3,
            "published_at": None}
        row["feature_provenance"] = {name: meta}
    result = train(dataset(rows))
    assert result["available"]
    assert result["diagnostics"]["rejected_row_count"] == 0
    assert result["models"][0]["family_id"] == "event"
    for row in rows:
        row["feature_provenance"][name]["global_context"] = False
    rejected = train(dataset(rows))
    assert rejected["models"] == []
    assert rejected["diagnostics"]["rejected_row_count"] == len(rows)


@pytest.mark.parametrize("identity", ["release_id", "dependency_group"])
def test_one_macro_release_cannot_become_sixty_independent_trades(identity):
    rows = dataset()["rows"]
    for row in rows:
        row["feature_provenance"][FEATURE][identity] = (
            "release:one" if identity == "dependency_group" else "one")
    result = train(dataset(rows))
    assert result["models"] == []
    cohort = result["diagnostics"]["cohorts"][0]
    assert cohort["group_count"] == 1
    assert cohort["trade_group_count"] == 60


def test_shared_macro_release_purges_the_entire_connected_trade_group():
    rows = dataset()["rows"]
    for index in (10, 45):
        rows[index]["feature_provenance"][FEATURE]["release_id"] = "shared-release"
    result = train(dataset(rows))
    assert result["available"]
    cohort = result["diagnostics"]["cohorts"][0]
    assert cohort["group_count"] == 59
    assert cohort["folds"][0]["purged_group_count"] == 1
    assert cohort["folds"][0]["train_row_count"] == 38


def test_release_dependencies_are_transitive_across_features_and_trade_reviews():
    rows = dataset(count=40)["rows"]
    # A-B and B-C are one dependency component, even with separate source IDs.
    for index, releases in enumerate((("A",), ("A", "B"), ("B",))):
        for ordinal, release in enumerate(releases):
            name = FEATURE if ordinal == 0 else "macro.second"
            rows[index]["features"][name] = rows[index]["features"][FEATURE]
            rows[index]["feature_provenance"][name] = {
                **rows[index]["feature_provenance"][FEATURE], "release_id": release}
    # Keep a single cohort's feature schema throughout.
    for row in rows:
        row["features"].setdefault("macro.second", row["features"][FEATURE])
        row["feature_provenance"].setdefault("macro.second", deepcopy(row["feature_provenance"][FEATURE]))
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["group_count"] == 38


def test_unused_feature_provenance_does_not_change_independent_training_groups():
    rows = dataset()["rows"]
    for row in rows:
        row["feature_provenance"]["unused"] = {"release_id": "unconsumed"}
    result = train(dataset(rows))
    assert result["available"]
    assert result["diagnostics"]["cohorts"][0]["group_count"] == 60


@pytest.mark.filterwarnings("error")
def test_finite_errors_with_overflowing_mean_scores_fail_closed():
    rows = dataset()["rows"]
    for row in rows[40:]:
        row["delta_net_r"] = 1e154
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["cohorts"][0]["reason"] == "VALIDATION_SCORE_NONFINITE"


def test_supporting_feature_sources_are_preserved_in_model_lineage():
    rows = dataset()["rows"]
    for row in rows:
        row["feature_provenance"][FEATURE]["supporting_source_ids"] = ["official:supporting"]
    artifact = train(dataset(rows))["models"][0]
    assert artifact["source_ids"] == ["official:rates", "official:supporting"]


@pytest.mark.parametrize("change", ["future_receipt", "future_observed", "period", "unit", "release_id", "missing", "unverified"])
def test_event_consensus_provenance_is_independently_causal_and_exact(change):
    rows = dataset(count=40)["rows"]
    name = "event.cpi.surprise"
    for row in rows:
        row["family_id"] = "event"
        row["features"] = {name: row["features"][FEATURE]}
        meta = row["feature_provenance"].pop(FEATURE)
        meta.update(release_id="cpi-release", period="2026-09", unit="pct",
            consensus_source_id="official:consensus", supporting_source_ids=["official:consensus"],
            consensus_received_ts=row["captured_ts"] - 3)
        meta["consensus_provenance"] = {**deepcopy(meta), "source_id": "official:consensus",
            "observed_ts": row["captured_ts"] - 4, "received_ts": row["captured_ts"] - 3,
            "published_at": None}
        row["feature_provenance"] = {name: meta}
    meta = rows[0]["feature_provenance"][name]
    if change == "missing": del meta["consensus_provenance"]
    elif change == "future_receipt": meta["consensus_provenance"]["received_ts"] = BASE
    elif change == "future_observed": meta["consensus_provenance"]["observed_ts"] = BASE
    elif change == "unverified": meta["consensus_provenance"]["source_verified"] = False
    else: meta["consensus_provenance"][change] = "other"
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 1


@pytest.mark.parametrize("location", ["cost", "position", "component", "feature"])
def test_synthetic_source_or_cost_evidence_cannot_hide_behind_real_row_flag(location):
    rows = dataset(count=40)["rows"]
    cost = rows[0]["cost_provenance"]
    targets = {"cost": cost, "position": cost["position_evidence"],
        "component": cost["components"]["immediate"]["component_provenance"]["spread"],
        "feature": rows[0]["feature_provenance"][FEATURE]}
    targets[location]["synthetic"] = True
    result = train(dataset(rows))
    assert result["models"] == []
    assert result["diagnostics"]["rejected_row_count"] == 1


def received_history_dataset(family='intermarket', *, first_source_id=None):
    """Actual producer→adapter→frozen dataset fixtures, never published inputs."""
    from test_edge_family_history import intermarket, position, T0 as SOURCE_T0
    from test_edge_family_dataset import snapshot, record, archive, build, T0 as REVIEW_T0
    source = intermarket('NAS100') if family == 'intermarket' else position()
    shift = REVIEW_T0 - SOURCE_T0 - (60 if family == 'intermarket' else 0)
    def rebase(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (key.endswith('_ts') or key.endswith('_at')) and isinstance(item, (int, float)):
                    value[key] = item + shift
                else: rebase(item)
        elif isinstance(value, list):
            for item in value: rebase(item)
    rebase(source)
    if family == 'intermarket':
        if first_source_id is not None:
            source['historical_series'][0]['source_id'] = first_source_id
        source['available_at'] = REVIEW_T0 - 10
        for series in source['historical_series']:
            series['available_at'] = REVIEW_T0 - 10
            for bar in series['bars']:
                bar[0] += shift; bar[2] = REVIEW_T0 - 10
    else:
        source['proxy_mapping'] = dict(source_instrument='CFTC088691', target_instrument='NAS100',
            validated=True, mapping_id='ci-position-map', validated_at=REVIEW_T0 - 1000)
    frozen = snapshot(); frozen['edge_family_sources'] = {family: [source]}
    return build(archive(record(frozen))), REVIEW_T0


@pytest.mark.parametrize('family', ['intermarket', 'positioning'])
def test_actual_history_producer_dataset_passes_trainer_admission(family):
    value, captured = received_history_dataset(family)
    original = deepcopy(value)
    result = train(value, trained_at=captured + 100000)
    assert value == original
    assert result['diagnostics']['accepted_row_count'] == 5
    assert result['diagnostics']['rejected_row_count'] == 1  # HOLD has no fit target.
    assert not result['models']  # One trade cannot satisfy the existing OOS floors.


@pytest.mark.parametrize('change', ['future_receipt', 'context_only', 'horizon_mismatch'])
def test_trainer_rejects_review_nested_history_reproductions(change):
    value, captured = received_history_dataset()
    for row in value['rows']:
        for meta in row['feature_provenance'].values():
            if change == 'future_receipt': meta['constituent_provenance'][0]['received_ts'] = captured + 100
            if change == 'context_only': meta['applicability_provenance'][0]['context_only'] = True
            if change == 'horizon_mismatch': meta['applicability_provenance'][0]['horizon_minutes'] = 15
    result = train(dataset(value['rows']), trained_at=captured + 100000)
    assert result['diagnostics']['accepted_row_count'] == 0
    assert result['diagnostics']['rejected_row_count'] == 6


@pytest.mark.parametrize('change', [
    'aggregate_receipt', 'missing_constituents', 'null_constituents', 'empty_constituents',
    'unknown_contract', 'missing_contract', 'missing_hash_map', 'bad_hash', 'contradictory_hash',
    'supporting_ids', 'duplicate_supporting_ids', 'provider', 'quote', 'base', 'symbol',
    'endpoint', 'duplicate_constituent', 'unverified', 'nested_context', 'nested_horizon',
    'null_applicability', 'deep_applicability', 'oversize_applicability',
])
def test_trainer_rejects_malformed_received_history_metadata(change):
    value, captured = received_history_dataset()
    for row in value['rows']:
        for meta in row['feature_provenance'].values():
            proof = meta['constituent_provenance'][0]
            if change == 'aggregate_receipt': meta['received_ts'] += 1
            if change == 'missing_constituents': meta.pop('constituent_provenance')
            if change == 'null_constituents': meta['constituent_provenance'] = None
            if change == 'empty_constituents': meta['constituent_provenance'] = []
            if change == 'unknown_contract': meta['history_contract_version'] = 'unknown'
            if change == 'missing_contract': meta.pop('history_contract_version', None)
            if change == 'missing_hash_map': meta.pop('supporting_body_sha256', None)
            if change == 'bad_hash': proof['body_sha256'] = 'x' * 64
            if change == 'contradictory_hash': proof['body_sha256'] = 'f' * 64
            if change == 'supporting_ids': meta['supporting_source_ids'] = ['unrelated-source']
            if change == 'duplicate_supporting_ids': meta['supporting_source_ids'] *= 2
            if change == 'provider': proof['provider'] = 'OTHER'
            if change == 'quote': proof['quote_currency'] = 'USDT'
            if change == 'base': proof['base_currency'] = 'UNRELATED'
            if change == 'symbol': proof['symbol'] = 'UNRELATED-USD'
            if change == 'endpoint': proof['start_ts'] += 60
            if change == 'duplicate_constituent': meta['constituent_provenance'].append(deepcopy(proof))
            if change == 'unverified': proof['source_verified'] = False
            if change == 'nested_context': meta['applicability_provenance'][0]['nested'] = {'context_only': True}
            if change == 'nested_horizon': meta['applicability_provenance'][0]['nested'] = {'horizon_minutes': 15}
            if change == 'null_applicability': meta['applicability_provenance'] = None
            if change == 'deep_applicability':
                nested = {}
                for _ in range(9): nested = {'nested': nested}
                meta['applicability_provenance'][0]['extra'] = nested
            if change == 'oversize_applicability': meta['applicability_provenance'][0]['extra'] = 'x' * 8001
    result = train(dataset(value['rows']), trained_at=captured + 100000)
    assert result['diagnostics']['accepted_row_count'] == 0
    assert result['diagnostics']['rejected_row_count'] == 6


@pytest.mark.parametrize('change', ['unit', 'kind', 'category', 'venue', 'series_id',
    'publication', 'unverified', 'report_gap', 'hash', 'supporting_ids'])
def test_trainer_rejects_position_constituent_inconsistency(change):
    value, captured = received_history_dataset('positioning')
    for row in value['rows']:
        for name, meta in row['feature_provenance'].items():
            if 'constituent_provenance' not in meta: continue  # Legacy current net.
            proof = meta['constituent_provenance'][1]
            if change in {'unit', 'kind', 'category', 'venue', 'series_id'}: proof[change] = 'OTHER'
            if change == 'publication': proof['published_at'] = captured + 1
            if change == 'unverified': proof['source_verified'] = False
            if change == 'report_gap': proof['report_ts'] += 60
            if change == 'hash': proof['body_sha256'] = 'f' * 64
            if change == 'supporting_ids': meta['supporting_source_ids'] = ['OTHER']
    result = train(dataset(value['rows']), trained_at=captured + 100000)
    assert result['diagnostics']['accepted_row_count'] == 0
    assert result['diagnostics']['rejected_row_count'] == 6


def test_trainer_legacy_applicability_is_recursive_without_inventing_history():
    value = dataset(count=1)
    value['rows'][0]['feature_provenance'][FEATURE]['applicability_provenance'] = [
        {'nested': {'horizon_minutes': 3}}]
    result = train(dataset(value['rows']))
    assert result['diagnostics']['accepted_row_count'] == 0


def test_trainer_requires_same_source_identity_and_hash_across_history_features():
    value, captured = received_history_dataset()
    for row in value['rows']:
        name = 'intermarket.COINBASEBTC-USD.return_5m_lag_1m'
        meta = row['feature_provenance'][name]
        proof = meta['constituent_provenance'][0]
        proof['body_sha256'] = 'f' * 64
        meta['supporting_body_sha256'][proof['source_id']] = 'f' * 64
    result = train(dataset(value['rows']), trained_at=captured + 100000)
    assert result['diagnostics']['accepted_row_count'] == 0


def test_history_hash_manifest_source_ids_are_not_scope_declaration_keys():
    value, captured = received_history_dataset(first_source_id='horizon_minutes')
    result = train(value, trained_at=captured + 100000)
    assert result['diagnostics']['accepted_row_count'] == 5
