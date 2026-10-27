"""Historical family market forecasts; separate from net-action/fill models.

Fit off-host on real completed bars and captured family features. Runtime reads
frozen models only. Price response does not assert broker PnL or action utility.
"""
from __future__ import annotations

from datetime import datetime
from bisect import bisect_right
import math
from zoneinfo import ZoneInfo

import numpy as np

from .config import ALL_INSTRUMENTS, CRYPTO_INSTRUMENTS
from .edge_family_adapters import FAMILIES
from .edge_family_training import FEATURE_PREFIXES, _feature_reason
from .mathematical_edge import (fingerprint, number, fit_head, predict, gain,
                               working_admission, SEARCH_HORIZONS)
from .real_input_integrity import simulated_evidence
from .forecast_variants import VARIANTS, variant_predictions

CONTRACT = 'edge-family-market-response-v1'
FEATURE_CONTRACT = 'edge-family-market-features-v1'
TARGET = 'OBSERVED_MARKET_DIRECTION_AND_MOVEMENT'
ZONES = {'NAS100': 'America/New_York', 'SP500': 'America/New_York',
         'US30': 'America/New_York', 'GER40': 'Europe/Berlin',
         'UK100': 'Europe/London', 'JPY100': 'Asia/Tokyo'}
PRODUCTS = {'BTCUSD': 'BTC-USD', 'ETHUSD': 'ETH-USD', 'SOLUSD': 'SOL-USD'}
MAX_HISTORY = 20000


def session_features(code, cutoff):
    local = datetime.fromtimestamp(cutoff, ZoneInfo(ZONES.get(code, 'UTC')))
    phase = 2 * math.pi * (local.hour * 3600 + local.minute * 60 + local.second) / 86400
    week = 2 * math.pi * local.weekday() / 7
    return {'session.local_day_sin': math.sin(phase), 'session.local_day_cos': math.cos(phase),
            'session.weekday_sin': math.sin(week), 'session.weekday_cos': math.cos(week)}


def _bars(source, captured):
    from scripts.run_mathematical_edge import _validated_bars
    # Large sidecar arrays have their own per-observation validation. A valid
    # 60-day flow archive must not exhaust the small metadata traversal budget
    # and reject every independently validated target family.
    if simulated_evidence({k: v for k, v in source.items() if k not in {'bars','trade_flow_history','event_window_bars'}}):
        raise ValueError('SIMULATED_TRAINING_SOURCE_NOT_ADMISSIBLE')
    raw = source.get('bars')
    if not isinstance(raw, list) or not 0 < len(raw) <= MAX_HISTORY:
        raise ValueError('TARGET_COMPLETED_BARS_UNAVAILABLE')
    if any(simulated_evidence(row) for row in raw):
        raise ValueError('SIMULATED_TRAINING_SOURCE_NOT_ADMISSIBLE')
    if source.get('interval') != '5m' or source.get('source_sha256') != fingerprint(raw):
        raise ValueError('TRAINING_SOURCE_HASH_OR_INTERVAL_INVALID')
    bars, _ = _validated_bars(raw, captured)
    if any(abs(b['bar_end_ts'] % 300) > 1 for b in bars):
        raise ValueError('SOURCE_BAR_ALIGNMENT_INVALID')
    return bars


def related_series(sources, captured):
    result = {}
    for source in sources:
        code = source.get('instrument') if isinstance(source, dict) else None
        if (code not in PRODUCTS or source.get('provider') != 'Coinbase Exchange'
                or source.get('ticker') != PRODUCTS[code]):
            continue
        try:
            bars = _bars(source, captured)
            if PRODUCTS[code] in result:
                raise ValueError('DUPLICATE_RELATED_SERIES')
            result[PRODUCTS[code]] = {b['bar_end_ts']: b['close'] for b in bars}
        except (ValueError, TypeError, OverflowError):
            # Ambiguous/invalid leaders cannot survive by last-write-wins.
            result[PRODUCTS[code]] = {}
    return result


def intermarket_features(series, cutoff):
    if len(series) != 3:
        return None
    values = {}
    for product in PRODUCTS.values():
        rows = series.get(product, {})
        a, b = number(rows.get(cutoff - 600)), number(rows.get(cutoff - 300))
        if a is None or b is None or min(a, b) <= 0:
            return None
        values[f'intermarket.COINBASE{product}.return_5m_aligned_lag5m'] = math.log(b/a)
    return values


def _history_rows(code, family, history, captured):
    result = {}
    if not isinstance(history, list) or len(history) > MAX_HISTORY:
        return result
    ambiguous = set()
    for row in history:
        if not isinstance(row, dict) or simulated_evidence(row):
            continue
        cutoff = number(row.get('captured_ts'))
        values, proof = row.get('features'), row.get('feature_provenance')
        if (cutoff is None or not 0 < cutoff <= captured or not isinstance(values, dict)
                or not 1 <= len(values) <= 16 or not isinstance(proof, dict)):
            continue
        valid = all(isinstance(name, str) and name.startswith(FEATURE_PREFIXES[family])
            and number(value) is not None and _feature_reason(proof.get(name), code, family,
                cutoff, allow_global=family in {'macro', 'event'}) is None
            for name, value in values.items())
        if not valid:
            continue
        groups={str(proof[name]['dependency_group'])+':'+str(proof[name]['observed_ts']) for name in values}
        # Explicit dependencies only ADD purging; repeated receipts of one daily
        # measurement cannot manufacture independent train/validation facts.
        dependencies=[proof[name].get('purge_dependency_id') for name in values
            if proof[name].get('purge_dependency_id') is not None]
        if any(not isinstance(value,str) or not 1<=len(value)<=160 for value in dependencies):continue
        treasury_dependencies=[]
        for name in values:
            meta=proof[name]
            if str(meta.get('source_id','')).startswith('official-treasury-curve:'):
                from .edge_family_treasury_factors import treasury_purge_id
                identity=treasury_purge_id(meta,cutoff)
                if identity is None:break
                treasury_dependencies.append(identity)
        else:
            dependencies.extend(treasury_dependencies)
            groups.update('explicit-purge:'+value for value in dependencies)
            item = {'features': values, 'feature_provenance': proof, 'group': tuple(sorted(groups))}
            if cutoff in result and result[cutoff] != item:
                ambiguous.add(cutoff)
            result[cutoff] = item
            continue
        # A malformed known-provider clock cannot bypass the stable curve group.
        continue
    return {cutoff: row for cutoff, row in result.items() if cutoff not in ambiguous}


