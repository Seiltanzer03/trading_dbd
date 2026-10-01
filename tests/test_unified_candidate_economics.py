from copy import deepcopy

import numpy as np
import pytest

from seiltanzer import unified_candidate_economics as economics
from test_extended_policy_evaluation import _snapshot


def snapshot(paths=None, weights=None):
    value = _snapshot()
    value["captured_ts"] = 1_900_000_000
    value["policy_manager"]["inputs"].update(rungs=[], be_after=10.)
    value["policy_manager"]["execution_cost_model"].update(
        immediate_full_close_r=.04, deferred_full_close_r=.02)
    value["policy_manager"]["risk_constraint"]["net_cvar_floor_r"] = -1.02
    if paths is not None:
        value["policy_manager"]["unified_scenario_bank"] = {
            "r_paths": paths, "weights": weights,
            "state_space": "R_multiple_of_initial_risk", "horizon_minutes": 240,
            "captured_ts": value["captured_ts"], "distribution_kind": "weighted_empirical",
            "measure": "historical_conditional_P", "source": "frozen test bank",
        }
    return value


def candidate(policy, identity=None, eligible=True, **params):
    return {"policy": policy, "candidate_id": identity or policy,
            "parameters": params, "eligible": eligible}


def test_weighted_bank_preserves_measure_and_prices_close_fractions_against_same_hold():
    value = snapshot([[1, -1, -1], [1, 0, 0], [1, 2, 3.4]], [.05, .15, .8])
    original = deepcopy(value)
    result = economics.price_unified_candidates(value,
        [candidate(policy) for policy in economics.BASE_FRACTIONS])
    assert value == original
    assert result["shared_scenario_bank"] is True
    assert result["authoritative_bank_reused"] is False
    assert result["bank"]["measure"] == "historical_conditional_P"
    assert result["bank"]["distribution_kind"] == "weighted_empirical"
    assert result["unique_execution_replays"] == 1
    hold = result["candidates"]["HOLD"]
    assert hold["expected_net_r"] == pytest.approx(2.33)
    assert hold["cvar10_net_r"] == pytest.approx(-.52)
    for policy, fraction in economics.BASE_FRACTIONS.items():
        row = result["candidates"][policy]
        assert row["expected_net_r"] == pytest.approx(fraction * .96 + (1 - fraction) * 2.33)
        assert row["cvar10_net_r"] == pytest.approx(fraction * .96 + (1 - fraction) * -.52)
        assert row["execution_cost_r"] == pytest.approx(fraction * .04 + (1 - fraction) * .02)
        assert row["delta_expected_r"] == pytest.approx(row["expected_net_r"] - hold["expected_net_r"])
        assert row["bank_id"] == hold["bank_id"]


def test_identical_stop_geometry_has_exactly_identical_outcomes_without_replay_duplication():
    value = snapshot([[1, .7, -1], [1, 2, .2]], [.5, .5])
    rows = [candidate("HOLD"), candidate("MOVE_TO_BE", stop_price=100),
            candidate("TIGHTEN_STOP", eligible=False, stop_price=100)]
    result = economics.price_unified_candidates(value, rows)
    assert result["unique_execution_replays"] == 2
    first, second = result["candidates"]["MOVE_TO_BE"], result["candidates"]["TIGHTEN_STOP"]
    for name in ("expected_net_r", "cvar10_net_r", "delta_expected_r", "paired_delta_ci95_lower_r"):
        assert first[name] == second[name]
    assert second["original_candidate_eligible"] is False
    assert rows[2]["eligible"] is False


def test_time_stop_uses_original_common_horizon_and_never_resurrects_stopped_path():
    value = snapshot([[1, 1.2, 1.8], [1, -1.2, 3.5]], [.5, .5])
    deadline = value["captured_ts"] + 120 * 60
    result = economics.price_unified_candidates(value,
        [candidate("HOLD"), candidate("TIME_STOP", deadline_ts=deadline)])
    row = result["candidates"]["TIME_STOP"]
    assert row["expected_net_r"] == pytest.approx((1.2 - 1) * .5 - .02)
    assert row["cvar10_net_r"] == pytest.approx(-1.02)
    assert row["horizon_minutes"] == 240
    late = economics.price_unified_candidates(value,
        [candidate("TIME_STOP", deadline_ts=value["captured_ts"] + 241 * 60)])
    assert late["candidates"]["TIME_STOP"]["available"] is False


