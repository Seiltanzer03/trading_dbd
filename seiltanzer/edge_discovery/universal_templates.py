"""Bounded interpretable templates for strategy-agnostic universal outcomes.

Legacy directional EDE keeps its exact frozen template universe.  PASS 5 builds a
parallel discovery universe that may include newly registered families such as
RATES without silently changing the legacy exam.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import product
from typing import Iterable

from .filters import CandidateTemplate, ConditionTemplate, candidate_templates
from .rates_registry import RATES_FEATURE_DEFINITIONS
from .registry import FEATURES, FeatureDefinition
from .research_policy import interaction_feature_pairs


UNIVERSAL_TEMPLATE_CONTRACT_VERSION = "g1s-universal-structured-template-v1"
MAX_UNIVERSAL_TEMPLATES = 384
MAX_UNIVERSAL_CONDITIONS = 3


def universal_feature_definitions() -> tuple[FeatureDefinition, ...]:
    """Feature definitions admitted to the PASS 5 discovery layer.

    RATES remain separate from the live canonical registry until a real T0
    capture path exists.  They are nevertheless valid historical/off-host
    discovery features with explicit causality and staleness contracts.
    """
    by_id = {item.feature_id: item for item in tuple(FEATURES) + tuple(RATES_FEATURE_DEFINITIONS)}
    return tuple(by_id[key] for key in sorted(by_id))


def _numeric_single(feature_id: str) -> tuple[CandidateTemplate, CandidateTemplate]:
    return (
        CandidateTemplate((ConditionTemplate(feature_id, "train_relative", "ABOVE_MEDIAN"),)),
        CandidateTemplate((ConditionTemplate(feature_id, "train_relative", "BELOW_MEDIAN"),)),
    )


def universal_candidate_templates(
    *, eligible_feature_ids: Iterable[str],
    feature_definitions: Iterable[FeatureDefinition] | None = None,
) -> tuple[CandidateTemplate, ...]:
    """Return deterministic bounded templates without changing legacy EDE.

    Legacy/base templates are retained. Newly declared numeric features receive
    only above/below-train-median singles plus family-policy-bounded pairwise
    interactions.  There is no unrestricted Cartesian product.
    """
    definitions = tuple(feature_definitions or universal_feature_definitions())
    eligible = set(str(value) for value in eligible_feature_ids)
    by_id = {item.feature_id: item for item in definitions}

    values: dict[str, CandidateTemplate] = {
        item.template_id: item for item in candidate_templates()
    }
    single_conditions: dict[str, list[ConditionTemplate]] = defaultdict(list)
    for feature_id in sorted(eligible):
        feature = by_id.get(feature_id)
        if feature is None or feature.research_scope != "G1S" or not feature.training_eligibility:
            continue
        if feature.datatype in {"float", "int", "number"}:
            for template in _numeric_single(feature_id):
                values.setdefault(template.template_id, template)
                single_conditions[feature_id].append(template.conditions[0])

    for left_id, right_id, _policy in interaction_feature_pairs(
        definitions,
        eligible_feature_ids=eligible,
        activation="CURRENT_SELECTIVE",
    ):
        left = single_conditions.get(left_id, ())
        right = single_conditions.get(right_id, ())
        for first, second in product(left, right):
            template = CandidateTemplate((first, second))
            values.setdefault(template.template_id, template)

    ordered = [values[key] for key in sorted(values)]
    if any(item.complexity > MAX_UNIVERSAL_CONDITIONS for item in ordered):
        raise RuntimeError("universal structured template depth exceeded")
    if len(ordered) > MAX_UNIVERSAL_TEMPLATES:
        # Keep all newly eligible singles first, then deterministic bounded
        # interaction/base remainder. This prevents a new family from silently
        # disappearing simply because legacy IDs sort earlier.
        mandatory_ids = {
            template.template_id
            for feature_id in eligible
            for template in _numeric_single(feature_id)
            if feature_id in by_id
        }
        mandatory = [item for item in ordered if item.template_id in mandatory_ids]
        remainder = [item for item in ordered if item.template_id not in mandatory_ids]
        if len(mandatory) > MAX_UNIVERSAL_TEMPLATES:
            raise RuntimeError("eligible feature singles exceed universal template cap")
        ordered = mandatory + remainder[:MAX_UNIVERSAL_TEMPLATES-len(mandatory)]
    return tuple(ordered)