def market_dataset(code, family, bars, captured, horizon, *, related=None, history=None):
    bars = sorted([b for b in bars if b['bar_end_ts'] <= captured], key=lambda b: b['bar_end_ts'])
    xs, ys, times, ends, groups, names = [], [], [], [], [], None
    steps, last_end = horizon // 5, -1
    observations = _history_rows(code, family, history, captured) if history is not None else {}
    observation_times = sorted(observations)
    for i in range(len(bars) - steps):
        start, end = bars[i]['bar_end_ts'], bars[i + steps]['bar_end_ts']
        if start < last_end or end - start != horizon * 60:
            continue
        if any(bars[j+1]['bar_end_ts'] - bars[j]['bar_end_ts'] != 300 for j in range(i, i+steps)):
            continue
        if family == 'session':
            features, group = session_features(code, start), (str(start),)
        elif family == 'intermarket':
            features, group = intermarket_features(related or {}, start), (str(start),)
        else:
            index = bisect_right(observation_times, start) - 1
            row = observations[observation_times[index]] if index >= 0 else None
            if row and any(_feature_reason(row['feature_provenance'][name], code, family, start,
                    allow_global=family in {'macro', 'event'}) is not None for name in row['features']):
                row = None
            features, group = (row['features'], row['group']) if row else (None, ())
        if not features:
            continue
        keys = sorted(features)
        if names is None:
            names = keys
        if keys != names:
            continue  # No imputation and no per-row change of feature meaning.
        ret = math.log(bars[i+steps]['close'] / bars[i]['close'])
        xs.append([features[k] for k in names]); ys.append([float(ret > 0), float(abs(ret) > .0002), ret])
        times.append(start); ends.append(end); groups.append(group); last_end = end
    return {'x': np.asarray(xs), 'y': np.asarray(ys), 'times': np.asarray(times),
            'ends': np.asarray(ends), 'groups': groups, 'feature_names': names or []}


def _indices(data, indices, column):
    indices = np.asarray(indices, dtype=int)
    if column == 0:
        indices = indices[data['y'][indices, 1] == 1]
    return indices


def _purged(data, train, valid):
    forbidden = {group for i in valid for group in data['groups'][i]}
    return np.asarray([i for i in train if not forbidden.intersection(data['groups'][i])], dtype=int)


def _fit_family(code, family, bars, captured, horizons, related, history, source,
                *, datasets_override=None, boundaries_override=None, feature_variation_required=False,
                pretest_selection_only=False):
    datasets = ({h: market_dataset(code, family, bars, captured, h, related=related, history=history)
                for h in horizons} if datasets_override is None else datasets_override)
    anchor = datasets[min(horizons)]
    if len(anchor['times']) < 180:
        return {'status': 'INSUFFICIENT_HISTORY', 'reason': 'INSUFFICIENT_NONOVERLAPPING_FAMILY_OBSERVATIONS',
                'forecast': None, 'training_n': len(anchor['times'])}
    # One calendar grid for all heads/horizons, frozen before any scoring.
    times = anchor['times']
    boundaries = ([float(times[int(len(times)*f)]) for f in (.4, .55, .7, .8)]
                  if boundaries_override is None else boundaries_override)
    heads = {}
    for column, name in enumerate(('direction', 'movement')):
        candidates = []
        for horizon, data in datasets.items():
            if len(data['times']) < 180:
                continue
            gains = {variant: [] for variant in VARIANTS}
            for left, right in zip(boundaries[:-1], boundaries[1:]):
                train = _indices(data, np.flatnonzero(data['ends'] < left - 300), column)
                valid = _indices(data, np.flatnonzero((data['times'] >= left) &
                    (data['times'] < right) & (data['ends'] < boundaries[-1] - 300)), column)
                train = _purged(data, train, valid)
                if len(train) < 40 or len(valid) < 10 or len(set(data['y'][train, column])) < 2:
                    break
                head = fit_head(data['x'][train], data['y'][train, column])
                prediction = predict(head, data['x'][valid])
                for variant in VARIANTS:
                    gains[variant].append(gain(data['y'][valid, column],
                        variant_predictions(prediction, head['baseline'], variant), head['baseline']))
            for variant, values in gains.items():
                if len(values) == 3:
                    candidates.append((float(np.mean(values)), horizon, values, variant))
        if not candidates:
            heads[name] = {'working_supported': False, 'reason': 'HEAD_CLASSES_OR_VALIDATION_UNAVAILABLE', 'forecast': None}
            continue
        _, horizon, validation, variant = max(candidates, key=lambda c: c[0])
        data = datasets[horizon]
        selection = {'horizon_minutes':horizon, 'validation_gains_mbit':validation,
            'feature_names':data['feature_names'], 'selection_uses_final_test':False,
            'probability_variant': variant, 'probability_variant_candidates': list(VARIANTS)}
        train = _indices(data, np.flatnonzero(data['ends'] < boundaries[-1] - 300), column)
        test = _indices(data, np.flatnonzero(data['times'] >= boundaries[-1]), column)
        # Archive pretest selection/fit never depends on final movement labels.
        train = _purged(data, train, np.flatnonzero(data['times'] >= boundaries[-1])
                        if feature_variation_required else test)
        if feature_variation_required and (not len(train) or not np.any(np.ptp(data['x'][train],axis=0)>0)):
            heads[name] = {'working_supported':False,'reason':'NO_TRAINING_FEATURE_VARIATION','forecast':None}
            continue
        if pretest_selection_only:
            heads[name] = selection
            continue
        if len(train) < 40 or len(test) < 10:
            heads[name] = {**selection, 'working_supported': False, 'reason': 'PURGED_FINAL_TEST_UNAVAILABLE', 'forecast': None}
            continue
        head = fit_head(data['x'][train], data['y'][train, column])
        head['probability_transform'] = variant
        test_gain = gain(data['y'][test, column], predict(head, data['x'][test]), head['baseline'])
        blocks = [gain(data['y'][ix, column], predict(head, data['x'][ix]), head['baseline'])
                  if len(ix) >= 3 else None for ix in np.array_split(test, 3)]
        admission = working_admission(validation, blocks, test_gain)
        heads[name] = {**selection, 'head': head, 'horizon_minutes': horizon, 'working_supported': admission['supported'],
            'working_admission': admission, 'validation_gains_mbit': validation, 'test_gain_mbit': test_gain,
            'test_block_gains_mbit': blocks, 'test_n': len(test), 'train_n': len(train),
            'fit_end_ts': float(data['ends'][train[-1]]), 'test_start_ts': boundaries[-1],
            'selection_uses_final_test': False, 'feature_names': data['feature_names']}
    if not any(('validation_gains_mbit' if pretest_selection_only else 'head') in row for row in heads.values()):
        return {'status': 'HEAD_TRAINING_UNAVAILABLE', 'reason': 'NO_HEAD_WITH_CAUSAL_VALIDATION',
                'forecast': None, 'training_n': len(anchor['times']), 'heads': heads}
    # Models retain the pre-test fit: reported OOS describes the delivered fit.
    body = {'contract_version': CONTRACT, 'feature_contract_version': FEATURE_CONTRACT,
        'family_id': family, 'instrument': code,
        'status': 'PRETEST_SELECTED' if pretest_selection_only else 'TRAINED', 'target_kind': TARGET,
        'training_n': len(anchor['times']), 'training_cutoff': float(anchor['ends'][-1]),
        'trained_at': captured, 'heads': heads, 'source': source,
        'dataset_sha256': fingerprint({'x': anchor['x'].tolist(), 'y': anchor['y'].tolist(),
                                      'times': anchor['times'].tolist(), 'ends': anchor['ends'].tolist()}),
        'validation_time_boundaries': boundaries, 'requested_horizons_minutes': list(horizons),
        'features_are_captured_receipts': family not in {'session', 'intermarket'},
        'historical_bar_availability': 'COMPLETED_PROVIDER_BAR_WITH_5M_DRIVER_LAG_NOT_RECORDED_RECEIPT',
        'calendar_market_open_asserted': False, 'net_economic_proof': False, 'automatic_execution': False,
        'probability_variant_search': {'variants': list(VARIANTS), 'selection_data': 'EARLY_PURGED_VALIDATION_ONLY',
                                     'prospective_confirmation': False}}
    body['model_sha256'] = fingerprint(body)
    return body


