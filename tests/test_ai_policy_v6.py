from types import SimpleNamespace

import seiltanzer.ai_policy as policy
import seiltanzer.ai_policy_v6 as policy_v6
import seiltanzer.ai_verdict as verdict


def _inputs():
    return policy.PolicyInputs(
        r0=1.83,
        T=2.97,
        sigma_R=0.75,
        drift_R=0.0,
        skew_R=-0.17,
        term_slope=0.0,
        horizon_minutes=602.0,
        max_r=1.95,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2),
        rung_fraction=0.10,
        be_after=1.5,
        option_available=True,
        chain_age_sec=300.0,
        chain_status="delayed",
        proxy_quality="reference_proxy",
        source="test",
    )


def test_indicator_trailing_is_not_used_as_cvar_floor():
    spec = policy.risk_constraint(
        _inputs(), {}, {"trailing_stop_r": 1.40}
    )
    assert spec["cvar_floor_r"] == 0.0
    assert spec["trailing_modelled"] is False
    assert "trailing" in spec["rule"]

    explicit = policy.risk_constraint(
        _inputs(), {}, {"stop_r": 0.50, "trailing_stop_r": 1.40}
    )
    assert explicit["cvar_floor_r"] == 0.50
    assert "stop_r" in explicit["source"]


def test_stable_hold_is_confirmed_even_when_data_reliability_is_low(monkeypatch):
    def fake_select(*args, **kwargs):
        return {
            "policy": "HOLD",
            "provisional_policy": "HOLD",
            "status": "manual_data_conflict",
            "automatic_execution_allowed": False,
            "authority_stability": {
                "checks": 8,
                "winner_counts": {"HOLD": 8},
                "winner_shares": {"HOLD": 1.0},
            },
            "source_stability_share": 1.0,
            "reasons": ["надёжность расчёта низкая"],
        }

    monkeypatch.setattr(policy_v6, "_BASE_SELECT", fake_select)
    result = policy.select_final_policy(
        "HOLD",
        {"selected_share": 1.0, "selected_count": 11, "checks": 11},
        {"HOLD": {"cvar10_r": 0.429}},
        {
            "adverse_confirmation_families": [],
            "data_quality": {"reliability": {"level": "низкая"}},
        },
        _inputs(),
        {"cvar_floor_r": 0.0, "eligible": ["HOLD", "EXIT"]},
    )
    assert result["status"] == "confirmed_hold"
    assert result["working_action_confirmed"] is True
    assert result["execution_required"] is False
    assert result["automatic_execution_allowed"] is False
    assert result["execution_policy"] is None
    assert any("не отменяет" in reason for reason in result["reasons"])


def _base_analysis():
    return {
        "inputs": {"r0": 1.83},
        "recommendation": {
            "policy": "HOLD",
            "raw_optimizer_policy": "HOLD",
        },
        "gate": {
            "status": "confirmed_hold",
            "working_action_confirmed": True,
            "automatic_execution_allowed": False,
        },
    }


def test_analysis_adds_exact_price_triggers_refresh_time_and_input_roles(monkeypatch):
    monkeypatch.setattr(policy_v6, "_BASE_ANALYZE", lambda *a, **k: _base_analysis())
    engine = SimpleNamespace(settings=SimpleNamespace(chain_poll_sec=600.0))
    tick = {
        "ts": 1_000.0,
        "feeds": {
            "price": {"value": 4171.90, "status": "live", "source": "Swissquote"},
            "proxy_price": {"value": 382.57, "status": "live", "source": "Yahoo"},
            "chain": {"ts": 700.0, "status": "delayed", "source": "yfinance GLD"},
            "vols": {"vix": {"status": "delayed"}, "gvz": {"status": "delayed"}},
        },
        "atr": {"available": True},
        "regime": {"phase": "impulse"},
        "vrp": {"ratio": 1.36},
        "levels": {"vwap": 4168.0},
        "correlation": {"status": "delayed"},
        "filters": {"all_pass": True},
        "ladder": {"max_r": 1.95},
    }
    trade = {
        "entry": 4058.90,
        "stop": 3997.15,
        "direction": "long",
    }
    result = policy.analyze_policies(engine, tick, {"available": True}, trade)
    triggers = result["recalculation_triggers"]
    assert triggers["minus_0_15_r"]["r"] == 1.68
    assert round(triggers["minus_0_15_r"]["price"], 4) == 4162.64
    assert triggers["plus_0_15_r"]["r"] == 1.98
    assert round(triggers["plus_0_15_r"]["price"], 4) == 4181.165
    assert triggers["chain_refresh"]["next_attempt_ts"] == 1300.0
    assert triggers["chain_refresh"]["seconds_until_attempt"] == 300.0
    assert triggers["chain_refresh"]["guarantees_live_direct"] is False
    audit = result["input_audit"]
    assert audit["rows"]["instrument_price"]["role"] == "optimizer_and_geometry"
    assert audit["rows"]["oi_gex_strike_landscape"]["role"] == "context_only"
    assert audit["all_inputs_equally_weighted"] is False
    assert result["management_model_scope"]["indicator_trailing_modelled"] is False


