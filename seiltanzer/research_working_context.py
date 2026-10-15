"""Frozen preliminary preferences and labelled substitutes, never missing facts.

Existing completed price bars can describe pressure, not reconstruct a book.
Previous-release references are not market consensus. Financing sensitivities
are assumptions, not broker quotes, and never alter verified net economics.
"""
from __future__ import annotations

import json
import math

from .config import ALL_INSTRUMENTS, CRYPTO_INSTRUMENTS
from .mathematical_edge import completed_live_bars, fingerprint
from .real_input_integrity import simulated_evidence
from .snapshot_clock import market_observation_cutoff

VERSION = 'source-bound-preliminary-research-v1'
MAX_BYTES = 8000
POLICY_FACTOR = .25


def _num(value):
    try:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except OverflowError:
        return None


def _code(snapshot):
    return (snapshot.get('strategy') or {}).get('instrument') or snapshot.get('instrument')


def _source_valid(source, code, cutoff):
    if not isinstance(source, dict) or code not in ALL_INSTRUMENTS or simulated_evidence(source):
        return False
    expected = ALL_INSTRUMENTS[code]
    provider, symbol = ('Binance', expected.binance_symbol) if code in CRYPTO_INSTRUMENTS else ('Yahoo', expected.yahoo)
    observed, received = (_num(source.get(k)) for k in ('observed_ts', 'available_at'))
    return (source.get('provider') == provider and source.get('source_symbol') == symbol
        and source.get('target_instrument') == code and source.get('source_verified') is True
        and source.get('derived') is not True and observed is not None and received is not None
        and cutoff is not None and 0 < observed <= received <= cutoff and cutoff-observed < 900)


def _release_reference(snapshot, cutoff):
    from .edge_family_adapters import _meta
    result = {'available': False, 'measurement_kind': 'PREVIOUS_RELEASE_REFERENCE_NOT_MARKET_CONSENSUS',
              'market_consensus_observed': False, 'prepublication_expectation_reconstructed': False}
    sources = snapshot.get('edge_family_sources') or {}
    if not isinstance(sources, dict) or simulated_evidence(sources):
        sources = {}
    records = sources.get('event') or []
    records = [records] if isinstance(records, dict) else records
    if not isinstance(records, list) or len(records) > 128:
        records = []
    else:
        records = list(records)
    root = snapshot.get('macro_context_v1') or snapshot.get('macro_t0_context') or {}
    numeric = root.get('numeric_macro') if isinstance(root,dict) else None
    releases = numeric.get('releases') if isinstance(numeric,dict) else None
    if (isinstance(releases,dict) and len(releases)<=32 and not simulated_evidence(root)):
        for family in ('NFP','CPI'):
            release = releases.get(family)
            if (not isinstance(release,dict) or release.get('status')!='VALID'
                    or release.get('official_source_verified') is not True):
                continue
            payload = release.get('payload')
            if not isinstance(payload,dict):
                continue
            if family == 'NFP':
                actual, previous = (_num(payload.get(k)) for k in ('payroll_change_k','previous_payroll_change_k'))
                unit = 'thousands_of_jobs'
            else:
                actual, change = (_num(payload.get(k)) for k in ('headline_mom_pct','headline_mom_change_pp'))
                previous = actual-change if actual is not None and change is not None else None
                unit = 'percent'
            records.append({**release, 'source_id': release.get('release_id'),
                'source_verified': True, 'global_context': True, 'instrument': _code(snapshot),
                'observed_ts': release.get('published_at') or release.get('available_at'),
                'max_age_sec': 4*3600. if release.get('published_at') else 45*86400.,
                'source_clock_kind': 'ACTUAL_RELEASE_PUBLICATION' if release.get('published_at')
                                     else 'OFFICIAL_FIRST_AVAILABILITY_NOT_EVENT_PUBLICATION',
                'actual': actual, 'previous_value': previous, 'unit': unit,
                'previous_reference_method': 'AS_REPORTED_CURRENT_RECEIPT' if family=='NFP'
                                             else 'DERIVED_FROM_REPORTED_CURRENT_CHANGE'})
    for row in records:
        if not isinstance(row, dict):
            continue
        actual, previous, published = (_num(row.get(k)) for k in ('actual', 'previous_value', 'published_at'))
        asof = row.get('source_clock_kind') == 'OFFICIAL_FIRST_AVAILABILITY_NOT_EVENT_PUBLICATION'
        meta, _ = _meta(row, family='macro' if asof else 'event', instrument=_code(snapshot), cutoff=cutoff, allow_global=True)
        if (meta and actual is not None and previous is not None
                and (asof or (published is not None and 0 < published <= meta['received_ts'] <= cutoff))
                and row.get('release_id') and row.get('period') and row.get('unit')):
            if row.get('previous_unit', row['unit']) != row['unit']:
                continue
            return {**result, 'available': True, 'actual': actual, 'previous_reference': previous,
                'actual_minus_previous_reference': actual-previous, 'unit': str(row['unit'])[:64],
                'period': str(row['period'])[:64], 'release_id': str(row['release_id'])[:128],
                'source_id': meta['source_id'], 'observed_ts': meta['observed_ts'],
                'received_ts': meta['received_ts'], 'dependency_group': meta['dependency_group'],
                'source_clock_kind': row.get('source_clock_kind', 'ACTUAL_RELEASE_PUBLICATION'),
                'max_age_sec': meta['max_age_sec'],
                'previous_reference_method': row.get('previous_reference_method','AS_REPORTED_CURRENT_RECEIPT'),
                'previous_vintage': 'AS_REPORTED_IN_CURRENT_RECEIPT_NOT_PREPUBLICATION_CONSENSUS'}
    return result