def train_market_families(sources, captured, *, horizons=SEARCH_HORIZONS, feature_history=None,
                         macro_archive=None, related_sources=None, family_target_sources=None):
    if number(captured) is None or captured <= 0 or not horizons or any(h not in SEARCH_HORIZONS for h in horizons):
        raise ValueError('INVALID_CAPTURE_OR_HORIZON')
    if not isinstance(sources, list) or len(sources) > 32:
        raise ValueError('TRAINING_SOURCE_BOUND_INVALID')
    if macro_archive is not None:
        from .edge_family_macro_archive import OfficialMacroArchive
        if not isinstance(macro_archive,OfficialMacroArchive) or macro_archive.captured_ts != captured:
            raise ValueError('OFFICIAL_MACRO_ARCHIVE_NOT_VALIDATED_FOR_CUTOFF')
    if related_sources is not None and (not isinstance(related_sources,list) or len(related_sources)>3):
        raise ValueError('RELATED_TRAINING_SOURCE_BOUND_INVALID')
    related = related_series(sources+(related_sources or []), captured)
    history = feature_history if (isinstance(feature_history, dict)
        and not simulated_evidence(feature_history, recursive=False)) else {}
    indexed, duplicates = {}, set()
    for source in sources:
        code = source.get('instrument') if isinstance(source, dict) else None
        if code in indexed:
            duplicates.add(code)
        indexed[code] = source
    from .reference_market_series import validate_reference_series, PROVIDER, CONTRACT as REFERENCE_CONTRACT
    references, reference_duplicates = {}, set()
    if family_target_sources is not None:
        if not isinstance(family_target_sources,list) or len(family_target_sources)>10:
            raise ValueError('FAMILY_REFERENCE_SOURCE_BOUND_INVALID')
        for reference in family_target_sources:
            code=reference.get('instrument') if isinstance(reference,dict) else None
            if code in references:reference_duplicates.add(code)
            references[code]=reference
    result = {}
    for code in ALL_INSTRUMENTS:
        rows = result[code] = {}
        source = indexed.get(code)
        try:
            if code in duplicates:
                raise ValueError('DUPLICATE_TARGET_SOURCE')
            reference=references.get(code)
            # Choose by actual input availability, before fitting or seeing any
            # held-out score. An optional damaged lane cannot relabel a target.
            if (code not in reference_duplicates and reference is not None
                    and validate_reference_series(reference,code,captured) is None
                    and len(reference['bars'])>=1+180*(min(horizons)//5)):
                source=reference
            if not isinstance(source, dict):
                raise ValueError('TARGET_COMPLETED_BARS_UNAVAILABLE')
            bars = _bars(source, captured)
            expected = ALL_INSTRUMENTS[code]
            if code in CRYPTO_INSTRUMENTS:
                if source.get('provider') != 'Binance' or source.get('ticker') != expected.binance_symbol:
                    raise ValueError('TARGET_CONFIGURED_SERIES_MISMATCH')
            elif (source.get('provider') != PROVIDER and
                    (source.get('provider') != 'Yahoo Finance via yfinance' or source.get('ticker') != expected.yahoo)):
                raise ValueError('TARGET_CONFIGURED_SERIES_MISMATCH')
            provenance = {'provider': source['provider'], 'ticker': source['ticker'], 'interval': '5m',
                'bars_sha256': fingerprint(bars), 'not_broker_execution_bars': True}
            if source.get('provider')==PROVIDER:
                if validate_reference_series(source,code,captured) is not None:
                    raise ValueError('TARGET_CONFIGURED_SERIES_MISMATCH')
                provenance.update(reference_contract=REFERENCE_CONTRACT,resolved_symbol=source['resolved_symbol'],
                    reference_received_ts=source['received_ts'],
                    reference_scope='INDEPENDENT_FORECAST_REFERENCE_NOT_EXECUTION_QUOTE')
            code_history = history.get(code)
            if not isinstance(code_history, dict) or simulated_evidence(code_history, recursive=False):
                code_history = {}
            for family in FAMILIES:
                observations = code_history.get(family)
                if family=='order_flow' and code in CRYPTO_INSTRUMENTS and 'trade_flow_history' in source:
                    from .edge_family_trade_flow import fit_trade_flow
                    rows[family]=fit_trade_flow(code,bars,captured,horizons,source,provenance)
                    continue
                if family in {'macro','event'} and macro_archive is not None:
                    selection=(_fit_family(code,family,bars,captured,horizons,related,observations,provenance,
                        pretest_selection_only=True) if observations is not None else None)
                    # Freeze native vs public-archive features from pretest
                    # eligibility. A missing/flat held-out test cannot switch
                    # the feature contract after observing its outcomes.
                    if selection is not None and selection['status']=='PRETEST_SELECTED':
                        rows[family]=_fit_family(code,family,bars,captured,horizons,related,observations,provenance)
                    else:
                        if family=='macro':
                            from .edge_family_macro_archive import fit_archive_macro
                            rows[family]=fit_archive_macro(code,bars,captured,horizons,macro_archive,provenance)
                        else:
                            from .edge_family_event_archive import fit_archive_events
                            rows[family]=fit_archive_events(code,bars,captured,horizons,macro_archive,provenance,
                                target_source=source)
                    continue
                if family not in {'session', 'intermarket'} and observations is None:
                    rows[family] = {'status': 'HISTORICAL_FEATURES_UNAVAILABLE',
                                    'reason': 'CAPTURED_' + family.upper() + '_FEATURE_HISTORY_MISSING', 'forecast': None}
                    continue
                rows[family] = _fit_family(code, family, bars, captured, horizons, related, observations, provenance)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            for family in FAMILIES:
                rows[family] = {'status': 'SOURCE_UNAVAILABLE', 'reason': str(exc)[:160], 'forecast': None}
    return result


def features_from_archive(archive):
    """Extract only actual immutable observations; keep this matrix private."""
    import json
    from .edge_family_archive import assemble_archive
    from .edge_family_adapters import build_edge_family_evidence
    if not isinstance(archive, dict) or simulated_evidence(archive, recursive=False):
        return {}
    validated = assemble_archive([], previous=archive)
    result = {}
    for record in validated['episodes']:
        snapshot = json.loads(record['snapshot_json'])
        code = (snapshot.get('strategy') or {}).get('instrument') or snapshot.get('instrument')
        if code not in ALL_INSTRUMENTS:
            continue
        snapshot.pop('edge_family_models', None)
        snapshot.pop('edge_family_market_context', None)
        families = build_edge_family_evidence(snapshot)['families']
        for family, row in families.items():
            features = {name:value for name,value in row['features'].items()
                        if name.startswith(FEATURE_PREFIXES[family])
                        and not row['feature_provenance'].get(name, {}).get('context_only')}
            names = sorted(features)[:16]
            if not names:
                continue
            result.setdefault(code, {}).setdefault(family, []).append({
                'captured_ts': snapshot['captured_ts'], 'features': {n:features[n] for n in names},
                'feature_provenance': {n:row['feature_provenance'][n] for n in names}})
    return result


def aligned_related_features(record, cutoff):
    """Same two actual closes as historical5m bars, with real receipt proofs."""
    try:
        if simulated_evidence(record) or record.get('market_return_contract') != FEATURE_CONTRACT:
            return None
        origin = number(record.get('market_return_origin_ts'))
        if origin is None or origin % 300 or not 0 <= cutoff-origin < 900:
            return None
        rows = record.get('aligned_related_series')
        if not isinstance(rows, list) or len(rows) != 3:
            return None
        series, proofs = {}, {}
        for item in rows:
            product = item.get('symbol')
            received = number(item.get('available_at'))
            closes = item.get('closes')
            digest = item.get('body_sha256')
            if (product not in PRODUCTS.values() or product in series
                    or item.get('provider') != 'Coinbase Exchange' or item.get('quote_currency') != 'USD'
                    or received is None or not origin-300 <= received <= cutoff
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(c not in '0123456789abcdef' for c in digest)
                    or not isinstance(item.get('source_id'), str) or not item['source_id']
                    or not isinstance(closes, list) or len(closes) != 2
                    or any(not isinstance(row, list) or len(row) != 2 for row in closes)
                    or [row[0] for row in closes] != [origin-600, origin-300]):
                return None
            series[product] = {row[0]: row[1] for row in closes}
            proofs[product] = {'source_id': item['source_id'], 'body_sha256': digest,
                'provider': item['provider'], 'symbol': product, 'quote_currency': 'USD',
                'observed_ts': origin-300, 'received_ts': received,
                'window_seconds': 300., 'lag_seconds': 300., 'forecast_origin_ts': origin}
        values = intermarket_features(series, origin)
        if values is None:
            return None
        return values, {name: proofs[product] for product in series
            for name in [f'intermarket.COINBASE{product}.return_5m_aligned_lag5m']}
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def _model_reason(model, code, family, cutoff):
    try:
        if not isinstance(model, dict) or simulated_evidence(model):
            return 'SIMULATED_OR_INVALID_MARKET_MODEL'
        if (model.get('contract_version') != CONTRACT or model.get('feature_contract_version') != FEATURE_CONTRACT
                or model.get('target_kind') != TARGET or model.get('instrument') != code
                or model.get('family_id') != family or model.get('status') != 'TRAINED'
                or model.get('automatic_execution') is not False or model.get('net_economic_proof') is not False
                or model.get('model_sha256') != fingerprint({k:v for k,v in model.items() if k != 'model_sha256'})):
            return 'MARKET_MODEL_CONTRACT_OR_HASH_INVALID'
        trained, end = number(model.get('trained_at')), number(model.get('training_cutoff'))
        if trained is None or end is None or not 0 < end <= trained <= cutoff or cutoff-end >= 90*86400:
            return 'MARKET_MODEL_CLOCK_OR_AGE_INVALID'
        if 'official_macro_archive' in model or (family=='macro' and model.get('features_are_captured_receipts') is False):
            from .edge_family_macro_archive import archive_model_provenance_valid
            if family!='macro' or not archive_model_provenance_valid(model,trained):
                return 'MARKET_MACRO_ARCHIVE_PROVENANCE_INVALID'
        if 'public_trade_flow_archive' in model or (family=='order_flow' and model.get('features_are_captured_receipts') is False):
            from .edge_family_trade_flow import archive_provenance_valid
            if family!='order_flow' or not archive_provenance_valid(model,code,cutoff):
                return 'MARKET_TRADE_FLOW_ARCHIVE_PROVENANCE_INVALID'
        if 'official_event_archive' in model or (family=='event' and model.get('features_are_captured_receipts') is False):
            from .edge_family_event_archive import archive_provenance_valid
            if family!='event' or not archive_provenance_valid(model,trained,cutoff):
                return 'MARKET_EVENT_ARCHIVE_PROVENANCE_INVALID'
        heads = model.get('heads')
        if not isinstance(heads, dict) or set(heads) != {'direction', 'movement'}:
            return 'MARKET_MODEL_HEADS_INVALID'
        if not any(isinstance(row, dict) and 'head' in row for row in heads.values()):
            return 'MARKET_MODEL_HEADS_UNTRAINED'
        for row in heads.values():
            if not isinstance(row, dict):
                return 'MARKET_MODEL_HEADS_INVALID'
            if 'head' not in row:
                continue
            names = row['feature_names']; head = row['head']
            if not isinstance(head, dict):
                return 'MARKET_MODEL_VALIDATION_OR_HEAD_INVALID'
            variant = head.get('probability_transform', 'DIRECT')
            if (variant not in VARIANTS or row.get('probability_variant', 'DIRECT') != variant
                    or ('probability_variant_candidates' in row
                        and row['probability_variant_candidates'] != list(VARIANTS))):
                return 'MARKET_MODEL_PROBABILITY_TRANSFORM_INVALID'
            admission = working_admission(row['validation_gains_mbit'], row['test_block_gains_mbit'], row['test_gain_mbit'])
            if (not isinstance(names, list) or not 1 <= len(names) <= 16 or len(set(names)) != len(names)
                    or any(not isinstance(n, str) or not n.startswith(FEATURE_PREFIXES[family]) for n in names)
                    or row.get('horizon_minutes') not in SEARCH_HORIZONS
                    or row.get('selection_uses_final_test') is not False
                    or row.get('working_admission') != admission or row.get('working_supported') is not admission['supported']
                    or number(row.get('fit_end_ts')) is None or number(row.get('test_start_ts')) is None
                    or not 0 < row['fit_end_ts'] < row['test_start_ts'] <= end
                    or row.get('train_n', 0) < 40 or row.get('test_n', 0) < 10
                    or any(not isinstance(head.get(k), list) or len(head[k]) != len(names) for k in ('mean','scale'))
                    or not isinstance(head.get('beta'), list) or len(head['beta']) != len(names)+1
                    or any(number(v) is None for k in ('mean','scale','beta') for v in head[k])
                    or any(v <= 0 for v in head['scale'])
                    or number(head.get('baseline')) is None or not 0 < head['baseline'] < 1):
                return 'MARKET_MODEL_VALIDATION_OR_HEAD_INVALID'
        source = model.get('source') or {}
        if not isinstance(source, dict):
            return 'MARKET_TARGET_CONFIGURED_SERIES_MISMATCH'
        expected = ALL_INSTRUMENTS[code]
        from .reference_market_series import PROVIDER,model_source_valid
        reference=source.get('provider')==PROVIDER
        valid_reference=reference and model_source_valid(source,code)
        valid_legacy=(not reference
                and source.get('provider')==('Binance' if code in CRYPTO_INSTRUMENTS else 'Yahoo Finance via yfinance')
                and source.get('ticker')==(expected.binance_symbol if code in CRYPTO_INSTRUMENTS else expected.yahoo))
        if (source.get('interval') != '5m' or source.get('not_broker_execution_bars') is not True
                or not (valid_reference or valid_legacy)):
            return 'MARKET_TARGET_CONFIGURED_SERIES_MISMATCH'
    except (ValueError, TypeError, KeyError, OverflowError):
        return 'MARKET_MODEL_INVALID'
    return None


def freeze_context_models(context,*,force=False):
    """Lossless wire encoding only when the complete context exceeds its budget."""
    import base64,json,zlib
    from .operational_edge_compaction import MARKET_CONTEXT_MAX_BYTES
    if not force and len(json.dumps(context,allow_nan=False,separators=(',',':')).encode())<=MARKET_CONTEXT_MAX_BYTES:
        return
    for item in context['families'].values():
        if not isinstance(item,dict):continue
        model=item.get('model')
        if not isinstance(model,dict):continue
        try:raw=json.dumps(model,allow_nan=False,separators=(',',':')).encode()
        except (TypeError,ValueError):continue
        if len(raw)>24_000:continue
        item['model_encoded']=base64.b64encode(zlib.compress(raw)).decode('ascii')
        item['model_encoding']='json-zlib-v1'
        del item['model']


_CONTEXT_HEADERS=('contract_version','available','reason','network_calls','runtime_fitting',
                  'market_observation_ts','published_for_sha')


def pack_market_context(context):
    """Lossless transport of the complete bounded frozen input, not a new model."""
    import base64,json,zlib
    from .operational_edge_compaction import MARKET_CONTEXT_MAX_BYTES
    if not isinstance(context,dict) or 'context_encoded' in context:return context
    try:raw=json.dumps(context,allow_nan=False,separators=(',',':')).encode()
    except (ValueError,TypeError):return context
    if len(raw)>MARKET_CONTEXT_MAX_BYTES:return context
    packed={k:context[k] for k in _CONTEXT_HEADERS if k in context}
    packed.update(context_encoding='json-zlib-v1',context_encoded=base64.b64encode(zlib.compress(raw)).decode(),
                  context_sha256=fingerprint(context))
    return packed if len(json.dumps(packed,separators=(',',':')).encode())<len(raw) else context


def unpack_market_context(context):
    if not isinstance(context,dict) or 'context_encoded' not in context:return context
    try:
        import base64,json,zlib
        from .operational_edge_compaction import MARKET_CONTEXT_MAX_BYTES
        encoded=context.get('context_encoded')
        if (context.get('context_encoding')!='json-zlib-v1' or not isinstance(encoded,str)
                or not 0<len(encoded)<=45000):return None
        decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(encoded,validate=True),MARKET_CONTEXT_MAX_BYTES+1)
        if (len(raw)>MARKET_CONTEXT_MAX_BYTES or not decoder.eof or decoder.unused_data
                or decoder.unconsumed_tail):return None
        value=json.loads(raw)
        if not isinstance(value,dict) or 'context_encoded' in value:return None
        headers={k:value[k] for k in _CONTEXT_HEADERS if k in value}
        actual={k:v for k,v in context.items() if k not in ('context_encoding','context_encoded','context_sha256')}
        if actual!=headers or context.get('context_sha256')!=fingerprint(value):return None
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError,zlib.error):return None


