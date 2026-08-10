from copy import deepcopy
from dataclasses import replace

import pytest

from seiltanzer import ai_policy_v15
from seiltanzer.ai_policy_base import POLICY_FRACTIONS, PolicyInputs
from seiltanzer.ai_verdict_v17 import _dynamic_block
from seiltanzer.derived_scenario_ensemble import (
    SCENARIO_ORDER,
    _choose_candidate,
    _scenario_inputs,
    evaluate_derived_scenarios,
    scenario_materiality,
)


def _metric(value, slope, acceleration=0.0, confidence=0.8):
    return {
        "available": True, "value": value, "slope": slope,
        "acceleration": acceleration, "noise": 0.02,
        "time_span_minutes": 12, "confidence": confidence,
    }


def _tick():
    return {
        "option_derivative_state": {
            "available": True, "option_state_confidence": 0.8,
            "metrics": {
                "barrier_ev": _metric(0.2, -0.01, -0.001),
                "width": _metric(1.8, 0.01),
                "iv": _metric(0.25, 0.002),
                "rv": _metric(0.20, 0.001),
                "skew": _metric(0.10, 0.002),
            },
        },
        "interaction_state": {"items": [{
            "name": "gex_stiffness_x_live_price_impulse", "score": 0.4,
        }]},
        "analytics": {"cross_asset": {
            "observed_pairs": 10, "network_tension": 0.2,
            "fragmentation": 0.1, "max_break_velocity": 0.15,
            "active_breaks_count": 1,
        }},
    }


def _inputs():
    return PolicyInputs(
        r0=0.2, T=2.0, sigma_R=1.0, drift_R=0.0, skew_R=0.0,
        term_slope=0.0, horizon_minutes=360, max_r=0.2,
        rungs=(1.0, 1.5, 2.0), rung_fraction=0.1, be_after=1.5,
        option_available=True, chain_age_sec=30, chain_status="live",
        proxy_quality="reference_proxy", source="test",
    )


def _run_once(inputs, *, n_paths, n_steps, seed):
    underlying = 0.45 + inputs.drift_R - 0.25 * (inputs.sigma_R - 1.0) - 0.2 * inputs.skew_R
    metrics = {}
    for name, fraction in POLICY_FRACTIONS.items():
        metrics[name] = {
            "name": name,
            "expected_final_r": (1 - fraction) * underlying + fraction * inputs.r0,
            "median_final_r": (1 - fraction) * underlying + fraction * inputs.r0,
            "cvar10_r": (1 - fraction) * (-0.78 * inputs.sigma_R) + fraction * inputs.r0,
            "p_final_loss": max(0.0, 0.45 * (1 - fraction)),
        }
    return metrics, object()


def _choice(metrics, r0, *, cvar_floor):
    eligible = [name for name, row in metrics.items()
                if row["cvar10_r"] >= cvar_floor]
    winner = max(eligible or metrics, key=lambda name: metrics[name]["expected_final_r"])
    return winner, {"cvar_floor_r": cvar_floor, "eligible": eligible}


def test_scenario_ensemble_has_required_states_metrics_and_bounded_weights():
    ensemble = evaluate_derived_scenarios(
        inputs=_inputs(), tick=_tick(), old_policy="HOLD",
        run_once=_run_once, raw_policy_choice=_choice,
        floor_for_r=lambda _r: -1.0, policy_fractions=POLICY_FRACTIONS,
        source_stability={"winner_shares": {"HOLD": 0.75}},
    )
    assert tuple(row["name"] for row in ensemble["scenarios"]) == SCENARIO_ORDER
    assert sum(row["weight"] for row in ensemble["scenarios"]) == pytest.approx(1.0)
    assert ensemble["family"] == "option_distribution"
    assert ensemble["independent_vote"] is False
    assert ensemble["promotion_allowed"] is False
    assert ensemble["materiality_contract"]["weight_min"] == 0.05
    assert all("material" in row and "materiality_reason" in row
               for row in ensemble["scenarios"])
    for row in ensemble["policies"].values():
        assert set(row) >= {
            "expected_net_r", "median_net_r", "cvar10_net_r", "p_loss",
            "worst_stress_r", "stress_survival", "policy_stability",
            "source_stability",
        }
        assert 0 <= row["stress_survival"] <= 1
        assert 0 <= row["policy_stability"] <= 1


def test_gamma_and_correlation_stresses_have_distinct_nondirectional_mechanics():
    inputs = _inputs()
    inputs = replace(inputs, drift_R=0.12)
    scenarios = _scenario_inputs(inputs, {
        "edge_direction": 1.0,
        "signals": {"gamma_stress": 0.8, "correlation_stress": 0.8},
    })
    gamma = scenarios["GAMMA_STRESS"]
    correlation = scenarios["CORRELATION_STRESS"]
    assert gamma.drift_R == pytest.approx(inputs.drift_R), (
        "magnitude-only OI×gamma must not invent adverse directional drift")
    assert correlation.drift_R == pytest.approx(inputs.drift_R * 0.6), (
        "correlation stress must shrink drift confidence toward zero")
    assert gamma.sigma_R != correlation.sigma_R
    assert (gamma.drift_R, gamma.sigma_R) != (correlation.drift_R, correlation.sigma_R)