def _financing_sensitivity(snapshot):
    result = {'available': False, 'measurement_kind': 'ASSUMED_LINEAR_FINANCING_SENSITIVITY',
              'broker_cost_verified': False, 'applied_to_net_economics': False,
              'assumptions_are_not_broker_limits': True}
    geometry = snapshot.get('trade_geometry') or {}
    if not isinstance(geometry, dict) or simulated_evidence(geometry):
        return result
    entry, stop, price = (_num(geometry.get(k)) for k in ('entry', 'original_stop', 'current'))
    if any(v is None or v <= 0 for v in (entry, stop, price)) or entry == stop:
        return result
    ratio = price / abs(entry-stop)
    scenarios = [{'annual_rate_percent_assumption': rate, 'charged_days_assumption': days,
                  'cost_r_assumption': ratio * rate/100 * days/365}
                 for rate in (0., 5., 15.) for days in (1, 3)]
    if any(not math.isfinite(row['cost_r_assumption']) for row in scenarios):
        return result
    return {**result, 'available': True, 'scenarios': scenarios,
        'basis': 'per_unit_remaining_position_in_original_R; assumed_linear_price_contract',
        'includes_spread_commission_or_actual_rollover_schedule': False}


def attach_research_working_context(engine, snapshot):
    """Use frozen in-memory inputs only; bounded, no fitting/provider or writes."""
    cutoff, code = market_observation_cutoff(snapshot), _code(snapshot)
    context = {'contract_version': VERSION, 'instrument': code, 'available': False,
        'captured_ts': snapshot.get('captured_ts'), 'market_observation_ts': cutoff,
        'reason': 'PRELIMINARY_PRICE_INPUT_UNAVAILABLE', 'rules': [],
        'historical_profit_proven': False, 'automatic_execution_allowed': False,
        'network_calls': False, 'runtime_fitting': False, 'alternatives': {
            'order_book': {'available': False, 'measurement_kind': 'BAR_PRESSURE_PROXY_NOT_ORDER_BOOK',
                           'broker_order_book_observed': False},
            'consensus': {'available': False, 'market_consensus_observed': False,
                          'prepublication_expectation_reconstructed': False},
            'broker_swap': {'available': False, 'broker_cost_verified': False,
                            'applied_to_net_economics': False}}}
    snapshot['research_working_context'] = context
    if cutoff is None or simulated_evidence(snapshot, recursive=False):
        return
    context['alternatives']['consensus'] = _release_reference(snapshot, cutoff)
    context['alternatives']['broker_swap'] = _financing_sensitivity(snapshot)
    feed = getattr(engine, 'market', None)
    source = getattr(feed, 'intraday_source_authority', None)
    if (getattr(feed, 'instrument_code', None) != code or getattr(feed, 'demo', False)
            or getattr(feed, 'freeze_reason', None) or not _source_valid(source, code, cutoff)):
        return
    raw = getattr(feed, 'intraday_ohlcv', None)
    if (not isinstance(raw, (list, tuple)) or len(raw) > 4096
            or any(simulated_evidence(row) for row in raw)):
        return
    try:
        bars = completed_live_bars(raw, cutoff)
    except (KeyError, TypeError, ValueError, OverflowError):
        return
    tail = bars[-13:]
    if (len(tail) != 13 or cutoff-tail[-1]['bar_end_ts'] >= 900
            or any(b['bar_end_ts']-a['bar_end_ts'] != 300 for a,b in zip(tail, tail[1:]))
            or any(any(_num(row.get(k)) is None or row[k] <= 0 for k in ('open','high','low','close'))
                   or row['high'] < max(row['open'], row['close'])
                   or row['low'] > min(row['open'], row['close']) for row in tail)):
        return
    returns = [math.log(b['close'])-math.log(a['close']) for a,b in zip(tail, tail[1:])]
    scale = math.sqrt(sum(v*v for v in returns)/len(returns))
    impulse = math.log(tail[-1]['close'])-math.log(tail[-4]['close'])
    pressure = sum((b['close']-b['open'])/(b['high']-b['low'])
                   if b['high'] > b['low'] else 0. for b in tail[-3:])/3
    affinity = max(-1., min(1., impulse/(3*scale))) if scale > 0 else 0.
    context['rules'] = [
        {'rule_id': 'PRICE_MOMENTUM_HYPOTHESIS', 'affinity': affinity,
         'observations': {'return_15m': impulse, 'rms_return_5m': scale},
         'scope': '15m adaptation; AQR monthly evidence does not validate this horizon',
         'reference': 'https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum'},
        {'rule_id': 'BAR_PRESSURE_MANUAL_PROXY', 'affinity': pressure,
         'observations': {'mean_body_to_range_3bars': pressure},
         'scope': 'price-path heuristic; no signed volume, OFI, depth or broker tape'}]
    from .edge_family_adapters import build_edge_family_evidence
    session = build_edge_family_evidence(snapshot)['families']['session']
    features = session.get('features') or {}
    to_close, from_open = (_num(features.get(k)) for k in ('session.minutes_to_close','session.minutes_from_open'))
    if (session.get('session_context', {}).get('calendar_complete') is True
            and to_close is not None and from_open is not None and 0 < to_close <= 30 and from_open >= 30):
        opening = cutoff-from_open*60
        first = [b for b in bars if opening < b['bar_end_ts'] <= opening+1800]
        if (len(first) == 6 and first[0]['bar_end_ts'] == opening+300
                and all(b['bar_end_ts']-a['bar_end_ts'] == 300 for a,b in zip(first,first[1:]))):
            ret = math.log(first[-1]['close'])-math.log(first[0]['open'])
            amplitude = sum((b['high']-b['low'])/b['open'] for b in first)
            context['rules'].append({'rule_id': 'OPENING_TO_CLOSING_SESSION_HYPOTHESIS',
                'affinity': max(-1., min(1., ret/amplitude)) if amplitude else 0.,
                'observations': {'opening_30m_return_excluding_overnight': ret, 'minutes_to_close': to_close},
                'scope': 'adapted same-session price hypothesis; excludes overnight gap from published SPY rule',
                'reference': 'https://doi.org/10.1016/j.jfineco.2018.05.009'})
    context.update(available=True, reason='FROZEN_OBSERVED_PRICE_WITH_LABELLED_PRELIMINARY_RULES',
        observed_ts=tail[-1]['bar_end_ts'], source_provenance={k:source[k] for k in (
            'provider','source_symbol','target_instrument','source_verified','observed_ts','available_at','derived') if k in source},
        source_ids=[f"{source['provider']}:{source['source_symbol']}:completed5m"],
        evidence_family_ids=['price_path'])
    context['alternatives']['order_book'].update(available=True, pressure_proxy=pressure,
                                               dependency_group='price_path')
    context['context_sha256'] = fingerprint(context)
    if len(json.dumps(context, allow_nan=False, separators=(',', ':')).encode()) > MAX_BYTES:
        snapshot['research_working_context'] = {'contract_version': VERSION, 'available': False,
            'reason': 'PRELIMINARY_CONTEXT_EXCEEDS_BOUND'}
        return


