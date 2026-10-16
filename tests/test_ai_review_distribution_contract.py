from copy import deepcopy

import pytest

from seiltanzer import ai_runtime_report_v20 as report
from seiltanzer import unified_candidate_economics as economics
from seiltanzer.unified_edge_audit import render_unified_ensemble_lines
from seiltanzer.unified_edge_ensemble import build_unified_ensemble
from test_ai_report_coherence import selected_snapshot
from test_ai_snapshot_market_refresh import fixture as refresh_fixture
from test_unified_candidate_economics import snapshot as priced_snapshot, candidate
from test_unified_edge_ensemble import snapshot as ensemble_snapshot, llm


@pytest.mark.parametrize("policy", ["MOVE_TO_BE", "TRAIL_GAMMA_FLIP", "EXTEND_TAKE"])
def test_missing_quantitative_score_abstains_without_weights_above_one(policy):
    snapshot = ensemble_snapshot()
    before = deepcopy(snapshot)
    audit = build_unified_ensemble(snapshot, llm(policy="HOLD", policy_scores={
        "HOLD": 1., policy: -.5}))
    row = next(row for row in audit["candidates"] if row["policy"] == policy)
    assert not row["eligible"]
    parts = row["component_contributions"]
    quantitative = next(part for part in parts if part["component_id"] == "quantitative_base")
    assert quantitative["score"] is None
    assert quantitative["effective_weight"] == 0.
    assert all(0 <= part["effective_weight"] <= 1 for part in parts)
    assert sum(part["effective_weight"] for part in parts) == pytest.approx(1.)
    assert sum(part["contribution"] for part in parts) == pytest.approx(row["score"])
    assert audit["selected_policy"] == "CLOSE_25"
    assert snapshot == before


def test_common_bank_statistics_use_weighted_net_outcomes_and_partial_close_costs():
    snapshot = priced_snapshot([[1., 3.], [1., -1.], [1., 1.]], [.05, .15, .8])
    original = deepcopy(snapshot)
    result = economics.price_unified_candidates(snapshot, [
        candidate("HOLD"), candidate("CLOSE_25"), candidate("EXIT")])
    assert result["available"], result
    hold, partial, exit_row = (result["candidates"][key] for key in ("HOLD", "CLOSE_25", "EXIT"))
    for row, median in ((hold, .98), (partial, .975), (exit_row, .96)):
        assert row["median_net_r"] == pytest.approx(median)
        assert row["statistics_basis"] == "COMMON_BANK_NET_OUTCOMES_NOT_CALIBRATED_PHYSICAL_P"
    for row in (hold, partial):
        assert row["p_final_profit"] == pytest.approx(.85)
        assert row["p_final_loss"] == pytest.approx(.15)
        assert row["p_giveback_0_25_from_now"] == pytest.approx(.15)
        assert row["p_giveback_0_50_from_now"] == pytest.approx(.15)
    assert exit_row["p_final_profit"] == pytest.approx(1.)
    assert exit_row["p_final_loss"] == 0.
    assert snapshot == original


def test_weighted_statistics_ignore_zero_mass_and_keep_zero_outcome_distinct_from_loss():
    snapshot = priced_snapshot([[1., 3.], [1., .75], [1., .5], [1., 0.]], [0., .2, .3, .5])
    snapshot["policy_manager"]["execution_cost_model"].update(
        immediate_full_close_r=0., deferred_full_close_r=0.)
    row = economics.price_unified_candidates(snapshot, [candidate("HOLD")])["candidates"]["HOLD"]
    assert row["median_net_r"] == 0.  # Lower inverse of the weighted empirical CDF.
    assert row["p_final_profit"] == pytest.approx(.5)
    assert row["p_final_loss"] == 0.
    assert row["p_giveback_0_25_from_now"] == pytest.approx(1.)
    assert row["p_giveback_0_50_from_now"] == pytest.approx(.8)


