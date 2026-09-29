from types import SimpleNamespace

import numpy as np

from seiltanzer import extended_policy_evaluation as evaluation


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
        "CONDITIONAL_POLICY_HAS_NO_QUANTIFIED_EXECUTION_CONTRACT")


def test_extended_action_rejects_no_benefit_even_if_cvar_passes(monkeypatch):
    monkeypatch.setattr(evaluation, "simulate_option_paths", lambda inputs, **kw:
                        SimpleNamespace(strategy_outcome=np.full(1200, .1)))
    row = evaluation.evaluate_extended_action(_snapshot(), _action())
    assert row["status"] == "blocked"
    assert row["reason"] == "NO_MATERIAL_ROBUST_EXPECTED_GAIN"
