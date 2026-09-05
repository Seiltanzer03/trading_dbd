from __future__ import annotations

from seiltanzer.edge_discovery.filters import (
    CandidateTemplate,
    candidate_templates,
    _policy_mixed_templates,
)
from seiltanzer.edge_discovery.registry import FEATURES
from seiltanzer.edge_discovery.research_policy import (
    FAMILY_INTERACTION_POLICIES,
    interaction_feature_pairs,
)


def test_multi_family_interaction_policies_are_active():
    active_policies = {
        (p.left_family, p.right_family): p
        for p in FAMILY_INTERACTION_POLICIES
        if p.activation == "CURRENT_SELECTIVE"
    }
    expected_pairs = [
        ("OPTIONS", "VOLATILITY"),
        ("OPTION_DYNAMICS", "VOLATILITY"),
        ("PRICE", "VOLATILITY"),
        ("PRICE", "CROSS_ASSET"),
        ("REGIME", "OPTIONS"),
        ("REGIME", "OPTION_DYNAMICS"),
        ("REGIME", "CROSS_ASSET"),
        ("OPTIONS", "CROSS_ASSET"),
        ("OPTION_DYNAMICS", "CROSS_ASSET"),
    ]
    for left, right in expected_pairs:
        assert (left, right) in active_policies, f"Policy ({left}, {right}) must be CURRENT_SELECTIVE"


def test_interaction_feature_pairs_generates_multi_family_pairs():
    eligible = {
        f.feature_id for f in FEATURES
        if f.research_scope == "G1S" and f.training_eligibility
    }
    pairs = interaction_feature_pairs(
        FEATURES,
        eligible_feature_ids=eligible,
        activation="CURRENT_SELECTIVE",
    )
    assert len(pairs) >= 100
    policy_ids = {p[2] for p in pairs}
    assert "OPTIONS__X__VOLATILITY__CURRENT_SELECTIVE" in policy_ids
    assert "OPTION_DYNAMICS__X__VOLATILITY__CURRENT_SELECTIVE" in policy_ids
    assert "PRICE__X__VOLATILITY__CURRENT_SELECTIVE" in policy_ids
    assert "PRICE__X__CROSS_ASSET__CURRENT_SELECTIVE" in policy_ids
    assert "REGIME__X__OPTIONS__CURRENT_SELECTIVE" in policy_ids
    assert "REGIME__X__OPTION_DYNAMICS__CURRENT_SELECTIVE" in policy_ids


def test_candidate_templates_contains_multi_family_interaction_hypotheses():
    eligible = {
        f.feature_id for f in FEATURES
        if f.research_scope == "G1S" and f.training_eligibility
    }
    templates = candidate_templates(eligible)
    assert len(templates) == 248
    complexity_2 = [t for t in templates if t.complexity == 2]
    assert len(complexity_2) > 0
    # Verify that multi-family pairs exist among complexity-2 hypotheses
    feature_pairs = [
        (t.conditions[0].feature_id, t.conditions[1].feature_id)
        for t in complexity_2
    ]
    assert len(feature_pairs) > 0