def _valid_context(snapshot):
    context = snapshot.get('research_working_context') or {}
    if (not isinstance(context, dict) or context.get('contract_version') != VERSION
            or context.get('available') is not True or simulated_evidence(context)
            or simulated_evidence(snapshot, recursive=False)
            or any(context.get(k) is not False for k in ('historical_profit_proven',
                'automatic_execution_allowed','network_calls','runtime_fitting'))):
        return None
    try:
        if (len(json.dumps(context, allow_nan=False, separators=(',', ':')).encode()) > MAX_BYTES
                or context.get('context_sha256') != fingerprint({k:v for k,v in context.items() if k!='context_sha256'})):
            return None
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    cutoff = market_observation_cutoff(snapshot)
    captured, observed = _num(snapshot.get('captured_ts')), _num(context.get('observed_ts'))
    origin = _num(context.get('market_observation_ts'))
    if (cutoff is None or captured is None or origin is None or observed is None
            or not 0 < observed <= origin <= cutoff <= captured or captured-observed >= 900
            or context.get('instrument') != _code(snapshot)
            or not _source_valid(context.get('source_provenance'), _code(snapshot), origin)):
        return None
    rules = context.get('rules')
    if (not isinstance(rules, list) or not 1 <= len(rules) <= 3
            or any(not isinstance(r, dict) or _num(r.get('affinity')) is None or not -1 <= r['affinity'] <= 1
                   or r.get('rule_id') not in {'PRICE_MOMENTUM_HYPOTHESIS','BAR_PRESSURE_MANUAL_PROXY',
                                              'OPENING_TO_CLOSING_SESSION_HYPOTHESIS'} for r in rules)
            or len({r['rule_id'] for r in rules}) != len(rules)):
        return None
    alternatives = context.get('alternatives')
    if (not isinstance(alternatives, dict) or set(alternatives) != {'order_book','consensus','broker_swap'}
            or any(not isinstance(row, dict) or type(row.get('available')) is not bool
                   for row in alternatives.values())):
        return None
    book, consensus, swap = (alternatives[k] for k in ('order_book','consensus','broker_swap'))
    if (book.get('available') is not True or book.get('broker_order_book_observed') is not False
            or _num(book.get('pressure_proxy')) is None or not -1 <= book['pressure_proxy'] <= 1
            or consensus.get('market_consensus_observed') is not False
            or consensus.get('prepublication_expectation_reconstructed') is not False
            or swap.get('broker_cost_verified') is not False or swap.get('applied_to_net_economics') is not False):
        return None
    if consensus['available'] and (any(_num(consensus.get(k)) is None for k in
            ('actual_minus_previous_reference','observed_ts','received_ts','max_age_sec'))
            or not 0 < consensus['observed_ts'] <= consensus['received_ts'] <= origin
            or consensus.get('measurement_kind') != 'PREVIOUS_RELEASE_REFERENCE_NOT_MARKET_CONSENSUS'
            or consensus.get('source_clock_kind') not in {
                'ACTUAL_RELEASE_PUBLICATION','OFFICIAL_FIRST_AVAILABILITY_NOT_EVENT_PUBLICATION'}
            or not 0 < consensus['max_age_sec'] <= (
                45*86400. if consensus['source_clock_kind']=='OFFICIAL_FIRST_AVAILABILITY_NOT_EVENT_PUBLICATION' else 4*3600.)
            or origin-consensus['observed_ts'] > consensus['max_age_sec']
            or not isinstance(consensus.get('source_id'),str) or not consensus['source_id']
            or not isinstance(consensus.get('dependency_group'),str) or not consensus['dependency_group']):
        return None
    scenarios = swap.get('scenarios')
    if swap['available'] and (swap.get('measurement_kind') != 'ASSUMED_LINEAR_FINANCING_SENSITIVITY'
            or not isinstance(scenarios,list) or len(scenarios)!=6
            or any(not isinstance(row,dict) or _num(row.get('cost_r_assumption')) is None
                   or row['cost_r_assumption'] < 0 or _num(row.get('annual_rate_percent_assumption')) is None
                   or _num(row.get('charged_days_assumption')) is None for row in scenarios)
            or {(row['annual_rate_percent_assumption'],row['charged_days_assumption']) for row in scenarios}
               != {(r,d) for r in (0.,5.,15.) for d in (1,3)}):
        return None
    source = context['source_provenance']
    if context.get('source_ids') != [f"{source['provider']}:{source['source_symbol']}:completed5m"]:
        return None
    return context


