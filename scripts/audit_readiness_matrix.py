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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('edge_family_sources_latest.json'))
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--code-sha', help='Exact code SHA used by the invoking workflow')
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
        for limitation in report['limitations']:
            print(limitation)


if __name__ == '__main__':
    main()
