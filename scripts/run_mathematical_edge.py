#!/usr/bin/env python3
"""Bounded off-host search on actual bars, with a complete instrument matrix."""
import argparse
import json
import time
from pathlib import Path
from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.mathematical_edge import (
    CONTRACT, SEARCH_HORIZONS, TARGET_CONTRACT, fingerprint, number, train_instrument,
)


def _fresh_sources(captured_ts):
    """Fetch independently; one unavailable provider series does not stop others."""
    from seiltanzer.mathematical_edge_sources import collect_fresh_sources
    sources, errors, _ = collect_fresh_sources(captured_ts)
    return sources, errors


def _validated_bars(raw, captured_ts):
    """Finite OHLC and one observation per completed timestamp; never fill gaps."""
    if not isinstance(raw, list) or len(raw) > 20000:
        raise ValueError('source bars must be a list of at most 20000 observations')
    unique = {}
    exclusions = {'future_or_unfinished': 0, 'invalid_ohlc': 0, 'duplicate_timestamp': 0}
    for row in raw:
        if not isinstance(row, dict):
            exclusions['invalid_ohlc'] += 1
            continue
        end = number(row.get('bar_end_ts'))
        values = [number(row.get(key)) for key in ('open', 'high', 'low', 'close')]
        if end is None or any(value is None or value <= 0 for value in values):
            exclusions['invalid_ohlc'] += 1
            continue
        open_, high, low, close = values
        if low > min(open_, close) or high < max(open_, close) or high < low:
            exclusions['invalid_ohlc'] += 1
            continue
        if end > captured_ts:
            exclusions['future_or_unfinished'] += 1
            continue
        clean = {'bar_end_ts': end, 'open': open_, 'high': high, 'low': low, 'close': close}
        if end in unique:
            if unique[end] != clean:
                raise ValueError('conflicting duplicate source bar')
            exclusions['duplicate_timestamp'] += 1
        unique[end] = clean
    return [unique[end] for end in sorted(unique)], exclusions


def _instrument_matrix(rows, sources, captured_ts):
    metadata = {source['instrument']: source for source in sources}
    matrix = {}
    for code, instrument in ALL_INSTRUMENTS.items():
        row = rows[code]
        source = metadata.get(code, {})
        cutoff = number(row.get('training_cutoff'))
        age = (captured_ts - cutoff) / 86400 if cutoff is not None else None
        supported = [name for name, diag in row.get('diagnostics', {}).items()
                     if diag.get('working_supported')]
        path_heads = row.get('path_heads') or {}
        admission = None
        if instrument.asset_class == 'crypto':
            from seiltanzer.mathematical_edge import crypto_training_source_admission
            admission = crypto_training_source_admission({'sources': sources}, row, code)
        matrix[code] = {
            'configured_price_source': instrument.price_label,
            'historical_ticker': source.get('ticker') or source.get('cached_ticker') or instrument.yahoo,
            'source_available': bool(source), 'status': row['status'],
            'reason': row.get('reason'), 'search_completed': bool(row.get('search_completed')),
            'requested_horizons_minutes': row.get('requested_horizons_minutes', []),
            'searched_horizons_minutes': [int(h) for h, value in row.get('candidate_audit', {}).items()
                                        if value.get('status') == 'VALIDATED_FOR_SELECTION'],
            'selected_horizon_minutes': row.get('horizon_minutes'), 'supported_heads': supported,
            'path_targets': {name: {'status': value['status'], 'working_supported': value['working_supported'],
                                   'horizon_minutes': value.get('horizon_minutes'),
                                   'gain_mbit': value.get('gain_mbit'), 'test_n': value.get('test_n'),
                                   'search_completed': value['search_completed']}
                             for name, value in path_heads.items()},
            'management_role': ('DIRECTION_SOFT_RANKING' if 'direction' in supported else
                                'TIME_STOP_REDUCE_TAKE_ONLY' if 'movement' in supported else
                                'GENERIC_PATH_MANAGEMENT' if any(value['working_supported'] for value in path_heads.values()) else 'NO_WEIGHT'),
            'training_price_source_admission': admission,
            'training_age_days': round(age, 2) if age is not None else None,
            'training_age_multiplier': round(max(0., 1-age/90), 6) if age is not None else 0.,
            'training_too_old_for_runtime': age is not None and age >= 90,
            'not_broker_execution_bars': source.get('not_broker_execution_bars', True),
            'net_economic_proof': False,
            'historical_provider': source.get('provider') or source.get('cached_provider'),
            'source_coverage': source.get('coverage'),
            'collection_fallback': source.get('collection_fallback'),
            'fresh_collection_errors': source.get('fresh_collection_errors', []),
            'source_semantics': source.get('source_semantics') or source.get('cached_semantics'),
        }
        if admission is not None and not admission['available'] and (supported or any(
                value.get('working_supported') for value in path_heads.values())):
            matrix[code]['management_role'] = 'DIAGNOSTIC_ONLY_PRICE_SERIES_MAPPING_UNVALIDATED'
    return matrix