def research_context_audit(snapshot):
    context = _valid_context(snapshot)
    return context if context is not None else {'available': False,
        'reason': 'PRELIMINARY_CONTEXT_UNAVAILABLE_OR_REJECTED'}


def preliminary_family_features(snapshot):
    """Manual-only derived/scenario facts; excluded from observed training."""
    context = _valid_context(snapshot)
    if context is None:
        return {}
    source = context['source_provenance']
    meta = {'source_id': context['source_ids'][0], 'source_verified': True,
        'source_instrument': _code(snapshot), 'observed_ts': context['observed_ts'],
        'received_ts': source['available_at'], 'available_at': source['available_at'],
        'max_age_sec': 900., 'quality': POLICY_FACTOR, 'dependency_group': 'price_path',
        'context_only': True, 'derived': True, 'manual_preliminary': True,
        'preliminary_context_sha256': context['context_sha256']}
    alternatives = context['alternatives']
    result = {'order_flow': {'flow.bar_pressure_proxy': (alternatives['order_book']['pressure_proxy'],
              {**meta, 'measurement_kind': 'BAR_PRESSURE_PROXY_NOT_ORDER_BOOK',
               'broker_order_book_observed': False})}}
    carry = alternatives['broker_swap']
    if carry.get('available'):
        value = next(row['cost_r_assumption'] for row in carry['scenarios']
                     if row['annual_rate_percent_assumption']==15 and row['charged_days_assumption']==3)
        result['value_carry'] = {'carry.assumed_15pct_3day_cost_r': (value,
            {**meta, 'measurement_kind': carry['measurement_kind'], 'broker_cost_verified': False,
             'applied_to_net_economics': False, 'annual_rate_percent_assumption': 15,
             'charged_days_assumption': 3})}
    benchmark = alternatives['consensus']
    if benchmark.get('available'):
        result['event'] = {'event.previous_release_change_reference':
            (benchmark['actual_minus_previous_reference'], {**meta,
                'source_id': benchmark['source_id'], 'observed_ts': benchmark['observed_ts'],
                'received_ts': benchmark['received_ts'], 'available_at': benchmark['received_ts'],
                'dependency_group': benchmark['dependency_group'], 'max_age_sec': benchmark['max_age_sec'],
                'source_clock_kind': benchmark['source_clock_kind'],
                'measurement_kind': benchmark['measurement_kind'], 'market_consensus_observed': False,
                'prepublication_expectation_reconstructed': False})}
    return result


