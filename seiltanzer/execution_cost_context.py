"""Bounded frozen execution-cost evidence; no indicative quote becomes a fill.

This contract is an ingestion boundary, not a broker adapter. Explicit broker
identity, position units and causal evidence are necessary to replace assumed
costs. Unknown components stay null, including when other components are zero.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from .canonical_market_context import canonical_instrument_code

VERSION = 'broker-execution-cost-context-v1'
MAX_BYTES = 128_000
COMPONENTS = ('spread', 'commission', 'slippage', 'manual_latency')


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _unavailable(reason):
    return {'version': VERSION, 'available': False, 'complete_costs_available': False,
            'reason': reason, 'immediate_full_close_r': None,
            'deferred_full_close_r': None, 'assumed': None,
            'missing_is_zero_cost': False, 'automatic_execution_allowed': False}


def validate_execution_cost_context(document, *, snapshot, expected_deployment_sha,
                                    document_sha256=None):
    """Validate a caller-owned immutable document without writes or providers."""
    if not isinstance(document, dict) or document.get('version') != VERSION:
        return _unavailable('EXECUTION_CONTEXT_SCHEMA_INVALID')
    if (not isinstance(expected_deployment_sha, str) or len(expected_deployment_sha) != 40
            or any(c not in '0123456789abcdef' for c in expected_deployment_sha)
            or document.get('deployment_sha') != expected_deployment_sha):
        return _unavailable('EXECUTION_CONTEXT_DEPLOYMENT_SHA_MISMATCH')
    trade = snapshot.get('trade_identity') or {}
    strategy = snapshot.get('strategy') or {}
    geometry = snapshot.get('trade_geometry') or {}
    instrument = canonical_instrument_code(strategy.get('instrument') or snapshot.get('instrument'))
    if (document.get('source_verified') is not True
            or not isinstance(document.get('source_id'), str) or not document['source_id']
            or not document.get('broker_id') or not document.get('account_id')
            or document.get('trade_id') != snapshot.get('trade_id')
            or canonical_instrument_code(document.get('instrument')) != instrument
            or document.get('direction') not in {'long', 'short'}
            or document.get('direction') != strategy.get('direction', geometry.get('direction'))):
        return _unavailable('EXECUTION_CONTEXT_SOURCE_OR_TRADE_IDENTITY_INVALID')
    # Never assert that a public broker quote belongs to this user's account.
    if (trade.get('broker_id') != document['broker_id']
            or trade.get('account_id') != document['account_id']):
        return _unavailable('EXECUTING_BROKER_ACCOUNT_IDENTITY_UNAVAILABLE_OR_MISMATCH')
    cutoff = _number(snapshot.get('captured_ts'))
    observed, received, max_age = (_number(document.get(k)) for k in ('observed_ts', 'received_ts', 'max_age_sec'))
    if (cutoff is None or observed is None or received is None or max_age is None
            or not 0 < observed <= received <= cutoff or not 0 < max_age <= 86400
            or cutoff - observed > max_age):
        return _unavailable('EXECUTION_CONTEXT_SOURCE_CLOCK_INVALID_OR_STALE')
    inputs = ((snapshot.get('policy_manager') or {}).get('inputs') or {})
    horizon = _number(inputs.get('horizon_minutes'))
    start, end = (_number(document.get(k)) for k in ('coverage_start_epoch', 'coverage_end_epoch'))
    if (horizon is None or horizon <= 0 or start is None or end is None
            or start > cutoff or end < cutoff + 60. * horizon):
        return _unavailable('EXECUTION_CONTEXT_HORIZON_COVERAGE_INCOMPLETE')
    units = snapshot.get('position_execution_units') or {}
    quantity, risk = (_number(document.get(k)) for k in ('quantity_units', 'risk_currency_per_unit'))
    known_quantity, known_risk = (_number(units.get(k)) for k in ('quantity_units', 'risk_currency_per_unit'))
    currency = document.get('currency')
    if (not isinstance(currency, str) or not currency or units.get('currency') != currency
            or document.get('quantity_basis') != 'current_remaining_position'
            or units.get('quantity_basis') != 'current_remaining_position'
            or quantity is None or risk is None or quantity <= 0 or risk <= 0
            or quantity != known_quantity or risk != known_risk):
        return _unavailable('EXECUTION_CONTEXT_CURRENCY_QUANTITY_OR_RISK_UNITS_UNAVAILABLE')
    channels, missing = {}, {}
    for channel in ('immediate', 'deferred'):
        supplied = document.get(channel)
        if not isinstance(supplied, dict):
            supplied = {}
        values, absent = {}, []
        for component in COMPONENTS:
            item = supplied.get(component)
            if item is None:
                values[component] = None
                absent.append(component)
                continue
            if not isinstance(item, dict):
                return _unavailable('EXECUTION_CONTEXT_COMPONENT_INVALID')
            value = _number(item.get('cost_currency_per_unit'))
            ts = _number(item.get('observed_ts'))
            if (value is None or value < 0 or ts is None or not 0 < ts <= observed
                    or cutoff - ts > max_age
                    or item.get('measurement_kind') not in {'executing_broker_quote', 'measured_broker_fill'}
                    or not item.get('source_id') or not item.get('evidence_sha256')
                    or not isinstance(item['evidence_sha256'], str) or len(item['evidence_sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in item['evidence_sha256'])):
                return _unavailable('EXECUTION_CONTEXT_COMPONENT_PROVENANCE_INVALID')
            values[component] = value / risk
        channels[channel] = {'components_r': values, 'total_r': sum(values.values()) if not absent else None}
        missing[channel] = absent
    complete = not any(missing.values())
    result = _unavailable('VERIFIED_COMPLETE_BROKER_COSTS' if complete else 'PARTIAL_BROKER_COSTS_MISSING_COMPONENTS')
    result.update(available=True, complete_costs_available=complete,
                  immediate_full_close_r=channels['immediate']['total_r'],
                  deferred_full_close_r=channels['deferred']['total_r'],
                  assumed=False if complete else None, source=document['source_id'],
                  broker_id=document['broker_id'], account_id=document['account_id'],
                  observed_ts=observed, received_ts=received, max_age_sec=max_age,
                  currency=currency, quantity_units=quantity, risk_currency_per_unit=risk,
                  quantity_basis='current_remaining_position', horizon_minutes=horizon,
                  components=channels, missing_components=missing,
                  deployment_sha=expected_deployment_sha, document_sha256=document_sha256,
                  includes_rollover=False, includes_settled_historical_costs=False,
                  evidence_scope='frozen_broker_cost_measurements_not_realized_total_position_net')
    return result


def load_execution_cost_context(path, *, snapshot, expected_deployment_sha,
                                expected_document_sha256=None):
    """Read at most 128 KiB; require a pinned SHA of the imported source file."""
    if not path:
        return _unavailable('BROKER_EXECUTION_COST_CONTEXT_UNCONFIGURED')
    if (not isinstance(expected_document_sha256, str) or len(expected_document_sha256) != 64
            or any(c not in '0123456789abcdef' for c in expected_document_sha256)):
        return _unavailable('EXECUTION_CONTEXT_DOCUMENT_SHA_UNAVAILABLE')
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        return _unavailable('EXECUTION_CONTEXT_FILE_UNAVAILABLE')
    if len(raw) > MAX_BYTES:
        return _unavailable('EXECUTION_CONTEXT_EXCEEDS_BYTE_BOUND')
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_document_sha256:
        return _unavailable('EXECUTION_CONTEXT_DOCUMENT_SHA_MISMATCH')
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return _unavailable('EXECUTION_CONTEXT_JSON_INVALID')
    return validate_execution_cost_context(document, snapshot=snapshot,
        expected_deployment_sha=expected_deployment_sha, document_sha256=digest)
