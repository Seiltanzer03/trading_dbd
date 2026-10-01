#!/usr/bin/env python3
"""Bounded off-host search on actual bars, with a complete instrument matrix."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import time
from pathlib import Path
from seiltanzer.config import ALL_INSTRUMENTS
from seiltanzer.mathematical_edge import (
    CONTRACT, SEARCH_HORIZONS, TARGET_CONTRACT, fingerprint, number, train_instrument,
)


def _fresh_sources(captured_ts):
    """Fetch independently; one unavailable provider series does not stop others."""
    import yfinance as yf
    from seiltanzer.g1_short_horizon_historical_wf import _frame_to_bars

    def fetch(item):
        code, instrument = item
        try:
            frame = yf.Ticker(instrument.yahoo).history(
                period='60d', interval='5m', auto_adjust=False, actions=False, timeout=15,
            )
            bars = [b for b in _frame_to_bars(frame) if b['bar_end_ts'] <= captured_ts]
            if not bars:
                return code, None, 'SOURCE_BARS_UNAVAILABLE'
            if len(bars) > 20000:
                return code, None, 'SOURCE_EXCEEDS_20000_BAR_BOUND'
            return code, {
                'instrument': code, 'bars': bars, 'source_sha256': fingerprint(bars),
                'ticker': instrument.yahoo, 'provider': 'Yahoo Finance via yfinance',
                'interval': '5m', 'source_kind': 'REAL_PROVIDER_COMPLETED_5M',
                'not_broker_execution_bars': True,
                'source_semantics': {
                    'bar_timestamp_semantics': 'provider interval start +300s',
                    'completed_bars_only_for_features': True,
                    'exact_live_broker_series': False, 'synthetic_price_history': False,
                    'synthetic_option_history': False, 'option_history_used': False,
                },
            }, None
        except Exception as exc:
            return code, None, f'{type(exc).__name__}: {str(exc)[:300]}'

    sources, errors = [], {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for code, source, error in pool.map(fetch, ALL_INSTRUMENTS.items()):
            if source:
                sources.append(source)
            else:
                errors[code] = error
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
        matrix[code] = {
            'configured_price_source': instrument.price_label,
            'historical_ticker': source.get('cached_ticker') or source.get('ticker') or instrument.yahoo,
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
            'training_age_days': round(age, 2) if age is not None else None,
            'training_age_multiplier': round(max(0., 1-age/90), 6) if age is not None else 0.,
            'training_too_old_for_runtime': age is not None and age >= 90,
            'not_broker_execution_bars': source.get('not_broker_execution_bars', True),
            'net_economic_proof': False,
        }
    return matrix


def build_report(sources, errors, captured_ts, horizons=SEARCH_HORIZONS):
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
            audited_sources.append({k: value for k, value in source.items() if k != 'bars'} | {
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
    return {
        'contract_version': CONTRACT, 'created_ts': captured_ts, 'instruments': rows,
        'production_authority': False, 'automatic_execution': False,
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
    parser.add_argument('--sources-output', help='save fetched real bars for exact offline reproduction')
    parser.add_argument('--output', required=True)
    parser.add_argument('--captured-ts', type=float, help='freeze causal observation cutoff for reproduction')
    parser.add_argument('--horizons', default=','.join(map(str, SEARCH_HORIZONS)))
    args = parser.parse_args()
    captured = args.captured_ts if args.captured_ts is not None else time.time()
    horizons = tuple(dict.fromkeys(int(item) for item in args.horizons.split(',')))
    if not horizons or any(h not in SEARCH_HORIZONS for h in horizons):
        parser.error('--horizons must be a subset of 15,30,60,120')
    if args.sources:
        data = json.loads(Path(args.sources).read_text())
        sources, errors = data['sources'], data.get('errors', {})
    else:
        sources, errors = _fresh_sources(captured)
        if args.sources_output:
            path = Path(args.sources_output)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'sources': sources, 'errors': errors, 'exported_ts': captured},
                                       ensure_ascii=False, allow_nan=False))
    report = build_report(sources, errors, captured, horizons)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
    for code, row in report['instruments'].items():
        print(code, row['status'], row.get('horizon_minutes'),
              {k: round(v['gain_mbit'], 3) if v.get('gain_mbit') is not None else None for k, v in row.get('diagnostics', {}).items()})

if __name__ == '__main__':
    main()
