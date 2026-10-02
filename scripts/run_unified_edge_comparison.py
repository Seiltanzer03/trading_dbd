#!/usr/bin/env python3
"""Compare schemes on identical stored inputs without provider calls or DB writes."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from seiltanzer.ai_policy_base import PolicyInputs
from seiltanzer.decision_research import validate_no_future_timestamps
from seiltanzer.execution_simulator import ExecutionSpec, replay_execution_path
from seiltanzer.rollover_economics import frozen_rollover_schedule, replay_rollover_cost
from seiltanzer.unified_candidate_economics import BASE_FRACTIONS, _variant, _weighted_cvar
from seiltanzer.unified_edge_ensemble import build_unified_ensemble, candidate_id, number


SCHEMES = ('balanced', 'llm20', 'quant100', 'legacy_control')


def unavailable(reason):
    return {'available': False, 'reason': reason, 'net_r_on_remaining': None,
            'net_r_on_original_position': None}


def observed_replay(snapshot, record, candidate):
    """Held-out observations enter here only after the frozen choice is made."""
    if not candidate:
        return unavailable('SELECTED_CANDIDATE_UNAVAILABLE')
    manager = snapshot.get('policy_manager') or {}
    data = manager.get('inputs') or {}
    try:
        inputs = PolicyInputs(**{**data, 'rungs': tuple(data['rungs'])})
    except (KeyError, TypeError, ValueError):
        return unavailable('FROZEN_EXECUTION_INPUTS_UNAVAILABLE')
    costs = manager.get('execution_cost_model') or (manager.get('selection_rule') or {}).get('execution_cost_model') or {}
    immediate, deferred = (number(costs.get('immediate_full_close_r')),
                           number(costs.get('deferred_full_close_r')))
    if immediate is None or deferred is None or min(immediate, deferred) < 0:
        return unavailable('EXPLICIT_EXECUTION_COSTS_UNAVAILABLE')
    captured = number(snapshot.get('captured_ts'))
    horizon = number(inputs.horizon_minutes)
    if captured is None or horizon is None or horizon <= 0:
        return unavailable('FROZEN_HORIZON_UNAVAILABLE')
    try:
        rollover_schedule, rollover_audit = frozen_rollover_schedule(snapshot, horizon)
    except ValueError as exc:
        return unavailable(str(exc))
    points = []
    for row in record.get('path_points') or []:
        ts, r = number(row.get('ts')), number(row.get('r'))
        if ts is None or r is None or ts < captured:
            continue
        points.append((ts, r))
    points = sorted(dict(points).items())
    # Never create a synthetic initial observation to rescue absent data.
    if (not points or abs(points[0][0] - captured) > 1e-6
            or abs(points[0][1] - inputs.r0) > 1e-4):
        return unavailable('ACTUAL_CAPTURE_AND_FUTURE_PATH_POINTS_UNAVAILABLE')
    immediate_exit = candidate['policy'] == 'EXIT'
    if len(points) < 2 and not immediate_exit:
        return unavailable('ACTUAL_CAPTURE_AND_FUTURE_PATH_POINTS_UNAVAILABLE')
    endpoint = min(captured + 60. * horizon, points[-1][0])
    resolved = record.get('stored_replay') or {}
    full_horizon = points[-1][0] >= captured + 60. * horizon
    # Uniformization is piecewise-linear interpolation of actual observations,
    # not extra measured ticks. It makes TIME_STOP's time coordinate correct.
    times = sorted({captured, endpoint, *(ts for ts, _ in points if ts <= endpoint)})
    values = np.interp(times, [p[0] for p in points], [p[1] for p in points])
    base = ExecutionSpec.from_values(current_r=inputs.r0, max_r=inputs.max_r,
        take_r=inputs.T, rungs=inputs.rungs, rung_fraction_original=inputs.rung_fraction,
        be_after_r=inputs.be_after, stop_r=inputs.stop_r)
    # PolicyInputs serializes r0 to four decimals; retain the measured initial
    # R for observed execution instead of changing the stored path observation.
    base = replace(base, current_r=points[0][1])
    try:
        spec = _variant(base, candidate, snapshot, inputs)
    except (ValueError, TypeError, OverflowError) as exc:
        return unavailable(str(exc))
    observed_time_stop = False
    if candidate['policy'] == 'TIME_STOP':
        deadline = number((candidate.get('parameters') or {}).get('deadline_ts'))
        if deadline is None:
            return unavailable('TIME_STOP_DEADLINE_UNAVAILABLE')
        if deadline <= endpoint:
            keep = [i for i, ts in enumerate(times) if ts < deadline]
            values = np.asarray([*(values[i] for i in keep),
                                 np.interp(deadline, times, values)], dtype=float)
            times = [*(times[i] for i in keep), deadline]
            observed_time_stop = True
        spec = replace(spec, time_stop_fraction=None)
    try:
        result = replay_execution_path(values, spec)
    except (ValueError, TypeError, OverflowError) as exc:
        return unavailable('INVALID_OBSERVED_REPLAY:' + str(exc))
    terminal_in_prefix = result.exit_reason != 'horizon'
    if candidate['policy'] == 'TIME_STOP' and not observed_time_stop and not terminal_in_prefix:
        return unavailable('TIME_STOP_DEADLINE_BEYOND_OBSERVED_PATH')
    # Truncation is not permission to settle at the last retained price. A
    # real barrier resolution, observed deadline or immediate full EXIT needs
    # no later continuation. This applies equally to original/modified rules.
    if record.get('path_truncated') and not (full_horizon or terminal_in_prefix
                                             or observed_time_stop or immediate_exit):
        return unavailable('TRUNCATED_PREFIX_WITHOUT_TERMINAL_RESOLUTION')
    if not full_horizon and not resolved and not (terminal_in_prefix or observed_time_stop or immediate_exit):
        return unavailable('UNRESOLVED_INCOMPLETE_OBSERVED_PATH')
    # A modified TAKE or STOP that has not resolved cannot claim economics
    # beyond the real path retained after the original trade was closed.
    if candidate['policy'] not in BASE_FRACTIONS and not full_horizon and result.exit_reason == 'horizon' and not observed_time_stop:
        return unavailable('EXTENDED_COUNTERFACTUAL_CONTINUATION_UNOBSERVED')
    fraction = BASE_FRACTIONS.get(candidate['policy'], 0.)
    gross = fraction * points[0][1] + (1. - fraction) * result.outcome_r
    try:
        rollover_cost = ((1. - fraction) * replay_rollover_cost(result, times, rollover_schedule)
                         if fraction < 1. else 0.)
    except ValueError as exc:
        return unavailable(str(exc))
    cost = fraction * immediate + (1. - fraction) * deferred + rollover_cost
    net = gross - cost
    position = snapshot.get('position_state') or {}
    remaining, realized = (number(position.get('remaining_position_fraction')),
                           number(position.get('realized_r_weighted')))
    mixed_basis = (realized + remaining * net if remaining is not None and realized is not None
                   and 0 <= remaining <= 1 else None)
    # Existing position-state realized R is GROSS. A modeled future net result
    # cannot make historical fills net when their actual costs are unavailable.
    prior_costs = number(position.get('realized_costs_r_weighted'))
    prior_costs_status = position.get('realized_costs_status')
    prior_net = None
    if prior_costs_status == 'AVAILABLE' and prior_costs is not None and prior_costs >= 0 and realized is not None:
        prior_net = realized - prior_costs
    elif prior_costs_status == 'NOT_APPLICABLE' and remaining == 1. and realized == 0.:
        prior_net = 0.
    total_net = (prior_net + remaining * net if prior_net is not None
                 and remaining is not None and 0 <= remaining <= 1 else None)
    stored = None
    try:
        replay = json.loads(resolved.get('replay_json') or '{}')
        stored = number(((replay.get('policies') or {}).get(candidate['policy']) or {}).get('net_realized_r'))
    except (ValueError, TypeError):
        pass
    return {'available': True, 'reason': 'OBSERVED_PATH_COUNTERFACTUAL_WITH_FROZEN_COST_MODEL',
            'net_r_on_remaining': float(net), 'net_r_on_original_position': total_net,
            'original_position_net_basis': 'prior_net_plus_future_modeled_net' if total_net is not None else 'UNAVAILABLE_PRIOR_NET_COSTS_OR_EXPOSURE',
            'prior_realized_costs_status': prior_costs_status or 'UNAVAILABLE',
            'prior_gross_plus_future_modeled_net_r': mixed_basis,
            'gross_r_on_remaining': float(gross), 'execution_cost_r': cost,
            'broker_rollover_cost_r': rollover_cost if rollover_audit['available'] else None,
            'broker_rollover_audit': rollover_audit,
            'net_cost_scope': ('FROZEN_EXECUTION_COSTS_AND_VERIFIED_BROKER_ROLLOVER'
                               if rollover_audit['available'] else 'FROZEN_EXECUTION_COSTS_ONLY_ROLLOVER_UNAVAILABLE'),
            'costs_assumed': costs.get('assumed') is not False,
            'exit_reason': 'immediate_exit' if immediate_exit else 'time_stop' if observed_time_stop and result.exit_reason == 'horizon' else result.exit_reason,
            'path_truncated': bool(record.get('path_truncated')),
            'terminal_resolution_within_observed_prefix': terminal_in_prefix or observed_time_stop or immediate_exit,
            'stored_base_replay_reported_net_r': stored,
            'stored_base_replay_basis': 'legacy_prior_gross_plus_future_modeled_net_not_verified_total_net',
            'prior_gross_plus_future_modeled_net_difference_vs_stored_r': mixed_basis - stored if mixed_basis is not None and stored is not None else None,
            'point_count': len(points), 'maximum_observation_gap_sec': max((b[0] - a[0] for a, b in zip(points, points[1:])), default=0.),
            'observed_horizon_minutes': (endpoint - captured) / 60.,
            'full_forecast_horizon_observed': full_horizon,
            'resolution_kind': resolved.get('resolution_kind'),
            'execution_assumption': 'piecewise_linear_barrier_fill_no_slippage; no_tick_order_or_price_impact',
            'endpoint_semantics': 'immediate_capture_exit' if immediate_exit else 'observed_terminal_resolution' if terminal_in_prefix or observed_time_stop else 'forecast_horizon' if full_horizon else 'resolved_actual_trade_episode_endpoint',
            'ledger_settled_profit_available': False, 'causal_profit_claim': False}


def compare_review(record):
    raw = record['snapshot_json']
    digest = hashlib.sha256(raw.encode('utf8')).hexdigest()
    if digest != record.get('snapshot_sha256'):
        raise ValueError('FROZEN_SNAPSHOT_SHA256_MISMATCH')
    snapshot = json.loads(raw)
    captured = number(snapshot.get('captured_ts'))
    if captured != number(record.get('captured_ts')) or snapshot.get('trade_id') != record.get('trade_id'):
        raise ValueError('FROZEN_SNAPSHOT_IDENTITY_MISMATCH')
    if snapshot.get('demo'):
        raise ValueError('SYNTHETIC_DEMO_NOT_ACTUAL_REVIEW')
    validate_no_future_timestamps(snapshot, captured)
    llm = snapshot.get('llm_shadow_decision')
    # Old shadow contracts knew the quant decision. They are an anchored opinion,
    # never retroactively described as an independent experiment or fresh call.
    llm_contract = (llm.get('input_contract') or {}) if isinstance(llm, dict) else {}
    frozen_llm_input = llm_contract.get('frozen_input_json') if isinstance(llm_contract, dict) else None
    actual_llm_response = llm.get('provider_response_json') if isinstance(llm, dict) else None
    llm_response_received = number(llm.get('provider_response_received_ts')) if isinstance(llm, dict) else None
    independent_llm = (isinstance(llm_contract, dict)
                       and llm_contract.get('version') == 'independent-llm-transport-v1'
                       and llm_contract.get('quant_selection_masked') is True
                       and llm.get('selection_masked') is True
                       and number(llm.get('input_captured_ts')) == captured
                       and number(llm_contract.get('input_captured_ts')) == captured
                       and llm_response_received is not None
                       and captured <= llm_response_received <= time.time() + 1.
                       and isinstance(frozen_llm_input, str)
                       and hashlib.sha256(frozen_llm_input.encode()).hexdigest() == llm_contract.get('frozen_input_sha256')
                       and isinstance(actual_llm_response, str)
                       and hashlib.sha256(actual_llm_response.encode()).hexdigest() == llm.get('provider_response_sha256'))
    anchored = bool(isinstance(llm, dict) and not independent_llm)
    audit = build_unified_ensemble(snapshot, llm)
    by_id = {row['candidate_id']: row for row in audit['candidates']}
    schemes = {}
    for choice in audit['scheme_comparisons']:
        if choice['scheme'] == 'legacy_control':
            continue
        selected = by_id.get(choice['selected_candidate_id'])
        schemes[choice['scheme']] = {'selection': choice,
            'observed_path': observed_replay(snapshot, record, selected)}
    decision = snapshot.get('effective_management_decision') or (snapshot.get('policy_manager') or {}).get('management_decision') or {}
    legacy_policy = record.get('production_policy')
    parameters = decision.get('parameters') or {}
    legacy_id = candidate_id(legacy_policy, parameters) if legacy_policy not in BASE_FRACTIONS else legacy_policy
    legacy_row = by_id.get(legacy_id)
    if legacy_row is None and legacy_policy in BASE_FRACTIONS:
        legacy_row = {'candidate_id': legacy_policy, 'policy': legacy_policy, 'parameters': {}}
    schemes['legacy_control'] = {'selection': {
        'scheme': 'legacy_control', 'selected_policy': legacy_policy,
        'selected_candidate_id': legacy_id, 'intervention': legacy_policy != 'HOLD',
        'expected_net_r': number((legacy_row or {}).get('expected_net_r')),
        'cvar10_net_r': number((legacy_row or {}).get('cvar10_net_r')),
        'evidence_type': 'STORED_PRODUCTION_CHOICE_REPRICED_IN_CURRENT_MODEL'},
        'observed_path': observed_replay(snapshot, record, legacy_row)}
    return {'review_id': record['review_id'], 'trade_id': record['trade_id'],
            'captured_ts': captured, 'instrument': audit['instrument'],
            'snapshot_sha256': digest, 'same_frozen_inputs_all_schemes': True,
            'llm_source': 'stored_actual_shadow_only' if isinstance(llm, dict) else 'unavailable',
            'legacy_llm_quant_anchoring': anchored,
            'llm_independent_input_contract': independent_llm,
            'legacy_llm_warning': 'stored opinion lacks verified selection-masked independent input contract; not independent fresh 15% LLM evidence' if anchored else None,
            'components': audit['components'], 'edge_families': audit['edge_families'],
            'scenario_bank': audit['scenario_bank'], 'schemes': schemes,
            'observed_hold_control': observed_replay(snapshot, record,
                {'candidate_id': 'HOLD', 'policy': 'HOLD', 'parameters': {}}),
            'counterfactuals': audit['counterfactuals']}


def _average(values):
    values = [number(v) for v in values]
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def summarize(reviews):
    # Use the identical cohort for every observed scheme comparison. Overlapping
    # reviews of one trade are explicitly not independent portfolio returns.
    shared = set.intersection(*(set(row['schemes']) for row in reviews)) if reviews else set()
    names = sorted((set(SCHEMES) | shared) - {'legacy_control'})
    paired = [r for r in reviews if r['observed_hold_control'].get('available')
              and all((r['schemes'][s]['observed_path']).get('available') for s in names)]
    result = {}
    for scheme in names:
        selected = [r['schemes'][scheme]['selection'] for r in reviews]
        paths = [r['schemes'][scheme]['observed_path'] for r in paired]
        net = [p['net_r_on_remaining'] for p in paths]
        deltas = [r['schemes'][scheme]['observed_path']['net_r_on_remaining']
                  - r['schemes']['quant100']['observed_path']['net_r_on_remaining'] for r in paired]
        # Useless intervention means no net benefit against quant100 on the same
        # held-out episode; its denominator contains only actual interventions.
        interventions = [r for r in paired if r['schemes'][scheme]['selection'].get('intervention')]
        useless = sum(r['schemes'][scheme]['observed_path']['net_r_on_remaining'] <=
                      r['schemes']['quant100']['observed_path']['net_r_on_remaining'] + 1e-8 for r in interventions)
        no_gain_vs_hold = sum(r['schemes'][scheme]['observed_path']['net_r_on_remaining'] <=
                             r['observed_hold_control']['net_r_on_remaining'] + 1e-8 for r in interventions)
        weights = np.full(len(net), 1. / len(net)) if net else None
        result[scheme] = {
            'model_scenarios': {'review_n': len(selected),
                'mean_selected_expected_net_r': _average(s.get('expected_net_r') for s in selected),
                'mean_selected_cvar10_net_r': _average(s.get('cvar10_net_r') for s in selected),
                'intervention_frequency': _average(float(s['intervention']) for s in selected if s.get('selected_policy')),
                'evidence_type': 'MODEL_SCENARIOS_NOT_HISTORICAL_PROFIT'},
            'observed_path_replay': {'available': bool(paths), 'paired_review_n': len(paths),
                'paired_distinct_trade_n': len({r['trade_id'] for r in paired}),
                'paired_verified_broker_rollover_quote_n': sum((p.get('broker_rollover_audit') or {}).get('available') is True for p in paths),
                'paired_broker_rollover_quote_unavailable_n': sum((p.get('broker_rollover_audit') or {}).get('available') is not True for p in paths),
                'net_cost_scope': 'frozen_execution_cost_estimates; broker_rollover_only_where_verified; not_settled_all_cost_ledger_net',
                'mean_net_r_on_remaining': _average(net),
                'descriptive_cvar10_net_r_on_remaining': _weighted_cvar(np.asarray(net), weights) if net else None,
                'mean_paired_delta_vs_quant100_r': _average(deltas),
                'intervention_n': len(interventions),
                'unhelpful_intervention_frequency_vs_quant100': useless / len(interventions) if interventions else None,
                'no_net_gain_intervention_frequency_vs_hold': no_gain_vs_hold / len(interventions) if interventions else None,
                'intervention_usefulness_scope': 'net_episode_gain_only; risk_reduction_not_equivalent_to_useless',
                'inference': 'bounded_review_sample; overlapping_trades; no_independent_statistical_or_causal_profit_claim'},
            'historical_economic_completeness': {'available': False,
                'reason': 'SETTLED_EXECUTION_LEDGER_FEES_SLIPPAGE_AND_COUNTERFACTUAL_CONTINUATION_NOT_EXPORTED',
                'ledger_profit_net_r': None}}
    return result


def summarize_acknowledgements(source):
    """Actual receipt observations, never inferred fills or latency costs."""
    cutoff = number(source.get('exported_ts'))
    rows, seen = [], set()
    supplied = source.get('execution_ack_observations')
    for row in supplied[:4096] if isinstance(supplied, list) else []:
        if not isinstance(row, dict):
            continue
        timestamp = number(row.get('acknowledged_ts'))
        identity = row.get('decision_id')
        stage = row.get('stage')
        key = (identity, stage)
        if (not isinstance(identity, str) or not identity or not isinstance(stage, str)
                or stage not in {'armed', 'executed', 'cancelled', 'declined'}
                or cutoff is None or timestamp is None or not 0 < timestamp <= cutoff or key in seen):
            continue
        seen.add(key)
        rows.append(row)
    delays = [number(row.get('acknowledgement_delay_sec')) for row in rows]
    delays = [value for value in delays if value is not None and value >= 0]
    stages = {stage: sum(row['stage'] == stage for row in rows) for stage in sorted({row['stage'] for row in rows})}
    return {'available': bool(rows), 'observation_n': len(rows),
            'distinct_decision_n': len({row['decision_id'] for row in rows}), 'stages': stages,
            'mean_acknowledgement_delay_sec': _average(delays),
            'maximum_acknowledgement_delay_sec': max(delays) if delays else None,
            'evidence_type': 'ACTUAL_USER_ACK_RECEIPTS_NOT_MODEL_SCENARIOS_OR_BROKER_FILL_TIMES',
            'actual_broker_fill_time_verified_n': 0, 'manual_latency_cost_r': None,
            'profit_or_effectiveness_proven': False}


def run_comparison(source, *, expected_sha=None):
    if source.get('read_only') is not True or len(source.get('reviews', [])) > 32:
        raise ValueError('requires a bounded read-only actual-review export')
    reviews, rejected = [], []
    for record in source.get('reviews') or []:
        try:
            reviews.append(compare_review(record))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            rejected.append({'review_id': record.get('review_id'), 'reason': str(exc)[:300]})
    instruments = sorted({r['instrument'] for r in reviews if r.get('instrument')})
    from seiltanzer.config import ALL_INSTRUMENTS
    return {'version': 'unified-edge-actual-review-comparison-v1', 'created_ts': time.time(),
            'expected_sha': expected_sha, 'source_exported_ts': source.get('exported_ts'),
            'paid_llm_calls': 0, 'production_or_database_writes': 0,
            'historical_profit_proven': False, 'available': bool(reviews),
            'review_n': len(reviews), 'reviews': reviews, 'rejected': rejected,
            'instrument_coverage': instruments,
            'unobserved_configured_instruments': sorted(set(ALL_INSTRUMENTS) - set(instruments)),
            'summary': summarize(reviews),
            'actual_execution_observations': summarize_acknowledgements(source),
            'promotion_authorized': False,
            'reason': 'BOUNDED_REAL_REVIEW_DESCRIPTIVE_COMPARISON' if reviews else 'ACTUAL_REVIEWS_UNAVAILABLE_OR_REJECTED'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reviews', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--expected-sha')
    args = parser.parse_args()
    report = run_comparison(json.loads(Path(args.reviews).read_text()), expected_sha=args.expected_sha)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))
    print(json.dumps({'review_n': report['review_n'], 'rejected_n': len(report['rejected']),
                      'historical_profit_proven': False, 'paid_llm_calls': 0}))


if __name__ == '__main__':
    main()
