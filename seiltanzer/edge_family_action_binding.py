"""Family-local TIME_STOP identities; production candidate IDs remain concrete.

Binding only removes the captured clock from the deadline. All supplied action
parameters and their presence remain part of the identity. No price geometry,
execution admission or eligibility is changed by this module.
"""
from __future__ import annotations

import hashlib
import json
import math

VERSION = 'edge-family-time-stop-binding-v1'
PREFIX = 'TIME_STOP_RELATIVE:'


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    return value


def validate_binding(binding):
    """Return canonical numeric parameters, rejecting unknown/inconsistent data."""
    if (not isinstance(binding, dict) or set(binding) != {'version', 'policy', 'parameters'}
            or binding['version'] != VERSION or binding['policy'] != 'TIME_STOP'):
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    params = binding['parameters']
    if (not isinstance(params, dict) or 'deadline_offset_sec' not in params
            or set(params) - {'deadline_offset_sec', 'timeout_minutes'}):
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    offset = _number(params['deadline_offset_sec'])
    if offset <= 0:
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    normalized = {'deadline_offset_sec': offset}
    if 'timeout_minutes' in params:
        minutes = _number(params['timeout_minutes'])
        if minutes <= 0 or minutes * 60. != offset:
            raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
        normalized['timeout_minutes'] = minutes
    return {'version': VERSION, 'policy': 'TIME_STOP', 'parameters': normalized}


def stable_action_id(binding):
    normalized = validate_binding(binding)
    raw = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return PREFIX + hashlib.sha256(raw).hexdigest()


def candidate_binding(candidate, captured_ts):
    """Recompute binding from truthful original candidate and capture clock."""
    from .unified_edge_ensemble import candidate_id

    if (not isinstance(candidate, dict) or candidate.get('policy') != 'TIME_STOP'
            or not isinstance(candidate.get('parameters'), dict)):
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    params = candidate['parameters']
    if 'deadline_ts' not in params or set(params) - {'deadline_ts', 'timeout_minutes'}:
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    captured = _number(captured_ts)
    if captured <= 0 or candidate.get('candidate_id') != candidate_id('TIME_STOP', params):
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    deadline = _number(params['deadline_ts'])
    normalized = {'deadline_offset_sec': deadline - captured}
    if 'timeout_minutes' in params:
        minutes = _number(params['timeout_minutes'])
        # The production producer adds the declared timeout to the epoch.
        # Repeating that exact addition avoids subtraction roundoff while
        # rejecting even an adjacent representable but different deadline.
        offset = minutes * 60.
        if deadline != captured + offset or deadline <= captured:
            raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
        normalized = {'deadline_offset_sec': offset, 'timeout_minutes': minutes}
    return validate_binding({'version': VERSION, 'policy': 'TIME_STOP', 'parameters': normalized})


def action_binding_valid(action, model):
    """Optional extension: legacy actions retain their original admission path."""
    if 'action_binding' not in model:
        return not (isinstance(action, str) and action.startswith(PREFIX))
    try:
        return stable_action_id(model['action_binding']) == action
    except (ValueError, TypeError, OverflowError):
        return False


def resolve_action(action, binding, snapshot):
    """Resolve one stable action to exactly one current concrete candidate."""
    from .unified_edge_ensemble import collect_candidates

    if stable_action_id(binding) != action:
        raise ValueError('TIME_STOP_ACTION_BINDING_INVALID')
    expected = validate_binding(binding)
    matches = []
    for candidate in collect_candidates(snapshot):
        if candidate['policy'] != 'TIME_STOP':
            continue
        try:
            if candidate_binding(candidate, snapshot.get('captured_ts')) == expected:
                matches.append(candidate['candidate_id'])
        except (ValueError, TypeError, OverflowError):
            continue
    if len(matches) != 1:
        raise ValueError('TIME_STOP_BOUND_CANDIDATE_UNAVAILABLE')
    return matches[0]
