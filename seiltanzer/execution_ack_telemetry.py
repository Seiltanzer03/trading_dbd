"""Observed human acknowledgements; no inferred fills, fees or latency costs."""
from __future__ import annotations

import hashlib
import json
import math

VERSION = 'execution-ack-observation-v1'


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def ensure_execution_ack_schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS execution_ack_observations (
        decision_id TEXT NOT NULL, stage TEXT NOT NULL, trade_id INTEGER NOT NULL,
        review_id TEXT, decision_created_ts REAL, acknowledged_ts REAL NOT NULL,
        acknowledgement_delay_sec REAL, execution_price_source TEXT,
        execution_price REAL, execution_r REAL, actual_broker_fill_ts REAL,
        actual_fee_currency REAL, actual_slippage_currency REAL,
        manual_latency_cost_r REAL, payload_json TEXT NOT NULL,
        PRIMARY KEY(decision_id,stage))''')
    connection.execute('CREATE INDEX IF NOT EXISTS ix_execution_ack_review_ts '
                       'ON execution_ack_observations(review_id,acknowledged_ts)')


def record_execution_ack(connection, *, decision, status, acknowledged_ts,
                         execution_price_source='unspecified', execution_price=None,
                         execution_r=None):
    """Caller records only after an accepted acknowledgement, in its transaction.

    This table is observational and does not modify management/position rows.
    UI display time and broker execution time are not the server's ack clock.
    """
    identifier = decision.get('decision_id') or decision.get('action_id')
    stage = {'recommended_not_executed': 'declined', 'cancelled': 'cancelled',
             'armed': 'armed', 'executed': 'executed'}.get(status)
    created, received = _number(decision.get('created_ts')), _number(acknowledged_ts)
    trade_id = decision.get('trade_id')
    if (not isinstance(identifier, str) or not 0 < len(identifier) <= 200
            or stage is None or not isinstance(trade_id, int) or isinstance(trade_id, bool)
            or received is None or received <= 0 or created is None or not 0 < created <= received):
        return {'available': False, 'reason': 'ACK_OBSERVATION_IDENTITY_OR_CLOCK_INVALID'}
    policy = decision.get('policy')
    geometry_action = policy in {'MOVE_TO_BE', 'TRAIL_GAMMA_FLIP', 'TIGHTEN_STOP', 'EXTEND_TAKE', 'REDUCE_TAKE'}
    has_fill = stage == 'executed' and not geometry_action
    supplied_fill = execution_price_source == 'user_supplied_broker_fill' and has_fill
    price, r = (_number(execution_price), _number(execution_r)) if has_fill else (None, None)
    original = decision.get('payload_json') or '{}'
    audit = {'version': VERSION, 'status': status, 'stage': stage,
             'timestamp_scope': 'server_received_ack_not_broker_execution_time',
             'latency_scope': 'decision_created_to_server_ack_not_ui_display_to_fill',
             'execution_evidence': 'ACTION_GEOMETRY_CONFIRMED_NOT_FILL' if geometry_action and stage == 'executed'
                                   else 'USER_REPORTED_FILL' if supplied_fill and price is not None
                                   else 'QUOTE_AT_ACK_ESTIMATE' if price is not None and execution_price_source == 'quote_at_acknowledgement_estimate'
                                   else 'EXECUTION_PRICE_UNVERIFIED' if price is not None else 'NO_OBSERVED_FILL',
             'broker_fill_independently_verified': False,
             'position_mutation_performed_by_telemetry': False,
             'manual_latency_cost_status': 'UNAVAILABLE_NO_PRICE_REFERENCE_AND_BROKER_FILL_CLOCK',
             'actual_broker_fill_ts': None, 'actual_fee_currency': None,
             'actual_slippage_currency': None, 'manual_latency_cost_r': None,
             'decision_payload_sha256': hashlib.sha256(str(original).encode()).hexdigest()}
    payload = json.dumps(audit, sort_keys=True, allow_nan=False, separators=(',', ':'))
    cursor = connection.execute('''INSERT OR IGNORE INTO execution_ack_observations
        (decision_id,stage,trade_id,review_id,decision_created_ts,acknowledged_ts,
         acknowledgement_delay_sec,execution_price_source,execution_price,execution_r,
         actual_broker_fill_ts,actual_fee_currency,actual_slippage_currency,
         manual_latency_cost_r,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,?)''',
        (identifier, stage, trade_id, decision.get('review_id'), created, received,
         received - created, execution_price_source, price, r, payload))
    return {'available': True, 'recorded': cursor.rowcount == 1,
            'idempotent': cursor.rowcount != 1, 'stage': stage,
            'acknowledgement_delay_sec': received - created, 'audit': audit}