def test_conditional_spike_cost_is_applied_once_to_full_current_remainder():
    value = snapshot([[1, 1.8, -1], [1, 1.1, 0]], [.5, .5])
    result = economics.price_unified_candidates(value,
        [candidate("HOLD"), candidate("SCALE_OUT_ON_SPIKE", trigger_price=117.5, close_fraction=.25)])
    row = result["candidates"]["SCALE_OUT_ON_SPIKE"]
    # Frozen .25 at 1.75R and .75 at -1R on first path; 0R on second.
    assert row["expected_net_r"] == pytest.approx((.25 * 1.75 - .75) * .5 - .02)
    assert row["execution_cost_r"] == .02


def test_one_driver_bank_generated_for_every_policy_and_deterministic(monkeypatch):
    draws = []
    original = economics._option_driver_bank
    def draw(inputs):
        draws.append(True)
        return original(inputs)
    monkeypatch.setattr(economics, "_option_driver_bank", draw)
    value = snapshot()
    rows = [candidate(policy) for policy in economics.BASE_FRACTIONS]
    rows.extend([candidate("MOVE_TO_BE", stop_price=100),
                 candidate("TIGHTEN_STOP", stop_price=105),
                 candidate("TRAIL_GAMMA_FLIP", stop_price=105),
                 candidate("REDUCE_TAKE", take_price=125),
                 candidate("EXTEND_TAKE", take_price=140),
                 candidate("SCALE_OUT_ON_SPIKE", trigger_price=117.5, close_fraction=.25),
                 candidate("TIME_STOP", deadline_ts=value["captured_ts"] + 120 * 60)])
    result = economics.price_unified_candidates(value, rows)
    again = economics.price_unified_candidates(value, rows)
    assert len(draws) == 2  # Once per complete evaluation, never per candidate.
    assert result == again
    assert len(result["candidates"]) == 12
    assert all(row["available"] for row in result["candidates"].values())
    assert result["bank"]["path_count"] == 1200
    assert result["bank"]["exact_authoritative_bank"] is False
    assert result["bank"]["bridge_events_reproduced"] is False
    assert result["unique_execution_replays"] == 7


@pytest.mark.parametrize("field, bad, reason", [
    ("weights", [.5, -1], "INVALID_FROZEN_SCENARIO_WEIGHTS"),
    ("horizon_minutes", 60, "FROZEN_BANK_HORIZON_MISMATCH"),
    ("captured_ts", 1900000001, "FROZEN_BANK_SNAPSHOT_TIMESTAMP_MISMATCH"),
    ("r_paths", [[0, 1], [1, 2]], "FROZEN_BANK_START_R_MISMATCH"),
])
def test_invalid_frozen_bank_never_silently_substitutes_new_diffusion(field, bad, reason, monkeypatch):
    value = snapshot([[1, 1.2], [1, 2]], [.5, .5])
    value["policy_manager"]["unified_scenario_bank"][field] = bad
    monkeypatch.setattr(economics, "_option_driver_bank", lambda inputs: pytest.fail("substituted bank"))
    result = economics.price_unified_candidates(value, [candidate("HOLD")])
    assert result["available"] is False
    assert result["reason"] == reason


def test_declared_empirical_distribution_requires_actual_paths(monkeypatch):
    value = snapshot()
    value["policy_manager"]["scenario_distribution_kind"] = "weighted_empirical"
    monkeypatch.setattr(economics, "_option_driver_bank", lambda inputs: pytest.fail("substituted distribution"))
    result = economics.price_unified_candidates(value, [candidate("HOLD")])
    assert result["reason"] == "DECLARED_PATH_DISTRIBUTION_REQUIRES_FULL_FROZEN_BANK"


def test_common_net_tail_gate_does_not_restore_original_blocked_candidate():
    value = snapshot([[1, -.9, -.9]], [1.])
    value["policy_manager"]["risk_constraint"]["net_cvar_floor_r"] = -.5
    result = economics.price_unified_candidates(value,
        [candidate("HOLD"), candidate("EXIT", eligible=False)])
    assert result["candidates"]["HOLD"]["hard_risk_pass"] is False
    assert result["candidates"]["EXIT"]["hard_risk_pass"] is True
    assert result["candidates"]["EXIT"]["original_candidate_eligible"] is False
    assert result["changes_original_admission"] is False


def test_geometry_and_missing_costs_fail_closed():
    value = snapshot()
    value["trade_geometry"]["current"] = 111
    assert economics.price_unified_candidates(value, []) ["reason"] == "FROZEN_INPUTS_TRADE_GEOMETRY_MISMATCH"
    value = snapshot()
    del value["policy_manager"]["execution_cost_model"]["immediate_full_close_r"]
    assert economics.price_unified_candidates(value, []) ["reason"] == "EXECUTION_COST_MODEL_UNAVAILABLE"
