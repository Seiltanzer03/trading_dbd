from seiltanzer.ai_policy import (
    PolicyInputs,
    _accepted_value_confirmation,
    _raw_policy_choice,
    local_iv_surface,
    select_final_policy,
)
from seiltanzer.ai_verdict import render_policy_report


def _inputs(r0=0.306):
    return PolicyInputs(
        r0=r0, T=2.5, sigma_R=1.0, drift_R=0.0, skew_R=0.0,
        term_slope=0.0, horizon_minutes=1440, max_r=r0,
        rungs=(1.0, 1.25, 1.5, 1.75, 2.0, 2.2), rung_fraction=0.10,
        be_after=1.5, option_available=True, chain_age_sec=318,
        chain_status="delayed", proxy_quality="reference_proxy", source="test",
    )


def _reported_metrics():
    values = {
        "HOLD": (0.379, 0.390, -0.886),
        "CLOSE_10": (0.372, 0.382, -0.767),
        "CLOSE_25": (0.361, 0.369, -0.588),
        "CLOSE_50": (0.343, 0.348, -0.290),
        "EXIT": (0.306, 0.306, 0.306),
    }
    return {
        name: {
            "name": name, "expected_final_r": expected,
            "median_final_r": median, "cvar10_r": cvar,
            "p_final_profit": 0.7, "p_giveback_0_25_from_now": 0.2,
            "p_giveback_0_50_from_now": 0.1,
        }
        for name, (expected, median, cvar) in values.items()
    }


def _stability(close50=9, exit_count=2, hold=0):
    counts = {"HOLD": hold, "CLOSE_10": 0, "CLOSE_25": 0,
              "CLOSE_50": close50, "EXIT": exit_count}
    return {
        "checks": 11,
        "policy_stats": {
            name: {"winner_count": count, "winner_share": count / 11,
                   "feasible_count": 11 if name in ("CLOSE_50", "EXIT") else 0,
                   "feasible_share": 1.0 if name in ("CLOSE_50", "EXIT") else 0.0}
            for name, count in counts.items()
        },
        "winner_counts": counts,
        "winner_shares": {name: count / 11 for name, count in counts.items()},
    }


def test_gate_cannot_restore_hold_below_hard_cvar_floor():
    metrics = _reported_metrics()
    raw, rule = _raw_policy_choice(metrics, 0.306)
    assert raw == "CLOSE_50"
    assert rule["eligible"] == ["CLOSE_50", "EXIT"]
    assert "HOLD" in rule["ineligible"]
    gate = select_final_policy(
        raw, _stability(), metrics, {"adverse_confirmation_count": 2},
        _inputs(), rule,
    )
    assert gate["policy"] == "CLOSE_50"
    assert gate["hold_feasible"] is False
    assert gate["tail_risk_override"] is True
    assert metrics[gate["policy"]]["cvar10_r"] >= rule["cvar_floor_r"]


def test_zero_stability_policy_is_not_emitted_as_final_action():
    metrics = _reported_metrics()
    raw, rule = _raw_policy_choice(metrics, 0.306)
    gate = select_final_policy(
        raw, _stability(close50=0, exit_count=11), metrics,
        {"adverse_confirmation_count": 3}, _inputs(), rule,
    )
    assert gate["policy"] == "EXIT"
    assert gate["policy"] != "HOLD"
    assert gate["status"] == "conflict_stability_fallback"


def test_edge_clamped_iv_wings_are_not_independent_confirmation():
    payload = {
        "value": [
            {"spot_at_snapshot": 100, "days": 2,
             "strikes": [97, 98, 99, 100, 101, 102, 103],
             "ivs": [.35, .30, .25, .20, .19, .18, .17], "expiry": "A"},
            {"spot_at_snapshot": 100, "days": 7,
             "strikes": [97, 98, 99, 100, 101, 102, 103],
             "ivs": [.34, .29, .24, .20, .19, .18, .17], "expiry": "B"},
        ],
        "spot_current": 100,
    }
    surface = local_iv_surface(payload)
    wing = surface["wing_coverage"]
    assert wing["put_5pct_status"] == "edge_clamped"
    assert wing["call_5pct_status"] == "edge_clamped"
    assert wing["independent_confirmation_eligible"] is False


def test_accepted_value_overhead_is_context_without_full_confirmation():
    result = _accepted_value_confirmation(
        {"poc": 0.55, "value_area_high": 0.70}, 0.30,
        {"moves": {"15m": {"directional_r": 0.02},
                    "60m": {"directional_r": 0.04}}},
        delta_ratio=0.03,
    )
    assert result["levels"] == ["poc", "value_area_high"]
    assert result["confirmed"] is False


def test_report_shows_shared_geometry_empirical_no_event_and_risk_tradeoff():
    metrics = _reported_metrics()
    for metric in metrics.values():
        metric.update({"p_final_profit": .7, "p_giveback_0_25_from_now": .2,
                       "p_giveback_0_50_from_now": .1})
    snapshot = {
        "policy_manager": {
            "recommendation": {
                "policy": "CLOSE_50", "raw_optimizer_policy": "CLOSE_50",
                "action_ru": "ЗАКРЫТЬ 50% ПОЗИЦИИ СЕЙЧАС",
                "remaining_fraction": .5, "remaining_management": "исходный стоп",
                "next_rung_r": 1.0, "automatic_execution_allowed": True,
            },
            "policies": metrics,
            "selection_rule": {"cvar_floor_r": -.494,
                               "eligible": ["CLOSE_50", "EXIT"],
                               "ineligible": {"HOLD": {"cvar10_r": -.886}}},
            "gate": {"status": "confirmed", "reasons": [],
                     "raw_policy": "CLOSE_50"},
            "scenario_geometry": {
                "scenario_count": 6500, "p_next_rung_before_stop": .357,
                "p_stop_before_next_rung": .059,
                "expected_event_minutes": 812.4,
                "no_event_empirical": {"60m": {"events": 0,
                    "scenarios": 6500, "display": ">99.9%"}},
            },
            "raw_optimizer_stability": {"selected_count": 9, "checks": 11,
                                         "selected_share": 9 / 11},
            "stability": {"selected_count": 9, "checks": 11,
                          "selected_share": 9 / 11},
            "risk_tradeoff": {"expected_cost_vs_hold_r": -.036,
                              "cvar_improvement_vs_hold_r": .596},
            "evidence": {"adverse_confirmations": [],
                         "supportive_contradictions": [],
                         "context_observations": [], "uncertainty_flags": [],
                         "data_quality": {"reliability": {
                             "level": "средняя", "reasons": ["delayed chain"]}}},
            "metric_coverage": {"summary": {"available_groups": 12,
                                               "total_groups": 12}},
            "inputs": {"chain_status": "delayed", "chain_age_sec": 318,
                       "proxy_quality": "reference_proxy"},
            "counterfactual_attribution": {"available": False},
            "cancellation_boundary": {"available": False,
                "reason": "Для HOLD границы отмены до исполнения нет; переоценка по событиям."},
        }
    }
    report = render_policy_report(snapshot)
    assert "0 из 6500" in report
    assert ">99.9%" in report
    assert "HOLD исключён" in report
    assert "Цена защиты относительно HOLD: -0.036R" in report
    assert "улучшение CVaR10: +0.596R" in report
    assert report.count("P рубежа раньше стопа") == 1
    assert "Надёжность расчёта: средняя" in report
