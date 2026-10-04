"""Bounded affine-R family identity; never reconstruct replay prices or labels.

Only explicit v1 descriptor equality is portable. Absence of every extension
field keeps the legacy exact contract; an unknown/partial extension fails closed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

VERSION = 'edge-family-affine-r-geometry-v1'
MAX_EVIDENCE_BYTES = 48 * 1024
MAX_DESCRIPTOR_BYTES = 32 * 1024
SUPPORTED = {'CLOSE_10', 'CLOSE_25', 'CLOSE_50', 'EXIT'}
EXTENSION_FIELDS = {'geometry_contract', 'geometry_descriptor', 'geometry_evidence',
                    'exact_geometry_sha256'}
PRICE_FIELDS = {'entry', 'original_stop', 'active_risk_barrier', 'current', 'final_take'}
EXPOSURE_PRICES = {'active_stop_price', 'original_stop', 'original_take', 'take'}
PARAMETER_PRICES = {'stop_price', 'take_price', 'trigger_price'}
ERROR = 'PORTABLE_GEOMETRY_INVALID'


def _json(value, limit):
    def check(item, depth=0):
        if depth > 16:
            raise ValueError(ERROR)
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError(ERROR)
            for child in item.values():
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise ValueError(ERROR)
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            _number(item)
    check(value)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                     allow_nan=False).encode()
    if len(raw) > limit:
        raise ValueError('PORTABLE_GEOMETRY_BOUND_EXCEEDED')
    return raw


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(ERROR)
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(ERROR)
    return value


def geometry_descriptor_sha256(descriptor: dict) -> str:
    return hashlib.sha256(_json(descriptor, MAX_DESCRIPTOR_BYTES)).hexdigest()


def portable_geometry(snapshot: dict) -> dict:
    """Validate original geometry first, then normalize known price fields only."""
    from .edge_family_dataset import _execution_geometry

    descriptor = _execution_geometry(snapshot)
    # Validate the entire projection too: optional exposure/parameter values may
    # otherwise escape numeric checks in the legacy exact geometry validator.
    _json(descriptor, MAX_EVIDENCE_BYTES)
    prices = descriptor['prices']
    entry = _number(prices['entry'])
    risk = entry - _number(prices['original_stop'])
    if not math.isfinite(risk) or risk == 0:
        raise ValueError(ERROR)

    def ratio(value):
        normalized = (_number(value) - entry) / risk
        if not math.isfinite(normalized):
            raise ValueError(ERROR)
        normalized = round(normalized, 12)
        return 0. if normalized == 0 else normalized

    # Check the unrounded arithmetic before deriving identity. Overflow or
    # cancellation must never be rounded into apparent execution consistency.
    for key, execution in (('current', 'r0'), ('active_risk_barrier', 'stop_r'), ('final_take', 'T')):
        value = (_number(prices[key]) - entry) / risk
        if not math.isfinite(value) or abs(value - descriptor['execution_inputs'][execution]) > 1e-4:
            raise ValueError('FROZEN_PRICE_AND_EXECUTION_R_MISMATCH')
    for key in ('remaining_position_fraction', 'realized_position_fraction', 'realized_r_weighted'):
        if key in descriptor['exposure']:
            descriptor['exposure'][key] = _number(descriptor['exposure'][key])
    descriptor['version'] = VERSION
    descriptor['prices'] = {key: ratio(value) for key, value in prices.items()}
    for key in EXPOSURE_PRICES & descriptor['exposure'].keys():
        descriptor['exposure'][key] = ratio(descriptor['exposure'][key])
    for candidate in descriptor['candidate_matrix']:
        for key in PARAMETER_PRICES & candidate['parameters'].keys():
            candidate['parameters'][key] = ratio(candidate['parameters'][key])
    # Normalize matrix order after prices: absolute ordering can differ for shorts.
    descriptor['candidate_matrix'].sort(key=lambda row: _json(row, MAX_DESCRIPTOR_BYTES))
    _json(descriptor, MAX_DESCRIPTOR_BYTES)
    return descriptor


def geometry_evidence(snapshot: dict) -> dict:
    """Frozen proof containing all fields consumed by original geometry validation."""
    portable_geometry(snapshot)
    manager = snapshot['policy_manager']
    evidence = {key: deepcopy(snapshot[key]) for key in
                ('captured_ts', 'trade_id', 'instrument', 'trade_geometry', 'position_state',
                 'active_management_candidates') if key in snapshot}
    evidence['strategy'] = {key: deepcopy(snapshot.get('strategy', {})[key])
                            for key in ('instrument', 'direction') if key in snapshot.get('strategy', {})}
    evidence['policy_manager'] = {key: deepcopy(manager[key]) for key in
        ('inputs', 'policies', 'selection_rule', 'risk_constraint', 'gate', 'input_audit') if key in manager}
    _json(evidence, MAX_EVIDENCE_BYTES)
    return evidence


def _has_extension(value):
    return bool(EXTENSION_FIELDS & value.keys())


def _descriptor_valid(descriptor):
    """Revalidate descriptor shape/topology using unit-risk geometry, without replay."""
    from .edge_family_dataset import EXECUTION_KEYS, PARAMETERS
    from .edge_family_action_binding import validate_binding
    from .unified_edge_ensemble import BASE
    if (not isinstance(descriptor, dict) or set(descriptor) != {
            'version', 'instrument', 'direction', 'execution_inputs', 'prices',
            'active_risk_barrier_type', 'exposure', 'candidate_matrix'}
            or descriptor['version'] != VERSION):
        raise ValueError(ERROR)
    _json(descriptor, MAX_DESCRIPTOR_BYTES)
    execution, prices, exposure, matrix = (descriptor[key] for key in
        ('execution_inputs', 'prices', 'exposure', 'candidate_matrix'))
    if (not isinstance(execution, dict) or set(execution) != {*EXECUTION_KEYS, 'rungs'}
            or not isinstance(prices, dict) or set(prices) != PRICE_FIELDS
            or prices.get('entry') != 0. or prices.get('original_stop') != -1.
            or not isinstance(exposure, dict) or set(exposure) - {
                'remaining_position_fraction', 'realized_position_fraction', 'realized_r_weighted',
                'active_stop_price', 'active_stop_type', 'be_armed', 'original_stop', 'original_take', 'take'}
            or not isinstance(matrix, list) or len(matrix) > 133):
        raise ValueError(ERROR)
    for key in PRICE_FIELDS:
        _number(prices[key])
    sign = 1. if descriptor['direction'] == 'long' else -1.
    active = []
    for candidate in matrix:
        if (not isinstance(candidate, dict) or set(candidate) != {'policy', 'parameters'}
                or candidate['policy'] not in {*BASE, *PARAMETERS}
                or not isinstance(candidate['parameters'], dict)):
            raise ValueError(ERROR)
        policy, params = candidate['policy'], deepcopy(candidate['parameters'])
        if policy in BASE:
            if params:
                raise ValueError(ERROR)
            continue
        if policy == 'TIME_STOP':
            params = validate_binding({'version': 'edge-family-time-stop-binding-v1',
                                       'policy': policy, 'parameters': params})['parameters']
            params['deadline_ts'] = 1. + params.pop('deadline_offset_sec')
        else:
            for key in PARAMETER_PRICES & params.keys():
                params[key] = _number(params[key]) * sign
        active.append({'policy': policy, 'parameters': params})
    position = deepcopy(exposure)
    for key in EXPOSURE_PRICES & position.keys():
        position[key] = _number(position[key]) * sign
    value = {'captured_ts': 1., 'strategy': {'instrument': descriptor['instrument'], 'direction': descriptor['direction']},
             'policy_manager': {'inputs': deepcopy(execution)}, 'position_state': position,
             'trade_geometry': {**{key: prices[key] * sign for key in PRICE_FIELDS},
                               'active_risk_barrier_type': descriptor['active_risk_barrier_type']},
             'active_management_candidates': active}
    if _json(portable_geometry(value), MAX_DESCRIPTOR_BYTES) != _json(descriptor, MAX_DESCRIPTOR_BYTES):
        raise ValueError(ERROR)


def _extension_reason(value):
    if not _has_extension(value):
        return None
    if value.get('geometry_contract') != VERSION:
        return 'PORTABLE_GEOMETRY_CONTRACT_UNSUPPORTED'
    try:
        descriptor = value['geometry_descriptor']
        _descriptor_valid(descriptor)
        if (geometry_descriptor_sha256(descriptor) != value.get('geometry_sha256')
                or descriptor['instrument'] != value.get('instrument')
                or descriptor['execution_inputs']['horizon_minutes'] != value.get('horizon_minutes')):
            return ERROR
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        return ERROR
    return None


def _portable_action(action, model):
    from .edge_family_action_binding import PREFIX, action_binding_valid
    return ((isinstance(action, str) and action in SUPPORTED and 'action_binding' not in model)
            or (isinstance(action, str) and action.startswith(PREFIX)
                and 'action_binding' in model and action_binding_valid(action, model)))


def portable_row_reason(row: dict) -> str | None:
    if not isinstance(row, dict):
        return ERROR
    reason = _extension_reason(row)
    if reason or not _has_extension(row):
        return reason
    if not _portable_action(row.get('action'), row):
        return 'PORTABLE_ACTION_UNSUPPORTED'
    try:
        from .edge_family_dataset import family_geometry_sha256
        from .edge_family_action_binding import candidate_binding, validate_binding
        from .unified_edge_ensemble import collect_candidates
        evidence = row['geometry_evidence']
        _json(evidence, MAX_EVIDENCE_BYTES)
        if (geometry_evidence(evidence) != evidence
                or evidence.get('captured_ts') != row.get('captured_ts')
                or evidence.get('trade_id') != row.get('trade_id')
                or family_geometry_sha256(evidence) != row.get('exact_geometry_sha256')
                or portable_geometry(evidence) != row['geometry_descriptor']):
            return ERROR
        costs = row.get('cost_provenance', {})
        position = costs.get('position_evidence', {})
        if (costs.get('direction') != row['geometry_descriptor']['direction']
                or any(_number(position.get(key)) != _number(evidence['trade_geometry'][key])
                       for key in ('entry', 'original_stop'))
                or _number(position.get('remaining_position_fraction')) !=
                   _number(evidence['position_state']['remaining_position_fraction'])):
            return 'PORTABLE_POSITION_EVIDENCE_MISMATCH'
        candidate = row['candidate']
        if (not isinstance(candidate, dict) or set(candidate) != {'candidate_id', 'policy', 'parameters'}
                or candidate not in [{key: item[key] for key in candidate}
                                     for item in collect_candidates(evidence)]):
            return 'PORTABLE_CANDIDATE_NOT_IN_ORIGINAL_MATRIX'
        if 'action_binding' in row:
            if candidate_binding(candidate, row['captured_ts']) != validate_binding(row['action_binding']):
                return ERROR
        elif candidate['candidate_id'] != row['action'] or candidate['policy'] != row['action']:
            return ERROR
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        return ERROR
    return None


def portable_artifact_reason(artifact: dict) -> str | None:
    if not isinstance(artifact, dict):
        return ERROR
    reason = _extension_reason(artifact)
    if reason or not _has_extension(artifact):
        return reason
    if {'geometry_evidence', 'exact_geometry_sha256'} & artifact.keys():
        return ERROR
    models = artifact.get('action_models')
    if (not isinstance(models, dict) or not models
            or any(not isinstance(model, dict) or not _portable_action(action, model)
                   for action, model in models.items())):
        return 'PORTABLE_ACTION_UNSUPPORTED'
    from .edge_family_action_binding import validate_binding
    matrix = artifact['geometry_descriptor']['candidate_matrix']
    for action, model in models.items():
        if 'action_binding' in model:
            candidate = {'policy': 'TIME_STOP',
                         'parameters': validate_binding(model['action_binding'])['parameters']}
        else:
            candidate = {'policy': action, 'parameters': {}}
        if candidate not in matrix:
            return 'PORTABLE_ACTION_NOT_IN_DESCRIPTOR'
    return None


def portable_geometry_matches(artifact: dict, snapshot: dict) -> bool:
    if (not isinstance(artifact, dict) or not _has_extension(artifact)
            or portable_artifact_reason(artifact) is not None):
        return False
    try:
        return portable_geometry(snapshot) == artifact['geometry_descriptor']
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        return False
