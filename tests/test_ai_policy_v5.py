import seiltanzer.ai_policy as policy
import seiltanzer.ai_policy_v5 as policy_v5
import seiltanzer.ai_verdict as verdict


def _inputs():
    return policy.PolicyInputs(
        r0=1.57,
        T=2.5,
        sigma_R=1.0,
        drift_R=0.0,
        skew_R=0.0,
        term_slope=0.0,
        horizon_minutes=682.0,
        max_r=1.95,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=160,
        chain_status="delayed",
        proxy_quality="reference_proxy",
        source="test",
    )


def _metrics():
    values = {
        "HOLD": (1.566, 0.342),
        "CLOSE_10": (1.565, 0.463),
        "CLOSE_25": (1.564, 0.646),
        "CLOSE_50": (1.563, 0.951),
        "EXIT": (1.560, 1.560),
    }
    return {
        name: {
            "name": name,
            "expected_final_r": expected,
            "median_final_r": expected,
            "cvar10_r": cvar,
            "p_final_profit": 1.0,
            "p_giveback_0_25_from_now": 0.0,
            "p_giveback_0_50_from_now": 0.0,
        }
        for name, (expected, cvar) in values.items()
    }


def test_stress_analysis_uses_same_zero_be_floor_as_base(monkeypatch):
    metrics = _metrics()

    def fake_run_once(inputs, *, n_paths, n_steps, seed):
        return metrics, None

    monkeypatch.setattr(policy_v5, "_run_once", fake_run_once)
    token = policy_v5._RISK_CTX.set({
        "effective_stop_floor_r": 0.0,
        "max_giveback_r": None,
    })
    try:
        result = policy.stability_analysis(_inputs(), "HOLD")
    finally:
        policy_v5._RISK_CTX.reset(token)

    assert result["winner_counts"]["HOLD"] == 11
    assert result["selected_count"] == 11
    assert all(row["cvar_floor_r"] == 0.0 for row in result["rows"])
    assert result["risk_rule"].startswith("same active stop")
    assert result["cost_rule"].startswith("same net execution")


def test_feasible_hold_without_adverse_evidence_cannot_become_close50(monkeypatch):
    def fake_base_select(*args, **kwargs):
        return {
            "policy": "CLOSE_50",
            "provisional_policy": "CLOSE_50",
            "status": "conflict_stability_fallback",
            "reasons": [
                "политика не выиграла ни одного stress-пересчёта",
                "исходная политика имела устойчивость 0%; выбран устойчивый вариант из допустимых",
            ],
            "automatic_execution_allowed": False,
            "authority_stability": {
                "winner_shares": {"HOLD": 0.75, "CLOSE_50": 0.0}
            },
        }

    monkeypatch.setattr(policy_v5, "_BASE_HARD_CVAR_SELECT", fake_base_select)
    result = policy_v5.select_final_policy(
        "HOLD",
        {"policy_stats": {}},
        _metrics(),
        {"adverse_confirmation_families": []},
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": list(_metrics())},
    )

    assert result["policy"] == "HOLD"
    assert result["provisional_policy"] == "HOLD"
    assert result["rejected_stress_fallback"] == "CLOSE_50"
    assert result["status"] == "hold_no_reduction_evidence"
    assert result["automatic_execution_allowed"] is False
    assert any("HOLD сохранён" in reason for reason in result["reasons"])


def test_report_calls_close50_rejected_stress_candidate_not_action():
    metrics = _metrics()
    snapshot = {
        "policy_manager": {
            "recommendation": {
                "policy": "HOLD",
                "raw_optimizer_policy": "HOLD",
                "action_ru": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
                "computed_action_ru": "НЕ СОКРАЩАТЬ ПОЗИЦИЮ",
                "execution_action_ru": "НИЧЕГО НЕ МЕНЯТЬ ПО ЭТОМУ ОТЧЁТУ",
                "remaining_fraction": 1.0,
                "remaining_management": "сохранить действующие правила",
            },
            "gate": {
                "raw_policy": "HOLD",
                "provisional_policy": "HOLD",
                "rejected_stress_fallback": "CLOSE_50",
                "automatic_execution_allowed": False,
                "status": "hold_no_reduction_evidence",
                "reasons": [
                    "HOLD сохранён: он проходит CVaR и нет независимых подтверждений сокращения"
                ],
                "source_stability_share": 0.75,
                "authority_stability": {
                    "checks": 8,
                    "winner_counts": {"HOLD": 6},
                    "description": "source stresses",
                },
            },
            "policies": metrics,
            "scenario_geometry": {
                "scenario_count": 6500,
                "rung_first_count": 3522,
                "stop_first_count": 3,
                "unresolved_count": 2975,
                "resolved_count": 3525,
                "p_next_rung_before_stop": 3522 / 6500,
                "p_stop_before_next_rung": 3 / 6500,
                "p_unresolved_full_horizon": 2975 / 6500,
                "full_horizon_minutes": 682,
                "mean_event_minutes_given_resolved": 253.1,
                "no_event_windows": {
                    "60m": {
                        "events": 213,
                        "no_event_count": 6287,
                        "scenarios": 6500,
                        "no_event_probability": 6287 / 6500,
                    }
                },
            },
            "selection_rule": {
                "cvar_floor_r": 0.0,
                "eligible": list(metrics),
                "ineligible": {},
            },
            "risk_constraint": {
                "cvar_floor_r": 0.0,
                "source": "strategy BE floor 0R",
                "rule": "active stop/BE/trailing",
                "max_giveback_r": None,
            },
            "execution_cost_model": {
                "immediate_full_close_r": 0.01,
                "deferred_full_close_r": 0.01,
                "source": "fallback",
                "assumed": True,
            },
            "risk_tradeoff": {
                "expected_delta_vs_hold_r": 0.0,
                "expected_delta_label": "расчётное преимущество над HOLD",
                "cvar_improvement_vs_hold_r": 0.0,
            },
            "raw_optimizer_stability": {
                "selected_count": 11, "checks": 11, "selected_share": 1.0
            },
            "stability": {
                "selected_count": 11, "checks": 11, "selected_share": 1.0
            },
            "evidence": {
                "adverse_confirmations": [],
                "supportive_contradictions": [],
                "context_observations": [],
                "adverse_confirmation_families": [],
                "supportive_confirmation_families": [],
                "mixed_confirmation_families": [],
                "data_quality": {
                    "reliability": {"level": "низкая", "reasons": ["delayed"]}
                },
            },
            "metric_coverage": {
                "summary": {"available_groups": 12, "total_groups": 12}
            },
            "inputs": {
                "chain_status": "delayed",
                "chain_age_sec": 160,
                "proxy_quality": "reference_proxy",
            },
            "counterfactual_attribution": {"available": False},
            "cancellation_boundary": {
                "available": False, "reason": "переход не найден"
            },
        }
    }

    report = verdict.render_policy_report(snapshot)
    first_lines = "\n".join(report.splitlines()[:5])
    assert "Основной расчёт: HOLD" in first_lines
    assert "Отклонённый stress-кандидат: CLOSE_50" in first_lines
    assert "Это не рекомендация" in first_lines
    assert "Расчётное действие: ЗАКРЫТЬ 50%" not in first_lines
