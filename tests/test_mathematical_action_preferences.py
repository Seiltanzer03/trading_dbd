from copy import deepcopy
import math

import pytest

from seiltanzer.mathematical_action_preferences import (
    BARRIER_LOG_TOLERANCE, TARGETS, mathematical_action_preferences,
)


def fixture(short=False):
    sign = -1 if short else 1
    snapshot = {"captured_ts": 1_900_000_000,
        "strategy": {"instrument": "NAS100", "direction": "short" if short else "long"},
        "trade_geometry": {"entry": 100., "original_stop": 110. if short else 90.,
            "current": 100., "active_risk_barrier": 100.*math.exp(-sign*.0002),
            "final_take": 100.*math.exp(sign*.0002)},
        "policy_manager": {"inputs": {"horizon_minutes": 30., "rungs": []}}}
    candidates = [{"policy": p, "candidate_id": p, "parameters": {}}
                  for p in ("HOLD", "CLOSE_25", "EXIT")]
    profile = {"instrument": "NAS100", "captured_ts": snapshot["captured_ts"],
               "training_cutoff": snapshot["captured_ts"]-86400, "available": False,
               "path_predictions": {}}
    return snapshot, candidates, profile


def head(name, p=.8, baseline=.5, **kwargs):
    return {"probability": p, "baseline_probability": baseline, "quality_multiplier": .8,
            "horizon_minutes": 30., "target_semantics": TARGETS[name],
            "training_age_days": 1., **kwargs}


@pytest.mark.parametrize("short", [False, True])
def test_order_maps_actual_barriers_and_short_uses_lower_first_complement(short):
    snapshot, candidates, profile = fixture(short)
    profile["path_predictions"]["upper_before_lower"] = head("upper_before_lower")
    original = deepcopy((snapshot, candidates, profile))
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["available"] is True
    sign = -1. if short else 1.
    assert result["scores"]["HOLD"] == pytest.approx(sign*.3)
    assert result["scores"]["CLOSE_25"] == pytest.approx(sign*.15)
    assert result["scores"]["EXIT"] == pytest.approx(-sign*.3)
    assert result["evidence_family_ids"] == ["price_path"]
    assert result["independent_evidence_vote"] is False
    assert result["quality"] == .8
    assert (snapshot, candidates, profile) == original


@pytest.mark.parametrize("short", [False, True])
def test_adverse_excursion_supports_protection_but_infers_no_first_touch_order(short):
    snapshot, candidates, profile = fixture(short)
    name = "upside_excursion" if short else "downside_excursion"
    profile["path_predictions"][name] = head(name)
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"]["EXIT"] > 0
    assert result["scores"]["HOLD"] < 0
    assert result["applicability"]["HOLD"]["matched_heads"] == [name]


def test_arbitrary_trade_stop_does_not_inherit_generic_2bp_prediction():
    snapshot, candidates, profile = fixture()
    snapshot["trade_geometry"]["active_risk_barrier"] = 90.
    profile["path_predictions"]["downside_excursion"] = head("downside_excursion")
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["available"] is False
    assert result["scores"] == {}
    assert result["applicability"]["EXIT"]["status"] == "diagnostic_only"


def test_explicit_barrier_tolerance_and_nearest_remaining_rung():
    snapshot, candidates, profile = fixture()
    snapshot["trade_geometry"]["final_take"] = 102.
    snapshot["trade_geometry"]["next_rung_price"] = 100.*math.exp(.0002)
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion")
    assert mathematical_action_preferences(snapshot, candidates, profile)["available"]
    snapshot["trade_geometry"]["next_rung_price"] = 100.*math.exp(.0002+2*BARRIER_LOG_TOLERANCE)
    assert mathematical_action_preferences(snapshot, candidates, profile)["scores"] == {}


def test_rungs_in_r_units_are_translated_using_original_risk_and_orientation():
    snapshot, candidates, profile = fixture(True)
    snapshot["trade_geometry"]["final_take"] = 98.
    rung_price = 100.*math.exp(-.0002)
    snapshot["policy_manager"]["inputs"]["rungs"] = [(100.-rung_price)/10.]
    profile["path_predictions"]["downside_excursion"] = head("downside_excursion")
    assert mathematical_action_preferences(snapshot, candidates, profile)["available"]


def test_candidate_horizon_mismatch_is_diagnostic():
    snapshot, candidates, profile = fixture()
    profile["path_predictions"]["upper_before_lower"] = head("upper_before_lower", horizon_minutes=60.)
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"] == {}
    assert result["applicability"]["HOLD"]["rejections"]["upper_before_lower"] == "HEAD_ACTION_HORIZON_MISMATCH"


@pytest.mark.parametrize("field,value", [("quality_multiplier", -.1), ("quality_multiplier", 0),
    ("quality_multiplier", 2), ("training_age_days", -1), ("probability", float("nan")),
    ("baseline_probability", -1), ("target_semantics", "OTHER_TARGET")])
def test_invalid_heads_never_vote(field, value):
    snapshot, candidates, profile = fixture()
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion", **{field: value})
    assert mathematical_action_preferences(snapshot, candidates, profile)["scores"] == {}


