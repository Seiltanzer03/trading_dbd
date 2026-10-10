"""Source-bound current LLM family interpretations inside its existing budget.

This is a manual preference, never a trained forecast, probability or net edge.
Only frozen observed features can be interpreted; no providers or fitting here.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .edge_family_adapters import FAMILIES, MAX_AGE_SEC


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def parse_family_assessments(value):
    """Optional bounded provider contract; legacy missing output stays empty."""
    from .llm_decision_shadow import VALID_POLICIES
    if value is None:
        return {}
    if not isinstance(value, dict) or not set(value).issubset(FAMILIES):
        raise RuntimeError('shadow_invalid_family_assessments')
    result = {}
    for family, item in value.items():
        if not isinstance(item, dict):
            raise RuntimeError('shadow_invalid_family_assessments')
        if not item:
            result[family] = {}
            continue
        features, scores, reason = (item.get(key) for key in ('feature_names', 'policy_scores', 'reason_ru'))
        if (not isinstance(features, list) or not 1 <= len(features) <= 4
                or any(not isinstance(name, str) or not 0 < len(name) <= 128 for name in features)
                or len(set(features)) != len(features)
                or not isinstance(scores, dict) or not 1 <= len(scores) <= 3
                or not set(scores).issubset(VALID_POLICIES)
                or any(_number(score) is None or not -1 <= score <= 1 for score in scores.values())
                or not isinstance(reason, str) or not reason.strip() or len(reason) > 240):
            raise RuntimeError('shadow_invalid_family_assessments')
        result[family] = {'feature_names': list(features),
                          'policy_scores': {key: float(score) for key, score in scores.items()},
                          'reason_ru': reason.strip()}
    return result


def _groups(rows):
    """Connected source families: A, A+B and B cannot count as three votes."""
    groups = []
    for row in rows:
        dependencies = set(row['working_evidence_family_ids'])
        merged, remaining = [row], []
        for group in groups:
            if dependencies.intersection(source for item in group for source in item['working_evidence_family_ids']):
                merged.extend(group)
                dependencies.update(source for item in group for source in item['working_evidence_family_ids'])
            else:
                remaining.append(group)
        # A new bridge can connect groups skipped before its dependencies grew.
        while True:
            linked = [group for group in remaining if dependencies.intersection(
                source for item in group for source in item['working_evidence_family_ids'])]
            if not linked:
                break
            for group in linked:
                remaining.remove(group)
                merged.extend(group)
                dependencies.update(source for item in group for source in item['working_evidence_family_ids'])
        groups = remaining + [merged]
    return groups


def working_family_preferences(families, llm, excluded_family=None):
    """Return eight admission rows, blended scores and exact additive shares."""
    llm = llm if isinstance(llm, dict) else {}
    families = families if isinstance(families, dict) else {}
    global_scores = {key: float(value) for key, value in (llm.get('policy_scores') or {}).items()
                     if _number(value) is not None and -1 <= value <= 1}
    try:
        opinions = parse_family_assessments(llm.get('family_assessments'))
    except RuntimeError:
        opinions = {}
    cutoff = _number(llm.get('captured_ts'))
    rows, accepted = {}, []
    for family in FAMILIES:
        row = deepcopy(families.get(family) or {'family_id': family, 'available': False, 'forecast_available': False})
        row.update(working_assessment_available=False,
                   working_assessment_kind='CURRENT_LLM_SOURCE_INTERPRETATION',
                   working_assessment_reason='NO_WORKING_FAMILY_ASSESSMENT',
                   working_policy_scores={}, working_feature_names=[],
                   working_source_ids=[], working_evidence_family_ids=[],
                   working_historical_profit_proven=False)
        rows[family] = row
        opinion = opinions.get(family)
        if not opinion:
            continue
        if llm.get('status') != 'ok':
            row['working_assessment_reason'] = 'CURRENT_LLM_UNAVAILABLE_OR_BLOCKED'
            continue
        if family == excluded_family:
            row['working_assessment_reason'] = 'WORKING_FAMILY_EXCLUDED_FOR_ABLATION'
            continue
        features = opinion['feature_names']
        observed = row.get('features') or {}
        provenance = row.get('feature_provenance') or {}
        if (row.get('available') is not True or any(
                _number(observed.get(name)) is None or not isinstance(provenance.get(name), dict)
                for name in features)):
            row['working_assessment_reason'] = 'CITED_OBSERVED_FEATURE_UNAVAILABLE'
            continue
        metas = [provenance[name] for name in features]
        clocks_valid = cutoff is not None and cutoff > 0
        for meta in metas:
            observed_ts, received_ts, age = (_number(meta.get(key))
                for key in ('observed_ts', 'received_ts', 'max_age_sec'))
            clocks_valid = clocks_valid and all(value is not None for value in (observed_ts, received_ts, age))
            if clocks_valid:
                clocks_valid = (0 < observed_ts <= received_ts <= cutoff
                    and 0 < age <= MAX_AGE_SEC[family] and cutoff - observed_ts <= age
                    and meta.get('source_verified') is True and bool(meta.get('source_id'))
                    and bool(meta.get('dependency_group')))
        if not clocks_valid:
            row['working_assessment_reason'] = 'WORKING_SOURCE_CLOCK_OR_PROVENANCE_INVALID'
            continue
        scores = opinion['policy_scores']
        if ((len(scores) > 1 and max(scores.values()) == min(scores.values()))
                or (len(scores) == 1 and next(iter(scores.values())) == 0)):
            row['working_assessment_reason'] = 'NO_RELATIVE_FAMILY_PREFERENCE'
            continue
        row.update(working_assessment_available=True,
                   working_assessment_reason='SOURCE_BOUND_MANUAL_INTERPRETATION',
                   working_policy_scores=dict(scores), working_feature_names=features,
                   working_reason_ru=opinion['reason_ru'],
                   working_source_ids=sorted({source for meta in metas for source in
                       (meta['source_id'], *meta.get('supporting_source_ids', [])) if source}),
                   working_evidence_family_ids=sorted({meta['dependency_group'] for meta in metas}),
                   working_observed_ts=min(meta['observed_ts'] for meta in metas),
                   working_max_age_sec=min(meta['observed_ts'] + meta['max_age_sec'] for meta in metas)
                       - min(meta['observed_ts'] for meta in metas))
        accepted.append(row)
    # A flat global vector has no relative opinion, regardless of its level.
    # When families supply the opinion, absent actions must remain absent.
    if accepted and len(global_scores) > 1 and max(global_scores.values()) == min(global_scores.values()):
        global_scores = {}
    blended, attributions, weights = dict(global_scores), {}, {}
    for policy in sorted({key for row in accepted for key in row['working_policy_scores']}):
        contributors = [row for row in accepted if policy in row['working_policy_scores']]
        groups = _groups(contributors)
        family_share = .5 if policy in global_scores else 1.
        factors = {row['family_id']: family_share / len(groups) / len(group)
                   for group in groups for row in group}
        values = {row['family_id']: factors[row['family_id']] * row['working_policy_scores'][policy]
                  for row in contributors}
        blended[policy] = (1. - family_share) * global_scores.get(policy, 0.) + sum(values.values())
        attributions[policy], weights[policy] = values, factors
    return {'scores': blended, 'families': rows, 'attributions': attributions,
            'attribution_weights': weights, 'accepted_count': len(accepted),
            'source_ids': sorted({source for row in accepted for source in row['working_source_ids']}),
            'evidence_family_ids': sorted({source for row in accepted for source in row['working_evidence_family_ids']})}