def _metric(expected, cvar):
    return {
        "expected_final_r": expected,
        "gross_expected_final_r": expected + 0.01,
        "execution_cost_r": 0.01,
        "median_final_r": expected,
        "cvar10_r": cvar,
        "p_final_profit": 0.99,
        "p_giveback_0_25_from_now": 0.30,
        "p_giveback_0_50_from_now": 0.20,
    }


def test_report_does_not_call_hold_unconfirmed_and_shows_prices_time_and_audit():
    snapshot = {
        "policy_manager": {
            "recommendation": {
                "policy": "HOLD",
                "raw_optimizer_policy": "HOLD",
                "working_action_confirmed": True,
            },
            "gate": {
                "status": "confirmed_hold",
                "working_action_confirmed": True,
                "automatic_execution_allowed": False,
                "authority_stability": {
                    "checks": 8,
                    "winner_counts": {"HOLD": 8},
                    "winner_shares": {"HOLD": 1.0},
                },
                "source_stability_share": 1.0,
                "reasons": ["HOLD подтверждён"],
            },
            "policies": {
                "HOLD": _metric(1.676, 0.429),
                "CLOSE_10": _metric(1.676, 0.553),
                "CLOSE_25": _metric(1.675, 0.740),
                "CLOSE_50": _metric(1.674, 1.050),
                "EXIT": _metric(1.672, 1.672),
            },
            "scenario_geometry": {
                "scenario_count": 6500,
                "rung_first_count": 4243,
                "stop_first_count": 4,
                "unresolved_count": 2253,
                "resolved_count": 4247,
                "p_next_rung_before_stop": 4243 / 6500,
                "p_stop_before_next_rung": 4 / 6500,
                "p_unresolved_full_horizon": 2253 / 6500,
                "full_horizon_minutes": 602,
                "mean_event_minutes_given_resolved": 180.6,
                "no_event_windows": {
                    "60m": {
                        "events": 893,
                        "no_event_count": 5607,
                        "scenarios": 6500,
                        "no_event_probability": 5607 / 6500,
                    }
                },
            },
            "selection_rule": {"cvar_floor_r": 0.0, "eligible": [
                "HOLD", "CLOSE_10", "CLOSE_25", "CLOSE_50", "EXIT"]},
            "risk_constraint": {
                "cvar_floor_r": 0.0,
                "source": "strategy BE floor 0R",
                "rule": "current stop/BE; indicator trailing is outside the model",
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
                "selected_count": 11, "checks": 11, "selected_share": 1.0},
            "stability": {
                "selected_count": 11, "checks": 11, "selected_share": 1.0},
            "evidence": {
                "adverse_confirmations": [],
                "supportive_contradictions": [],
                "context_observations": [],
                "adverse_confirmation_families": [],
                "supportive_confirmation_families": ["option_distribution"],
                "mixed_confirmation_families": [],
                "data_quality": {
                    "reliability": {"level": "низкая", "reasons": ["delayed"]}},
            },
            "metric_coverage": {
                "summary": {"available_groups": 12, "total_groups": 12}},
            "inputs": {
                "r0": 1.83,
                "chain_status": "delayed",
                "chain_age_sec": 300,
                "proxy_quality": "reference_proxy",
            },
            "counterfactual_attribution": {"available": False},
            "cancellation_boundary": {
                "available": False,
                "reason": "Для HOLD границы отмены до исполнения нет",
            },
            "recalculation_triggers": {
                "minus_0_15_r": {"r": 1.68, "price": 4162.64},
                "plus_0_15_r": {"r": 1.98, "price": 4181.165},
                "chain_refresh": {
                    "next_attempt_local": "2026-08-05T14:18:00+03:00",
                    "seconds_until_attempt": 180,
                    "poll_interval_sec": 600,
                    "overdue": False,
                    "current_source": "yfinance GLD options",
                    "current_status": "delayed",
                },
            },
            "input_audit": {
                "available_count": 2,
                "total_count": 2,
                "rows": {
                    "instrument_price": {
                        "available": True, "status": "live",
                        "source": "Swissquote", "role": "optimizer_and_geometry"},
                    "oi_gex_strike_landscape": {
                        "available": True, "role": "context_only"},
                },
            },
            "management_model_scope": {
                "ladder_modelled": True,
                "breakeven_modelled": True,
                "indicator_trailing_modelled": False,
            },
        }
    }
    report = verdict.render_policy_report(snapshot)
    top = "\n".join(report.splitlines()[:8])
    assert "HOLD подтверждён" in top
    assert "оно не подтверждено" not in top
    assert "НЕ ВЫСТАВЛЯТЬ НОВЫХ ОРДЕРОВ" in top
    assert "цена 4 162.64" in report
    assert "цена 4 181.16" in report
    assert "05.08.2026 14:18:00" in report
    assert "не гарантирует live/direct" in report
    assert "КАКИЕ ДАННЫЕ РЕАЛЬНО УЧТЕНЫ" in report
    assert "OI/GEX" in report
    assert "индикаторный трейлинг исключён" in report.lower()
    assert "БУ/trailing" not in report
