import numpy as np

import seiltanzer.ai_policy as policy
import seiltanzer.ai_policy_v4 as policy_v4
import seiltanzer.ai_verdict as verdict


def _inputs(*, r0=1.712, max_r=1.712):
    return policy.PolicyInputs(
        r0=r0,
        T=2.5,
        sigma_R=1.0,
        drift_R=-0.20,
        skew_R=0.0,
        term_slope=0.0,
        horizon_minutes=600.0,
        max_r=max_r,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=120,
        chain_status="delayed",
        proxy_quality="reference_proxy",
        source="test",
    )


def test_cvar_floor_follows_be_not_arbitrary_point_eight_giveback():
    spec = policy.risk_constraint(_inputs(), {}, {})
    assert spec["effective_stop_floor_r"] == 0.0
    assert spec["cvar_floor_r"] == 0.0
    assert spec["max_giveback_r"] is None
    assert "0.80" not in spec["rule"]


def test_explicit_max_giveback_can_tighten_strategy_floor():
    spec = policy.risk_constraint(
        _inputs(), {}, {"management": {"max_giveback_r": 0.5}}
    )
    assert spec["effective_stop_floor_r"] == 0.0
    assert spec["giveback_floor_r"] == 1.212
    assert spec["cvar_floor_r"] == 1.212


def test_user_example_hold_is_not_excluded_by_invented_floor():
    metrics = {
        "HOLD": {"name": "HOLD", "expected_final_r": 1.527, "cvar10_r": 0.270},
        "CLOSE_10": {"name": "CLOSE_10", "expected_final_r": 1.545, "cvar10_r": 0.414},
        "CLOSE_25": {"name": "CLOSE_25", "expected_final_r": 1.573, "cvar10_r": 0.630},
        "CLOSE_50": {"name": "CLOSE_50", "expected_final_r": 1.619, "cvar10_r": 0.991},
        "EXIT": {"name": "EXIT", "expected_final_r": 1.712, "cvar10_r": 1.712},
    }
    token = policy_v4._RISK_CTX.set(policy.risk_constraint(_inputs(), {}, {}))
    try:
        choice, rule = policy._raw_policy_choice(metrics, 1.712)
    finally:
        policy_v4._RISK_CTX.reset(token)
    assert choice == "EXIT"
    assert "HOLD" in rule["eligible"]
    assert rule["cvar_floor_r"] == 0.0


def test_exit_is_net_of_execution_costs():
    costs = {
        "immediate_full_close_r": 0.01,
        "deferred_full_close_r": 0.01,
    }
    token = policy_v4._COST_CTX.set(costs)
    try:
        metrics, _ = policy._run_once(
            _inputs(r0=0.5, max_r=0.5), n_paths=400, n_steps=60, seed=7
        )
    finally:
        policy_v4._COST_CTX.reset(token)
    assert metrics["EXIT"]["gross_expected_final_r"] == 0.5
    assert metrics["EXIT"]["expected_final_r"] == 0.49
    assert metrics["EXIT"]["cvar10_r"] == 0.49
    assert metrics["EXIT"]["execution_cost_r"] == 0.01


def test_geometry_reports_unresolved_and_conditional_event_time():
    sim = policy.PathSimulation(
        terminal=np.array([0.2, -1.0, 0.4, 0.1]),
        max_r=np.array([1.1, 0.2, 0.5, 0.4]),
        min_r=np.array([0.0, -1.0, 0.0, 0.0]),
        stop_time=np.array([np.nan, 0.5, np.nan, np.nan]),
        take_time=np.array([np.nan, np.nan, np.nan, np.nan]),
        rung_times={1.0: np.array([0.25, np.nan, np.nan, np.nan])},
        horizon_minutes=600.0,
    )
    geometry = policy_v4._event_geometry(
        sim, _inputs(r0=0.2, max_r=0.2)
    )
    assert geometry["rung_first_count"] == 1
    assert geometry["stop_first_count"] == 1
    assert geometry["unresolved_count"] == 2
    assert geometry["p_unresolved_full_horizon"] == 0.5
    assert geometry["mean_event_minutes_given_resolved"] == 225.0