@pytest.mark.parametrize("field", ["captured_ts", "latest_bar_end_ts", "training_cutoff"])
def test_future_profile_clock_never_votes(field):
    snapshot, candidates, profile = fixture()
    profile[field] = snapshot["captured_ts"]+1
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion")
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert not result["available"]
    assert result["reason"] == "PROFILE_CLOCK_INVALID_OR_FUTURE"


def test_early_first_touch_only_scores_exact_half_horizon_time_stop_not_direction():
    snapshot, candidates, profile = fixture()
    profile["path_predictions"]["early_first_touch"] = head("early_first_touch", p=.2)
    profile["probabilities"] = {"movement": .01, "direction": .99}
    candidates.append({"policy": "TIME_STOP", "candidate_id": "timer", "parameters": {
        "deadline_ts": snapshot["captured_ts"]+15*60}})
    candidates.append({"policy": "TIME_STOP", "candidate_id": "other_timer", "parameters": {
        "deadline_ts": snapshot["captured_ts"]+10*60}})
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"] == {"timer": pytest.approx(.3)}
    profile["path_predictions"] = {}
    assert mathematical_action_preferences(snapshot, candidates, profile)["scores"] == {}


def test_correlated_heads_are_averaged_not_added_as_extra_votes():
    snapshot, candidates, profile = fixture()
    profile["path_predictions"] = {
        "upside_excursion": head("upside_excursion"),
        "upper_before_lower": head("upper_before_lower"),
    }
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"]["HOLD"] == pytest.approx(.3)


def test_exact_candidate_stop_required_and_no_geometry_fallback():
    snapshot, candidates, profile = fixture()
    profile["path_predictions"]["downside_excursion"] = head("downside_excursion")
    candidates.extend([
        {"policy": "TIGHTEN_STOP", "candidate_id": "tight", "parameters": {
            "stop_price": snapshot["trade_geometry"]["active_risk_barrier"]}},
        {"policy": "TIGHTEN_STOP", "candidate_id": "bad", "parameters": {"stop_price": 99.}},
        {"policy": "TIGHTEN_STOP", "candidate_id": "missing", "parameters": {}},
    ])
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"]["tight"] == pytest.approx(.3)
    assert "bad" not in result["scores"] and "missing" not in result["scores"]


@pytest.mark.parametrize("field", ["captured_ts", "observed_ts", "training_cutoff"])
def test_future_head_clock_is_rejected_even_with_causal_profile(field):
    snapshot, candidates, profile = fixture()
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion",
        **{field: snapshot["captured_ts"]+1})
    assert mathematical_action_preferences(snapshot, candidates, profile)["scores"] == {}


def test_already_executed_rung_cannot_reactivate_on_price_retracement():
    snapshot, candidates, profile = fixture()
    rung = (100.*math.exp(.0002)-100.)/10.
    snapshot["policy_manager"]["inputs"].update(rungs=[rung], max_r=rung+.01)
    snapshot["trade_geometry"]["final_take"] = 102.
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion")
    assert mathematical_action_preferences(snapshot, candidates, profile)["scores"] == {}


def test_no_time_direction_from_excursion_or_order_heads():
    snapshot, candidates, profile = fixture()
    candidates = [{"policy": "TIME_STOP", "candidate_id": "timer", "parameters": {
        "deadline_ts": snapshot["captured_ts"]+15*60}}]
    profile["path_predictions"] = {name: head(name) for name in (
        "upside_excursion", "downside_excursion", "upper_before_lower")}
    assert mathematical_action_preferences(snapshot, candidates, profile)["scores"] == {}


def test_take_and_spike_need_their_actual_candidate_price():
    snapshot, candidates, profile = fixture()
    target = snapshot["trade_geometry"]["final_take"]
    candidates = [
        {"policy": "REDUCE_TAKE", "candidate_id": "take", "parameters": {"take_price": target}},
        {"policy": "REDUCE_TAKE", "candidate_id": "far_take", "parameters": {"take_price": 101.}},
        {"policy": "SCALE_OUT_ON_SPIKE", "candidate_id": "spike", "parameters": {
            "trigger_price": target, "close_fraction": .25}},
    ]
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion")
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"]["take"] == pytest.approx(-.3)
    assert result["scores"]["spike"] == pytest.approx(.075)
    assert "far_take" not in result["scores"]


def test_closer_rung_blocks_mapping_a_farther_candidate_take_or_spike():
    snapshot, candidates, profile = fixture()
    target = snapshot["trade_geometry"]["final_take"]
    snapshot["trade_geometry"]["next_rung_price"] = 100.*math.exp(.0001)
    candidates = [
        {"policy": "EXTEND_TAKE", "candidate_id": "take", "parameters": {"take_price": target}},
        {"policy": "SCALE_OUT_ON_SPIKE", "candidate_id": "spike", "parameters": {
            "trigger_price": target, "close_fraction": .25}},
    ]
    profile["path_predictions"]["upside_excursion"] = head("upside_excursion")
    result = mathematical_action_preferences(snapshot, candidates, profile)
    assert result["scores"] == {}
    assert all(row["reason"] == "CLOSER_ACTIVE_STRATEGY_GOAL_PRECEDES_CANDIDATE_TARGET"
               for row in result["applicability"].values())
