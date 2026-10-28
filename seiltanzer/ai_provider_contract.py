"""Bounded provider wire schema; local source, risk and score guards stay authoritative."""
from __future__ import annotations

from .edge_family_adapters import FAMILIES
from .llm_decision_shadow import VALID_POLICIES

POLICY_SCORE_ORDER = VALID_POLICIES
EXPLICIT_OPINION_CONTRACT = 'single-explicit-choice-v1'


def _object(properties):
    return dict(type='object', properties=properties,
                required=list(properties), additionalProperties=False)


def provider_response_format():
    """Use a portable strict schema; numeric/source admission remains local."""
    actions = sorted(VALID_POLICIES)
    preference = _object(dict(policy=dict(type='string', enum=actions),
                              score=dict(type='number')))
    family = _object(dict(
        family_id=dict(type='string', enum=sorted(FAMILIES)),
        feature_names=dict(type='array', items=dict(type='string')),
        policy_preferences=dict(type='array', items=preference),
        reason_ru=dict(type='string')))
    strings = lambda: dict(type='array', items=dict(type='string'))
    shadow = _object(dict(
        opinion_contract=dict(type='string', enum=[EXPLICIT_OPINION_CONTRACT]),
        policy=dict(type='string', enum=actions),
        confidence=dict(type='number'),
        reason_ru=dict(type='string'),
        key_evidence=strings(), counter_evidence=strings(),
        evidence_families=strings(),
        family_assessments=dict(type='array', items=family),
        invalidation_conditions=strings()))
    return dict(type='json_schema', json_schema=dict(
        name='independent_trade_preferences_v3', strict=True,
        schema=_object(dict(shadow_decision=shadow))))


def normalize_score_wire(shadow):
    """Decode the complete ordered vector; never infer or fill any preference.

    Keyed legacy responses retain the existing domain validator and archives.
    """
    scores = shadow.get('policy_scores')
    if not isinstance(scores, list):
        return shadow
    if (len(scores) != len(POLICY_SCORE_ORDER)
            or any(type(value) not in (int, float) or not -1 <= value <= 1
                   for value in scores)):
        raise RuntimeError('shadow_invalid_policy_scores')
    return {**shadow, 'policy_scores': dict(zip(POLICY_SCORE_ORDER, scores))}


def normalize_family_wire(shadow):
    """Translate strict array rows to the existing eight-family domain contract.

    Legacy dictionary responses retain their original isolation semantics.
    Structural duplicates cannot silently replace a different preference.
    """
    rows = shadow.get('family_assessments')
    if not isinstance(rows, list):
        return shadow
    if len(rows) > len(FAMILIES):
        raise RuntimeError('combined_provider_invalid_family_preferences')
    families = {}
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError('combined_provider_invalid_family_preferences')
        family = row.get('family_id')
        preferences = row.get('policy_preferences')
        if (not isinstance(family, str) or family not in FAMILIES or family in families
                or not isinstance(preferences, list) or not 1 <= len(preferences) <= 3):
            raise RuntimeError('combined_provider_invalid_family_preferences')
        scores = {}
        for preference in preferences:
            if not isinstance(preference, dict):
                raise RuntimeError('combined_provider_invalid_family_preferences')
            policy, score = preference.get('policy'), preference.get('score')
            if (not isinstance(policy, str) or policy not in VALID_POLICIES or policy in scores
                    or type(score) not in (int, float) or not -1 <= score <= 1):
                raise RuntimeError('combined_provider_invalid_family_preferences')
            scores[policy] = score
        families[family] = dict(feature_names=row.get('feature_names'),
            policy_scores=scores, reason_ru=row.get('reason_ru'))
    return {**shadow, 'family_assessments': families}
