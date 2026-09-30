from types import SimpleNamespace
from dataclasses import replace

import numpy as np

from seiltanzer import extended_policy_evaluation as evaluation
from seiltanzer.ai_policy_base import PolicyInputs, simulate_option_paths


def _snapshot():
    return {
        "trade_geometry": {"entry": 100, "original_stop": 90,
                           "active_risk_barrier": 90, "current": 110,
                           "final_take": 130},
        "policy_manager": {
            "input_audit": {"rows": {"instrument_price": {
                "available": True, "status": "live", "source": "direct"}}},
            "evidence": {"data_quality": {"reliability": {"level": "высокая"}}},
            "selection_rule": {"eligible": ["HOLD"], "indifference_band_r": .03},
            "risk_constraint": {"gross_cvar_floor_r": -1.0},
            "inputs": {"r0": 1.0, "T": 3.0, "sigma_R": 1.0,
                "drift_R": 0, "skew_R": 0, "term_slope": 0,
                "horizon_minutes": 240, "max_r": 1.0,
                "rungs": [1.5, 2.0], "rung_fraction": .1,
                "be_after": 1.5, "option_available": True,
                "chain_age_sec": 10, "chain_status": "live",
                "proxy_quality": "direct", "source": "test", "stop_r": -1.0},
        },
    }


def _action(policy="MOVE_TO_BE", **parameters):
    return {"status": "READY_FOR_MANUAL_CONFIRMATION", "policy": policy,
            "parameters": parameters or {"stop_price": 100.0}}


def test_extended_stop_gets_real_counterfactual_and_gross_cvar_gate(monkeypatch):
    def simulate(inputs, **_kwargs):
        values = np.full(1200, .10 if inputs.stop_r < 0 else .18)
        return SimpleNamespace(strategy_outcome=values)
    monkeypatch.setattr(evaluation, "simulate_option_paths", simulate)
    row = evaluation.evaluate_extended_action(_snapshot(), _action())
    assert row["status"] == "eligible"
    assert row["production_authority"] is True
    assert row["expected_delta_vs_hold_r"] == .08
    assert row["worst_seed_cvar10_gross_r"] == .18
    assert row["automatic_execution_allowed"] is False


def test_extended_action_rejects_low_reliability_and_missing_geometry():
    snapshot = _snapshot()
    snapshot["policy_manager"]["evidence"]["data_quality"]["reliability"]["level"] = "низкая"
    assert evaluation.evaluate_extended_action(snapshot, _action())["reason"] == (
        "LOW_DATA_RELIABILITY_FOR_EXTENDED_OVERRIDE")
    snapshot = _snapshot()
    assert evaluation.evaluate_extended_action(snapshot, _action("TIME_STOP", deadline_ts=123))["reason"] == (
        "INVALID_TIME_STOP_DEADLINE")


def test_unverified_conditional_actions_remain_blocked_even_with_forged_parameters():
    snapshot = _snapshot()
    snapshot["captured_ts"] = 1_900_000_000
    snapshot["policy_manager"]["inputs"]["chain_status"] = "delayed"
    cases = (
        ("TRAIL_GAMMA_FLIP", {"stop_price": 105.0, "anchor": "ENTRY_PRICE"},
         "GEX_CONTEXT_NOT_A_VERIFIED_EXECUTION_ANCHOR"),
        ("SCALE_OUT_ON_SPIKE", {"trigger_price": 115.0, "close_fraction": .10},
         "SPIKE_DUPLICATES_STRATEGY_RUNG"),
        ("TIME_STOP", {"deadline_ts": 1_900_014_401.0},
         "INVALID_TIME_STOP_DEADLINE"),
    )
    for policy, parameters, reason in cases:
        result = evaluation.evaluate_extended_action(snapshot, _action(policy, **parameters))
        assert result["status"] == "blocked"
        assert result["reason"] == reason
        assert result["production_authority"] is False


def test_extended_action_rejects_no_benefit_even_if_cvar_passes(monkeypatch):
    monkeypatch.setattr(evaluation, "simulate_option_paths", lambda inputs, **kw:
                        SimpleNamespace(strategy_outcome=np.full(1200, .1)))
    row = evaluation.evaluate_extended_action(_snapshot(), _action())
    assert row["status"] == "blocked"
    assert row["reason"] == "NO_MATERIAL_ROBUST_EXPECTED_GAIN"


def test_low_quality_needs_observed_independent_and_live_support(monkeypatch):
    snapshot = _snapshot()
    snapshot['policy_manager']['evidence']['data_quality']['reliability']['level'] = 'низкая'
    snapshot['policy_manager']['gate'] = {'degraded_authority_overlay': {'evidence': {
        'adverse_families': ['live_tape', 'option_distribution'],
        'live_adverse_families': ['live_tape'], 'observed_adverse_item_count': 2}}}
    monkeypatch.setattr(evaluation, 'simulate_option_paths', lambda inputs, **kw:
        SimpleNamespace(strategy_outcome=np.full(1200, .1 if inputs.stop_r < 0 else .2)))
    row = evaluation.evaluate_extended_action(snapshot, _action())
    assert row['status'] == 'eligible'
    assert row['authority_mode'] == 'degraded_manual'
    assert row['automatic_execution_allowed'] is False
    snapshot['policy_manager']['gate']['degraded_authority_overlay']['evidence']['observed_adverse_item_count'] = 0
    assert evaluation.evaluate_extended_action(snapshot, _action())['status'] == 'blocked'


def test_counterfactual_stream_keeps_unaffected_path_ids_identical():
    data = _snapshot()["policy_manager"]["inputs"]
    base = PolicyInputs(**{**data, "rungs": tuple(data["rungs"])})
    low_take = simulate_option_paths(base, n_paths=600, n_steps=80, seed=71,
                                     paired_stable_stream=True)
    high_take = simulate_option_paths(replace(base, T=4.0), n_paths=600,
                                      n_steps=80, seed=71, paired_stable_stream=True)
    # Other paths may have exited at 3R in one replay. That must not change
    # the random shocks assigned to the paths that never touched either take.
    unaffected = (low_take.max_r < 2.9) & (high_take.max_r < 2.9)
    assert unaffected.sum() > 100
    np.testing.assert_array_equal(low_take.terminal[unaffected],
                                  high_take.terminal[unaffected])
    np.testing.assert_array_equal(low_take.strategy_outcome[unaffected],
                                  high_take.strategy_outcome[unaffected])
