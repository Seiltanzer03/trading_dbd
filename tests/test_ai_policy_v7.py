from types import SimpleNamespace

import seiltanzer.ai_policy as policy
import seiltanzer.ai_policy_v7 as policy_v7
import seiltanzer.ai_verdict as verdict
import seiltanzer.ai_verdict_v7 as verdict_v7


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
    return {
        "adverse_confirmation_families": list(families),
        "supportive_confirmation_families": [],
        "mixed_confirmation_families": [],
        "data_quality": {"reliability": {"level": "низкая"}},
    }


def test_low_reliability_can_confirm_close50_for_manual_execution(monkeypatch):
    monkeypatch.setattr(
        policy_v7, "_BASE_SELECT",
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
        policy_v7, "_BASE_SELECT",
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


def test_delayed_option_family_alone_never_authorizes_exit(monkeypatch):
    monkeypatch.setattr(
        policy_v7, "_BASE_SELECT",
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
    monkeypatch.setattr(policy_v7, "_BASE_SELECT", lambda *a, **k: dict(base))
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
    monkeypatch.setattr(policy_v7, "_BASE_SELECT", lambda *a, **k: dict(base))
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
        "gate": {"policy": "HOLD", "status": "confirmed_hold", "working_action_confirmed": True},
        "decision_requirements": {"common": {}},
        "input_audit": {
            "available_count": 3,
            "total_count": 3,
            "rows": {
                "instrument_price": {"available": True, "role": "optimizer_and_geometry"},
                "option_proxy_price": {"available": True, "role": "option_moneyness_mapping"},
                "option_chain": {"available": True, "role": "option_anchor_optimizer_and_evidence"},
            },
        },
    }
    monkeypatch.setattr(policy_v7, "_BASE_ANALYZE", lambda *a, **k: base)
    tick = {
        "ts": 1_000.0,
        "feeds": {
            "price": {"value": 4191.72, "ts": 999.0, "status": "live", "source": "Swissquote", "symbol": "XAU/USD"},
            "proxy_price": {"value": 381.2, "ts": 990.0, "status": "delayed", "source": "stream GLD", "symbol": "GLD"},
            "chain": {"ts": 900.0, "status": "delayed", "source": "yfinance GLD options"},
            "vols": {},
        },
    }
    result = policy.analyze_policies(
        SimpleNamespace(), tick, {},
        {"entry": 4058.90, "stop": 3997.15, "direction": "long"},
    )
    step = result["strategy_next_step"]
    assert step["next_rung_r"] == 2.2
    assert round(step["next_rung_price"], 2) == 4194.75
    assert step["close_fraction_of_current_remainder"] == 0.1667
    assert result["economic_indifference"]["nearest_active_policy"]["policy"] == "CLOSE_10"
    price_row = result["input_audit"]["rows"]["instrument_price"]
    assert price_row["value"] == 4191.72
    assert price_row["symbol"] == "XAU/USD"
    assert price_row["age_sec"] == 1.0
    assert result["decision_requirements"]["common"]["data_reliability_must_not_be_low"] is False


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
        "**ЧТО ДОЛЖНО ИЗМЕНИТЬ РЕШЕНИЕ** —",
        "old rules",
        "",
        "**КАКИЕ ДАННЫЕ РЕАЛЬНО УЧТЕНЫ** —",
        "old audit",
        "",
        "**КАЧЕСТВО ДАННЫХ** —",
        "quality",
    ])


def test_report_calls_degraded_exit_manual_not_automatic(monkeypatch):
    monkeypatch.setattr(verdict_v7, "_BASE_RENDER", lambda snapshot: _base_report())
    snapshot = {
        "policy_manager": {
            "recommendation": {"policy": "EXIT", "execution_action_ru": "ЗАКРЫТЬ ВЕСЬ ОСТАТОК"},
            "gate": {
                "policy": "EXIT",
                "status": "confirmed_degraded_manual",
                "degraded_authority_overlay": {
                    "selected": {"policy": "EXIT", "expected_delta_vs_hold_r": -0.05, "cvar_gain_vs_hold_r": 1.2},
                    "evidence": {"live_adverse_count": 2, "total_adverse_count": 3},
                },
            },
            "economic_indifference": {},
            "decision_requirements": {"degraded_manual_policies": policy_v7._DEGRADED_REQUIREMENTS},
            "input_audit": {"available_count": 0, "total_count": 0, "rows": {}},
        }
    }
    report = verdict.render_policy_report(snapshot)
    top = "\n".join(report.splitlines()[:7])
    assert "ЗАКРЫТЬ ВЕСЬ ОСТАТОК" in top
    assert "ВЫПОЛНИТЬ ВРУЧНУЮ" in top
    assert "автоматическое исполнение запрещено" in top
    assert "подтверждено для ручного" in top
    assert "После ручного полного выхода" in report


def test_report_hold_preserves_strategy_orders_and_denies_growth_forecast(monkeypatch):
    monkeypatch.setattr(verdict_v7, "_BASE_RENDER", lambda snapshot: _base_report())
    snapshot = {
        "policy_manager": {
            "recommendation": {"policy": "HOLD"},
            "gate": {
                "status": "confirmed_hold",
                "working_action_confirmed": True,
                "authority_stability": {"checks": 8, "winner_counts": {"HOLD": 7}},
            },
            "policies": {"HOLD": _metric(2.164, 1.053)},
            "stability": {"selected_count": 10, "checks": 11},
            "economic_indifference": {
                "indifference_band_r": 0.03,
                "nearest_active_policy": {"policy": "CLOSE_10", "expected_delta_vs_hold_r": -0.001, "cvar_gain_vs_hold_r": 0.11},
                "exit_comparison": {"policy": "EXIT", "expected_delta_vs_hold_r": -0.01, "cvar_gain_vs_hold_r": 1.10},
                "policies_economically_close": True,
            },
            "strategy_next_step": {
                "next_rung_r": 2.2,
                "next_rung_price": 4194.75,
                "close_fraction_of_current_remainder": 0.1667,
            },
            "decision_requirements": {"degraded_manual_policies": policy_v7._DEGRADED_REQUIREMENTS},
            "input_audit": {"available_count": 0, "total_count": 0, "rows": {}},
        }
    }
    report = verdict.render_policy_report(snapshot)
    top = "\n".join(report.splitlines()[:9])
    assert "ПРЕДУСМОТРЕННЫЕ СТРАТЕГИЕЙ ОРДЕРА ЛЕСТНИЦЫ" in top
    assert "не прогноз обязательного продолжения роста" in top.lower()
    assert "2.200R" in top and "4 194.75" in top
    assert "ЭКОНОМИЧЕСКАЯ БЛИЗОСТЬ ПОЛИТИК" in report
    assert "Низкая надёжность больше не является абсолютным запретом" in report
