"""Independently pinned executing-position evidence, never a broker connection.

A verified read-only export binds current local geometry to execution units.
Activation still requires an actual executing-account source and a separately
configured immutable document pin. Cost evidence cannot supply this identity.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

VERSION = 'broker-position-context-v1'
MAX_BYTES = 48_000
IDENTITY_KEYS = ('broker_id', 'account_id', 'broker_position_id', 'trade_id', 'instrument', 'direction')
UNIT_KEYS = ('currency', 'quantity_units', 'risk_currency_per_unit', 'quantity_basis')


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _text(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value


def _hash(value, length):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{%d}' % length, value) is not None


def _result(reason, **details):
    # Audit is safe for generic status reporting: private account/position/source
    # identifiers only occur in the dedicated operational identity output.
    audit = {'version': VERSION, 'available': False, 'reason': reason,
        'broker_fill_verified': False, 'effectiveness_verified': False,
        'automatic_execution_allowed': False,
        'evidence_scope': 'frozen_executing_broker_position_observation_not_fill_proof'}
    audit.update(details)
    return {'version': VERSION, 'available': audit['available'], 'reason': reason, 'audit': audit}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('nonfinite value')
    return number


def _reject_constant(value):
    raise ValueError('nonfinite constant')


def _check_json_depth(document):
    # json.loads can accept deeply nested arrays on some Python runtimes.
    # Enforce the boundary without depending on their recursion limit.
    pending = [(document, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 32:
            raise ValueError('nested context')
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def _validate(document, snapshot, expected_deployment_sha, digest):
    if (not isinstance(document, dict) or document.get('version') != VERSION
            or not isinstance(snapshot, dict)):
        return _result('POSITION_CONTEXT_SCHEMA_INVALID')
    if (not _hash(expected_deployment_sha, 40)
            or document.get('deployment_sha') != expected_deployment_sha):
        return _result('POSITION_CONTEXT_DEPLOYMENT_SHA_MISMATCH')
    strategy = snapshot.get('strategy')
    geometry = snapshot.get('trade_geometry')
    position = snapshot.get('position_state')
    if not all(isinstance(row, dict) for row in (strategy, geometry, position)):
        return _result('POSITION_CONTEXT_LOCAL_GEOMETRY_UNAVAILABLE')
    trade_id = snapshot.get('trade_id')
    if (isinstance(trade_id, bool) or not isinstance(trade_id, (int, str))
            or not trade_id or type(document.get('trade_id')) is not type(trade_id)
            or document.get('trade_id') != trade_id
            or document.get('source_verified') is not True
            or document.get('measurement_kind') != 'executing_broker_position'
            or not _text(document.get('source_id'))
            or not _hash(document.get('evidence_sha256'), 64)
            or any(not _text(document.get(key)) for key in IDENTITY_KEYS if key != 'trade_id')
            or document.get('instrument') != strategy.get('instrument', snapshot.get('instrument'))
            or document.get('direction') not in ('long', 'short')
            or document.get('direction') != strategy.get('direction', geometry.get('direction'))):
        return _result('POSITION_CONTEXT_SOURCE_OR_TRADE_IDENTITY_INVALID')
    cutoff = _number(snapshot.get('captured_ts'))
    observed, received, max_age = (_number(document.get(key))
        for key in ('observed_ts', 'received_ts', 'max_age_sec'))
    if (any(value is None for value in (cutoff, observed, received, max_age))
            or not 0 < observed <= received <= cutoff or not 0 < max_age <= 60
            or cutoff - observed > max_age):
        return _result('POSITION_CONTEXT_CLOCK_INVALID_OR_STALE')
    for key in ('entry', 'original_stop', 'remaining_position_fraction'):
        current = position.get(key) if key == 'remaining_position_fraction' else geometry.get(key)
        value, known = _number(document.get(key)), _number(current)
        if value is None or known is None or value <= 0 or value != known:
            return _result('POSITION_CONTEXT_GEOMETRY_OR_REMAINING_FRACTION_MISMATCH')
    fraction = document['remaining_position_fraction']
    if (fraction > 1 or document['entry'] == document['original_stop']
            or (document['direction'] == 'long' and document['original_stop'] >= document['entry'])
            or (document['direction'] == 'short' and document['original_stop'] <= document['entry'])
            or ('remaining_position_fraction' in geometry
                and (_number(geometry['remaining_position_fraction']) is None
                     or geometry['remaining_position_fraction'] != fraction))):
        return _result('POSITION_CONTEXT_GEOMETRY_OR_REMAINING_FRACTION_MISMATCH')
    if (not _text(document.get('currency'))
            or document.get('quantity_basis') != 'current_remaining_position'
            or any(_number(document.get(key)) is None or document[key] <= 0
                   for key in ('quantity_units', 'risk_currency_per_unit'))):
        return _result('POSITION_CONTEXT_CURRENCY_QUANTITY_OR_RISK_UNITS_INVALID')
    identity = {key: document[key] for key in IDENTITY_KEYS}
    units = {key: document[key] for key in UNIT_KEYS}
    for root, expected in (('trade_identity', identity), ('position_execution_units', units)):
        if root not in snapshot:
            continue
        known = snapshot[root]
        if (not isinstance(known, dict) or any(key in known and (
                isinstance(known[key], bool) or known[key] != value
                or (key == 'trade_id' and type(known[key]) is not type(value)))
                for key, value in expected.items())):
            return _result('POSITION_CONTEXT_EXISTING_IDENTITY_OR_UNITS_MISMATCH')
    # Source IDs can contain private account information. Retain their exact
    # lineage only alongside the operational identity, not in generic audit.
    identity['position_evidence'] = {
        'version': VERSION, 'source_verified': True,
        'document_sha256': digest, **{key: document[key] for key in (
            'source_id', 'evidence_sha256', 'deployment_sha', 'measurement_kind',
            'observed_ts', 'received_ts', 'max_age_sec', 'entry', 'original_stop',
            'remaining_position_fraction')}}
    result = _result('VERIFIED_EXECUTING_BROKER_POSITION', available=True,
        observed_ts=observed, received_ts=received, max_age_sec=max_age,
        deployment_sha=expected_deployment_sha, document_sha256=digest,
        evidence_sha256=document['evidence_sha256'], measurement_kind=document['measurement_kind'],
        entry=document['entry'], original_stop=document['original_stop'],
        remaining_position_fraction=fraction, quantity_basis=units['quantity_basis'])
    result.update(trade_identity=identity, position_execution_units=units)
    return result


def load_position_execution_context(path, *, snapshot, expected_deployment_sha,
                                    expected_document_sha256=None):
    """Read <=48 KB locally; bind a separate exact pin, current trade and clocks.

    No provider/DB/network call, inferred fraction scaling, fill assertion or
    private identifiers in failure reasons. The original snapshot is unchanged.
    """
    if not path:
        return _result('BROKER_POSITION_CONTEXT_UNCONFIGURED')
    if not _hash(expected_document_sha256, 64):
        return _result('POSITION_CONTEXT_DOCUMENT_SHA_UNAVAILABLE')
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
    except (OSError, ValueError, TypeError):
        return _result('POSITION_CONTEXT_FILE_UNAVAILABLE')
    if len(raw) > MAX_BYTES:
        return _result('POSITION_CONTEXT_EXCEEDS_BYTE_BOUND')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_document_sha256:
        return _result('POSITION_CONTEXT_DOCUMENT_SHA_MISMATCH')
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object,
            parse_float=_finite_float, parse_constant=_reject_constant)
        _check_json_depth(document)
    except (ValueError, UnicodeDecodeError, RecursionError, OverflowError):
        return _result('POSITION_CONTEXT_JSON_INVALID')
    return _validate(document, snapshot, expected_deployment_sha, digest)
