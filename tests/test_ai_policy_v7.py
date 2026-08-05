from types import SimpleNamespace

import seiltanzer.ai_policy as policy
import seiltanzer.ai_policy_v8 as policy_v8
import seiltanzer.ai_policy_v9 as policy_v9
import seiltanzer.ai_verdict as verdict
import seiltanzer.ai_verdict_v9 as verdict_v9


def _inputs():
    return policy.PolicyInputs(
        r0=2.16,
        T=2.97,
        sigma_R=0.75,
        drift_R=-0.05,
        skew_R=-0.10,
        term_slope=0.0,
        horizon_minutes=540.0,
        max_r=2.176,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=240.0,
        chain_status="delayed",
        proxy_quality="reference_proxy",
        source="test",
    )


def _metric(expected, cvar, give25=0.30, give50=0.20):
    return {
        "name": "",
        "expected_final_r": expected,
        "cvar10_r": cvar,
        "p_giveback_0_25_from_now": give25,
        "p_giveback_0_50_from_now": give50,
    }


def _stability(policy_name, *, winner=0.8, feasible=1.0):
    return {
        "selected_share": winner,
        "selected_count": round(winner * 11),
        "checks": 11,
        "policy_stats": {
            policy_name: {
                "winner_share": winner,
                "feasible_share": feasible,
            },
            "HOLD": {
                "winner_share": 1.0 if policy_name != "HOLD" else winner,
                "feasible_share": 1.0,
            },
        },
    }


def _base_result(policy_name, *, source_winner=0.75, source_feasible=8):
    return {
        "policy": policy_name,
        "provisional_policy": policy_name,
        "execution_policy": None,
        "status": "manual_data_conflict",
        "automatic_execution_allowed": False,
        "working_action_confirmed": False,
        "reasons": ["надёжность расчёта низкая"],
        "authority_stability": {
            "checks": 8,
            "winner_counts": {policy_name: round(source_winner * 8)},
            "winner_shares": {policy_name: source_winner},
            "feasible_counts": {policy_name: source_feasible, "HOLD": 8},
        },
    }


def _evidence(families):
    metric_by_family = {
        "option_distribution": "barrier_ev_r",
        "live_tape": "live_60m_r",
        "orderflow_levels": "directional_volume_delta",
        "strategy_filters": "required_filters_fail",
    }
    return {
        "adverse_confirmation_families": list(families),
        "adverse_confirmations": [
            {
                "metric": metric_by_family[family],
                "family": family,
                "value": -0.2,
            }
            for family in families
        ],
        "supportive_confirmation_families": [],
        "mixed_confirmation_families": [],
        "data_quality": {"reliability": {"level": "низкая"}},
    }


def test_low_reliability_can_confirm_close50_for_manual_execution(monkeypatch):
    monkeypatch.setattr(
        policy_v8, "_BASE_SELECT",
        lambda *a, **k: _base_result("CLOSE_50", source_winner=0.75),
    )
    metrics = {
        "HOLD": _metric(2.16, 0.45, 0.32, 0.22),
        "CLOSE_50": _metric(2.13, 1.05, 0.15, 0.06),
    }
    result = policy.select_final_policy(
        "CLOSE_50",
        _stability("CLOSE_50", winner=0.82),
        metrics,
        _evidence(["option_distribution", "live_tape", "orderflow_levels"]),
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "CLOSE_50"]},
    )
    assert result["policy"] == "CLOSE_50"
    assert result["status"] == "confirmed_degraded_manual"
    assert result["working_action_confirmed"] is True
    assert result["manual_execution_required"] is True
    assert result["automatic_execution_allowed"] is False
    selected = result["degraded_authority_overlay"]["selected"]
    assert selected["cvar_gain_vs_hold_r"] == 0.60
    assert selected["expected_sacrifice_vs_hold_r"] == 0.03


def test_low_reliability_can_confirm_full_exit_with_broad_live_evidence(monkeypatch):
    monkeypatch.setattr(
        policy_v8, "_BASE_SELECT",
        lambda *a, **k: _base_result("EXIT", source_winner=0.75),
    )
    result = policy.select_final_policy(
        "EXIT",
        _stability("EXIT", winner=0.91),
        {
            "HOLD": _metric(2.16, 0.35, 0.36, 0.25),
            "EXIT": _metric(2.10, 2.10, 0.0, 0.0),
        },
        _evidence(["option_distribution", "live_tape", "orderflow_levels"]),
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "EXIT"]},
    )
    assert result["policy"] == "EXIT"
    assert result["status"] == "confirmed_degraded_manual"
    assert result["automatic_execution_allowed"] is False
    assert result["degraded_authority_overlay"]["selected"]["qualified"] is True


