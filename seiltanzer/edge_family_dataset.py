"""Causal frozen features and separately observed path-counterfactual labels.

This module never obtains sources or trains models. Its geometry helper has
only lightweight, lazy runtime dependencies; off-host replay is imported only
by the dataset builder. A verified cost quote remains an execution assumption,
not an observed fill or settled profit.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re

VERSION = 'edge-family-dataset-v1'
GEOMETRY_VERSION = 'edge-family-execution-geometry-v1'
MAX_EPISODES = 512
MAX_ARCHIVE_BYTES = 96 * 1024 * 1024
INPUT_KEYS = {'r0', 'T', 'sigma_R', 'drift_R', 'skew_R', 'term_slope',
              'horizon_minutes', 'max_r', 'rungs', 'rung_fraction', 'be_after',
              'option_available', 'chain_age_sec', 'chain_status', 'proxy_quality',
              'source', 'stop_r'}
EXECUTION_KEYS = ('r0', 'T', 'horizon_minutes', 'max_r', 'rung_fraction', 'be_after', 'stop_r')
GEOMETRY_KEYS = {'current', 'entry', 'original_stop', 'active_risk_barrier',
    'active_risk_barrier_type', 'final_take', 'current_r', 'r_to_active_stop',
    'r_to_final_take', 'remaining_position_fraction', 'realized_position_fraction',
    'probability_measure', 'take_first', 'stop_or_be_first', 'no_touch',
    'p50_resolution_minutes', 'p50_status', 'active_risk_barrier_breached', 'direction'}
PARAMETERS = {
    'MOVE_TO_BE': {'stop_price', 'anchor'},
    'TIGHTEN_STOP': {'stop_price', 'anchor'},
    'TRAIL_GAMMA_FLIP': {'stop_price', 'anchor', 'trailing_mode'},
    'REDUCE_TAKE': {'take_price', 'target_r'},
    'EXTEND_TAKE': {'take_price', 'anchor'},
    'SCALE_OUT_ON_SPIKE': {'trigger_price', 'close_fraction', 'target_r'},
    'TIME_STOP': {'deadline_ts', 'timeout_minutes'},
}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _synthetic(value):
    return any(_dict(value).get(key) for key in
               ('demo', 'synthetic', 'is_demo', 'synthetic_demo', 'contract_fixture'))


def _source_declaration_reason(value, horizon, *, supporting=False):
    """Check original feature evidence before adapters normalize its metadata.

    Recursion is restricted to a source and its supporting records. It must
    never inspect modeled scenario banks or removed learned artifacts.
    """
    if isinstance(value, dict):
        if _synthetic(value):
            return 'SYNTHETIC_FEATURE_SOURCE_EXCLUDED'
        if value.get('context_only'):
            return 'CONTEXT_ONLY_FEATURE_SOURCE_EXCLUDED'
        if value.get('horizon_minutes') is not None and _number(value['horizon_minutes']) != horizon:
            return 'FEATURE_SOURCE_HORIZON_MISMATCH'
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    if supporting:
        for child in children:
            reason = _source_declaration_reason(child, horizon, supporting=True)
            if reason:
                return reason
    return None


def _feature_snapshot(snapshot, horizon, review_id, exclusions):
    """Copy and filter original feature-producing paths, preserving rejections."""
    frozen = deepcopy(snapshot)

    def admitted(value, path, *, supporting=False):
        reason = _source_declaration_reason(value, horizon, supporting=supporting)
        if reason:
            exclusions.append({'review_id': review_id, 'source_path': path, 'reason': reason})
        return not reason

    sources = _dict(frozen.get('edge_family_sources'))
    if not admitted(sources, 'edge_family_sources'):
        frozen['edge_family_sources'] = {}
    else:
        for family, values in sources.items():
            values = values if isinstance(values, list) else [values]
            sources[family] = [source for index, source in enumerate(values)
                if isinstance(source, dict) and admitted(source,
                    f'edge_family_sources.{family}.{index}', supporting=True)]
    for key in ('macro_context_v1', 'macro_t0_context'):
        root = _dict(frozen.get(key))
        if not admitted(root, key):
            frozen[key] = {}
            continue
        numeric = _dict(root.get('numeric_macro'))
        if (not admitted(numeric, key + '.numeric_macro')
                or not admitted(numeric.get('candidate_vector'), key + '.numeric_macro.candidate_vector', supporting=True)
                or not admitted(numeric.get('releases'), key + '.numeric_macro.releases')):
            root['numeric_macro'] = {}
        else:
            releases = _dict(numeric.get('releases'))
            numeric['releases'] = {family: source for family, source in releases.items()
                if admitted(source, key + '.numeric_macro.releases.' + str(family), supporting=True)}
        for source_key in ('fomc', 'fomc_deterministic'):
            if not admitted(root.get(source_key), key + '.' + source_key, supporting=True):
                root.pop(source_key, None)
    return frozen


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf8')


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(value, length):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def family_geometry_sha256(snapshot: dict) -> str:
    """Hash exact execution geometry, with TIME_STOP expressed relative to T0.

    Source clocks and option distribution drivers do not define execution
    geometry. Unknown execution fields fail closed so a future rule cannot be
    silently grouped with today's action outcomes. Absolute price levels are
    retained; this first producer intentionally uses narrow exact cohorts.
    """
    from .canonical_market_context import canonical_instrument_code
    from .unified_edge_ensemble import BASE, collect_candidates

    snapshot = _dict(snapshot)
    manager = _dict(snapshot.get('policy_manager'))
    inputs = _dict(manager.get('inputs'))
    geometry = _dict(snapshot.get('trade_geometry'))
    position = _dict(snapshot.get('position_state'))
    if set(inputs) - INPUT_KEYS or set(geometry) - GEOMETRY_KEYS:
        raise ValueError('UNKNOWN_EXECUTION_GEOMETRY_FIELD')
    execution = {key: _number(inputs.get(key)) for key in EXECUTION_KEYS}
    rungs = inputs.get('rungs')
    remaining = _number(position.get('remaining_position_fraction'))
    prices = {key: _number(geometry.get(key)) for key in
              ('entry', 'original_stop', 'active_risk_barrier', 'current', 'final_take')}
    if (any(value is None for value in execution.values())
            or not isinstance(rungs, list) or len(rungs) > 64
            or any(_number(value) is None for value in rungs)
            or remaining is None or not 0 < remaining <= 1
            or any(value is None for value in prices.values())
            or prices['entry'] == prices['original_stop']
            or execution['horizon_minutes'] <= 0):
        raise ValueError('EXECUTION_GEOMETRY_INCOMPLETE')
    if position.get('armed_conditional_actions'):
        raise ValueError('EXISTING_CONDITIONAL_EXECUTION_NOT_REPLAY_SUPPORTED')
    if (position.get('strategy_terminal_event') or position.get('terminal_event')
            or (position.get('trade_id') is not None
                and position['trade_id'] != snapshot.get('trade_id'))):
        raise ValueError('FROZEN_POSITION_IDENTITY_OR_TERMINAL_STATE_INVALID')
    direction = _dict(snapshot.get('strategy')).get('direction', geometry.get('direction'))
    risk = prices['entry'] - prices['original_stop']
    if (direction not in {'long', 'short'} or (risk > 0) != (direction == 'long')
            or not execution['stop_r'] < execution['r0'] < execution['T']
            or execution['max_r'] < execution['r0'] - 1e-4
            or not 0 <= execution['rung_fraction'] <= 1):
        raise ValueError('FROZEN_EXECUTION_DIRECTION_OR_RANGE_INVALID')
    for price_key, input_key in (('current', 'r0'), ('active_risk_barrier', 'stop_r'),
                                  ('final_take', 'T')):
        if abs((prices[price_key] - prices['entry']) / risk - execution[input_key]) > 1e-4:
            raise ValueError('FROZEN_PRICE_AND_EXECUTION_R_MISMATCH')
    for position_key, price_key in (('original_stop', 'original_stop'),
                                    ('active_stop_price', 'active_risk_barrier'), ('take', 'final_take')):
        if position_key in position and _number(position[position_key]) != prices[price_key]:
            raise ValueError('FROZEN_POSITION_AND_PRICE_GEOMETRY_MISMATCH')
    if ('realized_position_fraction' in position and
            (_number(position['realized_position_fraction']) is None
             or abs(_number(position['realized_position_fraction']) + remaining - 1.) > 1e-10)):
        raise ValueError('FROZEN_POSITION_EXPOSURE_MISMATCH')
    policies = _dict(manager.get('policies'))
    if any(not isinstance(row, dict) for row in policies.values()):
        raise ValueError('FROZEN_POLICY_GEOMETRY_INVALID')
    for container, key in ((manager, 'selection_rule'), (manager, 'risk_constraint'),
                           (manager, 'gate'), (_dict(manager.get('gate')), 'degraded_authority_overlay'),
                           (manager, 'input_audit'), (_dict(manager.get('input_audit')), 'rows'),
                           (_dict(_dict(manager.get('input_audit')).get('rows')), 'instrument_price')):
        if container.get(key) is not None and not isinstance(container[key], dict):
            raise ValueError('FROZEN_POLICY_GEOMETRY_INVALID')
    execution['rungs'] = [_number(value) for value in rungs]
    active = snapshot.get('active_management_candidates') or []
    if not isinstance(active, list) or len(active) > 128:
        raise ValueError('CANDIDATE_GEOMETRY_EXCEEDS_BOUND')
    for candidate in active:
        if not isinstance(candidate, dict) or candidate.get('policy') not in {*BASE, *PARAMETERS}:
            raise ValueError('UNKNOWN_CANDIDATE_GEOMETRY')
        parameters = candidate.get('parameters') or {}
        if (not isinstance(parameters, dict)
                or set(parameters) - PARAMETERS.get(candidate['policy'], set())):
            raise ValueError('UNKNOWN_CANDIDATE_PARAMETER')
    matrix = []
    for candidate in collect_candidates(snapshot):
        policy, params = candidate['policy'], deepcopy(candidate['parameters'])
        if policy not in BASE and not params:
            continue
        if policy == 'TIME_STOP':
            deadline, captured = _number(params.get('deadline_ts')), _number(snapshot.get('captured_ts'))
            if deadline is None or captured is None:
                raise ValueError('TIME_STOP_DEADLINE_UNAVAILABLE')
            params['deadline_offset_sec'] = deadline - captured
            del params['deadline_ts']
        matrix.append({'policy': policy, 'parameters': params})
    matrix.sort(key=lambda row: _canonical(row))
    exposure = {key: position[key] for key in ('remaining_position_fraction',
        'realized_position_fraction', 'realized_r_weighted', 'active_stop_price',
        'active_stop_type', 'be_armed', 'original_stop', 'original_take', 'take') if key in position}
    return _hash({'version': GEOMETRY_VERSION,
        'instrument': canonical_instrument_code(_dict(snapshot.get('strategy')).get('instrument')
                                                or snapshot.get('instrument')),
        'direction': direction,
        'execution_inputs': execution, 'prices': prices,
        'active_risk_barrier_type': geometry.get('active_risk_barrier_type'),
        'exposure': exposure, 'candidate_matrix': matrix})


def _verified_costs(snapshot, horizon):
    """Recheck the normalized pinned loader evidence and actual admitted model."""
    from .execution_cost_context import COMPONENTS, VERSION as COST_VERSION
    from .rollover_economics import frozen_rollover_schedule
    from .canonical_market_context import canonical_instrument_code

    audit = _dict(snapshot.get('execution_cost_context_audit'))
    manager = _dict(snapshot.get('policy_manager'))
    model = _dict(manager.get('execution_cost_model')
                  or _dict(manager.get('selection_rule')).get('execution_cost_model'))
    identity, units = _dict(snapshot.get('trade_identity')), _dict(snapshot.get('position_execution_units'))
    cutoff = _number(snapshot.get('captured_ts'))
    instrument = canonical_instrument_code(_dict(snapshot.get('strategy')).get('instrument') or snapshot.get('instrument'))
    direction = _dict(snapshot.get('strategy')).get('direction')
    # Identity/units must come from a separate position observation, never
    # copied out of the cost document being checked against them.
    proof = _dict(identity.get('position_evidence'))
    observed_position, received_position, position_age = (_number(proof.get(key)) for key in
                                                         ('observed_ts', 'received_ts', 'max_age_sec'))
    if (_synthetic(proof) or _synthetic(identity) or _synthetic(units)
            or proof.get('version') != 'broker-position-context-v1'
            or proof.get('source_verified') is not True
            or proof.get('measurement_kind') != 'executing_broker_position'
            or not isinstance(proof.get('source_id'), str) or not proof['source_id']
            or not all(_sha(proof.get(key), length) for key, length in
                       (('evidence_sha256', 64), ('document_sha256', 64), ('deployment_sha', 40)))
            or any(value is None for value in (observed_position, received_position, position_age))
            or not 0 < observed_position <= received_position <= cutoff
            or not 0 < position_age <= 60 or cutoff - observed_position > position_age
            or identity.get('trade_id') != snapshot.get('trade_id')
            or identity.get('instrument') != instrument or identity.get('direction') != direction
            or not isinstance(identity.get('broker_position_id'), str) or not identity['broker_position_id']
            or any(_number(proof.get(key)) != _number(_dict(snapshot.get('trade_geometry')).get(key))
                   for key in ('entry', 'original_stop'))
            or _number(proof.get('remaining_position_fraction')) != _number(
                       _dict(snapshot.get('position_state')).get('remaining_position_fraction'))):
        raise ValueError('INDEPENDENT_EXECUTING_POSITION_EVIDENCE_REQUIRED')
    if (_synthetic(audit) or audit.get('version') != COST_VERSION or audit.get('available') is not True
            or audit.get('complete_costs_available') is not True or audit.get('assumed') is not False
            or audit.get('reason') != 'VERIFIED_COMPLETE_BROKER_COSTS'
            or not _sha(audit.get('document_sha256'), 64) or not _sha(audit.get('deployment_sha'), 40)
            or audit.get('source_verified') is not True
            or audit.get('trade_id') != snapshot.get('trade_id')
            or audit.get('instrument') != instrument or audit.get('direction') != direction
            or audit.get('deployment_sha') != proof['deployment_sha']
            or not all(isinstance(audit.get(key), str) and audit[key]
                       for key in ('source', 'broker_id', 'account_id', 'currency'))):
        raise ValueError('VERIFIED_COMPLETE_EXECUTION_COST_EVIDENCE_REQUIRED')
    observed, received, max_age = (_number(audit.get(key)) for key in ('observed_ts', 'received_ts', 'max_age_sec'))
    if (any(value is None for value in (observed, received, max_age))
            or not 0 < observed <= received <= cutoff or not 0 < max_age <= 86400
            or cutoff - observed > max_age or _number(audit.get('horizon_minutes')) != horizon):
        raise ValueError('EXECUTION_COST_CLOCK_OR_HORIZON_INVALID')
    start, end = (_number(audit.get(key)) for key in ('coverage_start_epoch', 'coverage_end_epoch'))
    if start is None or end is None or start > cutoff or end < cutoff + horizon * 60.:
        raise ValueError('EXECUTION_COST_HORIZON_COVERAGE_INCOMPLETE')
    if (identity.get('broker_id') != audit['broker_id'] or identity.get('account_id') != audit['account_id']
            or units.get('currency') != audit['currency']
            or audit.get('quantity_basis') != 'current_remaining_position'
            or units.get('quantity_basis') != 'current_remaining_position'
            or any(_number(audit.get(key)) is None or _number(audit[key]) <= 0
                   or _number(audit[key]) != _number(units.get(key))
                   for key in ('quantity_units', 'risk_currency_per_unit'))):
        raise ValueError('EXECUTION_COST_IDENTITY_OR_UNITS_MISMATCH')
    for channel in ('immediate', 'deferred'):
        record = _dict(_dict(audit.get('components')).get(channel))
        components = _dict(record.get('components_r'))
        values = [_number(components.get(name)) for name in COMPONENTS]
        total = _number(record.get('total_r'))
        if (set(components) != set(COMPONENTS) or any(value is None or value < 0 for value in values)
                or total is None or not math.isclose(sum(values), total, rel_tol=0, abs_tol=1e-12)
                or _number(audit.get(channel + '_full_close_r')) != total
                or _dict(audit.get('missing_components')).get(channel) != []):
            raise ValueError('EXECUTION_COST_COMPONENTS_OR_TOTAL_UNVERIFIED')
        component_provenance = _dict(record.get('component_provenance'))
        if set(component_provenance) != set(COMPONENTS):
            raise ValueError('EXECUTION_COST_COMPONENT_EVIDENCE_MISSING')
        for name in COMPONENTS:
            item = _dict(component_provenance[name])
            measurement, ts = _number(item.get('cost_currency_per_unit')), _number(item.get('observed_ts'))
            if (_synthetic(item) or measurement is None or measurement < 0 or ts is None or not 0 < ts <= observed
                    or cutoff - ts > max_age
                    or item.get('measurement_kind') not in {'executing_broker_quote', 'measured_broker_fill'}
                    or not isinstance(item.get('source_id'), str) or not item['source_id']
                    or not _sha(item.get('evidence_sha256'), 64)
                    or not math.isclose(measurement / audit['risk_currency_per_unit'], components[name],
                                        rel_tol=0, abs_tol=1e-12)):
                raise ValueError('EXECUTION_COST_COMPONENT_PROVENANCE_INVALID')
    # Replacing the actual replay model with an audit Boolean would be circular.
    for key in ('version', 'available', 'complete_costs_available', 'assumed', 'source',
                'broker_id', 'account_id', 'currency', 'quantity_basis', 'quantity_units',
                'risk_currency_per_unit', 'observed_ts', 'received_ts', 'max_age_sec',
                'horizon_minutes', 'document_sha256', 'deployment_sha', 'components',
                'immediate_full_close_r', 'deferred_full_close_r'):
        if model.get(key) != audit.get(key):
            raise ValueError('ADMITTED_EXECUTION_COST_MODEL_AUDIT_MISMATCH')
    for key in ('source_verified', 'trade_id', 'instrument', 'direction', 'coverage_start_epoch', 'coverage_end_epoch'):
        if model.get(key) != audit.get(key):
            raise ValueError('ADMITTED_EXECUTION_COST_MODEL_AUDIT_MISMATCH')
    _, rollover = frozen_rollover_schedule(snapshot, horizon)
    schedule = _dict(snapshot.get('broker_rollover_schedule'))
    if (_synthetic(schedule) or rollover.get('available') is not True or schedule.get('currency') != audit['currency']
            or _number(schedule.get('risk_currency_per_unit')) != _number(audit['risk_currency_per_unit'])
            or any(schedule.get(key) != audit.get(key) for key in
                   ('broker_id', 'account_id', 'trade_id', 'direction', 'quantity_basis'))
            or _number(schedule.get('quantity_units')) != _number(audit['quantity_units'])
            or schedule.get('included_in_base_costs') is not False):
        raise ValueError('VERIFIED_SEPARATE_BROKER_ROLLOVER_EVIDENCE_REQUIRED')
    provenance = deepcopy(audit)
    provenance.update(trade_id=snapshot.get('trade_id'),
        instrument=canonical_instrument_code(_dict(snapshot.get('strategy')).get('instrument') or snapshot.get('instrument')),
        direction=direction, position_evidence=deepcopy(proof),
        position_execution_units=deepcopy(units),
        broker_position_id=identity['broker_position_id'], broker_rollover_audit=rollover,
        broker_rollover_schedule=deepcopy(schedule),
        broker_rollover_schedule_sha256=_hash(schedule))
    return provenance


def _frozen_snapshot(record):
    from .canonical_market_context import canonical_instrument_code
    from .decision_research import validate_no_future_timestamps

    raw = record.get('snapshot_json')
    if (not isinstance(raw, str) or len(raw.encode('utf8')) > 2_000_000
            or hashlib.sha256(raw.encode('utf8')).hexdigest() != record.get('snapshot_sha256')):
        raise ValueError('FROZEN_SNAPSHOT_SHA256_OR_BYTE_BOUND_INVALID')
    snapshot = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_SNAPSHOT')))
    cutoff = _number(record.get('captured_ts'))
    if (not isinstance(snapshot, dict) or cutoff is None or cutoff <= 0
            or _number(snapshot.get('captured_ts')) != cutoff
            or snapshot.get('trade_id') != record.get('trade_id') or not record.get('review_id')):
        raise ValueError('FROZEN_SNAPSHOT_IDENTITY_MISMATCH')
    if any(snapshot.get(key) or record.get(key) for key in ('demo', 'synthetic', 'is_demo', 'synthetic_demo', 'contract_fixture')):
        raise ValueError('SYNTHETIC_REVIEW_EXCLUDED')
    # Learned model output is not feature evidence, nor a future source clock.
    snapshot.pop('edge_family_models', None)
    validate_no_future_timestamps(snapshot, cutoff, tolerance_sec=0.)
    instrument = canonical_instrument_code(_dict(snapshot.get('strategy')).get('instrument') or snapshot.get('instrument'))
    if _dict(snapshot.get('strategy')).get('direction') not in {'long', 'short'}:
        raise ValueError('FROZEN_TRADE_DIRECTION_UNAVAILABLE')
    if not instrument or (record.get('instrument') and canonical_instrument_code(record['instrument']) != instrument):
        raise ValueError('REVIEW_INSTRUMENT_MISMATCH')
    horizon = _number(_dict(_dict(snapshot.get('policy_manager')).get('inputs')).get('horizon_minutes'))
    if horizon is None or horizon <= 0:
        raise ValueError('FROZEN_HORIZON_UNAVAILABLE')
    if record.get('horizon_minutes') is not None and _number(record['horizon_minutes']) != horizon:
        raise ValueError('REVIEW_HORIZON_MISMATCH')
    queried = record.get('path_query_horizon_end_ts')
    if queried is not None and _number(queried) != cutoff + horizon * 60.:
        raise ValueError('RETAINED_PATH_HORIZON_MISMATCH')
    points = record.get('path_points')
    if not isinstance(points, list) or not 1 <= len(points) <= 6000:
        raise ValueError('RETAINED_OBSERVATION_PATH_UNAVAILABLE_OR_OVERSIZED')
    seen = set()
    geometry = _dict(snapshot.get('trade_geometry'))
    entry, stop = _number(geometry.get('entry')), _number(geometry.get('original_stop'))
    for point in points:
        ts, r = (_number(_dict(point).get(key)) for key in ('ts', 'r'))
        if ts is None or r is None or ts < cutoff or ts in seen:
            raise ValueError('RETAINED_OBSERVATION_PATH_INVALID')
        if point.get('instrument') and canonical_instrument_code(point['instrument']) != instrument:
            raise ValueError('RETAINED_OBSERVATION_INSTRUMENT_MISMATCH')
        if point.get('price') is not None:
            price = _number(point['price'])
            if (price is None or entry is None or stop is None or entry == stop
                    or abs((price - entry) / (entry - stop) - r) > 1e-4):
                raise ValueError('RETAINED_OBSERVATION_PRICE_AND_R_MISMATCH')
        seen.add(ts)
    return snapshot, instrument, cutoff, horizon


def build_family_dataset(archive: dict) -> dict:
    """Build bounded deterministic family rows, rejecting incomplete net labels."""
    from .edge_family_adapters import build_edge_family_evidence
    from .unified_edge_ensemble import collect_candidates
    from scripts.run_unified_edge_comparison import observed_replay

    rows, exclusions = [], []
    output = {'version': VERSION, 'rows': rows, 'exclusions': exclusions,
              'label_scope': 'OBSERVED_PATH_COUNTERFACTUAL_NOT_BROKER_FILL',
              'network_calls': False, 'runtime_fitting': False, 'historical_profit_proven': False}
    try:
        if _synthetic(archive):
            raise ValueError('SYNTHETIC_ARCHIVE_EXCLUDED')
        episodes = _dict(archive).get('episodes')
        if (_dict(archive).get('contract_version') != 'edge-family-archive-v1' or not isinstance(episodes, list)
                or len(episodes) > MAX_EPISODES or len(_canonical(episodes)) > MAX_ARCHIVE_BYTES
                or _hash(episodes) != archive.get('dataset_sha256')):
            raise ValueError('ARCHIVE_CONTRACT_BOUND_OR_SHA256_MISMATCH')
        identities = {}
        for episode in episodes:
            if not isinstance(episode, dict):
                raise ValueError('ARCHIVE_EPISODE_INVALID')
            identity = str(episode.get('review_id'))
            identities.setdefault(identity, set()).add(_hash(episode))
        seen = set()
        for record in sorted(episodes, key=lambda row: (_number(row.get('captured_ts')) or 0., str(row.get('review_id')))):
            review_id = str(record.get('review_id'))
            if review_id in seen:
                continue
            seen.add(review_id)
            try:
                if len(identities[review_id]) > 1:
                    raise ValueError('CONFLICTING_ARCHIVE_REVIEW_IDENTITY')
                snapshot, instrument, cutoff, horizon = _frozen_snapshot(record)
                geometry = family_geometry_sha256(snapshot)
                costs = _verified_costs(snapshot, horizon)
                # Original adapter clock/identity/mapping checks remain intact.
                feature_snapshot = _feature_snapshot(snapshot, horizon, review_id, exclusions)
                evidence = build_edge_family_evidence(feature_snapshot)
                hold = observed_replay(snapshot, record, {'policy': 'HOLD', 'parameters': {}})
                if (hold.get('available') is not True or hold.get('costs_assumed') is not False
                        or (not hold.get('full_forecast_horizon_observed') and hold.get('exit_reason') == 'horizon')):
                    raise ValueError('OBSERVED_HOLD_CONTINUATION_UNAVAILABLE')
                label_endpoint = cutoff + hold['observed_horizon_minutes'] * 60.
                # Interpolation consumes the first actual right-hand sample;
                # the label is unavailable for fitting until that sample exists.
                label_end = min(point['ts'] for point in record['path_points']
                                if point['ts'] >= label_endpoint)
                admitted = []
                for candidate in collect_candidates(snapshot):
                    if candidate['policy'] not in {'HOLD', 'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT'} and not candidate['parameters']:
                        continue
                    # Absolute TIME_STOP candidate IDs cannot currently bind a
                    # relative-time trained action to a later runtime review.
                    if candidate['policy'] == 'TIME_STOP':
                        exclusions.append({'review_id': review_id, 'action': candidate['candidate_id'],
                                           'reason': 'TIME_STOP_RUNTIME_ACTION_ID_NOT_TIME_INVARIANT'})
                        continue
                    replay = observed_replay(snapshot, record, candidate)
                    if (not replay.get('available') or replay.get('costs_assumed') is not False
                            or (not replay.get('full_forecast_horizon_observed') and replay.get('exit_reason') == 'horizon')):
                        exclusions.append({'review_id': review_id, 'action': candidate['candidate_id'],
                                           'reason': replay.get('reason', 'CANDIDATE_CONTINUATION_UNAVAILABLE')})
                        continue
                    admitted.append((candidate, replay))
                for family_id, family in sorted(evidence['families'].items()):
                    features, metas, windows = {}, {}, {}
                    for name, value in sorted(family['features'].items()):
                        meta = family['feature_provenance'].get(name)
                        if not meta or meta.get('context_only') or _number(meta.get('received_ts')) is None:
                            continue
                        features[name], metas[name] = value, deepcopy(meta)
                        if meta.get('window_seconds') is not None:
                            windows[name] = meta['window_seconds']
                    if not features:
                        exclusions.append({'review_id': review_id, 'family_id': family_id,
                            'reason': 'NO_ADMITTED_CAUSAL_FEATURES', 'source_rejections': family['rejected_sources']})
                        continue
                    if len(features) > 32:
                        exclusions.append({'review_id': review_id, 'family_id': family_id,
                                           'reason': 'FAMILY_FEATURE_BOUND_EXCEEDED'})
                        continue
                    for candidate, replay in admitted:
                        delta = replay['net_r_on_remaining'] - hold['net_r_on_remaining']
                        rows.append({'trade_id': record['trade_id'], 'review_id': record['review_id'],
                            'captured_ts': cutoff, 'label_end_ts': max(label_end, cutoff + replay['observed_horizon_minutes'] * 60.),
                            'instrument': instrument, 'family_id': family_id, 'horizon_minutes': horizon,
                            'geometry_sha256': geometry, 'features': deepcopy(features), 'feature_provenance': deepcopy(metas),
                            'feature_windows_sec': deepcopy(windows), 'action': candidate['candidate_id'],
                            'candidate': {key: deepcopy(candidate[key]) for key in ('candidate_id', 'policy', 'parameters')},
                            'delta_net_r': delta, 'costs_verified': True, 'cost_provenance': deepcopy(costs),
                            'synthetic': False, 'label_kind': 'OBSERVED_PATH_COUNTERFACTUAL_NOT_BROKER_FILL',
                            'execution_assumption': replay['execution_assumption'],
                            'net_basis': 'per_unit_of_current_remaining_position',
                            'snapshot_sha256': record['snapshot_sha256']})
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as exc:
                exclusions.append({'review_id': review_id, 'reason': str(exc)[:200]})
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        exclusions.append({'review_id': None, 'reason': str(exc)[:200]})
        rows.clear()
    output.update(dataset_sha256=_hash(rows), available=bool(rows),
                  reason='CAUSAL_COUNTERFACTUAL_ROWS_AVAILABLE' if rows else 'NO_ADMISSIBLE_CAUSAL_NET_LABELS')
    return output
