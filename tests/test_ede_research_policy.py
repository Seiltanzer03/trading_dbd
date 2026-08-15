from __future__ import annotations

from seiltanzer.edge_discovery.registry import FEATURES, FeatureDefinition
from seiltanzer.edge_discovery.research_policy import (
    DEFAULT_HORIZONS,
    FAMILY_INTERACTION_POLICIES,
    UNIVERSAL_TARGETS,
    FamilyInteractionPolicy,
    feature_research_policy,
    interaction_feature_pairs,
    research_policy_inventory,
)


def _feature(feature_id: str, family: str, *, datatype: str = "float") -> FeatureDefinition:
    return FeatureDefinition(
        feature_id=feature_id,
        family=family,
        source="test",
        datatype=datatype,
        t0_availability="causal",
        sampling_frequency="test",
        asof_timestamp="asof<=T0",
        quality="test",
        staleness="test",
        historical_availability="UNAVAILABLE",
        live_availability="AVAILABLE",
        training_eligibility=True,
        dependency_family=family.lower(),
    )


def test_existing_market_features_get_strategy_agnostic_research_contract():
    target = next(feature for feature in FEATURES if feature.feature_id == "option.iv")
    policy = feature_research_policy(target)
    assert policy.allowed_targets == UNIVERSAL_TARGETS
    assert policy.allowed_horizons == DEFAULT_HORIZONS
    assert "ACCELERATION" in policy.allowed_transforms
    assert policy.interaction_policy == "FAMILY_BOUNDED_V1"


def test_quality_metadata_never_becomes_market_predictor():
    target = next(feature for feature in FEATURES if feature.feature_id == "quality.staleness")
    policy = feature_research_policy(target)
    assert policy.allowed_targets == ()
    assert policy.allowed_horizons == ()
    assert policy.allowed_transforms == ()
    assert policy.interaction_policy == "NONE"


def test_synthetic_rates_family_enters_predeclared_pairs_without_core_gate_changes():
    rates = (
        _feature("rates.us10y_yield", "RATES"),
        _feature("rates.us02y_yield", "RATES"),
    )
    price = (_feature("price.synthetic_state", "PRICE"),)
    cross = (_feature("cross.synthetic_breadth", "CROSS_ASSET"),)
    features = rates + price + cross
    eligible = [feature.feature_id for feature in features]
    pairs = interaction_feature_pairs(features, eligible_feature_ids=eligible)
    pair_ids = {(left, right) for left, right, _policy in pairs}
    assert ("rates.us02y_yield", "price.synthetic_state") in pair_ids
    assert ("rates.us10y_yield", "cross.synthetic_breadth") in pair_ids

    inventory = research_policy_inventory(features)
    assert inventory["scoring_or_maturity_gates_changed"] is False
    rates_policy = next(
        item for item in inventory["features"]
        if item["feature_id"] == "rates.us10y_yield"
    )
    assert "FIRST_TOUCH" in rates_policy["allowed_targets"]


def test_family_policy_is_bounded_not_unrestricted_cartesian_product():
    left = tuple(_feature(f"test.left_{index}", "LEFT") for index in range(10))
    right = tuple(_feature(f"test.right_{index}", "RIGHT") for index in range(10))
    policy = FamilyInteractionPolicy("LEFT", "RIGHT", max_feature_pairs=7)
    features = left + right
    pairs = interaction_feature_pairs(
        features,
        eligible_feature_ids=[feature.feature_id for feature in features],
        policies=(policy,),
    )
    assert len(pairs) == 7
    assert len({(left_id, right_id) for left_id, right_id, _ in pairs}) == 7


def test_current_option_cross_policy_is_declarative_and_restricted_to_confirmation():
    policies = [
        policy for policy in FAMILY_INTERACTION_POLICIES
        if policy.left_family == "OPTIONS" and policy.right_family == "CROSS_ASSET"
        and policy.activation == "CURRENT_SELECTIVE"
    ]
    assert len(policies) == 1
    assert policies[0].right_feature_ids == ("cross.confirmation",)