def frozen_model(item):
    if 'model_encoded' not in item:
        return item.get('model')
    try:
        import base64,json,zlib
        encoded=item.get('model_encoded')
        if ('model' in item or item.get('model_encoding')!='json-zlib-v1'
                or not isinstance(encoded,str) or not 0<len(encoded)<=32_000):return None
        decoder=zlib.decompressobj()
        raw=decoder.decompress(base64.b64decode(encoded,validate=True),24_001)
        if (len(raw)>24_000 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):return None
        return json.loads(raw)
    except (ValueError,TypeError,UnicodeError,zlib.error):return None


def attach_market_family_context(engine, snapshot):
    """Bounded local read; freeze actual model inputs before current LLM."""
    import json
    from pathlib import Path
    from .mathematical_edge import load_artifact, completed_live_bars
    from .edge_family_adapters import build_edge_family_evidence
    context = {'contract_version': CONTRACT, 'families': {}, 'available': False,
               'reason': 'LOCAL_MARKET_MODELS_UNAVAILABLE', 'network_calls': False, 'runtime_fitting': False}
    snapshot['edge_family_market_context'] = context
    from .snapshot_clock import market_observation_cutoff
    cutoff = market_observation_cutoff(snapshot)
    frozen_cutoff=number(getattr(getattr(engine,'market',None),'frozen_inputs_captured_ts',None))
    if cutoff is not None and frozen_cutoff is not None and frozen_cutoff>0:
        # The canonical UI tick can predate the already-received input copy.
        # Quote age still belongs to that tick; these inputs belong to their
        # own original freeze, never to completion of the slow policy builder.
        cutoff=min(number(snapshot.get('captured_ts')),frozen_cutoff)
    context['market_observation_ts'] = cutoff
    code = (snapshot.get('strategy') or {}).get('instrument') or snapshot.get('instrument')
    feed = getattr(engine, 'market', None)
    if cutoff is None or code not in ALL_INSTRUMENTS or getattr(feed, 'instrument_code', None) != code:
        context['reason'] = 'MARKET_MODEL_SNAPSHOT_OR_FEED_INVALID'; return
    authority = getattr(feed, 'intraday_source_authority', {})
    if getattr(feed, 'demo', False) or simulated_evidence(snapshot, recursive=False) or simulated_evidence(authority):
        context['reason'] = 'SIMULATED_MARKET_INPUT_NOT_ADMISSIBLE'; return
    authority_invalid=not isinstance(authority,dict)
    if authority_invalid:authority={}
    expected = ALL_INSTRUMENTS[code]
    provider, ticker = ('Binance', expected.binance_symbol) if code in CRYPTO_INSTRUMENTS else ('Yahoo', expected.yahoo)
    observed, received = number(authority.get('observed_ts')), number(authority.get('available_at'))
    checks = {
        'provider': authority.get('provider') == provider,
        'source_symbol': authority.get('source_symbol') == ticker,
        'target_instrument': authority.get('target_instrument') == code,
        'source_verified': authority.get('source_verified') is True,
        'observation_clock': observed is not None and observed > 0,
        'receipt_clock': received is not None and received > 0,
        'observed_after_receipt': observed is None or received is None or observed <= received,
        'received_after_cutoff': received is None or received <= cutoff,
        'stale_observation': observed is None or cutoff-observed < 900,
        'crypto_derived': code not in CRYPTO_INSTRUMENTS or authority.get('derived') is False,
    }
    failed = [name for name,passed in checks.items() if not passed]
    from .reference_market_series import (PROVIDER as REFERENCE_PROVIDER, validate_reference_series,
                                          runtime_proof)
    reference=getattr(feed,'family_reference_series',None)
    reference_reason=validate_reference_series(reference,code,cutoff,runtime=True)
    legacy_reason=None
    if failed:
        context['failed_source_checks'] = failed
        legacy_reason=('MARKET_SOURCE_AUTHORITY_UNAVAILABLE' if authority_invalid
                       else 'MARKET_SOURCE_IDENTITY_OR_CLOCK_INVALID')
    completed = completed_live_bars(getattr(feed, 'intraday_ohlcv', []), cutoff)
    origin = math.floor(cutoff/300)*300
    if legacy_reason is None and (not completed or completed[-1]['bar_end_ts'] != origin):
        legacy_reason='LATEST_COMPLETED_MARKET_BAR_UNAVAILABLE'
    if legacy_reason is not None and reference_reason is not None:
        context['reason']=legacy_reason
    data_dir = getattr(getattr(engine, 'settings', None), 'data_dir', None)
    report = load_artifact(Path(data_dir)/'research'/'mathematical_edge_latest.json', cutoff) if data_dir else None
    if report is None:
        return
    matrix = report.get('family_market_models') or {}
    if not isinstance(matrix, dict) or simulated_evidence(matrix, recursive=False):
        return
    models = matrix.get(code) or {}
    if not isinstance(models, dict) or simulated_evidence(models, recursive=False):
        return
    context['published_for_sha']=report.get('published_for_sha')
    model_reasons={family:_model_reason(model,code,family,cutoff)
                   for family,model in models.items() if isinstance(model,dict) and model.get('status')=='TRAINED'}
    context['model_inventory']={family:{'trained_model_valid':reason is None,
        'target_provider':(models[family]['source'] if isinstance(models[family].get('source'),dict) else {}).get('provider'),
        'target_symbol':(models[family]['source'] if isinstance(models[family].get('source'),dict) else {}).get('ticker'),
        'model_sha256':models[family].get('model_sha256'),'reason':reason}
        for family,reason in model_reasons.items()}
    if legacy_reason is not None and reference_reason is not None:
        context['rejected_models']={family:(reference_reason if models[family]['source']['provider']==REFERENCE_PROVIDER
                                           else legacy_reason) for family,reason in model_reasons.items() if reason is None}
        return
    facts = build_edge_family_evidence(snapshot)['families']
    for family in FAMILIES:
        model = models.get(family)
        if not isinstance(model, dict) or model.get('status') != 'TRAINED':
            continue
        reason = model_reasons.get(family)
        if reason:
            context.setdefault('rejected_models', {})[family] = reason; continue
        uses_reference=model['source']['provider']==REFERENCE_PROVIDER
        input_reason=reference_reason if uses_reference else legacy_reason
        if input_reason is not None:
            context.setdefault('rejected_models',{})[family]=input_reason;continue
        family_provider=REFERENCE_PROVIDER if uses_reference else provider
        family_ticker=reference['ticker'] if uses_reference else ticker
        family_receipt=reference['received_ts'] if uses_reference else received
        if family == 'session':
            features = session_features(code, origin)
            proof = {name: {'source_verified': True, 'source_id': family_provider+':'+family_ticker+':observed-clock',
                'source_instrument': code, 'observed_ts': origin, 'received_ts': max(origin, family_receipt),
                'max_age_sec': 900., 'quality': 1., 'dependency_group': 'price_path'} for name in features}
        else:
            features, proof = facts[family]['features'], facts[family]['feature_provenance']
            if family=='order_flow' and 'public_trade_flow_archive' in model:
                from .edge_family_trade_flow import live_flow_observation
                flow=live_flow_observation(getattr(feed,'binance_trade_flow',None),code=code,cutoff=cutoff,origin=origin)
                if flow is None:
                    context.setdefault('rejected_models',{})[family]='ACTUAL_COMPLETED_BINANCE_TAKER_FLOW_UNAVAILABLE';continue
                features,proof=flow['features'],flow['feature_provenance']
            if family == 'positioning' and any(any(n.startswith('positioning.option_') for n in head.get('feature_names', []))
                    for head in model['heads'].values()):
                from .edge_family_option_interest import interest_observation
                from .passive_learning import PassiveLearningEngine
                chain=getattr(feed,'chain',None)
                interest=None
                if isinstance(chain,dict):
                    receipt=number(chain.get('ts'))
                    if receipt is not None and receipt<=origin:
                        metrics=chain.get('metrics')
                        try:
                            interest=interest_observation(code=code,metrics=metrics,source_id=chain.get('source'),
                                observed_ts=receipt,received_ts=receipt,quality=PassiveLearningEngine._quality(chain),
                                source_sha256=fingerprint(metrics))
                        except (ValueError,TypeError,OverflowError):
                            pass
                if interest is None or cutoff-interest['captured_ts']>1800:
                    context.setdefault('rejected_models',{})[family]='ACTUAL_CONFIGURED_OPTION_INTEREST_UNAVAILABLE';continue
                features,proof=interest['features'],interest['feature_provenance']
            if family == 'option' and any('option.iv' in head.get('feature_names', []) for head in model['heads'].values()):
                from .edge_family_native_history import option_observation
                from .passive_learning import PassiveLearningEngine
                chain = getattr(feed, 'chain', None)
                option = None
                if isinstance(chain, dict):
                    option_received = number(chain.get('ts'))
                    metrics = chain.get('metrics')
                    if option_received is not None and option_received <= origin:
                        try:
                            option = option_observation(code=code, metrics=metrics,
                                source_id=chain.get('source'), observed_ts=option_received,
                                received_ts=option_received, quality=PassiveLearningEngine._quality(chain),
                                source_sha256=fingerprint(metrics))
                        except (ValueError, TypeError, OverflowError):
                            pass  # Invalid optional input cannot remove other experts.
                if option is None or cutoff-option['captured_ts'] > 1800:
                    context.setdefault('rejected_models', {})[family] = 'ACTUAL_CONFIGURED_OPTION_INPUT_UNAVAILABLE'; continue
                features, proof = option['features'], option['feature_provenance']
            if family == 'intermarket':
                names = [f'intermarket.COINBASE{p}.return_5m_aligned_lag5m' for p in PRODUCTS.values()]
                if any(name not in features or (proof[name].get('forecast_origin_ts') != origin) for name in names):
                    context.setdefault('rejected_models', {})[family] = 'ALIGNED_RELATED_MARKET_WINDOW_UNAVAILABLE'; continue
                features, proof = ({n: features[n] for n in names}, {n: proof[n] for n in names})
        context['families'][family] = {'model': model, 'features': features, 'feature_provenance': proof,
                                        'forecast_origin_ts': origin}
        if uses_reference:
            context['families'][family]['target_reference']=runtime_proof(reference)
    # Root uses the existing frozen-input budget and is excluded explicitly as a
    # whole when oversized, rather than deleting coefficients or observations.
    from .operational_edge_compaction import MARKET_CONTEXT_MAX_BYTES
    context.update(available=bool(context['families']), reason='LOCAL_HASHED_HISTORICAL_MARKET_MODELS')
    freeze_context_models(context)
    if len(json.dumps(context, allow_nan=False, separators=(',', ':')).encode()) > MARKET_CONTEXT_MAX_BYTES:
        context.update(families={},available=False,reason='MARKET_CONTEXT_EXCEEDS_SNAPSHOT_BUDGET'); return