def research_working_component(snapshot):
    """Fallback preference only; valid inputs never imply calibrated accuracy."""
    context = _valid_context(snapshot)
    if context is None:
        return None
    rules = context['rules']
    direction = (snapshot.get('strategy') or {}).get('direction')
    sign = 1 if direction in ('long','buy') else -1 if direction in ('short','sell') else None
    if sign is None:
        return None
    affinity = sign*sum(row['affinity'] for row in rules)/len(rules)
    if abs(affinity) < 1e-12:
        return None
    return {'component_id': 'mathematical_edge', 'available': True, 'quality': POLICY_FACTOR,
        'quality_basis': 'MANUAL_PRELIMINARY_POLICY_FACTOR_NOT_CALIBRATED_ACCURACY',
        'observed_ts': context['observed_ts'], 'max_age_sec': 900., 'instrument': _code(snapshot),
        'scores': {'HOLD': 0., **{p:-f*affinity for p,f in
                    {'CLOSE_10':.1,'CLOSE_25':.25,'CLOSE_50':.5,'EXIT':1.}.items()}},
        'source_ids': context['source_ids'], 'evidence_family_ids': ['price_path'],
        'model_version': VERSION, 'reason': 'PRELIMINARY_SOURCE_BOUND_RESEARCH_RULES',
        'assessment_kind': 'MANUAL_RESEARCH_RULE_PREFERENCE_NOT_TRAINED_FORECAST',
        'historical_profit_proven': False, 'research_rules': rules,
        'research_alternatives': context['alternatives']}


def prompt_research_context(snapshot):
    """Preserve input roles, not another expert's preference before LLM votes."""
    context = _valid_context(snapshot)
    if context is None:
        return None
    return {k:v for k,v in context.items() if k not in {'rules','context_sha256'}} | {
        'snapshot_context_sha256': context['context_sha256'],
        'rules': [{k:v for k,v in row.items() if k!='affinity'} for row in context['rules']]}


def research_working_lines(context):
    if not isinstance(context, dict) or not context.get('alternatives'):
        return []
    alternative = context['alternatives']
    book = alternative.get('order_book') or {}
    consensus = alternative.get('consensus') or {}
    swap = alternative.get('broker_swap') or {}
    return [
        'Приближения входов: стакан — ' + ('давление по свечам, не наблюдаемый стакан/OFI' if book.get('available') else 'нет пригодных свечных данных') + '.',
        'Consensus — ' + ('разница с предыдущим значением публикации; не ожидание рынка до новости' if consensus.get('available') else 'нет подтверждённого предыдущего значения; ожидание рынка не восстановлено') + '.',
        'Своп брокера — ' + ('сценарии годовой ставки 0/5/15% и списания за 1/3 дня; это допущения, не тариф и не предельная комиссия' if swap.get('available') else 'приближение недоступно') + '; проверенная экономика не перезаписана.']
