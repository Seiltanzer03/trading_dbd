import seiltanzer.ai_policy as policy
import seiltanzer.ai_policy_v3 as policy_v3
import seiltanzer.ai_verdict as verdict


def _inputs(*, status="delayed", proxy="reference_proxy"):
    return policy.PolicyInputs(
        r0=0.319,
        T=2.5,
        sigma_R=1.0,
        drift_R=-0.24,
        skew_R=0.12,
        term_slope=0.10,
        horizon_minutes=7200,
        max_r=0.319,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=102,
        chain_status=status,
        proxy_quality=proxy,
        source="test",
    )


def _metrics():
    return {
        "HOLD": {"expected_final_r": 0.139, "cvar10_r": -1.000},
        "CLOSE_10": {"expected_final_r": 0.157, "cvar10_r": -0.868},
        "CLOSE_25": {"expected_final_r": 0.184, "cvar10_r": -0.670},
        "CLOSE_50": {"expected_final_r": 0.229, "cvar10_r": -0.341},
        "EXIT": {"expected_final_r": 0.319, "cvar10_r": 0.319},
    }


def _stability():
    return {
        "checks": 11,
        "policy_stats": {
            name: {
                "winner_count": 11 if name == "EXIT" else 0,
                "winner_share": 1.0 if name == "EXIT" else 0.0,
                "feasible_count": 11 if name in {"CLOSE_50", "EXIT"} else 0,
                "feasible_share": 1.0 if name in {"CLOSE_50", "EXIT"} else 0.0,
            }
            for name in policy.POLICY_FRACTIONS
        },
        "winner_counts": {
            name: 11 if name == "EXIT" else 0
            for name in policy.POLICY_FRACTIONS
        },
        "winner_shares": {
            name: 1.0 if name == "EXIT" else 0.0
            for name in policy.POLICY_FRACTIONS
        },
    }


def test_correlated_option_metrics_count_as_one_confirmation_family(monkeypatch):
    base_evidence = {
        "adverse_confirmations": [
            {"metric": "barrier_ev_r", "value": -0.11},
            {"metric": "rnd_median_r", "value": 0.101},
            {"metric": "local24h_put_call_skew_pp", "value": 23.75},
        ],
        "supportive_contradictions": [],
        "context_observations": [],
        "uncertainty_flags": [],
        "iv_surface": {
            "local_24h": [{
                "hours": 24.0,
                "put_call_skew_pp": 23.75,
                "curvature_pp": 51.8,
            }],
        },
        "data_quality": {
            "reliability": {"level": "низкая", "reasons": ["delayed"]},
        },
    }
    monkeypatch.setattr(
        policy_v3,
        "_ORIGINAL_BUILD_METRIC_EVIDENCE",
        lambda *args, **kwargs: base_evidence,
    )
    evidence = policy.build_metric_evidence(
        None, {}, {}, {}, _inputs(), None, {})

    assert evidence["adverse_confirmation_item_count"] == 2
    assert evidence["adverse_confirmation_count"] == 1
    assert evidence["adverse_confirmation_families"] == ["option_distribution"]
    assert not any(
        item["metric"] == "local24h_put_call_skew_pp"
        for item in evidence["adverse_confirmations"]
    )
    assert any(
        item["metric"] == "local24h_put_call_skew_pp"
        and item.get("context_only")
        for item in evidence["context_observations"]
    )


def test_low_reliability_blocks_even_stable_exit(monkeypatch):
    monkeypatch.setattr(
        policy,
        "authority_stability",
        lambda *args, **kwargs: {
            "checks": 8,
            "winner_counts": {**{name: 0 for name in policy.POLICY_FRACTIONS}, "EXIT": 8},
            "winner_shares": {**{name: 0.0 for name in policy.POLICY_FRACTIONS}, "EXIT": 1.0},
            "feasible_counts": {},
            "variants": [],
            "description": "test",
        },
    )
    result = policy.select_final_policy(
        "EXIT",
        _stability(),
        _metrics(),
        {
            "adverse_confirmation_count": 3,
            "adverse_confirmation_families": [
                "option_distribution", "live_tape", "orderflow_levels"
            ],
            "data_quality": {
                "reliability": {
                    "level": "низкая",
                    "full_exit_authority": False,
                }
            },
        },
        _inputs(),
        {
            "cvar_floor_r": -0.481,
            "eligible": ["CLOSE_50", "EXIT"],
            "ineligible": {"HOLD": {"cvar10_r": -1.0}},
        },
    )
    assert result["policy"] == "EXIT"
    assert result["status"] == "manual_data_conflict"
    assert result["automatic_execution_allowed"] is False
    assert result["execution_policy"] is None
    assert result["provisional_policy"] == "EXIT"


def test_manual_conflict_report_does_not_order_full_exit(monkeypatch):
    monkeypatch.setattr(
        verdict,
        "_ORIGINAL_RENDER_POLICY_REPORT",
        lambda snapshot: "\n".join([
            "**ДЕЙСТВИЕ** — ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС.",
            "Автоматическое исполнение запрещено: gate зафиксировал конфликт модели.",
            "",
            "**ПОЧЕМУ ВЫБРАНО** —",
            "Устойчивость сырого EXIT: 11/11 (100.0%).",
            "",
            "**ПОДТВЕРЖДЕНИЯ, ПРОТИВОРЕЧИЯ И КОНТЕКСТ** —",
            "Против удержания: barrier_ev_r; rnd_median_r.",
            "",
            "**ПОСЛЕ ИСПОЛНЕНИЯ** —",
            "Оставить 0% текущего остатка.",
            "",
            "**СЛЕДУЮЩИЙ ПЕРЕСЧЁТ** —",
            "Новая цепочка.",
        ]),
    )
    snapshot = {
        "policy_manager": {
            "recommendation": {
                "policy": "EXIT",
                "action_ru": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
                "computed_action_ru": "ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС",
                "execution_action_ru": "НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ; расчётная политика — EXIT",
            },
            "gate": {
                "automatic_execution_allowed": False,
                "status": "manual_data_conflict",
                "reasons": ["надёжность расчёта низкая"],
                "source_stability_share": 0.5,
                "authority_stability": {
                    "checks": 8,
                    "winner_counts": {"EXIT": 4},
                    "description": "drift/skew/term neutralisation",
                },
            },
            "evidence": {
                "adverse_confirmation_families": ["option_distribution"],
                "confirmation_independence": {
                    "adverse_families": 1,
                    "adverse_items": 2,
                },
            },
        }
    }
    report = verdict.render_policy_report(snapshot)
    first_line = report.splitlines()[0]
    assert "НЕ ИСПОЛНЯТЬ АВТОМАТИЧЕСКИ" in first_line
    assert "Расчётное действие: ЗАКРЫТЬ 100% ПОЗИЦИИ СЕЙЧАС" in first_line
    assert "Исполнение не подтверждено" in report
    assert "Независимые семьи подтверждений: 1" in report
    assert "Устойчивость к источнику данных для EXIT: 4/8" in report
