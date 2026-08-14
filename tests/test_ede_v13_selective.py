from __future__ import annotations

from seiltanzer.edge_discovery.registry import FEATURES
from seiltanzer.edge_discovery.selective import (
    MAX_CONDITIONS,
    MAX_SELECTIVE_TEMPLATES,
    QUANTILE_STATES,
    _baseline_failure,
    selective_templates,
)


def _eligible_ids() -> set[str]:
    return {
        item.feature_id for item in FEATURES
        if item.research_scope == "G1S" and item.training_eligibility
    }


def test_selective_search_budget_is_bounded_and_deterministic():
    eligible = _eligible_ids()
    first = selective_templates(eligible)
    second = selective_templates(eligible)
    assert [item.template_id for item in first] == [item.template_id for item in second]
    assert len(first) <= MAX_SELECTIVE_TEMPLATES
    assert max(item.complexity for item in first) <= MAX_CONDITIONS
    assert all(
        condition.feature_id in eligible
        for item in first for condition in item.conditions
    )


def test_single_feature_map_preserves_predeclared_option_quintiles():
    templates = selective_templates(_eligible_ids())
    states = {
        item.conditions[0].state
        for item in templates
        if item.complexity == 1
        and item.conditions[0].feature_id == "option.iv"
    }
    assert set(QUANTILE_STATES) <= states


def test_selective_interactions_cover_static_derivative_cross_and_regime():
    templates = selective_templates(_eligible_ids())
    feature_sets = {
        frozenset(condition.feature_id for condition in item.conditions)
        for item in templates
    }
    assert frozenset({
        "option.iv", "option_dynamics.iv_velocity"}) in feature_sets
    assert frozenset({
        "option_dynamics.gex_velocity", "cross.confirmation"}) in feature_sets
    assert frozenset({
        "option_dynamics.iv_velocity", "regime.trend"}) in feature_sets
    assert any(
        {"option_dynamics.iv_velocity", "cross.confirmation", "regime.trend"}
        == set(features)
        for features in feature_sets
    )


def test_identity_categories_do_not_consume_selective_budget():
    templates = selective_templates(_eligible_ids())
    searched = {
        condition.feature_id for item in templates for condition in item.conditions
    }
    assert "regime.asset" not in searched
    assert "regime.asset_family" not in searched
    assert "regime.session_utc" not in searched


def test_baseline_failure_requires_global_ret5_worse_than_neutral_control():
    candidate = {
        "global_ret5": {"brier": .27, "logloss": .72},
        "sanity_baselines": {"constant_0_5": {"brier": .25, "logloss": .693}},
    }
    assert _baseline_failure(candidate) is True
    candidate["global_ret5"]["brier"] = .23
    assert _baseline_failure(candidate) is False