def test_mixed_family_gives_no_adverse_vote_and_gamma_is_context():
    evidence = {
        "adverse_confirmations": [
            {"metric": "rnd_median_r", "family": "option_distribution"},
            {"metric": "gamma_context_toward", "value": "стопу"},
        ],
        "supportive_contradictions": [
            {"metric": "barrier_ev_r", "family": "option_distribution"},
        ],
        "context_observations": [],
    }
    result = policy_v4._normalise_evidence(evidence)
    assert result["adverse_confirmation_count"] == 0
    assert result["mixed_confirmation_families"] == ["option_distribution"]
    assert any(
        item["metric"] == "gamma_context_toward"
        for item in result["context_observations"]
    )


def test_manual_report_starts_with_plain_working_action():
    policies = {
        name: {
            "expected_final_r": value,
            "gross_expected_final_r": value + 0.01,
            "execution_cost_r": 0.01,
            "median_final_r": value,
            "cvar10_r": value - 0.2,
            "p_final_profit": 0.8,
            "p_giveback_0_25_from_now": 0.2,
            "p_giveback_0_50_from_now": 0.1,
        }
        for name, value in {
            "HOLD": 1.52,
            "CLOSE_10": 1.54,
            "CLOSE_25": 1.57,
            "CLOSE_50": 1.61,
            "EXIT": 1.70,
        }.items()
    }
    snapshot = {
        "policy_manager": {
            "recommendation": {
                "policy": "EXIT",
                "raw_optimizer_policy": "EXIT",
                "computed_action_ru": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
                "execution_action_ru": "НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ",
            },
            "gate": {
                "automatic_execution_allowed": False,
                "status": "manual_data_conflict",
                "reasons": ["надёжность расчёта низкая"],
                "source_stability_share": 0.75,
                "authority_stability": {
                    "checks": 8,
                    "winner_counts": {"EXIT": 6},
                },
            },
            "scenario_geometry": {
                "scenario_count": 6500,
                "rung_first_count": 3857,
                "stop_first_count": 1,
                "unresolved_count": 2642,
                "resolved_count": 3858,
                "p_next_rung_before_stop": 3857 / 6500,
                "p_stop_before_next_rung": 1 / 6500,
                "p_unresolved_full_horizon": 2642 / 6500,
                "full_horizon_minutes": 600,
                "mean_event_minutes_given_resolved": 211.1,
                "no_event_windows": {
                    "60m": {
                        "events": 736,
                        "no_event_count": 5764,
                        "scenarios": 6500,
                        "no_event_probability": 5764 / 6500,
                    }
                },
            },
            "policies": policies,
            "selection_rule": {"eligible": ["HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"]},
            "risk_constraint": {
                "cvar_floor_r": 0.0,
                "source": "strategy BE floor 0R",
                "rule": "active stop/BE/trailing; no invented profit-giveback cap",
                "max_giveback_r": None,
            },
            "execution_cost_model": {
                "immediate_full_close_r": 0.01,
                "deferred_full_close_r": 0.01,
                "source": "fallback",
                "assumed": True,
            },
            "risk_tradeoff": {
                "expected_delta_vs_hold_r": 0.18,
                "expected_delta_label": "расчётное преимущество над HOLD",
                "cvar_improvement_vs_hold_r": 1.0,
            },
            "raw_optimizer_stability": {"selected_count": 11, "checks": 11, "selected_share": 1.0},
            "stability": {"selected_count": 11, "checks": 11, "selected_share": 1.0},
            "evidence": {
                "adverse_confirmations": [],
                "supportive_contradictions": [],
                "context_observations": [],
                "adverse_confirmation_families": [],
                "supportive_confirmation_families": [],
                "mixed_confirmation_families": ["option_distribution"],
                "data_quality": {"reliability": {"level": "низкая", "reasons": ["delayed"]}},
            },
            "metric_coverage": {"summary": {"available_groups": 12, "total_groups": 12}},
            "inputs": {"chain_status": "delayed", "chain_age_sec": 400, "proxy_quality": "reference_proxy"},
            "counterfactual_attribution": {"available": False},
            "cancellation_boundary": {"available": False, "reason": "переход не найден"},
        }
    }
    report = verdict.render_policy_report(snapshot)
    assert report.splitlines()[0].startswith(
        "**ДЕЙСТВИЕ СЕЙЧАС** — НИЧЕГО НЕ МЕНЯТЬ"
    )
    assert "Расчётное действие: ЗАКРЫТЬ 100%" in report
    assert "Исполнение не подтверждено" in report
    assert "1/6500" in report
    assert "Среднее время до события только среди разрешившихся" in report
    assert "Смешанные семьи, не дающие голоса gate: option_distribution" in report
    assert "расчётное преимущество над HOLD: +0.180R" in report
    assert "Цена защиты" not in report
