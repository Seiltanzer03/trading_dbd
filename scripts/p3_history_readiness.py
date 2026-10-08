"""Safe P3 coverage projection of an already validated private pipeline result.

No fetching, replay, fitting or activation. Counts describe retained history,
not current source freshness, broker fills, or proven trading benefit.
"""
from collections import defaultdict

from seiltanzer.config import ALL_INSTRUMENTS

FAMILIES = ('intermarket', 'session')


def _count(value, maximum):
    return value if type(value) is int and 0 <= value <= maximum else 0


def build_p3_readiness(archive, dataset, diagnostics):
    from seiltanzer.edge_family_adapters import build_edge_family_evidence
    from seiltanzer.edge_family_dataset import (_frozen_snapshot, _feature_snapshot,
                                               _verified_costs, family_geometry_sha256)
    from seiltanzer.edge_family_history import feature_applicability_reason

    counts = defaultdict(lambda: defaultdict(set))
    # The caller has assembled/revalidated this bounded archive and dataset.
    # No snapshot/source IDs, feature values, clocks or free-form reasons escape.
    for record in archive['episodes']:
        code = record['instrument']
        if code not in ALL_INSTRUMENTS:
            continue
        identity = record['review_id']
        for family in FAMILIES:
            counts[code, family]['retained'].add(identity)
        try:
            snapshot, instrument, cutoff, horizon = _frozen_snapshot(record)
            if instrument != code:
                continue
            family_geometry_sha256(snapshot)
            for family in FAMILIES:
                counts[code, family]['usable'].add(identity)
            evidence = build_edge_family_evidence(_feature_snapshot(snapshot, horizon, identity, []))
            try:
                _verified_costs(snapshot, horizon)
                costs = True
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                costs = False
            for family in FAMILIES:
                row = evidence['families'][family]
                if any(not meta.get('context_only') and meta.get('received_ts') is not None
                       and feature_applicability_reason(meta, horizon) is None
                       for meta in row['feature_provenance'].values()):
                    counts[code, family]['features'].add(identity)
                if costs:
                    counts[code, family]['costs'].add(identity)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
            continue
    for row in dataset['rows']:
        key = row.get('instrument'), row.get('family_id')
        if key[0] in ALL_INSTRUMENTS and key[1] in FAMILIES:
            identity = row.get('review_id')
            if identity in counts[key]['retained']:
                counts[key]['labels'].add(identity)
    cohorts = diagnostics.get('training', {}).get('diagnostics', {}).get('cohorts', [])
    matrix = diagnostics.get('model_matrix', {})
    cells = []
    for code in ALL_INSTRUMENTS:
        for family in FAMILIES:
            selected = [row for row in cohorts if row.get('instrument') == code and row.get('family_id') == family]
            retained, usable, features, costs, labels = (len(counts[code, family][name])
                                               for name in ('retained', 'usable', 'features', 'costs', 'labels'))
            models = _count(matrix.get(code, {}).get(family, 0), 128)
            status = ('HISTORY_MISSING' if not retained else
                      'FROZEN_HISTORY_UNUSABLE' if not usable else
                      'SYNCHRONIZED_RETURNS_OR_MAPPING_REQUIRED' if not features and family == 'intermarket' else
                      'CALENDAR_OR_MAPPING_REQUIRED' if not features else
                      'COST_OR_POSITION_EVIDENCE_REQUIRED' if not costs else
                      'NET_ACTION_LABELS_UNAVAILABLE' if not labels else
                      'MODEL_PACKAGED_NOT_ACTIVE' if models else
                      'NO_TRAINING_COHORT' if not selected else
                      'INSUFFICIENT_INDEPENDENT_GROUPS' if all(row.get('reason') in
                          ('INSUFFICIENT_INDEPENDENT_GROUPS', 'INSUFFICIENT_TRAIN_GROUPS_AFTER_PURGE')
                          for row in selected) else 'OOS_NOT_VALIDATED')
            cells.append(dict(instrument=code, family=family, retained_review_count=retained,
                usable_frozen_review_count=usable,
                causal_feature_review_count=features, complete_cost_review_count=costs,
                net_label_review_count=labels, training_cohort_count=len(selected),
                max_cohort_independent_group_count=max((_count(row.get('group_count'), 512)
                                                       for row in selected), default=0),
                packaged_model_count=models, status=status))
    return dict(contract_version='p3-historical-readiness-v1', production_authority=False,
                basis='RETAINED_FROZEN_HISTORY_NOT_CURRENT_LIVE_READINESS',
                group_count_basis='MAX_SINGLE_COHORT_NOT_ADDITIVE', cells=cells)