def market_family_evidence(snapshot, families):
    """Compute forecasts and soft preferences; never rewrite action economics."""
    context = unpack_market_context(snapshot.get('edge_family_market_context')) or {}
    if (not isinstance(context, dict) or context.get('contract_version') != CONTRACT
            or simulated_evidence(context, recursive=False)):
        return []
    from .snapshot_clock import market_observation_cutoff
    cutoff = market_observation_cutoff(snapshot)
    # A fresh quote reprice does not retimestamp an earlier expert forecast.
    if 'market_observation_ts' in context:
        raw_origin = context['market_observation_ts']
        cutoff = None if isinstance(raw_origin, bool) else number(raw_origin)
        final_capture = number(snapshot.get('captured_ts'))
        if cutoff is None or final_capture is None or not 0 < cutoff <= final_capture:
            return []
    code = (snapshot.get('strategy') or {}).get('instrument') or snapshot.get('instrument')
    raw_horizon = ((snapshot.get('policy_manager') or {}).get('inputs') or {}).get('horizon_minutes')
    horizon = None if isinstance(raw_horizon, bool) else number(raw_horizon)
    direction = str((snapshot.get('strategy') or {}).get('direction') or '').lower()
    sign = 1. if direction in {'long', 'buy'} else -1. if direction in {'short', 'sell'} else None
    components = []
    records = context.get('families')
    if (not isinstance(records, dict) or simulated_evidence(records, recursive=False)
            or cutoff is None or horizon is None or horizon <= 0):
        return []
    for family in FAMILIES:
        row = families[family]
        inventory=(context.get('model_inventory') or {}).get(family) or {}
        if inventory.get('trained_model_valid') is True:
            row.update(market_model_trained=True,market_target_provider=inventory.get('target_provider'),
                       market_target_symbol=inventory.get('target_symbol'),market_target_is_execution_quote=False)
        item = records.get(family)
        if not isinstance(item, dict):
            if inventory.get('trained_model_valid') is True and row.get('forecast_available') is not True:
                reason=(context.get('rejected_models') or {}).get(family) or context.get('reason')
                row.update(readiness='TRAINED_MODEL_WAITING_CURRENT_INPUTS',reason=reason,needs_data=[reason])
            continue
        model = frozen_model(item)
        reason = 'SIMULATED_MARKET_CONTEXT_NOT_ADMISSIBLE' if simulated_evidence(item) else _model_reason(model, code, family, cutoff)
        if reason:
            row['forecast_rejections'].append({'reason': reason}); continue
        from .reference_market_series import PROVIDER as REFERENCE_PROVIDER,validate_runtime_proof
        reference=None
        if model['source']['provider']==REFERENCE_PROVIDER:
            reference=item.get('target_reference')
            reason=validate_runtime_proof(reference,code,cutoff)
            if reason:
                row['forecast_rejections'].append({'reason':reason});continue
        features, proof = item.get('features'), item.get('feature_provenance')
        origin = number(item.get('forecast_origin_ts'))
        if (not isinstance(features, dict) or not isinstance(proof, dict) or not 1 <= len(features) <= 16
                or origin is None or origin != math.floor(cutoff/300)*300
                or any(number(v) is None or _feature_reason(proof.get(n), code, family, cutoff,
                    allow_global=family in {'macro', 'event'}) is not None for n,v in features.items())):
            row['forecast_rejections'].append({'reason': 'MARKET_FEATURE_CLOCK_OR_PROVENANCE_INVALID'}); continue
        if family == 'session' and features != session_features(code, origin):
            row['forecast_rejections'].append({'reason': 'MARKET_SESSION_FEATURE_CONTRACT_INVALID'}); continue
        if family == 'intermarket' and any(proof[name].get('forecast_origin_ts') != origin
                or proof[name].get('window_seconds') != 300 or proof[name].get('lag_seconds') != 300
                or proof[name].get('observed_ts') != origin-300 for name in features):
            row['forecast_rejections'].append({'reason': 'MARKET_RELATED_WINDOW_OR_LAG_MISMATCH'}); continue
        predictions, supported = {}, {}
        final_capture = number(snapshot.get('captured_ts'))
        for name, head in model['heads'].items():
            if 'head' not in head or head['horizon_minutes'] > horizon:
                continue
            names = head['feature_names']
            if any(n not in features for n in names):
                continue
            probability = float(predict(head['head'], [features[n] for n in names]))
            predictions[name] = {'probability': probability, 'baseline_probability': head['head']['baseline'],
                'probability_variant': head.get('probability_variant', 'DIRECT'),
                'horizon_minutes': head['horizon_minutes'],
                'economic_comparison_horizon_minutes': horizon,
                'horizon_role': ('MATCHED_HORIZON_MANUAL_PREFERENCE' if head['horizon_minutes'] == horizon
                                 else 'SHORT_HORIZON_MANUAL_PREFERENCE'),
                'working_supported': head['working_supported'],
                'test_gain_mbit': head['test_gain_mbit'], 'test_n': head['test_n'],
                'evidence_tier': head['working_admission']['tier']}
            forecast_unexpired = final_capture is not None and final_capture < origin + 60.*head['horizon_minutes']
            if reference is not None:
                forecast_unexpired=forecast_unexpired and final_capture<reference['observed_ts']+900
            predictions[name]['forecast_expired'] = not forecast_unexpired
            if head['working_supported'] and forecast_unexpired:
                age = max(0., (cutoff-model['training_cutoff'])/86400)
                quality = head['working_admission']['weight_multiplier'] * min(1.,head['test_n']/100) \
                    * min(1., max(0., head['test_gain_mbit']/20)) * max(0., 1-age/90)
                supported[name] = (probability, quality)
        if not predictions:
            row['forecast_rejections'].append({'reason': 'MARKET_FORECAST_HORIZON_OR_FEATURES_UNAVAILABLE'}); continue
        metas = list(proof.values())
        row.update(available=True, availability='AVAILABLE', forecast_available=True,
            readiness='TRAINED_MARKET_FORECAST_AVAILABLE' if supported else 'TRAINED_FORECAST_NO_SUPPORTED_ADVANTAGE',
            forecast_kind='TRAINED_MARKET_RESPONSE_NOT_NET_ACTION_MODEL', market_predictions=predictions,
            forecast_origin_ts=origin, market_model_sha256=model['model_sha256'],
            reason='HISTORICAL_MARKET_RESPONSE; NOT_TRAINED_ACTION_PROFIT', weight_pool='mathematical_edge',
            standalone_vote=False, needs_data=[], features={**row['features'], **features},
            feature_provenance={**row['feature_provenance'], **proof},
            quality=min(m['quality'] for m in metas), observed_ts=min(m['observed_ts'] for m in metas),
            source_ids=sorted(set(row['source_ids']) | {m['source_id'] for m in metas}),
            evidence_family_ids=sorted(set(row['evidence_family_ids']) | {m['dependency_group'] for m in metas}))
        scores, qualities = {}, []
        if 'direction' in supported and sign is not None:
            p, quality = supported['direction']; affinity = sign*(2*p-1)
            scores = {'HOLD': 0., **{policy: -fraction*affinity for policy,fraction in
                       {'CLOSE_10':.1,'CLOSE_25':.25,'CLOSE_50':.5,'EXIT':1.}.items()}}
            qualities.append(quality)
        # A shorter direction head is a bounded current stance, never a
        # replacement forecast of the terminal economic return. A movement
        # head cannot prescribe a longer time/target change without matching
        # its outcome window, so keep that original action restriction.
        if ('movement' in supported
                and predictions['movement']['horizon_minutes'] == horizon):
            p, quality = supported['movement']
            baseline = predictions['movement']['baseline_probability']
            low_move = max(0., (baseline-p)/max(baseline,.01))
            if low_move > 0:
                scores.update(HOLD=0., TIME_STOP=low_move, REDUCE_TAKE=low_move); qualities.append(quality)
        row['market_preference_available'] = bool(scores)
        if not scores:
            continue
        observed = min(m['observed_ts'] for m in metas)
        score_heads = [name for name in supported if name == 'direction'
                       or name == 'movement' and predictions[name]['horizon_minutes'] == horizon]
        expiry = min(origin+60.*predictions[name]['horizon_minutes'] for name in score_heads)
        if reference is not None:expiry=min(expiry,reference['observed_ts']+900)
        components.append({'component_id':'mathematical_edge', 'instrument':code, 'available':True,
            'scores':scores, 'quality':min(qualities), 'family_ids':[family],
            'model_version':model['model_sha256'], 'source_ids':sorted({m['source_id'] for m in metas}),
            'evidence_family_ids':sorted({m['dependency_group'] for m in metas}),
            'observed_ts':observed,
            'max_age_sec':min(expiry, *(m['observed_ts']+m['max_age_sec'] for m in metas))-observed,
            'reason':'HISTORICAL_MARKET_RESPONSE_BOUNDED_MANUAL_PREFERENCE',
            'prediction_measure':TARGET, 'score_semantics':'affinity_not_expected_return', 'net_economic_proof':False})
    return components