def test_tiny_nonmaterial_stress_cannot_hard_veto_but_material_stress_can():
    metadata = {
        "available": True, "driver_confidence": 0.8,
        "source_quality": 0.8, "sample_span_minutes": 12,
    }
    scenarios = [
        {
            "name": "BASE", "cvar_floor_r": -1.0,
            "driver_metadata": {},
            "policies": {"HOLD": {"cvar10_r": -0.5}, "EXIT": {"cvar10_r": 0.1}},
        },
        {
            "name": "GAMMA_STRESS", "cvar_floor_r": -1.0,
            "driver_metadata": metadata,
            "policies": {"HOLD": {"cvar10_r": -1.5}, "EXIT": {"cvar10_r": 0.1}},
        },
    ]
    policies = {
        "HOLD": {"expected_net_r": 0.4, "worst_stress_cvar_r": -1.5},
        "EXIT": {"expected_net_r": 0.2, "worst_stress_cvar_r": 0.1},
    }
    fractions = {"HOLD": 0.0, "EXIT": 1.0}
    tiny = {"BASE": 0.999, "GAMMA_STRESS": 0.001}
    assert scenario_materiality("GAMMA_STRESS", 0.001, metadata)["material"] is False
    assert _choose_candidate(policies, scenarios, tiny, fractions, "HOLD", 0.001) == "HOLD"
    material = {"BASE": 0.9, "GAMMA_STRESS": 0.1}
    assert scenario_materiality("GAMMA_STRESS", 0.1, metadata)["material"] is True
    assert _choose_candidate(policies, scenarios, material, fractions, "HOLD", 0.1) == "EXIT"


def test_v15_never_changes_production_recommendation_without_promotion(monkeypatch):
    baseline = {
        "recommendation": {"policy": "HOLD", "action_ru": "HOLD"},
        "risk_constraint": {"effective_stop_floor_r": -1.0},
        "execution_cost_model": {}, "policies": {}, "evidence": {},
    }
    fake_ensemble = {
        "old_policy": "HOLD", "candidate_policy": "CLOSE_25",
        "candidate_differs": True, "policies": {}, "scenarios": [],
        "drivers": {}, "promotion_allowed": False,
    }
    monkeypatch.setattr(ai_policy_v15, "_BASE_ANALYZE", lambda *a, **k: deepcopy(baseline))
    monkeypatch.setattr(ai_policy_v15, "extract_policy_inputs", lambda tick: _inputs())
    monkeypatch.setattr(ai_policy_v15, "evaluate_derived_scenarios",
                        lambda **kwargs: deepcopy(fake_ensemble))
    monkeypatch.setattr(ai_policy_v15, "calibrated_switch_thresholds", lambda *a: [])
    monkeypatch.setattr(ai_policy_v15, "_record_shadow",
                        lambda *a: {"promotion_allowed": False})
    result = ai_policy_v15.analyze_policies(object(), {
        "trade": {"id": 1}, "option_derivative_state": {},
    }, {}, {})
    assert result["recommendation"] == baseline["recommendation"]
    assert result["shadow_policy_contract"]["new_candidate_policy"] == "CLOSE_25"
    assert result["shadow_policy_contract"]["action_changed"] is False
    assert result["shadow_policy_contract"]["promotion_allowed"] is False


def test_deterministic_verdict_block_states_effect_conflicts_and_thresholds():
    manager = {
        "derived_scenario_ensemble": {
            "old_policy": "HOLD", "candidate_policy": "CLOSE_25",
            "policies": {"CLOSE_25": {
                "expected_net_r": 0.1, "cvar10_net_r": -0.4,
                "worst_stress_r": -0.2,
            }},
            "scenarios": [
                {"name": "BASE", "weight": 0.7, "winner": "HOLD"},
                {"name": "SKEW_ADVERSE", "weight": 0.3, "winner": "CLOSE_25"},
            ],
            "validation_gate": {
                "promotion_reason": "manual reviewed calibration is required"},
        },
        "shadow_policy_contract": {
            "old_policy": "HOLD", "new_candidate_policy": "CLOSE_25"},
        "state_change_attribution": {
            "what_improved": [],
            "what_deteriorated": [
                {"metric": "barrier_ev_r", "delta": -0.21, "reference": "PREVIOUS_AI_REVIEW"}],
            "what_did_not_influence_low_confidence": [
                {"metric": "iv", "confidence": 0.2, "reason": "low confidence"}],
            "explicit_policy_effect": (
                "derived state changed only the shadow candidate; production action is unchanged"),
        },
        "derivative_switch_thresholds": [{
            "driver": "skew_adverse", "bounded_weight_threshold": 0.4,
            "candidate_policy": "CLOSE_25",
        }],
    }
    text = "\n".join(_dynamic_block(manager))
    for header in (
        "ПОЧЕМУ ИЗМЕНИЛОСЬ", "ЧТО ПОДТВЕРЖДАЕТ", "ЧТО ПРОТИВОРЕЧИТ",
        "ЧТО НЕ ИМЕЕТ ДОСТАТОЧНОГО ВЕСА", "DERIVATIVE THRESHOLDS",
    ):
        assert header in text
    assert "production action is unchanged" in text
    assert "not LLM" in text
