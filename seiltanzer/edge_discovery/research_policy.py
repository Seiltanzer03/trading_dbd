"""Declarative research policy for extensible strategy-agnostic EDE features.

The registry answers *what a feature is and whether it existed causally at T0*.
This module answers *where research is allowed to use it*.  Keeping the policy
separate from scoring/maturity lets future families such as RATES enter research
without rewriting statistical gates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .registry import FeatureDefinition


RESEARCH_POLICY_CONTRACT_VERSION = "g1s-ede-feature-research-policy-v1"

UNIVERSAL_TARGETS: tuple[str, ...] = (
    "DIRECTION",
    "RETURN",
    "MFE",
    "MAE",
    "FORWARD_VOLATILITY",
    "FIRST_TOUCH",
)
DEFAULT_HORIZONS: tuple[int, ...] = (15, 30, 60, 120, 240)
DEFAULT_NUMERIC_TRANSFORMS: tuple[str, ...] = (
    "RAW",
    "VELOCITY",
    "ACCELERATION",
    "ROLLING_RANK",
    "ROLLING_ZSCORE",
    "DIRECTION_CONSISTENCY",
)


@dataclass(frozen=True)
class FeatureResearchPolicy:
    feature_id: str
    family: str
    allowed_targets: tuple[str, ...]
    allowed_horizons: tuple[int, ...]
    allowed_transforms: tuple[str, ...]
    interaction_policy: str
    research_scope: str
    training_eligible: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FamilyInteractionPolicy:
    left_family: str
    right_family: str
    max_feature_pairs: int
    activation: str = "CURRENT_SELECTIVE"
    left_feature_ids: tuple[str, ...] = ()
    right_feature_ids: tuple[str, ...] = ()

    @property
    def policy_id(self) -> str:
        return f"{self.left_family}__X__{self.right_family}__{self.activation}"

    def as_dict(self) -> dict:
        return asdict(self)


# CURRENT_SELECTIVE reproduces the interaction families EDE already allowed,
# but as data rather than filter.py prefix checks.  9 OPTIONS features and the
# first 6 primitive OPTION_DYNAMICS features were the old effective limits;
# keeping those counts here makes this refactor hypothesis-universe neutral.
# RATES policies are dormant until real RATES features are registered and hence
# create zero hypotheses today. Broader interactions are predeclared for the
# universal-outcome phase and are not silently activated now.
FAMILY_INTERACTION_POLICIES: tuple[FamilyInteractionPolicy, ...] = (
    FamilyInteractionPolicy(
        "OPTIONS", "CROSS_ASSET", max_feature_pairs=9,
        activation="CURRENT_SELECTIVE",
        right_feature_ids=("cross.confirmation",),
    ),
    FamilyInteractionPolicy(
        "OPTION_DYNAMICS", "CROSS_ASSET", max_feature_pairs=6,
        activation="CURRENT_SELECTIVE",
        right_feature_ids=("cross.confirmation",),
    ),
    FamilyInteractionPolicy("RATES", "PRICE", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("RATES", "VOLATILITY", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("RATES", "OPTIONS", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("RATES", "CROSS_ASSET", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("RATES", "REGIME", 12, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("OPTIONS", "VOLATILITY", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("OPTION_DYNAMICS", "VOLATILITY", 16,
                            activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("PRICE", "VOLATILITY", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("PRICE", "CROSS_ASSET", 16, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("REGIME", "OPTIONS", 12, activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("REGIME", "OPTION_DYNAMICS", 12,
                            activation="CURRENT_SELECTIVE"),
    FamilyInteractionPolicy("REGIME", "CROSS_ASSET", 12, activation="CURRENT_SELECTIVE"),
)


def _allowed_transforms(feature: FeatureDefinition, *, market_predictor: bool) -> tuple[str, ...]:
    if not market_predictor:
        return ()
    numeric = feature.datatype in {"float", "int", "number"}
    if not numeric:
        return ("RAW",)
    # The first RATES adapter is the official *daily* Treasury CMT source.  A
    # forward-filled daily print must not silently acquire synthetic intraday
    # velocity/acceleration just because it is numeric.  The daily change,
    # z-score and rank are registered as explicit causal features instead.  A
    # future genuine intraday rates provider may use a different sampling
    # contract and therefore regain generic causal transforms.
    if feature.family == "RATES" and "daily" in feature.sampling_frequency.lower():
        return ("RAW",)
    return DEFAULT_NUMERIC_TRANSFORMS


def feature_research_policy(feature: FeatureDefinition) -> FeatureResearchPolicy:
    market_predictor = feature.research_scope == "G1S" and feature.training_eligibility
    return FeatureResearchPolicy(
        feature_id=feature.feature_id,
        family=feature.family,
        allowed_targets=UNIVERSAL_TARGETS if market_predictor else (),
        allowed_horizons=DEFAULT_HORIZONS if market_predictor else (),
        allowed_transforms=_allowed_transforms(feature, market_predictor=market_predictor),
        interaction_policy=("FAMILY_BOUNDED_V1" if market_predictor else "NONE"),
        research_scope=feature.research_scope,
        training_eligible=feature.training_eligibility,
    )


def research_policy_inventory(
    features: Iterable[FeatureDefinition],
) -> dict:
    policies = [feature_research_policy(feature) for feature in features]
    return {
        "contract_version": RESEARCH_POLICY_CONTRACT_VERSION,
        "features": [policy.as_dict() for policy in policies],
        "interaction_policies": [policy.as_dict() for policy in FAMILY_INTERACTION_POLICIES],
        "strategy_agnostic_targets": list(UNIVERSAL_TARGETS),
        "default_horizons": list(DEFAULT_HORIZONS),
        "scoring_or_maturity_gates_changed": False,
    }


def interaction_feature_pairs(
    features: Iterable[FeatureDefinition], *, eligible_feature_ids: Iterable[str],
    activation: str = "CURRENT_SELECTIVE",
    policies: Iterable[FamilyInteractionPolicy] = FAMILY_INTERACTION_POLICIES,
) -> tuple[tuple[str, str, str], ...]:
    """Return deterministic bounded feature pairs allowed by family policy.

    The result is feature IDs only; threshold/state expansion remains the job of
    the interpretable discovery layer.  No Cartesian product is allowed outside
    an explicit family policy and every policy has its own hard cap.
    """
    eligible = set(str(value) for value in eligible_feature_ids)
    definitions = {
        feature.feature_id: feature for feature in features
        if feature.feature_id in eligible
        and feature.research_scope == "G1S"
        and feature.training_eligibility
    }
    by_family: dict[str, list[str]] = {}
    for feature_id, feature in definitions.items():
        by_family.setdefault(feature.family, []).append(feature_id)
    for values in by_family.values():
        values.sort()

    output: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for policy in policies:
        if policy.activation != activation:
            continue
        left = by_family.get(policy.left_family, [])
        right = by_family.get(policy.right_family, [])
        if policy.left_feature_ids:
            allowed = set(policy.left_feature_ids)
            left = [feature_id for feature_id in left if feature_id in allowed]
        if policy.right_feature_ids:
            allowed = set(policy.right_feature_ids)
            right = [feature_id for feature_id in right if feature_id in allowed]
        count = 0
        for left_id in left:
            for right_id in right:
                if left_id == right_id:
                    continue
                key = tuple(sorted((left_id, right_id)))
                if key in seen:
                    continue
                output.append((left_id, right_id, policy.policy_id))
                seen.add(key)
                count += 1
                if count >= policy.max_feature_pairs:
                    break
            if count >= policy.max_feature_pairs:
                break
    return tuple(output)
