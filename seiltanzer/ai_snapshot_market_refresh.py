"""Refresh cached comparison geometry with actual quotes, without the heavy policy search."""
from __future__ import annotations

from copy import deepcopy
import time

from .position_state import StaleDecisionError
from .unified_edge_ensemble import BASE, number, _manual_candidate_preflight


def _materialized(snapshot):
    return (snapshot.get('materialization') or {}).get('deterministic_snapshot') is True


def _live_copy(engine, snapshot):
    tick = engine.canonical_tick_payload()
    trade = tick.get('trade') or {}
    code = (snapshot.get('strategy') or {}).get('instrument')
    if (str(trade.get('id')) != str(snapshot.get('trade_id'))
            or tick.get('instrument') != code or trade.get('instrument') != code):
        raise StaleDecisionError('live trade/instrument changed before comparison')
    out = deepcopy(snapshot)
    out['captured_ts'] = time.time()
    manager = out['policy_manager']
    from .ai_policy_v6 import _input_audit
    from .ai_policy_v7 import _enrich_input_audit
    current_audit = _input_audit(tick, {})
    manager['input_audit'] = current_audit
    _enrich_input_audit(manager, tick, {})
    quote = (tick.get('feeds') or {}).get('price') or {}
    row = current_audit['rows']['instrument_price']
    for key in ('production_authority', 'derived', 'is_proxy'):
        if key in quote:
            row[key] = quote[key]
    stamp = number(quote.get('ts') or quote.get('timestamp'))
    if stamp is None or not -5 <= out['captured_ts']-stamp <= 30:
        raise StaleDecisionError('fresh provider timestamp unavailable')
    row['timestamp'] = stamp
    geometry = out['trade_geometry']
    for name, field in (('entry', 'entry'), ('original_stop', 'stop')):
        if number(trade.get(field)) != number(geometry.get(name)):
            raise StaleDecisionError('live position geometry changed before comparison')
    geometry['current'] = number(quote.get('value'))
    entry, original, current = (number(geometry.get(k)) for k in ('entry', 'original_stop', 'current'))
    if entry is None or original is None or current is None or entry == original:
        raise StaleDecisionError('live risk geometry unavailable')
    geometry['current_r'] = (current-entry)/(entry-original)
    from .ai_report_semantics_guard import repair_snapshot_geometry, execution_barrier_reached
    repair_snapshot_geometry(out)
    if execution_barrier_reached(out) is not None:
        raise StaleDecisionError('live execution barrier reached before comparison')
    checked = deepcopy(out)
    checked_row = checked['policy_manager']['input_audit']['rows']['instrument_price']
    if checked_row.get('production_authority') is True:
        checked_row.pop('production_authority')
    if not _manual_candidate_preflight(checked):
        raise StaleDecisionError('fresh authoritative live quote/geometry unavailable')
    return out, tick