def test_family_labels_without_metric_rows_cannot_authorize_exit(monkeypatch):
    monkeypatch.setattr(
        policy_v8, "_BASE_SELECT",
        lambda *a, **k: _base_result("EXIT", source_winner=1.0),
    )
    evidence = {
        "adverse_confirmation_families": [
            "option_distribution", "live_tape", "orderflow_levels"
        ],
        "adverse_confirmations": [],
        "data_quality": {"reliability": {"level": "низкая"}},
    }
    result = policy.select_final_policy(
        "EXIT",
        _stability("EXIT", winner=1.0),
        {
            "HOLD": _metric(2.16, 0.20),
            "EXIT": _metric(2.10, 2.10, 0.0, 0.0),
        },
        evidence,
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "EXIT"]},
    )
    assert result["status"] != "confirmed_degraded_manual"
    overlay = result["degraded_authority_overlay"]
    assert overlay["selected"] is None
    assert set(overlay["evidence"]["incomplete_family_labels"]) == {
        "option_distribution", "live_tape", "orderflow_levels"
    }


def test_delayed_option_family_alone_never_authorizes_exit(monkeypatch):
    monkeypatch.setattr(
        policy_v8, "_BASE_SELECT",
        lambda *a, **k: _base_result("EXIT", source_winner=1.0),
    )
    result = policy.select_final_policy(
        "EXIT",
        _stability("EXIT", winner=1.0),
        {
            "HOLD": _metric(2.16, 0.20),
            "EXIT": _metric(2.10, 2.10, 0.0, 0.0),
        },
        _evidence(["option_distribution"]),
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "EXIT"]},
    )
    assert result["status"] != "confirmed_degraded_manual"
    assert result["degraded_authority_overlay"]["selected"] is None
    assert result["degraded_authority_overlay"]["evidence"]["option_only"] is True


def test_risk_overlay_can_override_hold_tie_break(monkeypatch):
    base = _base_result("HOLD", source_winner=1.0)
    base.update({
        "status": "confirmed_hold",
        "working_action_confirmed": True,
        "execution_required": False,
        "authority_stability": {
            "checks": 8,
            "winner_counts": {"HOLD": 8, "CLOSE_50": 0},
            "winner_shares": {"HOLD": 1.0, "CLOSE_50": 0.0},
            "feasible_counts": {"HOLD": 8, "CLOSE_50": 8},
        },
    })
    monkeypatch.setattr(policy_v8, "_BASE_SELECT", lambda *a, **k: dict(base))
    stability = {
        "selected_share": 1.0,
        "selected_count": 11,
        "checks": 11,
        "policy_stats": {
            "HOLD": {"winner_share": 1.0, "feasible_share": 1.0},
            "CLOSE_50": {"winner_share": 0.0, "feasible_share": 1.0},
        },
    }
    result = policy.select_final_policy(
        "HOLD",
        stability,
        {
            "HOLD": _metric(2.164, 1.053, 0.324, 0.196),
            "CLOSE_50": _metric(2.159, 1.604, 0.201, 0.057),
        },
        _evidence(["live_tape", "orderflow_levels"]),
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "CLOSE_50"]},
    )
    assert result["policy"] == "CLOSE_50"
    assert result["status"] == "confirmed_degraded_manual"
    assert result["degraded_authority_overlay"]["selected"]["risk_efficient_override"] is True


def test_no_adverse_live_evidence_keeps_confirmed_hold(monkeypatch):
    base = _base_result("HOLD", source_winner=1.0)
    base.update({"status": "confirmed_hold", "working_action_confirmed": True})
    monkeypatch.setattr(policy_v8, "_BASE_SELECT", lambda *a, **k: dict(base))
    result = policy.select_final_policy(
        "HOLD",
        _stability("HOLD", winner=1.0),
        {
            "HOLD": _metric(2.164, 1.053),
            "CLOSE_50": _metric(2.159, 1.604),
        },
        _evidence([]),
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "CLOSE_50"]},
    )
    assert result["policy"] == "HOLD"
    assert result["status"] == "confirmed_hold"
    assert result["degraded_authority_overlay"]["selected"] is None