def family_runtime_status(snapshot):
    """Small capture-time inference receipt, computed only by the background owner."""
    context=unpack_market_context(snapshot.get('edge_family_market_context')) or {}
    if not isinstance(context,dict) or context.get('contract_version')!=CONTRACT:
        return {}
    from .edge_family_adapters import build_edge_family_evidence
    evidence=build_edge_family_evidence(snapshot)
    rows={}
    inventory=context.get('model_inventory') or {}
    for family,row in evidence['families'].items():
        predictions={name:{k:head.get(k) for k in ('probability','horizon_minutes','working_supported','forecast_expired')}
                     for name,head in (row.get('market_predictions') or {}).items()}
        rows[family]={'trained_model_valid':(inventory.get(family) or {}).get('trained_model_valid') is True,
            'forecast_available_at_capture':any(p['forecast_expired'] is False for p in predictions.values()),
            'preference_available_at_capture':row.get('market_preference_available') is True,
            'target_provider':row.get('market_target_provider'),'target_symbol':row.get('market_target_symbol'),
            'forecast_origin_ts':row.get('forecast_origin_ts'),'model_sha256':row.get('market_model_sha256'),
            'reason':row.get('reason'),'predictions':predictions}
        reference=((context.get('families') or {}).get(family) or {}).get('target_reference')
        rows[family]['reference_valid_until_ts']=(reference['observed_ts']+900 if isinstance(reference,dict) else None)
    return {'contract_version':'family-runtime-forecast-receipt-v1','instrument':evidence['instrument'],
        'scope':'FROZEN_INPUTS_AT_CAPTURE_NOT_CURRENT_DECISION','captured_ts':snapshot.get('captured_ts'),
        'market_observation_ts':context.get('market_observation_ts'),'published_for_sha':context.get('published_for_sha'),
        'available_forecasts_at_capture':sum(r['forecast_available_at_capture'] for r in rows.values()),
        'qualified_preferences_at_capture':sum(r['preference_available_at_capture'] for r in rows.values()),
        'families':rows,'network_calls':False,'runtime_fitting':False,'final_action_selected':False}