def refresh_frozen_comparison(engine, snapshot):
    """Reprice base controls once on the existing bounded comparison engine.

    Actual provider time is retained. No shifted empirical bank, fabricated
    source freshness, feed refresh, provider call or heavy legacy search.
    Extended parameters and all candidates are subsequently evaluated by the
    existing API/ensemble path using these fresh inputs and the same seeds.
    """
    if not _materialized(snapshot):
        return snapshot
    manager = snapshot.get('policy_manager') or {}
    if (manager.get('unified_scenario_bank') is not None
            or snapshot.get('unified_scenario_bank') is not None
            or manager.get('scenario_distribution_kind') in
               {'empirical', 'weighted_empirical', 'historical', 'weighted_paths'}):
        raise StaleDecisionError('explicit empirical bank requires a new frozen build')
    out, tick = _live_copy(engine, snapshot)
    from .ai_policy_base import extract_policy_inputs
    inputs = extract_policy_inputs(tick)
    out['policy_manager']['inputs'] = inputs.as_dict()
    # Retain full-precision machine coordinates, never round into a barrier.
    out['policy_manager']['inputs']['r0'] = out['trade_geometry']['current_r']
    geometry = out['trade_geometry']
    risk_unit = geometry['entry']-geometry['original_stop']
    out['policy_manager']['inputs']['stop_r'] = (geometry['active_risk_barrier']-geometry['entry'])/risk_unit
    out['policy_manager']['inputs']['T'] = (geometry['final_take']-geometry['entry'])/risk_unit
    out['policy_manager']['inputs']['be_execution_confirmed'] = (out.get('position_state') or {}).get('be_execution_confirmed')
    from .unified_candidate_economics import price_unified_candidates
    rows = [dict(policy=policy, candidate_id=policy, parameters={}) for policy in BASE]
    common = price_unified_candidates(out, rows)
    if common.get('available') is not True:
        raise StaleDecisionError('fresh common economics unavailable: '+str(common.get('reason')))
    manager = out['policy_manager']
    policies = {}
    eligible = []
    for policy in BASE:
        row = common['candidates'][policy]
        if not row.get('available'):
            raise StaleDecisionError('fresh base comparison unavailable')
        policies[policy] = dict(expected_final_r=row['expected_net_r'],
            expected_final_r_net=row['expected_net_r'], cvar10_r=row['cvar10_net_r'],
            cvar10_r_net=row['cvar10_net_r'], gross_expected_final_r=row['expected_gross_r'],
            execution_cost_r=row['execution_cost_r'], outcomes_include_execution_costs=True,
            economics_basis=row['economics_basis'])
        if row.get('hard_risk_pass'):
            eligible.append(policy)
    manager['policies'] = policies
    integrity = out.get('report_integrity')
    if isinstance(integrity, dict):
        integrity['policies'] = deepcopy(policies)
        integrity['trade_geometry'] = deepcopy(out['trade_geometry'])
    manager['selection_rule'] = {**(manager.get('selection_rule') or {}), 'eligible': eligible,
        'best_expected_r': max((policies[k]['expected_final_r_net'] for k in eligible), default=None)}
    from .ai_policy_v6 import _chain_refresh
    manager['recalculation_triggers'] = {
        **(manager.get('recalculation_triggers') or {}),
        'current_r': geometry['current_r'],
        'minus_0_15_r': {'r': geometry['current_r']-.15, 'price': geometry['current']-.15*risk_unit},
        'plus_0_15_r': {'r': geometry['current_r']+.15, 'price': geometry['current']+.15*risk_unit},
        'chain_refresh': {**_chain_refresh(engine, tick), 'basis': 'current_quote_refresh'},
    }
    # Match the native v18 snapshot contract: a future polling appointment is
    # not an observed source timestamp. Keep its display strings/countdown.
    manager['recalculation_triggers']['chain_refresh'].pop('next_attempt_ts', None)
    # These event probabilities/geometry belong to the old heavy bridge build,
    # not the freshly priced comparison. Missing current statistics stay null.
    for key in ('take_first', 'stop_or_be_first', 'no_touch', 'p50_resolution_minutes'):
        geometry[key] = None
    manager['scenario_geometry'] = {}
    if isinstance(integrity, dict):
        integrity['scenario_geometry'] = {}
        integrity['trade_geometry'] = deepcopy(geometry)
    out['market_refresh'] = dict(version='cached-comparison-market-refresh-v1',
        available=True, original_snapshot_ts=snapshot['captured_ts'],
        heavy_policy_recomputed=False,
        actual_provider_timestamp=manager['input_audit']['rows']['instrument_price'].get('timestamp'),
        legacy_diagnostics_basis='original_cached_snapshot',
        common_path_count=common['bank']['path_count'] if 'bank' in common else common.get('path_count'))
    # Optional legacy diagnostics remain identified as original; fresh common
    # expected/CVaR is authoritative for the API selector and provider projection.
    return out


def assert_live_review_compatible(engine, snapshot, *, candidate=None, components=None):
    """Recheck actual quote/barriers after provider latency, before registration."""
    if not _materialized(snapshot):
        return
    live, _ = _live_copy(engine, snapshot)
    geometry = snapshot['trade_geometry']
    original_r = number(geometry.get('current_r'))
    live_r = number(live['trade_geometry'].get('current_r'))
    if original_r is None or live_r is None or abs(live_r-original_r) >= .15-1e-9:
        raise StaleDecisionError('live price moved beyond frozen comparison review range')
    for component in components or []:
        if (component.get('component_id') == 'quantitative_base'
                or (number(component.get('effective_weight')) or 0.) <= 0):
            continue
        observed, maximum = number(component.get('observed_ts')), number(component.get('max_age_sec'))
        if observed is None or maximum is None or live['captured_ts'] >= observed+maximum:
            raise StaleDecisionError('expert forecast expired before publication')
    if candidate is not None and candidate.get('policy') not in BASE:
        from .ai_policy_base import PolicyInputs
        from .execution_simulator import ExecutionSpec
        from .unified_candidate_economics import _variant
        from dataclasses import replace
        try:
            data = (snapshot.get('policy_manager') or {}).get('inputs') or {}
            inputs = replace(PolicyInputs(**{**data, 'rungs': tuple(data['rungs'])}), r0=live_r)
            base = ExecutionSpec.from_values(current_r=live_r, max_r=inputs.max_r,
                take_r=inputs.T, rungs=inputs.rungs, rung_fraction_original=inputs.rung_fraction,
                be_after_r=inputs.be_after, stop_r=inputs.stop_r,
                be_execution_confirmed=inputs.be_execution_confirmed)
            _variant(base, candidate, live, inputs)
        except (KeyError, TypeError, ValueError) as exc:
            raise StaleDecisionError('selected parameters no longer applicable') from exc