def test_analysis_shows_strategy_next_rung_and_enriched_quote_audit(monkeypatch):
    base = {
        "inputs": {
            "r0": 2.16,
            "max_r": 2.176,
            "rungs": [1.0, 1.25, 1.5, 1.75, 2.0, 2.2],
            "rung_fraction": 0.10,
        },
        "policies": {
            "HOLD": _metric(2.164, 1.053),
            "CLOSE_10": _metric(2.163, 1.163),
            "CLOSE_25": _metric(2.161, 1.328),
            "CLOSE_50": _metric(2.159, 1.604),
            "EXIT": _metric(2.154, 2.154),
        },
        "selection_rule": {"indifference_band_r": 0.03},
        "recommendation": {"policy": "HOLD"},
        "gate": {
            "policy": "HOLD",
            "status": "confirmed_hold",
            "working_action_confirmed": True,
        },
        "decision_requirements": {"common": {}},
        "input_audit": {
            "available_count": 3,
            "total_count": 3,
            "rows": {
                "instrument_price": {
                    "available": True,
                    "role": "optimizer_and_geometry",
                },
                "option_proxy_price": {
                    "available": True,
                    "role": "option_moneyness_mapping",
                },
                "option_chain": {
                    "available": True,
                    "role": "option_anchor_optimizer_and_evidence",
                },
            },
        },
    }
    monkeypatch.setattr(policy_v9, "_BASE_ANALYZE", lambda *a, **k: base)
    tick = {
        "ts": 1_000.0,
        "feeds": {
            "price": {
                "value": 4191.72,
                "ts": 999.0,
                "status": "live",
                "source": "Swissquote",
                "symbol": "XAU/USD",
            },
            "proxy_price": {
                "value": 381.2,
                "ts": 990.0,
                "status": "delayed",
                "source": "stream GLD",
                "symbol": "GLD",
            },
            "chain": {
                "ts": 900.0,
                "status": "delayed",
                "source": "yfinance GLD options",
            },
            "vols": {},
        },
    }
    result = policy.analyze_policies(
        SimpleNamespace(),
        tick,
        {},
        {"entry": 4058.90, "stop": 3997.15, "direction": "long"},
    )
    step = result["strategy_next_step"]
    assert step["next_rung_r"] == 2.2
    assert round(step["next_rung_price"], 2) == 4194.75
    assert step["close_fraction_of_current_remainder"] == 0.2
    assert result["economic_indifference"]["nearest_active_policy"]["policy"] == "CLOSE_10"
    price_row = result["input_audit"]["rows"]["instrument_price"]
    assert price_row["value"] == 4191.72
    assert price_row["symbol"] == "XAU/USD"
    assert price_row["age_sec"] == 1.0
    assert result["management_arbiter"]["winner"] == "STRATEGY"


def _policy_result(policy_name="CLOSE_50"):
    active = policy_name != "HOLD"
    return {
        "recommendation": {"policy": policy_name},
        "gate": {
            "policy": policy_name,
            "status": "confirmed_degraded_manual" if active else "confirmed_hold",
            "working_action_confirmed": True,
            "degraded_authority_overlay": {
                "selected": {"policy": policy_name} if active else None,
            },
        },
        "policies": {
            "HOLD": _metric(2.16, 0.8),
            policy_name: _metric(2.14, 1.4) if active else _metric(2.16, 0.8),
        },
    }


def test_management_sequence_reuses_same_pending_decision():
    result = _policy_result("CLOSE_50")
    result["management_arbiter"] = policy_v9._arbiter(result)
    first = policy.resolve_management_sequence(
        result, None, trade_id=12, captured_ts=1000.0
    )
    second = policy.resolve_management_sequence(
        result, first, trade_id=12, captured_ts=1010.0
    )
    assert first["execution_status"] == "pending_execution"
    assert second["decision_id"] == first["decision_id"]
    assert second["continuity"] == "continue_same_pending_decision"
    assert second["incremental_close_fraction"] == 0.50


def test_management_sequence_supersedes_pending_with_stronger_exit():
    close = _policy_result("CLOSE_50")
    close["management_arbiter"] = policy_v9._arbiter(close)
    pending = policy.resolve_management_sequence(
        close, None, trade_id=12, captured_ts=1000.0
    )
    exit_result = _policy_result("EXIT")
    exit_result["management_arbiter"] = policy_v9._arbiter(exit_result)
    new = policy.resolve_management_sequence(
        exit_result, pending, trade_id=12, captured_ts=1020.0
    )
    assert new["policy"] == "EXIT"
    assert new["decision_id"] != pending["decision_id"]
    assert new["supersedes_decision_id"] == pending["decision_id"]