def build_report(sources, errors, captured_ts, horizons=SEARCH_HORIZONS, *, family_history=None,
                 macro_archive=None,previous_family_reports=None,related_sources=None):
    rows, audited_sources = {}, []
    errors = dict(errors)
    for source in sources:
        code = source.get('instrument')
        if code not in ALL_INSTRUMENTS:
            errors[str(code)] = 'INSTRUMENT_NOT_CONFIGURED'
            continue
        if code in rows:
            raise ValueError(f'duplicate source instrument: {code}')
        try:
            bars, exclusions = _validated_bars(source.get('bars'), captured_ts)
            rows[code] = train_instrument(code, bars, captured_ts, horizons=horizons)
            audited_sources.append({k: value for k, value in source.items() if k not in {'bars','trade_flow_history'}} | {
                'bar_count': len(bars), 'validated_bars_sha256': fingerprint(bars),
                'bar_exclusions': exclusions,
                'first_bar_end_ts': bars[0]['bar_end_ts'] if bars else None,
                'last_bar_end_ts': bars[-1]['bar_end_ts'] if bars else None,
            })
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            errors[code] = f'INVALID_SOURCE_OR_TRAINING_ERROR: {type(exc).__name__}: {str(exc)[:300]}'
    for code in ALL_INSTRUMENTS:
        rows.setdefault(code, {
            'instrument': code, 'status': 'UNRESOLVED',
            'reason': errors.get(code, 'SOURCE_BARS_UNAVAILABLE'), 'weight_fraction': 0.,
            'search_completed': False, 'requested_horizons_minutes': list(horizons),
            'candidate_audit': {}, 'net_economic_proof': False,
        })
    from seiltanzer.mathematical_edge_archive import replay_frozen_fx_rule
    from seiltanzer.edge_family_market import train_market_families
    family_models = train_market_families(sources, captured_ts, horizons=horizons,
        feature_history=family_history,macro_archive=macro_archive,related_sources=related_sources)
    from seiltanzer.edge_family_model_retention import retain_fitted_families
    family_models,model_continuity=retain_fitted_families(family_models,previous_family_reports or [],captured_ts)
    return {
        'contract_version': CONTRACT, 'created_ts': captured_ts, 'instruments': rows,
        'production_authority': False, 'automatic_execution': False,
        'family_market_models': family_models,
        'family_model_continuity':model_continuity,
        'historical_macro_source': ({'available':True,**macro_archive.summary()} if macro_archive is not None
            else {'available':False,'reason':'OFFICIAL_MACRO_ARCHIVE_NOT_REQUESTED'}),
        'family_market_model_summary': {
            'current_run_fitted_models':model_continuity['current_run_fitted'],
            'retained_fitted_models':model_continuity['retained_fitted'],
            'trained_models': sum(row.get('status') == 'TRAINED' for rows in family_models.values() for row in rows.values()),
            'working_supported_models': sum(any(head.get('working_supported') for head in row.get('heads', {}).values())
                for rows in family_models.values() for row in rows.values()),
            'market_forecast_not_net_action_profit': True,
        },
        'user_policy': 'PERSONAL_WORKING_EVIDENCE_BOUNDED_SOFT_RANKING', 'source_errors': errors,
        'configured_instruments': list(ALL_INSTRUMENTS), 'requested_horizons_minutes': list(horizons),
        'target_contract': dict(TARGET_CONTRACT),
        'target_coverage': {
            'conditional_direction': 'SEARCHED_WHERE_SOURCE_AVAILABLE',
            'movement_above_2bp': 'SEARCHED_WHERE_SOURCE_AVAILABLE',
            'barrier_first_passage': 'GENERIC_SYMMETRIC_2BP_UPPER_FIRST_SAME_BAR_TIES_EXCLUDED',
            'adverse_excursion': 'GENERIC_2BP_DOWNSIDE_AND_UPSIDE_PATH_TOUCH_PROBABILITIES',
            'time_to_barrier': 'GENERIC_2BP_FIRST_TOUCH_BY_HALF_HORIZON_5M_COMPLETED_BAR_RESOLUTION',
        },
        'validation_semantics': 'PROPER_SCORE_OUT_OF_SAMPLE_NOT_NET_PNL',
        'net_economic_proof': False, 'prospective_profit_proof': False,
        'instrument_matrix': _instrument_matrix(rows, audited_sources, captured_ts),
        'archived_fx_reproduction': replay_frozen_fx_rule(
            Path(__file__).resolve().parents[1] / 'research_fixtures/mathematical_edge'),
        'sources': audited_sources,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sources')
    parser.add_argument('--family-history', help='optional actual captured PIT family feature history; no invented broker cost requirement')
    parser.add_argument('--historical-macro-bundle', help='existing official public-vintage bundle, validated offline')
    parser.add_argument('--macro-source-sha', help='original bundle source SHA; distinct from current model publication SHA')
    parser.add_argument('--sources-output', help='save fetched real bars for exact offline reproduction')
    parser.add_argument('--refresh-sources', action='store_true', help='refresh public bars off-host; --sources supplies read-only fallback')
    parser.add_argument('--model-cache-dir',help='Bounded local frozen family-model cache')
    parser.add_argument('--cache-dir', help='per-provider hash-verified off-host source cache')
    parser.add_argument('--source-days', type=int, default=60)
    parser.add_argument('--source-budget-seconds', type=float, default=360.)
    parser.add_argument('--output', required=True)
    parser.add_argument('--captured-ts', type=float, help='freeze causal observation cutoff for reproduction')
    parser.add_argument('--horizons', default=','.join(map(str, SEARCH_HORIZONS)))
    args = parser.parse_args()
    captured = args.captured_ts if args.captured_ts is not None else time.time()
    horizons = tuple(dict.fromkeys(int(item) for item in args.horizons.split(',')))
    if not horizons or any(h not in SEARCH_HORIZONS for h in horizons):
        parser.error('--horizons must be a subset of 15,30,60,120')
    sources, errors, attempts = [], {}, {}
    diagnostic_sources = [];previous_family_reports=[]
    if args.sources:
        data = json.loads(Path(args.sources).read_text())
        sources, errors = data['sources'], data.get('errors', {})
        diagnostic_sources = data.get('diagnostic_sources', [])
        if isinstance(data.get('previous_family_model_report'),dict):
            previous_family_reports.append(data['previous_family_model_report'])
    if args.refresh_sources or not args.sources:
        from seiltanzer.mathematical_edge_sources import collect_fresh_sources
        previous_related = diagnostic_sources
        diagnostic_sources = []
        sources, errors, attempts = collect_fresh_sources(
            captured, seed_sources=sources, cache_dir=args.cache_dir, days=args.source_days,
            budget_seconds=args.source_budget_seconds, diagnostic_sources=diagnostic_sources,
        )
        present = {item.get('instrument') for item in diagnostic_sources}
        diagnostic_sources += [item for item in previous_related if item.get('instrument') not in present
            and item.get('provider') == 'Coinbase Exchange'][:3-len(diagnostic_sources)]
    if args.sources_output:
        path = Path(args.sources_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'sources': sources, 'errors': errors, 'exported_ts': captured,
                                   'diagnostic_sources': diagnostic_sources,
                                   'collection_attempts': attempts}, ensure_ascii=False, allow_nan=False))
    from seiltanzer.edge_family_model_retention import read_fitted_cache,write_fitted_cache
    if args.model_cache_dir:
        previous=read_fitted_cache(Path(args.model_cache_dir)/'latest.json')
        if previous is not None:previous_family_reports.append(previous)
    family_history = None
    if args.family_history:
        history_path = Path(args.family_history)
        if history_path.stat().st_size > 96_000_000:
            parser.error('--family-history exceeds bounded input size')
        family_history = json.loads(history_path.read_text())
        if not isinstance(family_history, dict):
            parser.error('--family-history must be an instrument/family matrix')
    macro_archive=None;macro_error=None
    if args.historical_macro_bundle:
        try:
            from seiltanzer.edge_family_macro_archive import verified_macro_archive
            macro_path=Path(args.historical_macro_bundle)
            if macro_path.stat().st_size>96_000_000:
                raise ValueError('OFFICIAL_MACRO_ARCHIVE_INPUT_OVER_BOUND')
            data=json.loads(macro_path.read_text())
            if (isinstance(data,dict) and data.get('available') is False and data.get('read_only') is True
                    and data.get('archive_export_error') in ('OFFICIAL_MACRO_ARCHIVE_NOT_FOUND',
                        'OFFICIAL_MACRO_ARCHIVE_READ_FAILED','OFFICIAL_MACRO_ARCHIVE_INPUT_OVER_BOUND')):
                raise ValueError(data['archive_export_error'])
            if not args.macro_source_sha:
                raise ValueError('OFFICIAL_MACRO_ARCHIVE_SOURCE_SHA_MISSING')
            macro_archive=verified_macro_archive(data,
                expected_sha=args.macro_source_sha,captured_ts=captured)
        except (ValueError,TypeError,OSError) as exc:
            macro_error=str(exc)[:180]
    report = build_report(sources, errors, captured, horizons, family_history=family_history,
                         macro_archive=macro_archive,previous_family_reports=previous_family_reports,
                         related_sources=diagnostic_sources)
    if macro_error is not None:
        report['historical_macro_source']={'available':False,'reason':macro_error}
    report['collection_attempts'] = attempts
    report['diagnostic_source_inventory'] = [{k: v for k, v in source.items() if k != 'bars'} | {
        'bar_count': len(source.get('bars', [])), 'runtime_admission': False,
    } for source in diagnostic_sources]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
    if args.model_cache_dir:
        try:write_fitted_cache(report,Path(args.model_cache_dir)/'latest.json')
        except (OSError,ValueError,TypeError):
            print('FAMILY_MODEL_CACHE_WRITE_UNAVAILABLE; fresh report preserved')
    print('FAMILY_MODEL_CONTINUITY',json.dumps(report['family_model_continuity'],sort_keys=True))
    print('FAMILY_MARKET_MODELS', json.dumps(report['family_market_model_summary'], sort_keys=True))
    for code, row in report['instruments'].items():
        print(code, row['status'], row.get('horizon_minutes'),
              {k: round(v['gain_mbit'], 3) if v.get('gain_mbit') is not None else None for k, v in row.get('diagnostics', {}).items()})

if __name__ == '__main__':
    main()