def test_quote_refresh_retains_fresh_scalar_statistics_without_reviving_bridge_geometry(monkeypatch):
    from seiltanzer.ai_snapshot_market_refresh import refresh_frozen_comparison
    engine, snapshot, _ = refresh_fixture(monkeypatch)
    snapshot["policy_manager"]["policies"] = {"HOLD": {"p_final_profit": .123456}}
    snapshot["report_integrity"] = {"scenario_geometry": {"scenario_count": 6500}}
    before = deepcopy(snapshot)
    result = refresh_frozen_comparison(engine, snapshot)
    common = economics.price_unified_candidates(result, [candidate(policy) for policy in economics.BASE_FRACTIONS])
    for policy, expected in common["candidates"].items():
        row = result["policy_manager"]["policies"][policy]
        assert row["median_final_r_net"] == pytest.approx(expected["median_net_r"])
        assert row["median_final_r"] == pytest.approx(expected["median_net_r"])
        for key in ("p_final_profit", "p_final_loss", "p_giveback_0_25_from_now", "p_giveback_0_50_from_now"):
            assert row[key] == pytest.approx(expected[key])
        assert row["statistics_basis"] == expected["statistics_basis"]
        assert result["report_integrity"]["policies"][policy] == row
    assert result["policy_manager"]["policies"]["HOLD"]["p_final_profit"] != .123456
    assert result["policy_manager"]["scenario_geometry"] == {}
    assert result["trade_geometry"]["take_first"] is None
    assert snapshot == before


def test_unavailable_expert_does_not_publish_a_quality_estimate():
    audit = {"available": True, "selected_policy": "HOLD", "selected_candidate_id": "HOLD",
             "components": [{"component_id": "mathematical_edge", "available": False,
                 "availability": "UNAVAILABLE", "quality": 1., "nominal_weight": .15,
                 "effective_weight": 0., "reason": "COMPLETED_CAUSAL_5M_PRICE_FEATURES_UNAVAILABLE"}]}
    before = deepcopy(audit)
    text = "\n".join(render_unified_ensemble_lines(audit))
    assert "качество 100.0%" not in text
    assert "качество —" in text
    assert audit == before


def test_family_readiness_counts_describe_current_forecasts_not_all_fitted_models(monkeypatch):
    from seiltanzer import ai_verdict_v19
    monkeypatch.setattr(report, "_BASE_QUALITY_LINES", getattr(
        report, "_BASE_QUALITY_LINES", ai_verdict_v19._quality_lines), raising=False)
    snapshot = selected_snapshot("CLOSE_25")
    snapshot["policy_manager"]["unified_edge_ensemble"]["edge_families"] = {
        "session": {"available": False, "forecast_available": False}}
    text = "\n".join(report._quality_lines(snapshot))
    assert "Доступные сейчас обученные прогнозы семейств: 0/1" in text
    assert "не число обученных моделей" in text


def test_refreshed_report_identifies_probabilities_as_common_model_frequencies():
    from seiltanzer import ai_verdict_v19
    snapshot = selected_snapshot("CLOSE_25")
    snapshot["market_refresh"] = {"available": True, "comparison_statistics_basis":
        "COMMON_BANK_NET_OUTCOMES_NOT_CALIBRATED_PHYSICAL_P"}
    text = ai_verdict_v19.normalize_final_report(
        "**РАСЧЁТ ПОЛИТИК** —\nHOLD: Expected net +0.100R.\n\n"
        "**ЭКОНОМИЧЕСКАЯ БЛИЗОСТЬ ПОЛИТИК** —\nТекущая экономика.", snapshot)
    assert "Медиана и вероятности ниже рассчитаны" in text
    assert "не калиброванные physical P" in text


def test_missing_price_feature_explanation_distinguishes_history_from_current_quote():
    from seiltanzer.mathematical_edge import render_math_edge
    text = render_math_edge({"available": False,
        "reason": "COMPLETED_CAUSAL_5M_PRICE_FEATURES_UNAVAILABLE"})
    assert "13 последовательных завершённых 5-минутных свечей" in text
    assert "Текущая котировка не заменяет" in text
    assert "COMPLETED_CAUSAL_5M_PRICE_FEATURES_UNAVAILABLE" in text