def test_management_sequence_hold_cancels_pending_ai_plan():
    active = _policy_result("CLOSE_25")
    active["management_arbiter"] = policy_v9._arbiter(active)
    pending = policy.resolve_management_sequence(
        active, None, trade_id=12, captured_ts=1000.0
    )
    hold = _policy_result("HOLD")
    hold["management_arbiter"] = policy_v9._arbiter(hold)
    current = policy.resolve_management_sequence(
        hold, pending, trade_id=12, captured_ts=1030.0
    )
    assert current["policy"] == "HOLD"
    assert current["authority"] == "STRATEGY"
    assert current["continuity"] == "active_ai_plan_cancelled_by_current_arbiter"
    assert current["supersedes_decision_id"] == pending["decision_id"]


def _base_report():
    return "\n".join([
        "**ДЕЙСТВИЕ СЕЙЧАС** — OLD.",
        "",
        "**ОБЩАЯ ГЕОМЕТРИЯ СЦЕНАРИЕВ** —",
        "geometry",
        "",
        "**ПОЧЕМУ ВЫБРАНО** —",
        "choice",
        "",
        "**ПОСЛЕ ИСПОЛНЕНИЯ** —",
        "after",
        "",
        "**КАЧЕСТВО ДАННЫХ** —",
        "quality",
    ])


def test_report_repeats_pending_plan_without_creating_second_close(monkeypatch):
    monkeypatch.setattr(verdict_v9, "_BASE_RENDER", lambda snapshot: _base_report())
    snapshot = {
        "policy_manager": {
            "management_arbiter": {
                "winner": "AI",
                "effective_policy": "CLOSE_50",
                "ai_priority_bonus_r": 0.015,
                "strategy_score": 2.40,
                "ai_score_before_priority": 2.55,
                "ai_score_after_priority": 2.565,
            },
            "management_decision": {
                "decision_id": "T12-1000-CLOSE_50",
                "authority": "AI_OVERRIDE",
                "policy": "CLOSE_50",
                "model_policy": "CLOSE_50",
                "execution_status": "pending_execution",
                "continuity": "continue_same_pending_decision",
                "instruction_ru": "ЗАКРЫТЬ 50% ТЕКУЩЕГО ОСТАТКА",
                "incremental_close_fraction": 0.50,
                "remaining_fraction_after_action": 0.50,
                "previous": {
                    "decision_id": "T12-1000-CLOSE_50",
                    "policy": "CLOSE_50",
                    "execution_status": "pending_execution",
                },
            },
        }
    }
    report = verdict.render_policy_report(snapshot)
    top = "\n".join(report.splitlines()[:8])
    assert "ДЕЙСТВУЮЩЕЕ РЕШЕНИЕ ИИ" in top
    assert "не команда закрыть ещё" in top
    assert "T12-1000-CLOSE_50" in top
    assert "второй параллельной команды нет" in report


def test_report_hold_is_one_strategy_plan_not_growth_forecast(monkeypatch):
    monkeypatch.setattr(verdict_v9, "_BASE_RENDER", lambda snapshot: _base_report())
    snapshot = {
        "policy_manager": {
            "recommendation": {"policy": "HOLD"},
            "gate": {"status": "confirmed_hold"},
            "management_arbiter": {
                "winner": "STRATEGY",
                "effective_policy": "HOLD",
                "reason": "AI overlay не подтверждён",
                "strategy_score": 2.4,
                "ai_score_before_priority": 2.4,
                "ai_score_after_priority": 2.4,
            },
            "management_decision": {
                "decision_id": "T12-1000-STRATEGY",
                "authority": "STRATEGY",
                "policy": "HOLD",
                "model_policy": "HOLD",
                "execution_status": "strategy_active",
                "continuity": "strategy_continues",
                "instruction_ru": "ВЕСТИ ОСТАТОК ПО СТРАТЕГИИ",
                "incremental_close_fraction": 0.0,
                "remaining_fraction_after_action": 1.0,
            },
        }
    }
    report = verdict.render_policy_report(snapshot)
    top = "\n".join(report.splitlines()[:8])
    assert "HOLD подтверждён" in top
    assert "НЕ ВЫСТАВЛЯТЬ НОВЫХ ОРДЕРОВ ВНЕ СТРАТЕГИИ" in top
    assert "не прогноз обязательного продолжения" in top.lower()
