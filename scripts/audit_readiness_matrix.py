#!/usr/bin/env python3
"""Bounded, read-only audit of configured families at a frozen capture cutoff."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.edge_family_adapters import FAMILIES

MAX_BYTES = 1_000_000
MAX_SIDECAR_BYTES = 8_000_000


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value[:128] if isinstance(value, list) else []


def build_report(bundle, *, input_sha256):
    captured = number(bundle.get('captured_ts'))
    instruments = mapping(bundle.get('instruments'))
    cells = []
    for code in ALL_INSTRUMENTS:
        data = mapping(instruments.get(code))
        readiness = mapping(data.get('readiness'))
        for family in FAMILIES:
            row = mapping(readiness.get(family))
            reason = ('INSTRUMENT_CAPTURE_MISSING' if not data else
                      'FAMILY_READINESS_MISSING' if not row else row.get('reason', 'REASON_UNAVAILABLE'))
            rejections = [r.get('reason', 'REASON_UNAVAILABLE') for r in rows(row.get('rejected_sources'))
                          if isinstance(r, dict)]
            model_rejections = [r.get('reason', 'REASON_UNAVAILABLE') for r in rows(row.get('forecast_rejections'))
                                if isinstance(r, dict)]
            observed, max_age = number(row.get('observed_ts')), number(row.get('max_age_sec'))
            fresh = 'UNKNOWN'
            if captured is not None and observed is not None and max_age is not None:
                fresh = ('FUTURE_AT_CAPTURE' if observed > captured else
                         'STALE_AT_CAPTURE' if captured - observed > max_age else 'FRESH_AT_CAPTURE')
            available = row.get('available') is True and fresh not in ('FUTURE_AT_CAPTURE', 'STALE_AT_CAPTURE')
            forecast = available and row.get('forecast_available') is True and row.get('readiness') == 'VALIDATED_FORECAST_AVAILABLE'
            proofs = mapping(row.get('feature_provenance')).values()
            hashes = sorted({p['body_sha256'] for p in proofs if isinstance(p, dict)
                             and isinstance(p.get('body_sha256'), str) and len(p['body_sha256']) == 64})
            cells.append(dict(instrument=code, family=family,
                producer_status=row.get('implementation_status', 'NOT_REPORTED'),
                input_available=available, freshness=fresh, freshness_basis='FROZEN_CAPTURE_ONLY',
                target_mapping_status='REJECTED' if any('MAPPING' in str(r) or 'PROXY' in str(r) for r in rejections)
                    else 'ADMISSIBLE_INPUT' if available else 'UNKNOWN',
                independent_group_count=None, sample_status='NOT_REPORTED_IN_SOURCE_BUNDLE',
                model_status='VALIDATED_AT_CAPTURE' if forecast else 'REJECTED' if model_rejections else 'UNAVAILABLE',
                forecast_available=forecast, active_vote_status='NOT_REPORTED_IN_SOURCE_BUNDLE',
                observed_ts=observed, source_hashes=hashes, reason=reason,
                readiness=row.get('readiness', 'UNAVAILABLE'),
                missing_fields=rows(row.get('needs_data')), source_rejections=rejections,
                model_rejections=model_rejections,
                next_step='SOURCE_OR_MAPPING_REQUIRED' if not available else 'VALIDATED_MODEL_AND_INDEPENDENT_SAMPLES_REQUIRED'
                    if not forecast else 'CHECK_RUNTIME_SELECTOR_AND_APPLIED_WEIGHT'))
    return dict(contract_version='edge-readiness-audit-v1', input_sha256=input_sha256,
                published_for_sha=bundle.get('published_for_sha'),
                captured_ts=captured, production_authority=False, cells=cells,
                limitations=['Source bundle does not contain independent training group counts or applied runtime votes.',
                             'Freshness describes the saved capture, not current live readiness.'])


def enrich_report(report, *, expected_sha, training=None, reviews=None):
    """Describe saved evidence; never fit, replay scenarios, or grant authority."""
    cells = {(c['instrument'], c['family']): c for c in report['cells']}
    rejected = report['sidecar_rejections'] = []
    cutoff = number(report['captured_ts'])
    if (not expected_sha or cutoff is None or cutoff <= 0
            or report.get('published_for_sha') not in (None, expected_sha)):
        if training is not None or reviews is not None:
            rejected.append({'input': 'sidecars', 'reason': 'SOURCE_CODE_SHA_OR_CAPTURE_UNAVAILABLE'})
        return
    if training is not None:
        stamp = number(training.get('captured_ts'))
        cohorts = mapping(mapping(training.get('training')).get('diagnostics')).get('cohorts', [])
        if (training.get('version') != 'edge-family-pipeline-v1'
                or training.get('deployment_sha') != expected_sha
                or stamp is None or not 0 < stamp <= cutoff
                or not isinstance(cohorts, list) or len(cohorts) > 1024):
            rejected.append({'input': 'training', 'reason': 'TRAINING_SHA_CLOCK_OR_SCHEMA_MISMATCH'})
        else:
            for cell in cells.values():
                selected = []
                for row in cohorts:
                    if not isinstance(row, dict) or (row.get('instrument'), row.get('family_id')) != (cell['instrument'], cell['family']):
                        continue
                    count = row.get('group_count')
                    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        rejected.append({'input': 'training', 'reason': 'INVALID_COHORT_GROUP_COUNT'})
                        continue
                    selected.append({key: row[key] for key in ('horizon_minutes', 'action', 'geometry_sha256',
                        'row_count', 'group_count', 'available', 'reason', 'folds') if key in row})
                packaged = mapping(mapping(training.get('model_matrix')).get(cell['instrument'])).get(cell['family'], 0)
                if isinstance(packaged, bool) or not isinstance(packaged, int) or not 0 <= packaged <= 128:
                    packaged = None
                cell.update(training_captured_ts=stamp, training_cohorts=selected,
                    packaged_model_count=packaged,
                    training_model_status='PACKAGED_NOT_RUNTIME_VERIFIED' if packaged else 'NO_PACKAGED_MODEL',
                    independent_group_count=selected[0]['group_count'] if len(selected) == 1 else None,
                    sample_status='REPORTED_PER_COHORT_NOT_ADDITIVE' if selected else 'NO_REPORTED_COHORT')
    if reviews is not None:
        records = reviews.get('reviews')
        if (reviews.get('read_only') is not True or reviews.get('synthetic') is True
                or not isinstance(records, list) or len(records) > 32):
            rejected.append({'input': 'reviews', 'reason': 'REVIEW_EXPORT_SCHEMA_OR_BOUND'})
            return
        latest = {}
        for record in records:
            try:
                raw = record['snapshot_json']
                if not isinstance(raw, str) or len(raw.encode()) > 2_000_000:
                    raise ValueError('FROZEN_REVIEW_BYTE_BOUND')
                if hashlib.sha256(raw.encode()).hexdigest() != record.get('snapshot_sha256'):
                    raise ValueError('FROZEN_REVIEW_HASH_MISMATCH')
                snapshot = json.loads(raw)
                stamp = number(snapshot.get('captured_ts'))
                if (stamp is None or not 0 < stamp <= cutoff or stamp != number(record.get('captured_ts'))
                        or snapshot.get('trade_id') != record.get('trade_id')
                        or not isinstance(record.get('review_id'), str) or not record['review_id']
                        or snapshot.get('runtime_code_sha') != expected_sha
                        or any(snapshot.get(key) is True for key in ('demo', 'synthetic', 'is_demo'))):
                    raise ValueError('FROZEN_REVIEW_SHA_CLOCK_OR_IDENTITY_MISMATCH')
                from seiltanzer.decision_research import validate_no_future_timestamps
                validate_no_future_timestamps(snapshot, stamp)
                code = mapping(snapshot.get('strategy')).get('instrument', snapshot.get('instrument'))
                if code not in ALL_INSTRUMENTS:
                    raise ValueError('FROZEN_REVIEW_INSTRUMENT_UNAVAILABLE')
                key = (stamp, record['review_id'])
                if code not in latest or key > latest[code][0]:
                    latest[code] = (key, record, snapshot)
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                rejected.append({'input': 'reviews', 'reason': str(exc)[:160]})
        for code, (_, record, snapshot) in latest.items():
            ensemble = mapping(mapping(snapshot.get('policy_manager')).get('unified_edge_ensemble'))
            families = ensemble.get('edge_families', {})
            if isinstance(families, list):
                families = {row.get('family_id'): row for row in families if isinstance(row, dict)}
            components = {row.get('component_id'): row for row in rows(ensemble.get('components')) if isinstance(row, dict)}
            selected = [row for row in rows(ensemble.get('candidates')) if isinstance(row, dict)
                        and row.get('candidate_id') == ensemble.get('selected_candidate_id')]
            contributions = {row.get('component_id'): row for row in rows(selected[0].get('component_contributions'))
                             if isinstance(row, dict)} if len(selected) == 1 else {}
            for family in FAMILIES:
                cell, row = cells[(code, family)], mapping(mapping(families).get(family))
                if not row:
                    continue
                forecast = row.get('forecast_available') is True and row.get('readiness') == 'VALIDATED_FORECAST_AVAILABLE'
                pool = row.get('weight_pool')
                budget = number(mapping(components.get(pool)).get('effective_weight'))
                weight = number(mapping(contributions.get(pool)).get('effective_weight'))
                applied = ensemble.get('applied') is True
                if weight is not None and not 0 <= weight <= 1:
                    weight = None
                status = ('RANKING_NOT_APPLIED' if not applied else 'NO_VALIDATED_FAMILY_FORECAST' if not forecast
                    else 'POOL_WEIGHT_NOT_REPORTED' if weight is None else 'APPLIED_SHARED_POOL_OBSERVED' if weight > 0
                    else 'ADMITTED_POOL_HAS_ZERO_WEIGHT')
                cell.update(runtime_review_id=record['review_id'], runtime_snapshot_sha256=record['snapshot_sha256'],
                    runtime_captured_ts=record['captured_ts'], runtime_forecast_available=forecast,
                    runtime_input_available=row.get('available') is True,
                    runtime_weight_pool=pool, applied_pool_weight=weight if applied and forecast else None,
                    available_pool_budget=budget if budget is not None and 0 <= budget <= 1 else None,
                    standalone_family_weight=None, active_vote_status=status,
                    runtime_reason=row.get('reason'), runtime_missing_fields=rows(row.get('needs_data')))
    report['limitations'].append('Training cohort counts are not additive. Shared pool weight is not an exclusive family weight. Saved runtime evidence is historical, not current live readiness.')


def read_sidecar(path):
    with path.open('rb') as handle:
        raw = handle.read(MAX_SIDECAR_BYTES+1)
    if len(raw) > MAX_SIDECAR_BYTES:
        raise ValueError('SIDECAR_BYTE_BOUND_EXCEEDED')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('SIDECAR_NOT_OBJECT')
    return value, hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('edge_family_sources_latest.json'))
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--code-sha', help='Exact code SHA used by the invoking workflow')
    parser.add_argument('--training-report', type=Path, help='Saved pipeline diagnostics.json; never trains')
    parser.add_argument('--reviews', type=Path, help='Saved bounded read-only frozen review export')
    args = parser.parse_args()
    try:
        with args.input.open('rb') as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('INPUT_BYTE_BOUND_EXCEEDED')
        bundle = json.loads(raw)
        if not isinstance(bundle, dict):
            raise ValueError('INPUT_NOT_OBJECT')
        report = build_report(bundle, input_sha256=hashlib.sha256(raw).hexdigest())
        if args.code_sha and (len(args.code_sha) != 40 or any(c not in '0123456789abcdef' for c in args.code_sha)):
            raise ValueError('CODE_SHA_INVALID')
        report['code_sha'] = args.code_sha
        training, training_hash = read_sidecar(args.training_report) if args.training_report else (None, None)
        reviews, reviews_hash = read_sidecar(args.reviews) if args.reviews else (None, None)
        enrich_report(report, expected_sha=args.code_sha, training=training, reviews=reviews)
        report['evidence_input_sha256'] = {'training': training_hash, 'reviews': reviews_hash}
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        parser.error(type(exc).__name__ + ': ' + str(exc)[:160])
    if args.json:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
    else:
        print('Readiness at saved capture:', report['captured_ts'], 'input SHA256:', report['input_sha256'])
        print('Instrument | Family | Input | Freshness | Model | Forecast | Reason')
        for cell in report['cells']:
            print(' | '.join(str(cell[k]) for k in ('instrument', 'family', 'input_available',
                'freshness', 'model_status', 'forecast_available', 'reason')))
            if 'training_captured_ts' in cell or 'runtime_review_id' in cell:
                print('  saved evidence:', json.dumps({key: cell.get(key) for key in (
                    'independent_group_count', 'sample_status', 'packaged_model_count',
                    'training_model_status', 'runtime_review_id', 'runtime_captured_ts',
                    'runtime_forecast_available', 'active_vote_status', 'runtime_weight_pool',
                    'applied_pool_weight')}, sort_keys=True))
        for limitation in report['limitations']:
            print(limitation)


if __name__ == '__main__':
    main()
